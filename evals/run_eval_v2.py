#!/usr/bin/env python3
"""Standard eval v2 runner. Runs runs_per_page fresh, bounded executions per
page through agent.harness.run_case_execution, classifies each one with the
frozen rules in evals/score_v2.py (eval_config_v2.yaml), and writes one
results file that evals/score_v2.py can re-score.

Safeguards (eval_config_v2.yaml infra_errors, invalid_run_restart; the
approved plan's auth and billing rules):
- refuses to start inside a Claude Code session (CLAUDECODE) or when a
  guarded auth variable is set (names only; values are never read). It
  never reads a .env file.
- every execution has its own id, '<eval_id>-<case>-r<n>' plus '-a<k>' from
  attempt 2; the same id is the audit run_id, and only that id's audit rows
  are used to classify it.
- stops the series on the first auth or usage/rate-limit error and never
  falls back to another auth route.
- any other INVALID RUN stops the page and the series; on --resume the page
  reruns all its runs as a new attempt and every earlier record of the page
  is marked superseded (Kristian's decision 2A: a redone preflight comes
  first).
- pauses after 2 unclassified exceptions in a row; --resume continues the
  same attempt, nothing is superseded or replaced.
- a result whose is_error is not a real boolean is never read as false: the
  execution is an agent failure and the series stops.
- --resume reuses the stored eval_id and refuses when the config hash, key
  hash, model, git revision, budget, limits, runs per page or subset
  differ. A series interrupted while running is not resumed silently:
  --recover continues only when no execution was in flight, and otherwise
  marks the series abandoned, keeping every record and audit row.
- a series limited by --cases or --runs is labelled a smoke test, never a
  complete evaluation.

Usage (from a plain terminal, never inside Claude Code):
    python evals/run_eval_v2.py --set dev
    python evals/run_eval_v2.py --set dev --cases case_01 --runs 1   # smoke test
    python evals/run_eval_v2.py --set official --key-sha256 <hash>
    python evals/run_eval_v2.py --set dev --resume evals/results/v2/dev/<eval_id>.json [--recover]

Exit codes: 0 series complete (whatever the result label), 2 refused before
any execution, 3 stopped or abandoned (stop rule, INVALID RUN, a
non-boolean is_error, an interrupted execution, or an execution id that
already has audit rows), 4 paused (unclassified exceptions).
"""
import argparse
import asyncio
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agent import audit as agent_audit  # noqa: E402
from agent import harness  # noqa: E402
from agent.config import EVAL_MAX_BUDGET_USD, EVAL_MODEL, MAX_TOOL_CALLS, MAX_TURNS  # noqa: E402
from evals import score_v2  # noqa: E402

RESULTS_ROOT = REPO_ROOT / "evals" / "results" / "v2"
STOP_CATEGORIES = ("auth", "usage_or_rate_limit")
UNCLASSIFIED_PAUSE_AFTER = 2
CLAUDE_CODE_ENV = "CLAUDECODE"

# Set on the CLI process of every v2 execution, and only there. It identifies
# an evaluation startup to machine-wide session hooks, so they add no context
# to the model. Identification only: every harness check still runs.
EVAL_ENV = {"AGENT_EVAL_PROFILE": "claim-verification-eval"}

EXIT_OK, EXIT_REFUSED, EXIT_STOPPED, EXIT_PAUSED = 0, 2, 3, 4

# A series limited by --cases or --runs is a smoke test, never an evaluation.
SMOKE_KIND, FULL_KIND = "smoke_test", "full_series"
SMOKE_LABEL = "SMOKE TEST — not a complete evaluation"


class Refused(Exception):
    """A reason not to start; nothing is executed or written."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def git_state() -> tuple[str | None, bool | None]:
    """(HEAD commit, tracked tree dirty) or (None, None) without git."""
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
                              capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                               cwd=REPO_ROOT, capture_output=True, text=True,
                               check=True).stdout.strip() != ""
        return head, dirty
    except (OSError, subprocess.CalledProcessError):
        return None, None


def key_path_for(set_name: str, config: dict) -> Path:
    return REPO_ROOT / config["sets"][set_name]["answer_key"]


def rel(path: Path) -> str:
    try:
        return Path(path).resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return Path(path).as_posix()


def execution_id_for(eval_id: str, case_id: str, run_index: int, attempt: int) -> str:
    return f"{eval_id}-{case_id}-r{run_index}" + (f"-a{attempt}" if attempt > 1 else "")


def error_type_name(exc: BaseException | None) -> str | None:
    return None if exc is None else f"{type(exc).__module__}.{type(exc).__qualname__}"


def result_record(result) -> dict | None:
    """The frozen result shape. is_error is kept exactly as the SDK sent it,
    never converted; a non-boolean value fails the execution closed (see
    execute)."""
    if result is None:
        return None
    errors = result.errors
    return {
        "subtype": result.subtype,
        "is_error": result.is_error,
        "num_turns": result.num_turns,
        "result_text": result.result,
        "errors": [str(e) for e in errors] if isinstance(errors, list) else None,
    }


def next_execution(executions: list[dict], case_id: str, runs: int) -> tuple[int, int, bool] | None:
    """(attempt, run_index, starts_a_rerun) for the page's next execution,
    or None when its current attempt holds all its runs."""
    execs = [e for e in executions if e["case_id"] == case_id]
    if not execs:
        return 1, 1, False
    top = max(e["attempt"] for e in execs)
    current = sorted((e for e in execs if e["attempt"] == top), key=lambda e: e["run_index"])
    if current[-1]["status"] == score_v2.INVALID_RUN:
        return top + 1, 1, True
    if len(current) >= runs:
        return None
    return top, len(current) + 1, False


async def execute(eval_id: str, case_id: str, run_index: int, attempt: int, config: dict) -> dict:
    execution_id = execution_id_for(eval_id, case_id, run_index, attempt)
    started_at = utc_now()
    outcome = await harness.run_case_execution(
        case_id, execution_id, model=EVAL_MODEL, max_budget_usd=EVAL_MAX_BUDGET_USD,
        env=EVAL_ENV,
    )
    exc = outcome.error
    error_type = error_type_name(exc)
    error = None if exc is None else str(exc)
    result = result_record(outcome.result)
    findings = list(outcome.findings)
    rows = score_v2.load_audit_rows(agent_audit.AUDIT_DB_PATH, execution_id)
    verdict = score_v2.classify_execution(
        error_type, error, result, findings, rows, case_id, MAX_TURNS, MAX_TOOL_CALLS,
        config, error_origin=outcome.error_origin,
    )
    if result is not None and not isinstance(result["is_error"], bool):
        # V2-01: the scorer would read a missing or null is_error as false.
        # Never let that turn into a completed run; the series stops on it.
        verdict["failures"].append(
            f"result.is_error is {result['is_error']!r}, not a boolean (SDK contract break)")
        if verdict["status"] == score_v2.COMPLETED:
            verdict["status"] = score_v2.AGENT_FAILURE
    return {
        "execution_id": execution_id,
        "case_id": case_id,
        "run_index": run_index,
        "attempt": attempt,
        "superseded": False,
        "status": verdict["status"],
        "infra_category": verdict["infra_category"],
        "failures": verdict["failures"],
        "findings": findings,
        "error_type": error_type,
        "error": error,
        "error_origin": outcome.error_origin,
        "result": result,
        "num_turns": None if outcome.result is None else outcome.result.num_turns,
        "total_cost_usd": None if outcome.result is None else outcome.result.total_cost_usd,
        "init": outcome.init,
        "lifecycle": outcome.lifecycle,
        "audit_rows": len(rows),
        "started_at": started_at,
        "finished_at": utc_now(),
    }


def save(series: dict, out_path: Path) -> None:
    """Atomic write: a crash never leaves a half-written results file."""
    series["updated_at"] = utc_now()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(series, f, indent=2, ensure_ascii=False, default=str)
        f.write("\n")
    os.replace(tmp, out_path)


def add_event(series: dict, kind: str, detail: str) -> None:
    series["series_events"].append({"time": utc_now(), "kind": kind, "detail": detail})
    print(f"[{kind}] {detail}")


def resume_command(series: dict, out_path: Path) -> str:
    cmd = f"python evals/run_eval_v2.py --set {series['set']} --resume {rel(out_path)}"
    if series["set"] == "official":
        cmd += f" --key-sha256 {series['answer_key_sha256']}"
    return cmd


def halt(series: dict, state: str, kind: str, reason: str, out_path: Path, code: int) -> int:
    series["state"] = state
    add_event(series, kind, reason)
    save(series, out_path)
    print(f"Series {state}. Results so far: {rel(out_path)}")
    if state != "abandoned":
        print(f"To continue: {resume_command(series, out_path)}")
    return code


def subset_key(key: dict, series: dict) -> dict:
    subset = series.get("subset")
    if not subset:
        return key
    return {**key, "cases": [c for c in key["cases"] if c["case_id"] in subset["cases"]]}


async def run_series(series: dict, key: dict, config: dict, out_path: Path) -> int:
    subset = series.get("subset")
    runs = subset["runs"] if subset else config["runs_per_page"]
    case_ids = subset["cases"] if subset else [c["case_id"] for c in key["cases"]]
    unclassified_in_a_row = 0
    init_shown = False
    for case_id in case_ids:
        while (step := next_execution(series["executions"], case_id, runs)) is not None:
            attempt, run_index, rerun = step
            execution_id = execution_id_for(series["eval_id"], case_id, run_index, attempt)
            if score_v2.load_audit_rows(agent_audit.AUDIT_DB_PATH, execution_id):
                return halt(series, "abandoned", "stop",
                            f"{execution_id} already has audit rows but no record; an execution "
                            "id is never reused. Every record and audit row is kept; start a new series",
                            out_path, EXIT_STOPPED)
            if rerun:
                for e in series["executions"]:
                    if e["case_id"] == case_id:
                        e["superseded"] = True
                add_event(series, "rerun", f"{case_id}: attempt {attempt} reruns all {runs} run(s); "
                          f"every record of attempt(s) 1-{attempt - 1} is marked superseded")
                save(series, out_path)
            print(f"-> {execution_id}")
            # Marks the execution in flight: an interruption leaves it here,
            # so it can never be lost silently (see recover).
            series["pending"] = {"execution_id": execution_id, "started_at": utc_now()}
            save(series, out_path)
            record = await execute(series["eval_id"], case_id, run_index, attempt, config)
            series["executions"].append(record)
            series["pending"] = None
            save(series, out_path)
            init = record["init"]
            if init and not init_shown:
                init_shown = True
                print(f"   init check passed: apiKeySource {init.get('apiKeySource')!r}; "
                      f"model {init.get('model')!r}\n"
                      f"   observed MCP tools: {init.get('tools')!r}\n"
                      f"   lifecycle hook messages: {len(record['lifecycle'])} (no output allowed)")
            elif not init:
                print("   init check: not passed or no init message (see the failure text)")
            cost = record["total_cost_usd"]
            print(f"   {record['status']}"
                  + (f" ({record['infra_category']})" if record["infra_category"] else "")
                  + f" | turns {record['num_turns']} | "
                  + (f"${cost:.4f} SDK API-equivalent, not a charge" if cost is not None else "no cost")
                  + "".join(f"\n   - {f}" for f in record["failures"]))

            if record["status"] == score_v2.INVALID_RUN:
                category = record["infra_category"]
                if category in STOP_CATEGORIES:
                    reason = (f"stop rule: {category} error in {execution_id}; "
                              "the runner never falls back to another auth route")
                else:
                    reason = (f"INVALID RUN ({category}) in {execution_id}; page {case_id} stops; "
                              f"after a redone preflight, --resume reruns it as attempt {attempt + 1}")
                return halt(series, "stopped", "stop", reason, out_path, EXIT_STOPPED)
            if record["result"] is not None and not isinstance(record["result"]["is_error"], bool):
                return halt(series, "stopped", "stop",
                            f"{execution_id}: result.is_error is {record['result']['is_error']!r}, "
                            "not a boolean; recorded as an agent failure; check the SDK before "
                            "resuming", out_path, EXIT_STOPPED)
            raised =record["error_type"] is not None or record["error"] is not None
            if raised and record["infra_category"] is None:
                unclassified_in_a_row += 1
                if unclassified_in_a_row >= UNCLASSIFIED_PAUSE_AFTER:
                    return halt(series, "paused", "pause",
                                f"{unclassified_in_a_row} unclassified exceptions in a row "
                                f"(last: {execution_id}: {record['error_type']}); they stay scored "
                                "agent failures; --resume continues the same attempt",
                                out_path, EXIT_PAUSED)
            else:
                unclassified_in_a_row = 0

    scored = score_v2.score(subset_key(key, series), series["executions"], config)
    series["state"] = "complete"
    series["summary"] = {"kind": series["kind"],
                         **{k: scored[k] for k in ("result", "gates", "record_problems", "attempt_problems")}}
    add_event(series, "complete", f"result {scored['result']}")
    save(series, out_path)
    print()
    if series["kind"] == SMOKE_KIND:
        print(f"{SMOKE_LABEL}: {len(case_ids)} case(s) x {runs} run(s)")
    print(score_v2.format_report(scored))
    print(f"\nResults: {rel(out_path)}")
    return EXIT_OK


def prepare(args, environ) -> tuple[dict, dict, dict, Path]:
    """All checks before any execution. Returns (series, key, config,
    out_path) or raises Refused."""
    if CLAUDE_CODE_ENV in environ:
        raise Refused(f"{CLAUDE_CODE_ENV} is set: run from a plain terminal, not inside Claude Code")
    try:
        harness.check_env(environ)
    except harness.AuthGuardError as exc:
        raise Refused(str(exc)) from None

    config = score_v2.load_config()
    official = args.set == "official"
    if official and (args.cases or args.runs is not None):
        raise Refused("the official set always runs every page x runs_per_page")
    if official and not args.key_sha256:
        raise Refused("the official set needs --key-sha256 (the frozen answer-key hash)")

    key_path = key_path_for(args.set, config)
    if not key_path.is_file():
        raise Refused(f"answer key not found: {rel(key_path)}")
    key_hash = score_v2.key_sha256(key_path)
    if args.key_sha256 and args.key_sha256.lower() != key_hash:
        raise Refused(f"answer key SHA-256 {key_hash} differs from --key-sha256 {args.key_sha256}")
    key = score_v2.load_key(key_path)
    if key.get("set") != args.set:
        raise Refused(f"answer key declares set {key.get('set')!r}, expected {args.set!r}")
    if official and (problems := score_v2.key_problems(key, config)):
        raise Refused("the official key is not an official set: " + "; ".join(problems))
    config_hash = score_v2.key_sha256(score_v2.CONFIG_PATH)
    head, dirty = git_state()
    if official and (head is None or dirty):
        raise Refused("the official set needs a clean, committed tree (git HEAD known, no tracked changes)")

    if args.recover and not args.resume:
        raise Refused("--recover needs --resume <results file>")
    if args.resume:
        out_path = Path(args.resume)
        if not out_path.is_absolute():
            out_path = REPO_ROOT / out_path
        if not out_path.is_file():
            raise Refused(f"results file not found: {args.resume}")
        with open(out_path, encoding="utf-8") as f:
            series = json.load(f)
        stored = series.get("subset")
        if stored is not None:  # the stored subset must still be a valid one
            if not isinstance(stored, dict) or build_subset(
                    ",".join(stored.get("cases") or []), stored.get("runs"), key, config) != stored:
                raise Refused(f"cannot resume: stored subset {stored!r} is not valid for this key")
        cases = stored["cases"] if stored else [c["case_id"] for c in key["cases"]]
        runs = stored["runs"] if stored else config["runs_per_page"]
        outside = [e.get("execution_id") for e in series.get("executions") or []
                   if e.get("case_id") not in cases or not isinstance(e.get("run_index"), int)
                   or e["run_index"] > runs]
        if outside:
            raise Refused(f"cannot resume: record(s) outside the stored subset: {outside}")
        if args.cases or args.runs is not None:
            if build_subset(args.cases, args.runs, key, config) != stored:
                raise Refused(f"cannot resume: --cases/--runs differ from the stored subset {stored!r}")
        expected = {
            "version": 2, "eval_id": out_path.stem, "set": args.set,
            "answer_key_sha256": key_hash, "config_sha256": config_hash, "git_head": head,
            "model": EVAL_MODEL, "max_budget_usd": EVAL_MAX_BUDGET_USD, "max_turns": MAX_TURNS,
            "max_tool_calls": MAX_TOOL_CALLS, "runs_per_page": config["runs_per_page"],
            "kind": SMOKE_KIND if stored else FULL_KIND,
        }
        for field, value in expected.items():
            if series.get(field) != value:
                raise Refused(f"cannot resume: {field} is {series.get(field)!r} in the file, "
                              f"{value!r} now")
        state = series.get("state")
        if state in ("complete", "abandoned"):
            raise Refused(f"cannot resume: the series is {state}")
        if state == "running" and not args.recover:
            raise Refused("the series was interrupted while running (state 'running'); an execution "
                          "may have been in flight. Nothing was changed. Re-run with --recover to "
                          "check it: --recover continues only when no execution was in flight, and "
                          "otherwise marks the series abandoned, keeping every record and audit row")
        if args.recover and state != "running":
            raise Refused(f"--recover is only for an interrupted series (state is {state!r})")
        return series, key, config, out_path

    subset = build_subset(args.cases, args.runs, key, config)
    eval_id = f"ev2-{uuid.uuid4().hex[:8]}"
    out_path = RESULTS_ROOT / args.set / f"{eval_id}.json"
    series = {
        "version": 2,
        "eval_id": eval_id,
        "set": args.set,
        "answer_key": rel(key_path),
        "answer_key_sha256": key_hash,
        "config_sha256": config_hash,
        "git_head": head,
        "git_dirty": dirty,
        "model": EVAL_MODEL,
        "max_budget_usd": EVAL_MAX_BUDGET_USD,
        "max_turns": MAX_TURNS,
        "max_tool_calls": MAX_TOOL_CALLS,
        "runs_per_page": config["runs_per_page"],
        "subset": subset,
        "kind": SMOKE_KIND if subset else FULL_KIND,
        "started_at": utc_now(),
        "state": "running",
        "pending": None,
        "series_events": [],
        "executions": [],
    }
    return series, key, config, out_path


def build_subset(cases_arg, runs_arg, key: dict, config: dict) -> dict | None:
    """The dev smoke-test subset from --cases/--runs, or None for a full series."""
    if not cases_arg and runs_arg is None:
        return None
    known = [c["case_id"] for c in key["cases"]]
    cases = cases_arg.split(",") if cases_arg else known
    unknown = [c for c in cases if c not in known]
    if unknown or len(set(cases)) != len(cases):
        raise Refused(f"--cases must be distinct cases of the key; unknown: {unknown}")
    runs = config["runs_per_page"] if runs_arg is None else runs_arg
    if not isinstance(runs, int) or isinstance(runs, bool) or not 1 <= runs <= config["runs_per_page"]:
        raise Refused(f"--runs must be 1 to {config['runs_per_page']}")
    return {"cases": [c for c in known if c in cases], "runs": runs}


def recover(series: dict, out_path: Path) -> int | None:
    """Fail-closed recovery of an interrupted series. With an execution in
    flight its outcome is unknown: the series is marked abandoned and every
    record and audit row stays. None means nothing was in flight and the
    series may continue."""
    pending = series.get("pending")
    if pending:
        execution_id = pending.get("execution_id")
        rows = len(score_v2.load_audit_rows(agent_audit.AUDIT_DB_PATH, execution_id))
        return halt(series, "abandoned", "interrupted",
                    f"{execution_id} (started {pending.get('started_at')}) has no record and "
                    f"{rows} audit row(s); its outcome is unknown, so this series is abandoned. "
                    "Every record and audit row is kept; start a new series",
                    out_path, EXIT_STOPPED)
    add_event(series, "recover", "interrupted with no execution in flight; continuing")
    return None


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Standard eval v2 runner")
    parser.add_argument("--set", required=True, choices=["dev", "official"])
    parser.add_argument("--cases", help="dev only: comma-separated case ids (dry run)")
    parser.add_argument("--runs", type=int, help="dev only: runs per page, 1 to runs_per_page")
    parser.add_argument("--key-sha256", help="official: the frozen LF-normalized answer-key SHA-256")
    parser.add_argument("--resume", help="results file of a stopped or paused series")
    parser.add_argument("--recover", action="store_true",
                        help="with --resume: check a series interrupted while running (fail-closed)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
    args = parse_args(argv)
    try:
        series, key, config, out_path = prepare(args, os.environ)
    except Refused as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    print(f"Standard eval v2 — set {series['set']} — {series['eval_id']} — model {series['model']}")
    if series["kind"] == SMOKE_KIND:
        print(f"{SMOKE_LABEL}: {series['subset']}")
    if args.recover and (code := recover(series, out_path)) is not None:
        return code
    if args.resume:
        add_event(series, "resume", f"resumed from state {series['state']!r}")
        series["state"] = "running"
    return asyncio.run(run_series(series, key, config, out_path))


if __name__ == "__main__":
    sys.exit(main())
