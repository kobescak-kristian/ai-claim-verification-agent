#!/usr/bin/env python3
"""Standard eval v2 scorer. Pure and deterministic: takes an answer
key and execution records, returns claim assessments, page results, the
three gates and the secondary metrics defined in evals/eval_config_v2.yaml.

It never imports the Agent SDK and never calls a model. PASS or FAIL is
printed only for the official set (61 pages, 3 valid runs per page, key
declaring set "official"); every other set gets DEV ONLY — NOT ENOUGH CASES.

Usage (re-score a recorded results file):
    python evals/score_v2.py evals/results/v2/dev/<eval_id>.json
"""
import difflib
import json
import random
import re
import sqlite3
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "evals" / "eval_config_v2.yaml"

VERDICTS = ("SUPPORTED", "CONTRADICTED", "UNVERIFIABLE")
ANSWERABLE = ("SUPPORTED", "CONTRADICTED")
MISSING = "MISSING"
CONFLICT = "CONFLICT"
RUN_FAILED = "RUN_FAILED"

COMPLETED = "completed"
AGENT_FAILURE = "agent_failure"
INVALID_RUN = "invalid_run"

DEV_ONLY = "DEV ONLY — NOT ENOUGH CASES"
INVALID_LABEL = "INVALID RUN"

LOG_FINDING = "mcp__claimverify__log_finding"
TARGET_READ_TOOLS = ("mcp__claimverify__fetch_page", "mcp__claimverify__extract_claims")
BREAKER_TEXT = "Circuit breaker tripped"
REQUIRED_FINDING_FIELDS = ("claim_text", "verdict", "evidence_source", "evidence_note")


def load_config(path: Path = CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_key(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# --- Claim matching -----------------------------------------------------------

_CHAR_MAP = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "–": "-", "—": "-", "‒": "-", "−": "-",
})
_WHITESPACE = re.compile(r"\s+")
_LIST_NUMBER = re.compile(r"^\d+[.)] ")
_EDGE_QUOTES = "'\"`"


def normalize_claim(text: str) -> str:
    """The frozen normalization (eval_config_v2.yaml claim_matching). Two
    strings match only if their normalized forms are equal."""
    s = unicodedata.normalize("NFKC", text).translate(_CHAR_MAP)
    s = _WHITESPACE.sub(" ", s).strip()
    s = _LIST_NUMBER.sub("", s, count=1)
    while True:
        before = s
        s = s.strip().strip(_EDGE_QUOTES)
        if s.endswith("."):
            s = s[:-1]
        if s == before:
            break
    return s.casefold()


def nearest_claim(finding_text: str, claims: list[dict]) -> tuple[str | None, float]:
    """Diagnosis only: the expected claim closest to an unmatched finding.
    Marked 'candidate, not scored'; never changes any score."""
    norm = normalize_claim(finding_text)
    best_id, best_ratio = None, 0.0
    for claim in claims:
        ratio = difflib.SequenceMatcher(None, norm, normalize_claim(claim["text"])).ratio()
        if ratio > best_ratio:
            best_id, best_ratio = claim["id"], ratio
    return best_id, round(best_ratio, 3)


# --- Evidence -------------------------------------------------------------------

_HTML_REF = re.compile(r"[^\s,;'\"`()\[\]]+?\.html", re.IGNORECASE)


def cited_sources(evidence_source: str) -> list[tuple[str | None, str]]:
    """(directory part or None, basename) for every '.html' path cited."""
    cited = []
    for ref in _HTML_REF.findall(evidence_source or ""):
        parts = [p for p in re.split(r"[\\/]", ref) if p]
        if not parts:
            continue
        directory = "/".join(parts[:-1]) or None
        cited.append((directory, parts[-1]))
    return cited


def evidence_valid(evidence_source: str, allowed: list[str], case_id: str) -> bool:
    cited = cited_sources(evidence_source)
    if not cited:
        return False
    for directory, basename in cited:
        if basename not in allowed:
            return False
        if directory is not None and directory.split("/")[-1] != case_id:
            return False
    return True


# --- Completion and failure classification ---------------------------------------

def infra_category(text: str, config: dict) -> str | None:
    """Name of the matching infra pattern group, or None. Cap patterns are
    checked first: a cap breach is an agent failure, never infrastructure."""
    if not text:
        return None
    infra = config["infra_errors"]
    for pattern in infra["cap_patterns_agent_failure"]:
        if re.search(pattern, text):
            return None
    for name, pattern in infra["patterns"].items():
        if re.search(pattern, text):
            return name
    return None


def load_audit_rows(db_path: Path, run_id: str) -> list[dict]:
    if not Path(db_path).exists():
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM tool_calls WHERE run_id = ? ORDER BY id", (run_id,)
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _response_text(payload: dict) -> str:
    response = payload.get("tool_response")
    if isinstance(response, list):
        return "\n".join(
            item.get("text", "") for item in response if isinstance(item, dict)
        )
    if isinstance(response, dict):
        content = response.get("content")
        if isinstance(content, list):
            return "\n".join(i.get("text", "") for i in content if isinstance(i, dict))
        return json.dumps(response)
    return "" if response is None else str(response)


def _response_is_error(payload: dict) -> bool:
    response = payload.get("tool_response")
    if isinstance(response, dict) and response.get("is_error"):
        return True
    # The exact error texts agent/pages.py and agent/tools.py return.
    text = _response_text(payload)
    return (
        "resolves outside evals/dataset/" in text
        or text.startswith("No such file under evals/dataset/")
        or text.startswith(BREAKER_TEXT)
    )


def completion_failures(
    result: dict | None,
    findings: list[dict],
    audit_rows: list[dict],
    case_id: str,
    max_turns: int,
    max_tool_calls: int,
) -> list[str]:
    """Check the frozen completion criteria (eval_config_v2.yaml completion)
    for a run that raised no exception. Returns the failures; empty = complete."""
    failures = []
    if result is None:
        failures.append("no ResultMessage")
    else:
        if result.get("subtype") != "success" or result.get("is_error"):
            failures.append(f"result subtype {result.get('subtype')!r}, is_error {result.get('is_error')}")
        if result.get("num_turns") is not None and result["num_turns"] > max_turns:
            failures.append(f"num_turns {result['num_turns']} > {max_turns}")
    if not findings:
        failures.append("empty report: no finding logged")
    for i, f in enumerate(findings):
        if any(not isinstance(f.get(k), str) for k in REQUIRED_FINDING_FIELDS):
            failures.append(f"finding {i}: missing or non-string required field")
        elif f["verdict"] not in VERDICTS:
            failures.append(f"finding {i}: unrecognized verdict {f['verdict']!r}")

    pre = [r for r in audit_rows if r["event_type"] == "PreToolUse"]
    post = [r for r in audit_rows if r["event_type"] == "PostToolUse"]
    post_ids = {r["tool_use_id"] for r in post}
    if not pre:
        failures.append("audit: no PreToolUse rows for this run")
    unpaired = [r for r in pre if r["tool_use_id"] not in post_ids]
    if unpaired:
        names = sorted({r["tool_name"] for r in unpaired})
        failures.append(f"audit: {len(unpaired)} tool call(s) without PostToolUse ({', '.join(names)})")

    logged = 0
    target_read = False
    breaker = False
    target_path = f"{case_id}/target.html"
    for r in post:
        payload = json.loads(r["payload"])
        text = _response_text(payload)
        if BREAKER_TEXT in text:
            breaker = True
        if r["tool_name"] == LOG_FINDING and text.startswith("Logged:"):
            logged += 1
        if (
            r["tool_name"] in TARGET_READ_TOOLS
            and (payload.get("tool_input") or {}).get("path") == target_path
            and not _response_is_error(payload)
        ):
            target_read = True
    if logged != len(findings):
        failures.append(f"audit: {logged} logged log_finding response(s) vs {len(findings)} finding(s)")
    if not target_read:
        failures.append(f"audit: no successful read of {target_path}")
    if breaker or len(pre) > max_tool_calls:
        failures.append("circuit breaker tripped")
    return failures


def classify_execution(
    error_text: str | None,
    result: dict | None,
    findings: list[dict],
    audit_rows: list[dict],
    case_id: str,
    max_turns: int,
    max_tool_calls: int,
    config: dict,
) -> dict:
    """Status of one execution: completed, agent_failure (rule E) or
    invalid_run (test machinery). The raw error text is always kept."""
    if error_text:
        category = infra_category(error_text, config)
        return {
            "status": INVALID_RUN if category else AGENT_FAILURE,
            "infra_category": category,
            "failures": [f"exception: {error_text}"],
        }
    failures = completion_failures(
        result, findings, audit_rows, case_id, max_turns, max_tool_calls
    )
    if result is not None and (result.get("subtype") != "success" or result.get("is_error")):
        category = infra_category(result.get("result_text") or "", config)
        if category:
            return {"status": INVALID_RUN, "infra_category": category, "failures": failures}
    return {
        "status": COMPLETED if not failures else AGENT_FAILURE,
        "infra_category": None,
        "failures": failures,
    }


# --- Scoring -------------------------------------------------------------------------

def _assess(claim: dict, matched: list[dict], case_id: str) -> dict:
    verdicts = [f["verdict"] for f in matched]
    distinct = sorted(set(verdicts))
    if not matched:
        assessed = MISSING
    elif len(distinct) == 1:
        assessed = distinct[0]
    else:
        assessed = CONFLICT
    evidence = [
        evidence_valid(f["evidence_source"], claim["evidence_sources"], case_id)
        for f in matched if f["verdict"] in ANSWERABLE
    ]
    return {
        "verdicts": verdicts,
        "assessed": assessed,
        "evidence_ok": bool(evidence) and all(evidence),
        "evidence_per_finding": [
            {"verdict": f["verdict"], "evidence_source": f["evidence_source"],
             "valid": evidence_valid(f["evidence_source"], claim["evidence_sources"], case_id)
             if f["verdict"] in ANSWERABLE else None}
            for f in matched
        ],
    }


def _rules_for(claim: dict, assessment: dict) -> list[str]:
    truth = claim["verdict"]
    verdicts = assessment["verdicts"]
    rules = []
    if truth == "CONTRADICTED" and "SUPPORTED" in verdicts:
        rules.append("A")
    if truth == "CONTRADICTED" and not verdicts:
        rules.append("B")
    if truth == "UNVERIFIABLE" and "SUPPORTED" in verdicts:
        rules.append("C")
    if truth == "SUPPORTED" and any(
        e["verdict"] == "SUPPORTED" and not e["valid"]
        for e in assessment["evidence_per_finding"]
    ):
        rules.append("D")
    return rules


def _rate(num: int, den: int) -> float | None:
    return num / den if den else None


def _bootstrap(per_page: dict[str, tuple[int, int]], seed: int = 0, n: int = 2000):
    """Page-cluster percentile bootstrap 95% interval for a pooled rate."""
    pages = [p for p in per_page if per_page[p][1] > 0]
    if len(pages) < 2:
        return None
    rng = random.Random(seed)
    stats = []
    for _ in range(n):
        sample = [pages[rng.randrange(len(pages))] for _ in pages]
        num = sum(per_page[p][0] for p in sample)
        den = sum(per_page[p][1] for p in sample)
        stats.append(num / den)
    stats.sort()
    return [round(stats[int(0.025 * n)], 4), round(stats[int(0.975 * n) - 1], 4)]


def score(key: dict, executions: list[dict], config: dict | None = None) -> dict:
    config = config or load_config()
    runs_required = config["runs_per_page"]
    # Rule 2A: superseded runs are dropped only for a page whose superseded
    # runs include an INVALID RUN; any other superseded mark is ignored.
    rerun_pages = {e["case_id"] for e in executions
                   if e.get("superseded") and e["status"] == INVALID_RUN}
    improper_supersede = sorted({e["execution_id"] for e in executions
                                 if e.get("superseded") and e["case_id"] not in rerun_pages})
    by_case = defaultdict(list)
    for e in executions:
        if not (e.get("superseded") and e["case_id"] in rerun_pages):
            by_case[e["case_id"]].append(e)

    rows, events, unmatched, pages = [], [], [], []
    turns, costs = [], []
    executions_valid = executions_completed = 0
    complete_series = True

    for case in key["cases"]:
        case_id = case["case_id"]
        claims = case["claims"]
        norm_to_claim = {normalize_claim(c["text"]): c for c in claims}
        execs = sorted(by_case.get(case_id, []), key=lambda e: e["run_index"])
        valid = [e for e in execs if e["status"] != INVALID_RUN]
        if len(valid) != runs_required or len(execs) != len(valid) or \
                sorted(e["run_index"] for e in valid) != list(range(1, runs_required + 1)):
            complete_series = False
        page_rules = set()
        for e in valid:
            executions_valid += 1
            if e.get("num_turns") is not None:
                turns.append(e["num_turns"])
            if e.get("total_cost_usd") is not None:
                costs.append(e["total_cost_usd"])
            if e["status"] == AGENT_FAILURE:
                page_rules.add("E")
                events.append({"rule": "E", "case_id": case_id, "execution_id": e["execution_id"],
                               "claim_id": None, "detail": e.get("failures", [])})
                for claim in claims:
                    rows.append({"case_id": case_id, "execution_id": e["execution_id"],
                                 "run_index": e["run_index"], "claim_id": claim["id"],
                                 "truth": claim["verdict"], "scenario": claim.get("scenario"),
                                 "verdicts": [], "assessed": RUN_FAILED, "evidence_ok": False,
                                 "evidence_per_finding": [], "rules": []})
                continue
            executions_completed += 1
            matched = defaultdict(list)
            for f in e["findings"]:
                claim = norm_to_claim.get(normalize_claim(f["claim_text"]))
                if claim is None:
                    cand, ratio = nearest_claim(f["claim_text"], claims)
                    unmatched.append({"case_id": case_id, "execution_id": e["execution_id"],
                                      "claim_text": f["claim_text"], "verdict": f["verdict"],
                                      "candidate_not_scored": cand, "candidate_ratio": ratio})
                else:
                    matched[claim["id"]].append(f)
            for claim in claims:
                a = _assess(claim, matched[claim["id"]], case_id)
                rules = _rules_for(claim, a)
                for rule in rules:
                    page_rules.add(rule)
                    events.append({"rule": rule, "case_id": case_id,
                                   "execution_id": e["execution_id"], "claim_id": claim["id"],
                                   "detail": a["verdicts"]})
                rows.append({"case_id": case_id, "execution_id": e["execution_id"],
                             "run_index": e["run_index"], "claim_id": claim["id"],
                             "truth": claim["verdict"], "scenario": claim.get("scenario"),
                             **a, "rules": rules})
        pages.append({"case_id": case_id, "passed": not page_rules,
                      "rules": sorted(page_rules), "valid_runs": len(valid),
                      "invalid_runs": len(execs) - len(valid)})

    # Gates 2 and 3 (pooled over answerable assessments)
    answerable = [r for r in rows if r["truth"] in ANSWERABLE]
    g2_num = sum(1 for r in answerable if "UNVERIFIABLE" in r["verdicts"])
    g3_num = sum(1 for r in answerable if r["assessed"] == r["truth"] and r["evidence_ok"])
    den = len(answerable)
    g2_page, g3_page = defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0])
    for r in answerable:
        g2_page[r["case_id"]][0] += "UNVERIFIABLE" in r["verdicts"]
        g2_page[r["case_id"]][1] += 1
        g3_page[r["case_id"]][0] += r["assessed"] == r["truth"] and r["evidence_ok"]
        g3_page[r["case_id"]][1] += 1
    g2_by_run = {}
    for run in range(1, runs_required + 1):
        run_rows = [r for r in answerable if r["run_index"] == run]
        g2_by_run[run] = _rate(sum(1 for r in run_rows if "UNVERIFIABLE" in r["verdicts"]), len(run_rows))

    passing_pages = sum(1 for p in pages if p["passed"])
    gates_cfg = config["gates"]
    g2_rate, g3_rate = _rate(g2_num, den), _rate(g3_num, den)
    gate_1 = passing_pages >= gates_cfg["gate_1_page_reliability"]["passing_pages_min"]
    gate_2 = g2_rate is not None and g2_rate <= gates_cfg["gate_2_unnecessary_unverifiable"]["max_rate"]
    gate_3 = g3_rate is not None and g3_rate >= gates_cfg["gate_3_useful_verification"]["min_rate"]

    is_official = (
        key.get("set") == "official"
        and len(key["cases"]) == config["official_case_count"]
    )
    if not is_official:
        result = DEV_ONLY
    elif not complete_series or improper_supersede:
        result = INVALID_LABEL
    else:
        result = "PASS" if (gate_1 and gate_2 and gate_3) else "FAIL"

    # Secondary metrics
    n = len(rows)
    truth_dist = defaultdict(int)
    for case in key["cases"]:
        for c in case["claims"]:
            truth_dist[c["verdict"]] += 1
    correct = [r for r in rows if r["assessed"] == r["truth"]]
    pred_c = [r for r in rows if r["assessed"] == "CONTRADICTED"]
    true_c = [r for r in rows if r["truth"] == "CONTRADICTED"]
    true_u = [r for r in rows if r["truth"] == "UNVERIFIABLE"]
    flips = []
    per_claim = defaultdict(set)
    for r in rows:
        per_claim[(r["case_id"], r["claim_id"])].add(r["assessed"])
    for (case_id, claim_id), seen in sorted(per_claim.items()):
        if len(seen) > 1:
            flips.append({"case_id": case_id, "claim_id": claim_id, "assessed": sorted(seen)})
    by_scenario = defaultdict(lambda: {"assessments": 0, "correct": 0})
    for r in rows:
        by_scenario[r["scenario"]]["assessments"] += 1
        by_scenario[r["scenario"]]["correct"] += r["assessed"] == r["truth"]
    events_by_rule = defaultdict(int)
    for ev in events:
        events_by_rule[ev["rule"]] += 1

    return {
        "result": result,
        "set": key.get("set"),
        "gates": {
            "gate_1_page_reliability": {"passing_pages": passing_pages, "pages": len(pages),
                                        "required": gates_cfg["gate_1_page_reliability"]["passing_pages_min"],
                                        "met": gate_1},
            "gate_2_unnecessary_unverifiable": {"numerator": g2_num, "denominator": den, "rate": g2_rate,
                                                "max": gates_cfg["gate_2_unnecessary_unverifiable"]["max_rate"],
                                                "met": gate_2},
            "gate_3_useful_verification": {"numerator": g3_num, "denominator": den, "rate": g3_rate,
                                           "min": gates_cfg["gate_3_useful_verification"]["min_rate"],
                                           "met": gate_3},
        },
        "pages": pages,
        "improper_supersede": improper_supersede,
        "events": events,
        "unmatched_findings": unmatched,
        "assessments": rows,
        "secondary": {
            "pages": len(pages),
            "distinct_claims": sum(len(c["claims"]) for c in key["cases"]),
            "executions_valid": executions_valid,
            "executions_completed": executions_completed,
            "run_completion_rate": _rate(executions_completed, executions_valid),
            "ground_truth_distribution": dict(truth_dist),
            "pages_passed": passing_pages,
            "false_assurance_events_by_rule": dict(sorted(events_by_rule.items())),
            "false_assurance_events_total": len(events),
            "claims_affected": len({(e["case_id"], e["claim_id"]) for e in events if e["claim_id"]}),
            "pages_affected": sum(1 for p in pages if not p["passed"]),
            "extraction_coverage": _rate(sum(1 for r in rows if r["verdicts"]), n),
            "overall_verdict_accuracy": _rate(len(correct), n),
            "verdict_plus_evidence_accuracy": _rate(
                sum(1 for r in correct if r["truth"] == "UNVERIFIABLE" or r["evidence_ok"]), n),
            "contradicted_precision": _rate(sum(1 for r in pred_c if r["truth"] == "CONTRADICTED"), len(pred_c)),
            "contradicted_recall": _rate(sum(1 for r in true_c if r["assessed"] == "CONTRADICTED"), len(true_c)),
            "unnecessary_unverifiable_rate_by_run": g2_by_run,
            "genuine_unverifiable_identified": f"{sum(1 for r in true_u if r['assessed'] == 'UNVERIFIABLE')}/{len(true_u)}",
            "verdict_flips_between_runs": flips,
            "turns": {"min": min(turns), "max": max(turns), "mean": round(sum(turns) / len(turns), 2)} if turns else None,
            "sdk_api_equivalent_cost_usd_not_a_charge": {"total": round(sum(costs), 4),
                                                        "max_per_run": round(max(costs), 4)} if costs else None,
            "by_scenario": dict(sorted(by_scenario.items(), key=lambda kv: str(kv[0]))),
            "gate_2_page_bootstrap_95": _bootstrap({p: tuple(v) for p, v in g2_page.items()}),
            "gate_3_page_bootstrap_95": _bootstrap({p: tuple(v) for p, v in g3_page.items()}),
        },
    }


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.2%}"


def format_report(scored: dict) -> str:
    g = scored["gates"]
    s = scored["secondary"]
    g1, g2, g3 = g["gate_1_page_reliability"], g["gate_2_unnecessary_unverifiable"], g["gate_3_useful_verification"]
    info = " (information only)" if scored["result"] == DEV_ONLY else ""
    lines = [
        f"Standard eval v2 — set: {scored['set']}",
        "=" * 64,
        f"RESULT: {scored['result']}",
        "",
        f"Gates{info}:",
        f"  1 page reliability:        {g1['passing_pages']}/{g1['pages']} pages passed (needs >= {g1['required']} of 61)",
        f"  2 unnecessary UNVERIFIABLE: {g2['numerator']}/{g2['denominator']} = {_pct(g2['rate'])} (max {g2['max']:.0%})",
        f"  3 useful verification:      {g3['numerator']}/{g3['denominator']} = {_pct(g3['rate'])} (min {g3['min']:.0%})",
        "",
        "Failed pages:",
    ]
    failed = [p for p in scored["pages"] if not p["passed"]]
    lines += [f"  {p['case_id']}: rules {', '.join(p['rules'])}" for p in failed] or ["  (none)"]
    lines.append("")
    lines.append("Page-fail events:")
    lines += [f"  {e['rule']} {e['execution_id']} {e['claim_id'] or ''} {e['detail']}" for e in scored["events"]] or ["  (none)"]
    lines.append("")
    lines.append("Unmatched findings (candidate, not scored):")
    lines += [f"  {u['execution_id']}: {u['claim_text']!r} -> {u['candidate_not_scored']} ({u['candidate_ratio']})"
              for u in scored["unmatched_findings"]] or ["  (none)"]
    lines += [
        "",
        "Secondary (reported, not gated):",
        f"  executions completed: {s['executions_completed']}/{s['executions_valid']} ({_pct(s['run_completion_rate'])})",
        f"  extraction coverage: {_pct(s['extraction_coverage'])}",
        f"  overall verdict accuracy: {_pct(s['overall_verdict_accuracy'])}",
        f"  verdict + evidence accuracy: {_pct(s['verdict_plus_evidence_accuracy'])}",
        f"  CONTRADICTED precision / recall: {_pct(s['contradicted_precision'])} / {_pct(s['contradicted_recall'])}",
        f"  genuine UNVERIFIABLE identified: {s['genuine_unverifiable_identified']}",
        f"  verdict flips between runs: {len(s['verdict_flips_between_runs'])}",
        f"  turns: {s['turns']}",
        f"  cost (SDK API-equivalent, not a charge): {s['sdk_api_equivalent_cost_usd_not_a_charge']}",
    ]
    return "\n".join(lines)


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python evals/score_v2.py <results.json>", file=sys.stderr)
        sys.exit(2)
    with open(sys.argv[1], encoding="utf-8") as f:
        recorded = json.load(f)
    key = load_key(REPO_ROOT / recorded["answer_key"])
    print(format_report(score(key, recorded["executions"])))


if __name__ == "__main__":
    main()
