#!/usr/bin/env python3
"""Standard eval v2 scorer. Pure and deterministic: takes an answer
key and execution records, returns claim assessments, page results, the
three gates and the secondary metrics defined in evals/eval_config_v2.yaml.

It never imports the Agent SDK and never calls a model. PASS or FAIL is
printed only for the official set (61 pages, 3 valid runs per page, key
declaring set "official"); every other set gets DEV ONLY — NOT ENOUGH CASES.

Usage (re-score a recorded results file; refused if the answer key's
SHA-256 differs from the one the file records):
    python evals/score_v2.py evals/results/v2/dev/<eval_id>.json
"""
import difflib
import hashlib
import json
import posixpath
import random
import re
import sqlite3
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agent.config import MAX_TURNS  # noqa: E402  (constants only; no SDK import)

CONFIG_PATH = REPO_ROOT / "evals" / "eval_config_v2.yaml"

VERDICTS = ("SUPPORTED", "CONTRADICTED", "UNVERIFIABLE")
ANSWERABLE = ("SUPPORTED", "CONTRADICTED")
MISSING = "MISSING"
CONFLICT = "CONFLICT"
RUN_FAILED = "RUN_FAILED"

COMPLETED = "completed"
AGENT_FAILURE = "agent_failure"
INVALID_RUN = "invalid_run"
STATUSES = (COMPLETED, AGENT_FAILURE, INVALID_RUN)

DEV_ONLY = "DEV ONLY — NOT ENOUGH CASES"
INVALID_LABEL = "INVALID RUN"

LOG_FINDING = "mcp__claimverify__log_finding"
TARGET_READ_TOOLS = ("mcp__claimverify__fetch_page", "mcp__claimverify__extract_claims")
BREAKER_TEXT = "Circuit breaker tripped"
REQUIRED_FINDING_FIELDS = ("claim_text", "verdict", "evidence_source", "evidence_note")
POST_SUCCESS = "PostToolUse"
POST_FAILURE = "PostToolUseFailure"
SDK_RESULT_ERROR_PREFIX = "Claude Code returned an error result:"
AUTH_GUARD_TYPE = "agent.harness.AuthGuardError"
PROCESS_ERROR_TYPE = "claude_agent_sdk._errors.ProcessError"
PLAIN_EXCEPTION_TYPE = "builtins.Exception"
SDK_CALL_ORIGIN = "sdk_call"

# eval_config_v2.yaml execution_record.required_fields
RECORD_FIELDS = {
    "execution_id": str, "case_id": str, "run_index": int, "attempt": int,
    "superseded": bool, "status": str, "findings": list,
}
# eval_config_v2.yaml execution_record.evidence_fields: present, may be null
EVIDENCE_FIELDS = {"error_type": str, "error": str, "result": dict}


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
_LIST_BULLET = re.compile(r"^- ")
_LIST_NUMBER = re.compile(r"^\d+[.)] ")
_EDGE_QUOTES = "'\"`"


def normalize_claim(text: str) -> str:
    """The frozen normalization (eval_config_v2.yaml claim_matching). Two
    strings match only if their normalized forms are equal."""
    s = unicodedata.normalize("NFKC", text).translate(_CHAR_MAP)
    s = _WHITESPACE.sub(" ", s).strip()
    s = _LIST_BULLET.sub("", s, count=1)
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

def sdk_origin(error_type: str | None, error_text: str | None) -> bool:
    """True when an exception comes from the SDK or CLI, or is the harness's
    auth refusal (eval_config_v2.yaml infra_errors.sdk_origin). Exceptions
    from runner or agent code are never matched against infra patterns."""
    if not error_type:
        return False
    if error_type.startswith("claude_agent_sdk.") or error_type == AUTH_GUARD_TYPE:
        return True
    return error_type == PLAIN_EXCEPTION_TYPE and (error_text or "").startswith(SDK_RESULT_ERROR_PREFIX)


def _result_failed(result: dict | None) -> bool:
    return result is not None and (result.get("subtype") != "success" or bool(result.get("is_error")))


def classified_text(error_type: str | None, error_text: str | None, result: dict | None) -> str:
    """The text infra patterns are matched against: SDK-origin exception text
    plus the text and errors of a non-success result. A success result's
    text is the agent's own answer and is never included."""
    parts = []
    if sdk_origin(error_type, error_text):
        parts.append(f"{error_type.rsplit('.', 1)[-1]}: {error_text or ''}")
    if _result_failed(result):
        parts.append(str(result.get("result_text") or ""))
        errors = result.get("errors")
        if isinstance(errors, list):
            parts.extend(str(e) for e in errors)
    return "\n".join(p for p in parts if p)


def _cli_crash(error_type: str | None, error_text: str | None, error_origin: str | None,
               result: dict | None, config: dict) -> bool:
    """infra_errors.type_rules.cli_process: the CLI ended before any result.
    A plain Exception counts only with the origin the harness established
    at the SDK call boundary and the SDK's exact crash text; the text alone
    is not enough."""
    if result is not None:
        return False
    if error_type == PROCESS_ERROR_TYPE:
        return True
    return (
        error_type == PLAIN_EXCEPTION_TYPE
        and error_origin == SDK_CALL_ORIGIN
        and re.search(config["infra_errors"]["cli_crash_signature"], error_text or "") is not None
    )


def infra_category(
    error_type: str | None, error_text: str | None, result: dict | None, config: dict,
    error_origin: str | None = None,
) -> str | None:
    """Name of the infra category of a failed run, or None (agent failure).
    Order: agent-failure subtypes, then cap patterns, then pattern groups,
    then type rules (eval_config_v2.yaml infra_errors)."""
    infra = config["infra_errors"]
    if result is not None and result.get("subtype") in infra["agent_failure_subtypes"]:
        return None
    text = classified_text(error_type, error_text, result)
    for pattern in infra["cap_patterns_agent_failure"]:
        if re.search(pattern, text):
            return None
    for name, pattern in infra["patterns"].items():
        if re.search(pattern, text):
            return name
    if _cli_crash(error_type, error_text, error_origin, result, config):
        return "cli_process"
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


def finding_problems(findings: list) -> list[str]:
    """Malformed agent output (eval_config_v2.yaml completion): each one
    makes the run rule E, never INVALID RUN."""
    problems = []
    for i, f in enumerate(findings):
        if not isinstance(f, dict) or any(not isinstance(f.get(k), str) for k in REQUIRED_FINDING_FIELDS):
            problems.append(f"finding {i}: missing or non-string required field")
        elif f["verdict"] not in VERDICTS:
            problems.append(f"finding {i}: unrecognized verdict {f['verdict']!r}")
    return problems


def _is_target_path(path, case_id: str) -> bool:
    if not isinstance(path, str) or not path:
        return False
    return posixpath.normpath(path).casefold() == f"{case_id}/target.html".casefold()


def completion_failures(
    result: dict | None,
    findings: list[dict],
    audit_rows: list[dict],
    case_id: str,
    max_turns: int,
    max_tool_calls: int,
) -> list[str]:
    """Check the frozen completion criteria (eval_config_v2.yaml completion).
    Returns the failures; empty = complete."""
    failures = []
    if result is None:
        failures.append("no ResultMessage")
    else:
        if _result_failed(result):
            failures.append(f"result subtype {result.get('subtype')!r}, is_error {result.get('is_error')}")
        turns = result.get("num_turns")
        if not isinstance(turns, int) or isinstance(turns, bool):
            failures.append(f"num_turns {turns!r} is not an integer")
        elif turns > max_turns:
            failures.append(f"num_turns {turns} > {max_turns}")
    if not findings:
        failures.append("empty report: no finding logged")
    failures += finding_problems(findings)

    pre = [r for r in audit_rows if r["event_type"] == "PreToolUse"]
    post = [r for r in audit_rows if r["event_type"] == POST_SUCCESS]
    failed = [r for r in audit_rows if r["event_type"] == POST_FAILURE]
    closed_ids = {r["tool_use_id"] for r in post + failed}
    if not pre:
        failures.append("audit: no PreToolUse rows for this run")
    unpaired = [r for r in pre if r["tool_use_id"] not in closed_ids]
    if unpaired:
        names = sorted({r["tool_name"] for r in unpaired})
        failures.append(f"audit: {len(unpaired)} tool call(s) without PostToolUse or PostToolUseFailure "
                        f"({', '.join(names)})")

    logged = 0
    target_read = False
    breaker = False
    for r in post:
        payload = json.loads(r["payload"])
        text = _response_text(payload)
        if BREAKER_TEXT in text:
            breaker = True
        if r["tool_name"] == LOG_FINDING and text.startswith("Logged:"):
            logged += 1
        if (
            r["tool_name"] in TARGET_READ_TOOLS
            and _is_target_path((payload.get("tool_input") or {}).get("path"), case_id)
            and not _response_is_error(payload)
        ):
            target_read = True
    for r in failed:  # an error response: never a read, never a Logged row
        if BREAKER_TEXT in str(json.loads(r["payload"]).get("error") or ""):
            breaker = True
    if logged != len(findings):
        failures.append(f"audit: {logged} logged log_finding response(s) vs {len(findings)} finding(s)")
    if not target_read:
        failures.append(f"audit: no successful read of {case_id}/target.html")
    if breaker or len(pre) > max_tool_calls:
        failures.append("circuit breaker tripped")
    return failures


def _has_exception(error_type: str | None, error_text: str | None) -> bool:
    """An exception is present when its type or its text is recorded, even
    with an empty message (eval_config_v2.yaml completion)."""
    return error_type is not None or error_text is not None


def classify_execution(
    error_type: str | None,
    error_text: str | None,
    result: dict | None,
    findings: list[dict],
    audit_rows: list[dict],
    case_id: str,
    max_turns: int,
    max_tool_calls: int,
    config: dict,
    error_origin: str | None = None,
) -> dict:
    """Status of one execution: completed, agent_failure (rule E) or
    invalid_run (test machinery). The result is classified even when the
    SDK raised. The raw error text is always kept."""
    raised = _has_exception(error_type, error_text)
    failures = [f"exception: {error_type}: {error_text or ''}"] if raised else []
    failures += completion_failures(
        result, findings, audit_rows, case_id, max_turns, max_tool_calls
    )
    if not failures:
        return {"status": COMPLETED, "infra_category": None, "failures": []}
    category = infra_category(error_type, error_text, result, config, error_origin) \
        if (raised or _result_failed(result)) else None
    return {
        "status": INVALID_RUN if category else AGENT_FAILURE,
        "infra_category": category,
        "failures": failures,
    }


def status_problem(e: dict, config: dict, max_turns: int = MAX_TURNS) -> str | None:
    """eval_config_v2.yaml execution_record.status_integrity: re-derive what
    the record's own evidence allows. None when the status is consistent."""
    error_type, error_text, result = e["error_type"], e["error"], e["result"]
    raised = _has_exception(error_type, error_text)
    category = infra_category(error_type, error_text, result, config, e.get("error_origin")) \
        if (raised or _result_failed(result)) else None
    status = e["status"]
    if status == INVALID_RUN:
        if category is None:
            return "status invalid_run, but its error and result show no infrastructure category"
        if e.get("infra_category") not in (None, category):
            return f"recorded infra_category {e['infra_category']!r}, evidence gives {category!r}"
    elif status == COMPLETED:
        if raised:
            return "status completed, but an exception is recorded"
        if result is None or _result_failed(result):
            return "status completed, but there is no success result"
        turns = result.get("num_turns")
        if not isinstance(turns, int) or isinstance(turns, bool) or turns > max_turns:
            return f"status completed, but num_turns is {turns!r} (max {max_turns})"
        if not e["findings"]:
            return "status completed, but no finding is recorded (empty report)"
    elif category is not None:
        return f"status agent_failure, but its evidence classifies as infrastructure ({category})"
    return None


def _id_suffix(e: dict) -> str:
    """eval_config_v2.yaml execution_record.required_fields.execution_id:
    '-r<run_index>', plus '-a<attempt>' from attempt 2."""
    return f"-r{e['run_index']}" + (f"-a{e['attempt']}" if e["attempt"] > 1 else "")


def record_problems(key: dict, executions: list) -> tuple[list[dict], list[str]]:
    """Split execution records into well-formed ones and test-infrastructure
    problems (eval_config_v2.yaml execution_record.malformed). A record with
    any problem is left out of scoring."""
    case_ids = {c["case_id"] for c in key["cases"]}
    seen, good, problems = set(), [], []
    for i, e in enumerate(executions):
        if not isinstance(e, dict):
            problems.append(f"execution #{i}: not an object")
            continue
        eid = e.get("execution_id")
        label = eid if isinstance(eid, str) else f"execution #{i}"
        bad = [field for field, typ in RECORD_FIELDS.items()
               if not isinstance(e.get(field), typ) or (typ is int and isinstance(e.get(field), bool))]
        bad += [field for field, typ in EVIDENCE_FIELDS.items()
                if field not in e or not (e[field] is None or isinstance(e[field], typ))]
        if "error_origin" in e and not (e["error_origin"] is None or isinstance(e["error_origin"], str)):
            bad.append("error_origin")
        if bad:
            problems.append(f"{label}: missing or wrongly typed field(s) {', '.join(bad)}")
            continue
        if e["status"] not in STATUSES:
            problems.append(f"{label}: unknown status {e['status']!r}")
        elif e["attempt"] < 1:
            problems.append(f"{label}: attempt {e['attempt']} < 1")
        elif eid in seen:
            problems.append(f"{label}: duplicate execution_id")
        elif e["case_id"] not in case_ids:
            problems.append(f"{label}: case_id {e['case_id']!r} is not in the answer key")
        elif not eid.endswith(_id_suffix(e)):
            problems.append(f"{label}: execution_id does not end with {_id_suffix(e)!r} "
                            "(its run_index and attempt)")
        else:
            good.append(e)
        seen.add(eid)
    return good, problems


def apply_status_integrity(executions: list[dict], config: dict) -> tuple[list[dict], list[str]]:
    """Records whose status their own evidence does not support stay in
    scoring as agent failures (rule E), so a failure they carry never
    disappears and they never justify a page rerun; each is a
    record-integrity problem (official result INVALID RUN)."""
    out, problems = [], []
    for e in executions:
        problem = status_problem(e, config)
        if problem is None:
            out.append(e)
            continue
        problems.append(f"{e['execution_id']}: record integrity: {problem}")
        out.append({**e, "status": AGENT_FAILURE, "recorded_status": e["status"],
                    "failures": list(e.get("failures") or []) + [f"record integrity: {problem}"]})
    return out, problems


def key_problems(key: dict, config: dict) -> list[str]:
    """Why a key that declares set official is not an official set."""
    if key.get("set") != "official":
        return []
    ids = [c.get("case_id") for c in key["cases"]]
    pattern = config["sets"]["official"]["case_id_pattern"]
    problems = []
    if len(ids) != config["official_case_count"]:
        problems.append(f"{len(ids)} cases, needs {config['official_case_count']}")
    if len(set(ids)) != len(ids):
        problems.append(f"{len(ids) - len(set(ids))} duplicate case_id(s)")
    bad = sorted({str(i) for i in ids if not isinstance(i, str) or not re.match(pattern, i)})
    if bad:
        problems.append(f"case_id(s) not matching {pattern}: {', '.join(bad)}")
    return problems


def key_sha256(path: Path) -> str:
    """The LF-normalized SHA-256 of an answer key (eval_config_v2.yaml
    results.hash_forms): every CR LF becomes LF before hashing, so the
    checkout's line endings never change the hash. The scorer, the runner
    and the preflight all use this function. The sealed ZIP's hash is a
    separate raw-byte hash and is never compared with this one."""
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def rescore_key_problem(recorded: dict, key_path: Path) -> str | None:
    """eval_config_v2.yaml results.rescore: None when the key file is the one
    the results file records."""
    expected = recorded.get("answer_key_sha256")
    if not expected:
        return "the results file records no answer_key_sha256"
    actual = key_sha256(key_path)
    if actual != expected:
        return f"answer key SHA-256 {actual} differs from the recorded {expected}"
    return None


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


def _attempt_problems(case_id: str, execs: list[dict], runs_required: int) -> list[str]:
    """eval_config_v2.yaml invalid_run_restart.attempts, for one page. The
    scored attempt's own completeness is checked separately. Statuses here
    are after status_integrity, so an unsupported INVALID RUN label never
    justifies a rerun."""
    problems = []
    attempts = sorted({e["attempt"] for e in execs})
    if attempts and attempts != list(range(1, attempts[-1] + 1)):
        problems.append(f"{case_id}: attempts {attempts} are not numbered 1, 2, ... without gaps")
    scored = attempts[-1] if attempts else None
    for a in attempts[:-1]:
        earlier = sorted((e for e in execs if e["attempt"] == a), key=lambda e: e["run_index"])
        not_superseded = sorted(e["execution_id"] for e in earlier if not e["superseded"])
        if not_superseded:
            problems.append(f"{case_id}: attempt {a} was rerun but {', '.join(not_superseded)} not superseded")
        indexes = [e["run_index"] for e in earlier]
        if indexes != list(range(1, len(indexes) + 1)) or len(indexes) > runs_required:
            problems.append(f"{case_id}: attempt {a} holds runs {indexes}; an earlier attempt needs runs "
                            f"1 to k without a gap or duplicate, k <= {runs_required}")
            continue
        if earlier[-1]["status"] != INVALID_RUN:
            problems.append(f"{case_id}: attempt {a} was superseded, but its last run "
                            f"{indexes[-1]} is not a supported INVALID RUN")
        early_invalid = [e["execution_id"] for e in earlier[:-1] if e["status"] == INVALID_RUN]
        if early_invalid:
            problems.append(f"{case_id}: attempt {a} continued after an INVALID RUN ({', '.join(early_invalid)})")
    marked = sorted(e["execution_id"] for e in execs if e["attempt"] == scored and e["superseded"])
    if marked:
        problems.append(f"{case_id}: superseded mark in the scored attempt {scored} ({', '.join(marked)}); "
                        "a single run is never replaced on its own")
    return problems


def _assess_findings(e: dict, claims: list[dict], norm_to_claim: dict, case_id: str, base: dict):
    """Rules A-D and F over well-formed findings. Returns (rows, events,
    unmatched)."""
    rows, events, unmatched = [], [], []
    matched = defaultdict(list)
    for f in e["findings"]:
        claim = norm_to_claim.get(normalize_claim(f["claim_text"]))
        if claim is None:
            cand, ratio = nearest_claim(f["claim_text"], claims)
            unmatched.append({**base, "claim_text": f["claim_text"], "verdict": f["verdict"],
                              "candidate_not_scored": cand, "candidate_ratio": ratio})
            if f["verdict"] == "SUPPORTED":
                events.append({**base, "rule": "F", "claim_id": None, "detail": f["claim_text"]})
        else:
            matched[claim["id"]].append(f)
    for claim in claims:
        a = _assess(claim, matched[claim["id"]], case_id)
        rules = _rules_for(claim, a)
        events += [{**base, "rule": rule, "claim_id": claim["id"], "detail": a["verdicts"]}
                   for rule in rules]
        rows.append({**base, "run_index": e["run_index"], "claim_id": claim["id"],
                     "truth": claim["verdict"], "scenario": claim.get("scenario"),
                     **a, "rules": rules})
    return rows, events, unmatched


def _check_run(e: dict, claims: list[dict], norm_to_claim: dict, case_id: str):
    """Rules A-F for one valid execution. Returns (rows, events, unmatched);
    rows are the claim assessments of the run."""
    base = {"case_id": case_id, "execution_id": e["execution_id"],
            "attempt": e["attempt"], "superseded": e["superseded"]}
    failures = list(e.get("failures") or [])
    if e["status"] == COMPLETED:
        failures += finding_problems(e["findings"])  # malformed agent output is rule E
    if e["status"] == AGENT_FAILURE or failures:
        rows = [{**base, "run_index": e["run_index"], "claim_id": claim["id"],
                 "truth": claim["verdict"], "scenario": claim.get("scenario"),
                 "verdicts": [], "assessed": RUN_FAILED, "evidence_ok": False,
                 "evidence_per_finding": [], "rules": []} for claim in claims]
        return rows, [{**base, "rule": "E", "claim_id": None, "detail": failures}], []
    return _assess_findings(e, claims, norm_to_claim, case_id, base)


def _observe_invalid_run(e: dict, claims: list[dict], norm_to_claim: dict, case_id: str) -> list[dict]:
    """eval_config_v2.yaml invalid_run_restart.invalid_run_observations:
    rules A, C, D and F over an INVALID RUN execution's findings, labelled
    NOT SCORED. They never fail a page and never change a gate."""
    base = {"case_id": case_id, "execution_id": e["execution_id"], "attempt": e["attempt"],
            "superseded": e["superseded"], "label": "NOT SCORED"}
    problems = finding_problems(e["findings"])
    if problems:
        return [{**base, "rule": None, "claim_id": None, "detail": problems}]
    _rows, events, _unmatched = _assess_findings(e, claims, norm_to_claim, case_id, base)
    return [ev for ev in events if ev["rule"] != "B"]


def score(key: dict, executions: list[dict], config: dict | None = None) -> dict:
    config = config or load_config()
    runs_required = config["runs_per_page"]
    executions, bad_records = record_problems(key, executions)
    executions, integrity = apply_status_integrity(executions, config)
    bad_records += integrity
    by_case = defaultdict(list)
    for e in executions:
        by_case[e["case_id"]].append(e)

    rows, events, unmatched, pages, history, attempt_problems = [], [], [], [], [], []
    not_scored = []
    turns, costs = [], []
    executions_valid = executions_completed = 0
    complete_series = True

    for case in key["cases"]:
        case_id = case["case_id"]
        claims = case["claims"]
        norm_to_claim = {normalize_claim(c["text"]): c for c in claims}
        execs = sorted(by_case.get(case_id, []), key=lambda e: (e["attempt"], e["run_index"]))
        attempt_problems += _attempt_problems(case_id, execs, runs_required)
        scored_attempt = max((e["attempt"] for e in execs), default=None)
        current = [e for e in execs if e["attempt"] == scored_attempt]
        valid = [e for e in current if e["status"] != INVALID_RUN]
        if len(valid) != runs_required or len(current) != len(valid) or \
                sorted(e["run_index"] for e in valid) != list(range(1, runs_required + 1)):
            complete_series = False
        page_rules = set()
        for e in execs:
            scored = e["attempt"] == scored_attempt
            history.append({"execution_id": e["execution_id"], "case_id": case_id,
                            "attempt": e["attempt"], "run_index": e["run_index"],
                            "status": e["status"], "recorded_status": e.get("recorded_status", e["status"]),
                            "superseded": e["superseded"], "scored": scored})
            if e["status"] == INVALID_RUN:
                not_scored += _observe_invalid_run(e, claims, norm_to_claim, case_id)
                continue
            run_rows, run_events, run_unmatched = _check_run(e, claims, norm_to_claim, case_id)
            events += run_events
            unmatched += run_unmatched
            page_rules.update(ev["rule"] for ev in run_events)
            if not scored:  # a superseded attempt: its events count, its rows do not
                continue
            rows += run_rows
            executions_valid += 1
            if not any(ev["rule"] == "E" for ev in run_events):
                executions_completed += 1
            if e.get("num_turns") is not None:
                turns.append(e["num_turns"])
            if e.get("total_cost_usd") is not None:
                costs.append(e["total_cost_usd"])
        pages.append({"case_id": case_id, "passed": not page_rules,
                      "rules": sorted(page_rules), "valid_runs": len(valid),
                      "invalid_runs": len(current) - len(valid), "scored_attempt": scored_attempt})

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

    bad_key = key_problems(key, config)
    is_official = key.get("set") == "official" and not bad_key
    if not is_official:
        result = DEV_ONLY
    elif not complete_series or attempt_problems or bad_records:
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
        "key_problems": bad_key,
        "record_problems": bad_records,
        "attempt_problems": attempt_problems,
        "events": events,
        "not_scored_observations": not_scored,
        "history": history,
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
    ]
    for title, items in (("Key problems (not an official set)", scored["key_problems"]),
                         ("Record problems (left out of scoring)", scored["record_problems"]),
                         ("Attempt problems", scored["attempt_problems"])):
        if items:
            lines += [f"{title}:"] + [f"  {p}" for p in items] + [""]
    lines.append("Failed pages:")
    failed = [p for p in scored["pages"] if not p["passed"]]
    lines += [f"  {p['case_id']}: rules {', '.join(p['rules'])}" for p in failed] or ["  (none)"]
    lines.append("")
    lines.append("Page-fail events:")
    lines += [f"  {e['rule']} {e['execution_id']} {e['claim_id'] or ''} {e['detail']}"
              + (f" (superseded attempt {e['attempt']}, still fails the page)" if e["superseded"] else "")
              for e in scored["events"]] or ["  (none)"]
    if scored["not_scored_observations"]:
        lines.append("")
        lines.append("Observations in INVALID RUN executions (NOT SCORED; no page or gate effect):")
        lines += [f"  NOT SCORED {o['rule'] or 'malformed findings'} {o['execution_id']} "
                  f"{o['claim_id'] or ''} {o['detail']}" for o in scored["not_scored_observations"]]
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
    key_path = REPO_ROOT / recorded["answer_key"]
    problem = rescore_key_problem(recorded, key_path)
    if problem:
        print(f"Refused: {problem}", file=sys.stderr)
        sys.exit(2)
    print(format_report(score(load_key(key_path), recorded["executions"])))


if __name__ == "__main__":
    main()
