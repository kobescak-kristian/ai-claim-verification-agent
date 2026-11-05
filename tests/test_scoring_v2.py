"""Fixture tests for the Standard eval v2 scorer and dataset tool.

No model calls and no Agent SDK: every execution record here is built in
the test code. The synthetic 61-page fixture is generated from a fixed seed
(synthetic claim records only, no HTML, no link to the hold-out).
"""
import copy
import hashlib
import json
import random
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agent.audit import _SCHEMA  # noqa: E402
from agent.config import DATASET_ROOT  # noqa: E402
from evals import make_dataset  # noqa: E402
from evals.score_v2 import (  # noqa: E402
    AGENT_FAILURE,
    COMPLETED,
    CONFLICT,
    DEV_ONLY,
    INVALID_LABEL,
    INVALID_RUN,
    classify_execution,
    evidence_valid,
    format_report,
    key_sha256,
    load_audit_rows,
    load_config,
    load_key,
    normalize_claim,
    rescore_key_problem,
    score,
)
from evals.score_v2 import main as score_main  # noqa: E402

DEV_KEY_PATH = REPO_ROOT / "evals" / "answer_keys" / "dev.json"
GROUND_TRUTH_PATH = REPO_ROOT / "evals" / "ground_truth.json"
CONFIG = load_config()

LOG = "mcp__claimverify__log_finding"
EXTRACT = "mcp__claimverify__extract_claims"


# --- helpers ------------------------------------------------------------------------

def perfect_findings(case: dict) -> list[dict]:
    return [
        {
            "claim_text": c["text"],
            "verdict": c["verdict"],
            "evidence_source": f"{case['case_id']}/{c['evidence_sources'][0]}" if c["evidence_sources"] else "none",
            "evidence_note": "fixture",
        }
        for c in case["claims"]
    ]


SUCCESS_RESULT = {"subtype": "success", "is_error": False, "num_turns": 8, "result_text": "done", "errors": []}
NO_ERROR = {"error_type": None, "error": None, "result": SUCCESS_RESULT}


def mark_invalid(e: dict) -> dict:
    """An INVALID RUN record with the evidence that supports it (an API 529
    as the SDK reports it)."""
    e.update(status=INVALID_RUN, infra_category="overloaded_or_5xx", error_type="builtins.Exception",
             error="Claude Code returned an error result: API Error: 529 Overloaded",
             result={"subtype": "success", "is_error": True, "num_turns": 1,
                     "result_text": "API Error: 529 Overloaded", "errors": []})
    return e


def perfect_executions(key: dict, runs: int = 3) -> list[dict]:
    return [
        {
            "execution_id": f"fx-{case['case_id']}-r{r}",
            "case_id": case["case_id"],
            "run_index": r,
            "attempt": 1,
            "superseded": False,
            "status": COMPLETED,
            "failures": [],
            "num_turns": 8,
            "total_cost_usd": 0.1,
            **copy.deepcopy(NO_ERROR),
            "findings": perfect_findings(case),
        }
        for case in key["cases"]
        for r in range(1, runs + 1)
    ]


def synthetic_official_key(seed: int = 114) -> dict:
    """61 pages, 2-4 claims each, about 60/25/15 S/C/U, with the answerable
    count a multiple of 20 so the 5% and 80% boundaries are exact."""
    rng = random.Random(seed)
    counts = [rng.choice([2, 3, 4]) for _ in range(61)]
    total = sum(counts)
    answerable = int(0.88 * total) // 20 * 20
    n_c = round(0.25 * total)
    verdicts = (["CONTRADICTED"] * n_c + ["SUPPORTED"] * (answerable - n_c)
                + ["UNVERIFIABLE"] * (total - answerable))
    rng.shuffle(verdicts)
    cases, it = [], iter(verdicts)
    for i, n in enumerate(counts, start=1):
        cid = f"h{i:03d}"
        claims = []
        for j in range(1, n + 1):
            v = next(it)
            claims.append({
                "id": f"{cid}_c{j}",
                "text": f"Synthetic product {i} has property {j} equal to {i * 10 + j}.",
                "verdict": v,
                "evidence_sources": [] if v == "UNVERIFIABLE" else [f"source_{rng.choice([1, 2])}_spec_sheet.html"],
                "scenario": "exact_spec",
            })
        cases.append({"case_id": cid, "product": f"Synthetic {i}", "claims": claims})
    return {"set": "official", "cases": cases}


def by_id(executions: list[dict]) -> dict:
    return {e["execution_id"]: e for e in executions}


def rerun_page(execs: list[dict], case: dict, invalid_run: int | None, attempt: int = 2) -> list[dict]:
    """Decision 2A in attempt form: the previous attempt stopped at run
    `invalid_run`, an INVALID RUN with its evidence (later runs never ran;
    None = no INVALID RUN, for refusal tests); the page's earlier executions
    are marked superseded and a fresh, perfect attempt of 3 runs is added."""
    out = [e for e in copy.deepcopy(execs)
           if not (e["case_id"] == case["case_id"] and e["attempt"] == attempt - 1
                   and invalid_run is not None and e["run_index"] > invalid_run)]
    for e in out:
        if e["case_id"] != case["case_id"]:
            continue
        e["superseded"] = True
        if e["attempt"] == attempt - 1 and e["run_index"] == invalid_run:
            mark_invalid(e)
    for r in (1, 2, 3):
        out.append({"execution_id": f"fx-{case['case_id']}-r{r}-a{attempt}", "case_id": case["case_id"],
                    "run_index": r, "attempt": attempt, "superseded": False, "status": COMPLETED,
                    "failures": [], "num_turns": 8, "total_cost_usd": 0.1, **copy.deepcopy(NO_ERROR),
                    "findings": perfect_findings(case)})
    return out


def claim_of(key: dict, verdict: str, skip: int = 0) -> tuple[dict, dict]:
    found = [(case, c) for case in key["cases"] for c in case["claims"] if c["verdict"] == verdict]
    return found[skip]


def set_verdict(execs, case, claim, run, verdict, evidence=None):
    e = by_id(execs)[f"fx-{case['case_id']}-r{run}"]
    for f in e["findings"]:
        if f["claim_text"] == claim["text"]:
            f["verdict"] = verdict
            if evidence is not None:
                f["evidence_source"] = evidence


@pytest.fixture
def dev_key():
    return load_key(DEV_KEY_PATH)


@pytest.fixture
def official_key():
    return synthetic_official_key()


# --- dev key ------------------------------------------------------------------------

class TestDevKey:
    def test_dev_key_matches_v1_ground_truth(self, dev_key):
        with open(GROUND_TRUTH_PATH, encoding="utf-8") as f:
            gt = json.load(f)
        v1 = [(c["case_id"], cl["id"], cl["text"], cl["verdict"]) for c in gt["cases"] for cl in c["claims"]]
        v2 = [(c["case_id"], cl["id"], cl["text"], cl["verdict"]) for c in dev_key["cases"] for cl in c["claims"]]
        assert v2 == v1

    def test_v1_evidence_source_is_listed(self, dev_key):
        with open(GROUND_TRUTH_PATH, encoding="utf-8") as f:
            gt = {cl["id"]: cl for c in json.load(f)["cases"] for cl in c["claims"]}
        for case in dev_key["cases"]:
            for cl in case["claims"]:
                if cl["verdict"] != "UNVERIFIABLE":
                    assert gt[cl["id"]]["evidence_source"] in cl["evidence_sources"]

    def test_answer_keys_outside_dataset(self):
        for key_path in (REPO_ROOT / "evals" / "answer_keys").glob("*.json"):
            assert not key_path.resolve().is_relative_to(DATASET_ROOT)

    def test_dev_perfect_agent_is_dev_only(self, dev_key):
        scored = score(dev_key, perfect_executions(dev_key), CONFIG)
        assert scored["result"] == DEV_ONLY
        assert scored["result"] not in ("PASS", "FAIL")
        g = scored["gates"]
        assert g["gate_1_page_reliability"]["passing_pages"] == 12
        assert g["gate_2_unnecessary_unverifiable"]["rate"] == 0.0
        assert g["gate_3_useful_verification"]["rate"] == 1.0

    def test_dev_check_passes(self):
        assert make_dataset.check(DATASET_ROOT, DEV_KEY_PATH) == []


# --- official gate ------------------------------------------------------------------

class TestOfficialGate:
    def test_fixture_shape(self, official_key):
        cases = official_key["cases"]
        assert len(cases) == 61
        assert all(2 <= len(c["claims"]) <= 4 for c in cases)
        verdicts = [cl["verdict"] for c in cases for cl in c["claims"]]
        assert sum(v != "UNVERIFIABLE" for v in verdicts) % 20 == 0
        for v, share in (("SUPPORTED", 0.60), ("CONTRADICTED", 0.25), ("UNVERIFIABLE", 0.15)):
            assert abs(verdicts.count(v) / len(verdicts) - share) < 0.06

    def test_perfect_agent_passes(self, official_key):
        scored = score(official_key, perfect_executions(official_key), CONFIG)
        assert scored["result"] == "PASS"
        assert scored["gates"]["gate_1_page_reliability"]["passing_pages"] == 61

    def test_two_runs_per_page_is_invalid(self, official_key):
        scored = score(official_key, perfect_executions(official_key, runs=2), CONFIG)
        assert scored["result"] == INVALID_LABEL

    def test_outstanding_invalid_run_blocks_result(self, official_key):
        execs = perfect_executions(official_key)
        mark_invalid(execs[0])
        assert score(official_key, execs, CONFIG)["result"] == INVALID_LABEL

    def test_superseded_invalid_run_after_page_rerun(self, official_key):
        execs = perfect_executions(official_key)
        fresh = rerun_page(execs, official_key["cases"][0], invalid_run=2)
        scored = score(official_key, fresh, CONFIG)
        assert scored["result"] == "PASS"
        assert scored["pages"][0]["scored_attempt"] == 2
        assert len(scored["history"]) == len(execs) - 1 + 3  # run 3 never ran after the INVALID RUN

    def test_superseded_without_invalid_run_is_refused(self, official_key):
        """2A allows only a whole-page rerun after an INVALID RUN; a bad run
        marked superseded and replaced on its own must not reach PASS."""
        execs = perfect_executions(official_key)
        case = official_key["cases"][0]
        bad = copy.deepcopy(by_id(execs)[f"fx-{case['case_id']}-r2"])
        bad["execution_id"] = "old-" + bad["execution_id"]
        bad["status"] = AGENT_FAILURE
        bad["superseded"] = True
        scored = score(official_key, [bad] + execs, CONFIG)
        assert scored["result"] == INVALID_LABEL
        assert any(bad["execution_id"] in p for p in scored["attempt_problems"])
        page = next(p for p in scored["pages"] if p["case_id"] == case["case_id"])
        assert "E" in page["rules"]  # the bad run is still scored

    def test_sixty_pages_is_not_official(self, official_key):
        official_key["cases"] = official_key["cases"][:60]
        assert score(official_key, perfect_executions(official_key), CONFIG)["result"] == DEV_ONLY

    @pytest.mark.parametrize("failed,expected", [(2, "PASS"), (3, "FAIL")])
    def test_failed_page_boundary(self, official_key, failed, expected):
        execs = perfect_executions(official_key)
        for i in range(failed):
            case = official_key["cases"][i]
            by_id(execs)[f"fx-{case['case_id']}-r2"]["status"] = AGENT_FAILURE
        scored = score(official_key, execs, CONFIG)
        assert scored["gates"]["gate_1_page_reliability"]["passing_pages"] == 61 - failed
        assert scored["result"] == expected

    def test_several_errors_on_one_page_are_one_failed_page(self, official_key):
        execs = perfect_executions(official_key)
        case = official_key["cases"][0]
        for run in (1, 2, 3):
            by_id(execs)[f"fx-{case['case_id']}-r{run}"]["status"] = AGENT_FAILURE
        scored = score(official_key, execs, CONFIG)
        assert scored["gates"]["gate_1_page_reliability"]["passing_pages"] == 60
        assert scored["secondary"]["false_assurance_events_by_rule"] == {"E": 3}

    def _answerable_rows(self, key):
        return [(case, c) for case in key["cases"] for c in case["claims"] if c["verdict"] != "UNVERIFIABLE"]

    @pytest.mark.parametrize("k,expected", [(None, "PASS"), (1, "FAIL")])
    def test_unnecessary_unverifiable_boundary(self, official_key, k, expected):
        execs = perfect_executions(official_key)
        den = 3 * len(self._answerable_rows(official_key))
        n = den // 20 + (k or 0)  # exactly 5%, then one more
        supported = [(case, c) for case, c in self._answerable_rows(official_key) if c["verdict"] == "SUPPORTED"]
        slots = [(case, c, r) for case, c in supported for r in (1, 2, 3)][:n]
        for case, c, r in slots:
            set_verdict(execs, case, c, r, "UNVERIFIABLE")
        scored = score(official_key, execs, CONFIG)
        g2 = scored["gates"]["gate_2_unnecessary_unverifiable"]
        assert g2["numerator"] == n and g2["denominator"] == den
        assert scored["gates"]["gate_1_page_reliability"]["passing_pages"] == 61
        assert scored["result"] == expected

    @pytest.mark.parametrize("k,expected", [(None, "PASS"), (1, "FAIL")])
    def test_useful_verification_boundary(self, official_key, k, expected):
        execs = perfect_executions(official_key)
        den = 3 * len(self._answerable_rows(official_key))
        n = den // 5 + (k or 0)  # exactly 80% credited, then one fewer
        supported = [(case, c) for case, c in self._answerable_rows(official_key) if c["verdict"] == "SUPPORTED"]
        slots = [(case, c, r) for case, c in supported for r in (1, 2, 3)][:n]
        assert len(slots) == n
        for case, c, r in slots:
            set_verdict(execs, case, c, r, "CONTRADICTED")  # tolerated error: no page fail
        scored = score(official_key, execs, CONFIG)
        g3 = scored["gates"]["gate_3_useful_verification"]
        assert g3["numerator"] == den - n
        assert scored["gates"]["gate_1_page_reliability"]["passing_pages"] == 61
        assert scored["gates"]["gate_2_unnecessary_unverifiable"]["numerator"] == 0
        assert scored["result"] == expected


# --- page-fail rules A-E ---------------------------------------------------------------

class TestPageFailRules:
    def _only_failed(self, key, execs, case_id, rule):
        scored = score(key, execs, CONFIG)
        failed = [p for p in scored["pages"] if not p["passed"]]
        assert [(p["case_id"], p["rules"]) for p in failed] == [(case_id, [rule])]
        return scored

    def test_rule_a_contradicted_marked_supported(self, official_key):
        execs = perfect_executions(official_key)
        case, c = claim_of(official_key, "CONTRADICTED")
        set_verdict(execs, case, c, 2, "SUPPORTED")
        self._only_failed(official_key, execs, case["case_id"], "A")

    def test_rule_b_contradicted_missing(self, official_key):
        execs = perfect_executions(official_key)
        case, c = claim_of(official_key, "CONTRADICTED")
        e = by_id(execs)[f"fx-{case['case_id']}-r3"]
        e["findings"] = [f for f in e["findings"] if f["claim_text"] != c["text"]]
        self._only_failed(official_key, execs, case["case_id"], "B")

    def test_rule_c_unverifiable_marked_supported(self, official_key):
        execs = perfect_executions(official_key)
        case, c = claim_of(official_key, "UNVERIFIABLE")
        set_verdict(execs, case, c, 1, "SUPPORTED", evidence="source_1_spec_sheet.html")
        self._only_failed(official_key, execs, case["case_id"], "C")

    def test_rule_d_supported_with_invalid_evidence(self, official_key):
        execs = perfect_executions(official_key)
        case, c = claim_of(official_key, "SUPPORTED")
        set_verdict(execs, case, c, 1, "SUPPORTED", evidence="source_9_forum_post.html")
        scored = self._only_failed(official_key, execs, case["case_id"], "D")
        assert scored["gates"]["gate_3_useful_verification"]["numerator"] == \
            scored["gates"]["gate_3_useful_verification"]["denominator"] - 1

    def test_rule_e_incomplete_run(self, official_key):
        execs = perfect_executions(official_key)
        case = official_key["cases"][5]
        by_id(execs)[f"fx-{case['case_id']}-r1"]["status"] = AGENT_FAILURE
        scored = self._only_failed(official_key, execs, case["case_id"], "E")
        assert scored["secondary"]["run_completion_rate"] == pytest.approx(182 / 183)

    def test_tolerated_errors_do_not_fail_pages(self, official_key):
        execs = perfect_executions(official_key)
        s_case, s = claim_of(official_key, "SUPPORTED")
        c_case, c = claim_of(official_key, "CONTRADICTED")
        u_case, u = claim_of(official_key, "UNVERIFIABLE")
        set_verdict(execs, s_case, s, 1, "CONTRADICTED")
        set_verdict(execs, s_case, s, 2, "UNVERIFIABLE")
        set_verdict(execs, c_case, c, 1, "UNVERIFIABLE")
        set_verdict(execs, u_case, u, 1, "CONTRADICTED")
        scored = score(official_key, execs, CONFIG)
        assert all(p["passed"] for p in scored["pages"])
        assert scored["gates"]["gate_2_unnecessary_unverifiable"]["numerator"] == 2
        assert scored["gates"]["gate_3_useful_verification"]["numerator"] == \
            scored["gates"]["gate_3_useful_verification"]["denominator"] - 3


# --- matching ------------------------------------------------------------------------

class TestMatching:
    CLAIM = "The SoundWave Pro X2 is priced at $129.99."

    @pytest.mark.parametrize("variant", [
        "The SoundWave Pro X2 is priced at $129.99.",
        "The SoundWave Pro X2 is priced at $129.99",
        "3. The SoundWave Pro X2 is priced at $129.99.",
        '"The SoundWave Pro X2 is priced at $129.99."',
        "“The SoundWave Pro X2 is priced at $129.99.”",
        "  the soundwave   PRO X2 is priced at $129.99. ",
        "- The SoundWave Pro X2 is priced at $129.99.",  # the fetch_page bullet
    ])
    def test_variants_match(self, variant):
        assert normalize_claim(variant) == normalize_claim(self.CLAIM)

    @pytest.mark.parametrize("variant", [
        "The SoundWave Pro X2 costs $129.99.",
        "priced at $129.99",
        "The SoundWave Pro X2 is priced at $129.99 at retail.",
    ])
    def test_reworded_and_substring_do_not_match(self, variant):
        assert normalize_claim(variant) != normalize_claim(self.CLAIM)

    def test_unmatched_findings_earn_nothing_and_are_listed(self, dev_key):
        execs = perfect_executions(dev_key)
        case = next(c for c in dev_key["cases"] if c["case_id"] == "case_07")
        near_miss = "Battery life of 11.5 hours per charge."
        for e in execs:
            if e["case_id"] == "case_07" and e["run_index"] == 1:
                for f in e["findings"]:
                    if f["claim_text"] == case["claims"][0]["text"]:
                        f["claim_text"] = near_miss
        scored = score(dev_key, execs, CONFIG)
        assert [u["claim_text"] for u in scored["unmatched_findings"]] == [near_miss]
        assert scored["unmatched_findings"][0]["candidate_not_scored"] == "case_07_c1"
        page = next(p for p in scored["pages"] if p["case_id"] == "case_07")
        assert page["rules"] == ["B"]  # the CONTRADICTED claim counts as missing
        assert scored["gates"]["gate_3_useful_verification"]["numerator"] == \
            scored["gates"]["gate_3_useful_verification"]["denominator"] - 1


# --- duplicates ------------------------------------------------------------------------

class TestDuplicates:
    def test_conflicting_duplicates_cannot_hide_supported(self, dev_key):
        execs = perfect_executions(dev_key)
        e = by_id(execs)["fx-case_02-r1"]
        c1 = dev_key["cases"][1]["claims"][0]  # CONTRADICTED
        hidden = {"claim_text": c1["text"], "verdict": "SUPPORTED",
                  "evidence_source": "case_02/source_2_retailer.html", "evidence_note": "x"}
        e["findings"].insert(0, hidden)  # earlier SUPPORTED, later CONTRADICTED
        scored = score(dev_key, execs, CONFIG)
        row = next(r for r in scored["assessments"]
                   if r["execution_id"] == "fx-case_02-r1" and r["claim_id"] == c1["id"])
        assert row["assessed"] == CONFLICT
        assert next(p for p in scored["pages"] if p["case_id"] == "case_02")["rules"] == ["A"]
        assert scored["gates"]["gate_3_useful_verification"]["numerator"] == \
            scored["gates"]["gate_3_useful_verification"]["denominator"] - 1

    def test_unverifiable_among_duplicates_counts_for_gate_2(self, dev_key):
        execs = perfect_executions(dev_key)
        e = by_id(execs)["fx-case_01-r1"]
        c = dev_key["cases"][0]["claims"][0]
        e["findings"].append({"claim_text": c["text"], "verdict": "UNVERIFIABLE",
                              "evidence_source": "none", "evidence_note": "x"})
        scored = score(dev_key, execs, CONFIG)
        assert scored["gates"]["gate_2_unnecessary_unverifiable"]["numerator"] == 1

    def test_identical_duplicates_count_once(self, dev_key):
        execs = perfect_executions(dev_key)
        e = by_id(execs)["fx-case_01-r1"]
        e["findings"].append(dict(e["findings"][0]))
        scored = score(dev_key, execs, CONFIG)
        g3 = scored["gates"]["gate_3_useful_verification"]
        assert g3["numerator"] == g3["denominator"]
        assert all(p["passed"] for p in scored["pages"])


# --- evidence --------------------------------------------------------------------------

class TestEvidence:
    ALLOWED = ["source_1_spec_sheet.html", "source_3_press_release.html"]

    @pytest.mark.parametrize("cited,ok", [
        ("case_01/source_1_spec_sheet.html", True),
        ("source_1_spec_sheet.html", True),
        ("evals/dataset/case_01/source_1_spec_sheet.html", True),
        ("case_01\\source_1_spec_sheet.html", True),
        ("case_01/source_1_spec_sheet.html, case_01/source_3_press_release.html", True),
        ("case_01/source_1_spec_sheet.html and case_01/source_2_retailer.html", False),
        ("none", False),
        ("", False),
        ("case_02/source_1_spec_sheet.html", False),
        ("case_01/target.html", False),
    ])
    def test_path_forms(self, cited, ok):
        assert evidence_valid(cited, self.ALLOWED, "case_01") is ok


# --- failure classification and completion -------------------------------------------------

SDK_RESULT_ERROR = "builtins.Exception"  # the SDK's error-result exception type
PROCESS_ERROR = "claude_agent_sdk._errors.ProcessError"
CLI_NOT_FOUND = "claude_agent_sdk._errors.CLINotFoundError"
CLI_JSON = "claude_agent_sdk._errors.CLIJSONDecodeError"
AUTH_GUARD = "agent.harness.AuthGuardError"


def error_result(text: str, subtype: str = "success", errors=None, status=None) -> dict:
    """An is_error result as the CLI reports an API error."""
    return {"subtype": subtype, "is_error": True, "num_turns": 1, "result_text": text,
            "errors": errors or [], "api_error_status": status}


class TestClassification:
    def _classify(self, error_type=None, error=None, result=None, **kw):
        return classify_execution(error_type, error, result, kw.get("findings", []), kw.get("rows", []),
                                  "case_01", 20, 60, CONFIG)

    # Texts as they appear in the bundled CLI 2.1.191 (claude.exe), except
    # the 429/529 bodies and the 401 form, which follow its "API Error: "
    # prefix with the API's own error JSON.
    @pytest.mark.parametrize("text,category", [
        ("Not logged in · Please run /login", "auth"),
        ("Invalid API key · Fix external API key", "auth"),
        ("OAuth token revoked · Please run /login", "auth"),
        ("Authentication error · This may be a temporary network issue, please try again", "auth"),
        ('API Error: 401 {"type":"error","error":{"type":"authentication_error","message":"invalid x-api-key"}}',
         "auth"),
        ('API Error: 429 {"type":"rate_limit_error"}', "transient_rate_limit"),
        ("Request rejected (429)", "transient_rate_limit"),
        ("Server is temporarily limiting requests (not your usage limit)", "transient_rate_limit"),
        ("API Error: Server is temporarily limiting requests (not your usage limit) · Rate limited",
         "transient_rate_limit"),
        ("Claude AI usage limit reached", "subscription_limit"),
        ("You're out of usage credits. /model to switch models.", "subscription_limit"),
        ("You've hit your monthly spend limit. Run /usage-credits to manage your limit", "subscription_limit"),
        ("Credit balance is too low", "subscription_limit"),
        # The CLI builds these from "You've hit your " plus the limit name.
        ("You've hit your session limit · resets 3pm (UTC)", "subscription_limit"),
        ("You've hit your weekly limit · resets Nov 9, 3pm (UTC)", "subscription_limit"),
        ("You've hit your Sonnet limit · resets 3pm (UTC)", "subscription_limit"),
        ("You've hit your Opus limit · resets 3pm (UTC)", "subscription_limit"),
        ("You've hit your Fable 5 limit · resets 3pm (UTC)", "subscription_limit"),
        ("You've hit your usage limit · resets 3pm (UTC)", "subscription_limit"),
        ("You've hit your limit · resets 3pm (UTC)", "subscription_limit"),
        ("API Error: 529 overloaded_error", "overloaded_or_5xx"),
        ("Repeated 529 Overloaded errors", "overloaded_or_5xx"),
        ("API Error: 500 Internal server error", "overloaded_or_5xx"),
        ("Request timed out. Check your internet connection and proxy settings", "network"),
        ("Connection error.", "network"),
        ("Unable to connect to API. Check your internet connection", "network"),
        ("Unable to connect to API: SSL certificate verification failed. Check your proxy or corporate "
         "SSL certificates", "network"),
    ])
    def test_error_result_infra_texts_are_invalid_run(self, text, category):
        out = self._classify(result=error_result(text))
        assert out["status"] == INVALID_RUN and out["infra_category"] == category

    def test_result_is_kept_when_the_sdk_raises(self):
        """RT1-02: the SDK's exception text is generic; the kept result decides."""
        out = self._classify(SDK_RESULT_ERROR, "Claude Code returned an error result: success",
                             error_result("API Error: 529 Overloaded"))
        assert out["status"] == INVALID_RUN and out["infra_category"] == "overloaded_or_5xx"
        assert "Claude Code returned an error result: success" in out["failures"][0]

    def test_errors_list_is_classified(self):
        out = self._classify(SDK_RESULT_ERROR, "Claude Code returned an error result: Credit balance is too low",
                             error_result("", errors=["Credit balance is too low"]))
        assert out["infra_category"] == "subscription_limit"

    def test_limit_text_is_classified_when_the_sdk_raises(self):
        """The SDK's exception says only 'success'; the kept result decides."""
        out = self._classify(SDK_RESULT_ERROR, "Claude Code returned an error result: success",
                             error_result("You've hit your session limit · resets 3pm (UTC)", status=429))
        assert out["status"] == INVALID_RUN and out["infra_category"] == "subscription_limit"

    def test_transient_text_is_never_read_as_a_subscription_limit(self):
        """It contains 'usage limit', so its group must be tried first."""
        names = list(CONFIG["infra_errors"]["patterns"])
        assert names.index("transient_rate_limit") < names.index("subscription_limit")
        text = "API Error: Server is temporarily limiting requests (not your usage limit) · Rate limited"
        out = self._classify(result=error_result(text, status=429))
        assert out["infra_category"] == "transient_rate_limit"

    @pytest.mark.parametrize("text", ["", "A raw API message with no known phrase"])
    def test_status_429_without_a_known_text_is_unrecognized(self, text):
        out = self._classify(result=error_result(text, status=429))
        assert out["status"] == INVALID_RUN and out["infra_category"] == "unrecognized_429"

    @pytest.mark.parametrize("text", ["Your organization budget is exhausted", "Monthly turn limit exceeded"])
    def test_status_429_skips_the_cap_patterns(self, text):
        out = self._classify(result=error_result(text, status=429))
        assert out["status"] == INVALID_RUN and out["infra_category"] == "unrecognized_429"

    def test_a_cap_subtype_still_decides_with_status_429(self):
        out = self._classify(result=error_result("Your organization budget is exhausted",
                                                 subtype="error_max_budget_usd", status=429))
        assert out["status"] == AGENT_FAILURE and out["infra_category"] is None

    @pytest.mark.parametrize("result", [
        error_result("A raw API message with no known phrase", status=500),
        error_result("A raw API message with no known phrase"),
        {**error_result("A raw API message with no known phrase", status=429), "is_error": False,
         "subtype": "error_during_execution"},
    ])
    def test_only_a_cli_api_error_with_status_429_is_unrecognized_429(self, result):
        out = self._classify(result=result)
        assert out["status"] == AGENT_FAILURE and out["infra_category"] is None

    def test_a_quoted_limit_phrase_inside_a_line_does_not_match(self):
        """The prefix is anchored, like the CLI's own startsWith check."""
        out = self._classify(result=error_result("The page said: You've hit your session limit"))
        assert out["status"] == AGENT_FAILURE and out["infra_category"] is None

    @pytest.mark.parametrize("error_type,text", [
        ("builtins.RuntimeError", "You've hit your session limit · resets 3pm (UTC)"),
        ("builtins.Exception", "You've hit your session limit · resets 3pm (UTC)"),
        ("builtins.ValueError", "Server is temporarily limiting requests (not your usage limit)"),
    ])
    def test_limit_text_in_an_agent_exception_is_not_classified(self, error_type, text):
        out = self._classify(error_type, text)
        assert out["status"] == AGENT_FAILURE and out["infra_category"] is None

    @pytest.mark.parametrize("error_type,text,result,category", [
        (CLI_NOT_FOUND, "Claude Code not found", None, "cli_spawn"),
        (PROCESS_ERROR, "Command failed with exit code 1 (exit code: 1)", None, "cli_process"),
        (AUTH_GUARD, "apiKeySource is 'ANTHROPIC_API_KEY', expected 'none'", None, "auth"),
    ])
    def test_sdk_exceptions(self, error_type, text, result, category):
        out = self._classify(error_type, text, result)
        assert out["status"] == INVALID_RUN and out["infra_category"] == category

    @pytest.mark.parametrize("error_type,text,result", [
        # RT1-04: runner or agent code is never matched against infra patterns
        ("json.decoder.JSONDecodeError", "Expecting value: line 1 column 500 (char 499)", None),
        ("builtins.KeyError", "'h001' while scoring 429 findings", None),
        ("builtins.RuntimeError", "MCP server claimverify failed to start: ImportError in agent/tools.py", None),
        ("builtins.ValueError", "unauthorized verdict value", None),
        ("builtins.KeyError", "'claim_text'", None),
        # caps decide by subtype, whatever the text says
        (None, None, error_result("Reached maximum number of turns (20); API Error: 500",
                                  subtype="error_max_turns")),
        (None, None, error_result("Error: Exceeded USD budget (1.5); API Error: 529",
                                  subtype="error_max_budget_usd")),
        # cap text beats an infra type rule
        (PROCESS_ERROR, "Command failed with exit code 1: max budget exceeded", None),
        (SDK_RESULT_ERROR, "Claude Code returned an error result: Reached maximum number of turns (20)", None),
        # SDK origin, but no anchored pattern: unclassified, so never INVALID RUN
        (CLI_JSON, "Failed to decode JSON: {\"col\": 500, \"n\": 429}...", None),
        (SDK_RESULT_ERROR, "Claude Code returned an error result: error_during_execution",
         error_result("", subtype="error_during_execution")),
        # a plain Exception without the SDK's prefix is runner code
        ("builtins.Exception", "Not logged in · Please run /login", None),
    ])
    def test_agent_failures(self, error_type, text, result):
        out = self._classify(error_type, text, result)
        assert out["status"] == AGENT_FAILURE and out["infra_category"] is None
        if text:
            assert text in out["failures"][0]

    def test_success_result_text_is_never_matched(self):
        """A success result's text is the agent's own answer."""
        result = {"subtype": "success", "is_error": False, "num_turns": 3,
                  "result_text": "Not logged in? API Error: 529. Usage limit reached."}
        out = self._classify(result=result)  # fails completion: no findings, no audit
        assert out["status"] == AGENT_FAILURE and out["infra_category"] is None

    def test_limit_text_in_a_success_result_is_never_matched(self):
        result = {"subtype": "success", "is_error": False, "num_turns": 3, "api_error_status": None,
                  "result_text": "You've hit your session limit · resets 3pm (UTC)"}
        out = self._classify(result=result)
        assert out["status"] == AGENT_FAILURE and out["infra_category"] is None


class TestCompletion:
    @pytest.fixture
    def db(self, tmp_path):
        path = tmp_path / "audit.db"
        conn = sqlite3.connect(path)
        conn.execute(_SCHEMA)
        conn.commit()
        conn.close()
        return path

    def _insert(self, db, run_id, tool_use_id, tool, event, tool_input, response=None):
        payload = {"tool_input": tool_input}
        if event == "PostToolUse":
            payload["tool_response"] = [{"type": "text", "text": response}]
        elif event == "PostToolUseFailure":  # the hook input's error field (SDK 0.2.110)
            payload.update(error=response, is_interrupt=False)
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO tool_calls (run_id, case_id, session_id, tool_use_id, tool_name, "
                     "event_type, timestamp, payload) VALUES (?, 'case_01', 's', ?, ?, ?, 't', ?)",
                     (run_id, tool_use_id, tool, event, json.dumps(payload)))
        conn.commit()
        conn.close()

    def _good_run(self, db, run_id="r1", target="case_01/target.html", read_response="Found 1 candidate claim(s):\n1. x"):
        self._insert(db, run_id, "t1", EXTRACT, "PreToolUse", {"path": target})
        self._insert(db, run_id, "t1", EXTRACT, "PostToolUse", {"path": target}, read_response)
        self._insert(db, run_id, "t2", LOG, "PreToolUse", {"claim_text": "x"})
        self._insert(db, run_id, "t2", LOG, "PostToolUse", {"claim_text": "x"}, "Logged: SUPPORTED — 'x'")

    FINDINGS = [{"claim_text": "x", "verdict": "SUPPORTED",
                 "evidence_source": "case_01/source_1_spec_sheet.html", "evidence_note": "n"}]
    RESULT = {"subtype": "success", "is_error": False, "num_turns": 5, "result_text": "done"}

    def _status(self, db, run_id="r1", findings=None, result=None):
        rows = load_audit_rows(db, run_id)
        return classify_execution(None, None, result or self.RESULT,
                                  self.FINDINGS if findings is None else findings,
                                  rows, "case_01", 20, 60, CONFIG)

    def test_complete_run(self, db):
        self._good_run(db)
        out = self._status(db)
        assert out["status"] == COMPLETED, out["failures"]

    def test_other_run_rows_are_ignored(self, db):
        self._good_run(db, run_id="other")
        assert self._status(db)["status"] == AGENT_FAILURE

    @pytest.mark.parametrize("tool", [LOG, EXTRACT, "mcp__claimverify__fetch_page",
                                      "mcp__claimverify__compare_source"])
    def test_any_call_without_post_fails(self, db, tool):
        self._good_run(db)
        self._insert(db, "r1", "t3", tool, "PreToolUse", {"path": "case_01/source_1_spec_sheet.html"})
        out = self._status(db)
        assert out["status"] == AGENT_FAILURE
        assert any("without PostToolUse or PostToolUseFailure" in f for f in out["failures"])

    @pytest.mark.parametrize("tool", [LOG, EXTRACT, "mcp__claimverify__fetch_page",
                                      "mcp__claimverify__compare_source"])
    def test_failure_row_closes_the_pair(self, db, tool):
        """RT1-01: a tool error is recorded as PostToolUseFailure, not PostToolUse."""
        self._good_run(db)
        self._insert(db, "r1", "t3", tool, "PreToolUse", {"path": "case_01/nope.html"})
        self._insert(db, "r1", "t3", tool, "PostToolUseFailure", {"path": "case_01/nope.html"},
                     "MCP tool returned error")
        out = self._status(db)
        assert out["status"] == COMPLETED, out["failures"]

    def test_failure_row_is_not_a_target_read(self, db):
        self._insert(db, "r1", "t1", EXTRACT, "PreToolUse", {"path": "case_01/target.html"})
        self._insert(db, "r1", "t1", EXTRACT, "PostToolUseFailure", {"path": "case_01/target.html"},
                     "Found 1 candidate claim(s):\n1. x")
        self._insert(db, "r1", "t2", LOG, "PreToolUse", {"claim_text": "x"})
        self._insert(db, "r1", "t2", LOG, "PostToolUse", {"claim_text": "x"}, "Logged: SUPPORTED — 'x'")
        assert any("no successful read" in f for f in self._status(db)["failures"])

    def test_failure_row_is_not_a_logged_row(self, db):
        self._good_run(db)
        self._insert(db, "r1", "t4", LOG, "PreToolUse", {"claim_text": "y"})
        self._insert(db, "r1", "t4", LOG, "PostToolUseFailure", {"claim_text": "y"}, "Logged: SUPPORTED — 'y'")
        out = self._status(db, findings=self.FINDINGS * 2)
        assert any("1 logged log_finding response(s) vs 2" in f for f in out["failures"])

    def test_breaker_text_in_failure_row(self, db):
        self._good_run(db)
        self._insert(db, "r1", "t9", EXTRACT, "PreToolUse", {"path": "case_01/target.html"})
        self._insert(db, "r1", "t9", EXTRACT, "PostToolUseFailure", {"path": "case_01/target.html"},
                     "Circuit breaker tripped: tool-call ceiling (60) reached for this run.")
        assert any("circuit breaker" in f for f in self._status(db)["failures"])

    @pytest.mark.parametrize("target", ["./case_01/target.html", "case_01//target.html",
                                        "case_01/./target.html", "CASE_01/target.html"])
    def test_target_path_forms(self, db, target):
        """RT1-09: the path is compared after normpath and casefold; the
        response must still be a successful read."""
        self._good_run(db, target=target)
        assert self._status(db)["status"] == COMPLETED

    @pytest.mark.parametrize("target", ["case_02/target.html", "case_01/source_1_spec_sheet.html",
                                        "case_01/../case_02/target.html"])
    def test_other_paths_are_not_the_target(self, db, target):
        self._good_run(db, target=target)
        assert any("no successful read" in f for f in self._status(db)["failures"])

    def test_malformed_findings_fail_completion(self, db):
        self._good_run(db)
        for bad in ([dict(self.FINDINGS[0], verdict="supported")], [{"claim_text": "x"}], ["x"]):
            out = self._status(db, findings=bad)
            assert out["status"] == AGENT_FAILURE

    def test_logged_count_must_equal_findings(self, db):
        self._good_run(db)
        out = self._status(db, findings=self.FINDINGS * 2)
        assert any("logged log_finding" in f for f in out["failures"])

    def test_empty_report(self, db):
        self._good_run(db)
        out = self._status(db, findings=[])
        assert any("empty report" in f for f in out["failures"])

    def test_unrecognized_verdict(self, db):
        self._good_run(db)
        bad = [dict(self.FINDINGS[0], verdict="MAYBE")]
        assert any("unrecognized verdict" in f for f in self._status(db, findings=bad)["failures"])

    def test_no_successful_target_read(self, db):
        self._good_run(db, read_response="No such file under evals/dataset/: case_01/target.html")
        out = self._status(db)
        assert any("no successful read" in f for f in out["failures"])

    def test_circuit_breaker_trip(self, db):
        self._good_run(db)
        self._insert(db, "r1", "t9", EXTRACT, "PreToolUse", {"path": "case_01/target.html"})
        self._insert(db, "r1", "t9", EXTRACT, "PostToolUse", {"path": "case_01/target.html"},
                     "Circuit breaker tripped: tool-call ceiling (60) reached for this run.")
        assert any("circuit breaker" in f for f in self._status(db)["failures"])


# --- attempts, records, rule F and the official label (first red-team amendment) ---------

def _page(scored, case_id):
    return next(p for p in scored["pages"] if p["case_id"] == case_id)


class TestAttempts:
    def test_partial_swap_is_invalid(self, official_key):
        """RT1-05: replacing runs 1 and 2 but keeping run 3 must not reach PASS."""
        execs = perfect_executions(official_key)
        case = official_key["cases"][0]
        cid = case["case_id"]
        contradicted = next(c for c in case["claims"] if c["verdict"] != "UNVERIFIABLE")
        set_verdict(execs, case, contradicted, 1, "UNVERIFIABLE")
        mark_invalid(by_id(execs)[f"fx-{cid}-r2"])
        for r in (1, 2):
            by_id(execs)[f"fx-{cid}-r{r}"]["superseded"] = True
            fresh = copy.deepcopy(by_id(perfect_executions(official_key))[f"fx-{cid}-r{r}"])
            fresh.update(execution_id=f"fx-{cid}-r{r}-a2", attempt=2)
            execs.append(fresh)
        scored = score(official_key, execs, CONFIG)
        assert scored["result"] == INVALID_LABEL
        assert any(f"fx-{cid}-r3 not superseded" in p for p in scored["attempt_problems"])

    def test_events_of_a_superseded_attempt_still_fail_the_page(self, official_key):
        """RT1-06 and the rerun-attempt ruling: a rule A-E event seen in a valid
        run of an earlier attempt still fails the page and stays reported."""
        execs = perfect_executions(official_key)
        case, claim = claim_of(official_key, "CONTRADICTED")
        cid = case["case_id"]
        set_verdict(execs, case, claim, 1, "SUPPORTED")  # rule A
        by_id(execs)[f"fx-{cid}-r2"]["status"] = AGENT_FAILURE  # rule E
        fresh = rerun_page(execs, case, invalid_run=3)
        scored = score(official_key, fresh, CONFIG)
        page = _page(scored, cid)
        assert page["rules"] == ["A", "E"] and not page["passed"]
        assert {e["rule"] for e in scored["events"] if e["superseded"]} == {"A", "E"}
        assert scored["result"] == "PASS"  # one failed page of 61 is within the gate
        assert "(superseded attempt 1, still fails the page)" in format_report(scored)
        # superseded runs stay out of the gate 2 and 3 denominators
        perfect = score(official_key, perfect_executions(official_key), CONFIG)
        assert scored["gates"]["gate_3_useful_verification"]["denominator"] == \
            perfect["gates"]["gate_3_useful_verification"]["denominator"]
        assert scored["secondary"]["executions_valid"] == 183

    def test_earlier_attempt_without_invalid_run_is_invalid(self, official_key):
        execs = perfect_executions(official_key)
        case = official_key["cases"][0]
        fresh = rerun_page(execs, case, invalid_run=None)  # nothing was INVALID RUN
        scored = score(official_key, fresh, CONFIG)
        assert scored["result"] == INVALID_LABEL
        assert any("is not a supported INVALID RUN" in p for p in scored["attempt_problems"])

    def test_attempt_gap_is_invalid(self, official_key):
        execs = perfect_executions(official_key)
        fresh = rerun_page(execs, official_key["cases"][0], invalid_run=1, attempt=3)
        scored = score(official_key, fresh, CONFIG)
        assert scored["result"] == INVALID_LABEL
        assert any("without gaps" in p for p in scored["attempt_problems"])

    def test_second_rerun(self, official_key):
        execs = perfect_executions(official_key)
        case = official_key["cases"][0]
        twice = rerun_page(rerun_page(execs, case, invalid_run=1), case, invalid_run=2, attempt=3)
        assert score(official_key, twice, CONFIG)["result"] == "PASS"

    def test_incomplete_scored_attempt_is_invalid(self, official_key):
        execs = perfect_executions(official_key)
        fresh = [e for e in rerun_page(execs, official_key["cases"][0], invalid_run=1)
                 if e["execution_id"] != f"fx-{official_key['cases'][0]['case_id']}-r3-a2"]
        assert score(official_key, fresh, CONFIG)["result"] == INVALID_LABEL


class TestRecords:
    def test_lowercase_verdict_is_rule_e(self, official_key):
        """RT1-07: a malformed finding in a run recorded as completed is agent
        output: rule E, never a silent pass and never INVALID RUN."""
        execs = perfect_executions(official_key)
        case, claim = claim_of(official_key, "CONTRADICTED")
        set_verdict(execs, case, claim, 1, "supported")
        scored = score(official_key, execs, CONFIG)
        assert _page(scored, case["case_id"])["rules"] == ["E"]
        assert scored["result"] == "PASS"  # one failed page; the series is still valid

    def test_missing_finding_field_is_rule_e(self, official_key):
        execs = perfect_executions(official_key)
        case = official_key["cases"][0]
        del by_id(execs)[f"fx-{case['case_id']}-r1"]["findings"][0]["evidence_note"]
        assert _page(score(official_key, execs, CONFIG), case["case_id"])["rules"] == ["E"]

    @pytest.mark.parametrize("mutate,expect", [
        (lambda e: e.update(status="complete"), "unknown status"),
        (lambda e: e.pop("attempt"), "attempt"),
        (lambda e: e.update(run_index="1"), "run_index"),
        (lambda e: e.update(superseded="no"), "superseded"),
        (lambda e: e.update(attempt=True), "attempt"),
        (lambda e: e.update(attempt=0), "attempt 0 < 1"),
    ])
    def test_malformed_record_is_invalid(self, official_key, mutate, expect):
        execs = perfect_executions(official_key)
        mutate(execs[0])
        scored = score(official_key, execs, CONFIG)
        assert scored["result"] == INVALID_LABEL
        assert any(expect in p for p in scored["record_problems"]), scored["record_problems"]

    def test_stray_execution_is_invalid(self, official_key):
        """RT1-12: an execution for a case outside the key is never ignored."""
        execs = perfect_executions(official_key)
        stray = dict(copy.deepcopy(execs[0]), execution_id="fx-h999-r1", case_id="h999", status=AGENT_FAILURE)
        scored = score(official_key, execs + [stray], CONFIG)
        assert scored["result"] == INVALID_LABEL
        assert any("h999" in p for p in scored["record_problems"])

    def test_duplicate_execution_id_is_invalid(self, official_key):
        execs = perfect_executions(official_key)
        scored = score(official_key, execs + [copy.deepcopy(execs[0])], CONFIG)
        assert scored["result"] == INVALID_LABEL
        assert any("duplicate execution_id" in p for p in scored["record_problems"])

    def test_dev_record_problems_are_reported(self, dev_key):
        execs = perfect_executions(dev_key)
        execs[0]["status"] = "complete"
        scored = score(dev_key, execs, CONFIG)
        assert scored["result"] == DEV_ONLY and scored["record_problems"]
        assert "Record problems (left out of scoring)" in format_report(scored)


class TestRuleF:
    def _paraphrase(self, key, verdict):
        execs = perfect_executions(key)
        case, claim = claim_of(key, verdict)
        e = by_id(execs)[f"fx-{case['case_id']}-r1"]
        for f in e["findings"]:
            if f["claim_text"] == claim["text"]:
                f["claim_text"] = "A paraphrase that matches no paragraph."
        return execs, case

    def test_unmatched_supported_fails_the_page(self, dev_key):
        """RT1-08 and the unmatched-SUPPORTED ruling."""
        execs, case = self._paraphrase(dev_key, "SUPPORTED")
        scored = score(dev_key, execs, CONFIG)
        assert _page(scored, case["case_id"])["rules"] == ["F"]
        assert [e["rule"] for e in scored["events"]] == ["F"]

    def test_unmatched_unverifiable_claim_marked_supported(self, dev_key):
        execs, case = self._paraphrase(dev_key, "UNVERIFIABLE")
        for e in execs:
            for f in e["findings"]:
                if f["claim_text"] == "A paraphrase that matches no paragraph.":
                    f["verdict"] = "SUPPORTED"
        assert _page(score(dev_key, execs, CONFIG), case["case_id"])["rules"] == ["F"]

    def test_unmatched_unverifiable_does_not_fail(self, dev_key):
        execs, case = self._paraphrase(dev_key, "UNVERIFIABLE")
        scored = score(dev_key, execs, CONFIG)
        assert _page(scored, case["case_id"])["passed"]
        assert len(scored["unmatched_findings"]) == 1

    def test_fetch_page_bullet_matches(self, dev_key):
        execs = perfect_executions(dev_key)
        for f in execs[0]["findings"]:
            f["claim_text"] = "- " + f["claim_text"]
        scored = score(dev_key, execs, CONFIG)
        assert scored["unmatched_findings"] == [] and all(p["passed"] for p in scored["pages"])


class TestOfficialLabel:
    def test_duplicate_case_id_is_not_official(self, official_key):
        """RT1-12: 61 entries but 60 distinct pages is not an official set."""
        official_key["cases"][1] = copy.deepcopy(official_key["cases"][0])
        scored = score(official_key, perfect_executions(official_key)[3:], CONFIG)
        assert scored["result"] == DEV_ONLY
        assert any("duplicate case_id" in p for p in scored["key_problems"])

    def test_case_id_pattern(self, official_key):
        official_key["cases"][0]["case_id"] = "x001"
        scored = score(official_key, perfect_executions(official_key), CONFIG)
        assert scored["result"] == DEV_ONLY
        assert any("x001" in p for p in scored["key_problems"])


class TestRescore:
    def _recorded(self, tmp_path, key_text='{"set": "dev", "cases": []}'):
        key_path = tmp_path / "key.json"
        key_path.write_text(key_text, encoding="utf-8")
        recorded = {"answer_key": str(key_path), "answer_key_sha256": key_sha256(key_path), "executions": []}
        return recorded, key_path

    def test_same_key_is_accepted(self, tmp_path):
        recorded, key_path = self._recorded(tmp_path)
        assert rescore_key_problem(recorded, key_path) is None

    def test_changed_key_is_refused(self, tmp_path, monkeypatch, capsys):
        recorded, key_path = self._recorded(tmp_path)
        key_path.write_text('{"set": "dev", "cases": [] }', encoding="utf-8")
        assert "differs from the recorded" in rescore_key_problem(recorded, key_path)
        results = tmp_path / "results.json"
        results.write_text(json.dumps(recorded), encoding="utf-8")
        monkeypatch.setattr(sys, "argv", ["score_v2.py", str(results)])
        with pytest.raises(SystemExit) as exc:
            score_main()
        assert exc.value.code == 2 and "Refused" in capsys.readouterr().err

    def test_missing_hash_is_refused(self, tmp_path):
        recorded, key_path = self._recorded(tmp_path)
        del recorded["answer_key_sha256"]
        assert "records no answer_key_sha256" in rescore_key_problem(recorded, key_path)


# --- second amendment: the red-team re-check fixes ---------------------------------

def _rule_a_on_run1(key, pages=3):
    execs = perfect_executions(key)
    hit = []
    for case in key["cases"]:
        claim = next((c for c in case["claims"] if c["verdict"] == "CONTRADICTED"), None)
        if claim is not None and len(hit) < pages:
            set_verdict(execs, case, claim, 1, "SUPPORTED")
            hit.append(case)
    return execs, hit


class TestStatusIntegrity:
    def test_unsupported_invalid_run_cannot_erase_a_failure(self, official_key):
        """Re-check RT1R-01: a bad run relabelled invalid_run with no infra
        evidence does not justify a rerun, and its failure stays."""
        execs, hit = _rule_a_on_run1(official_key)
        for case in hit:
            by_id(execs)[f"fx-{case['case_id']}-r1"]["status"] = INVALID_RUN  # no evidence
        for case in hit:
            execs = rerun_page(execs, case, invalid_run=None)  # keeps the relabelled run 1
            execs = [e for e in execs if not (e["case_id"] == case["case_id"] and e["attempt"] == 1
                                              and e["run_index"] > 1)]
        scored = score(official_key, execs, CONFIG)
        assert scored["result"] == INVALID_LABEL
        for case in hit:
            assert not _page(scored, case["case_id"])["passed"]
        assert sum("record integrity" in p for p in scored["record_problems"]) == 3
        assert any("is not a supported INVALID RUN" in p for p in scored["attempt_problems"])

    @pytest.mark.parametrize("mutate,expect", [
        (lambda e: e.update(error_type=PROCESS_ERROR, error="Command failed with exit code 1"),
         "an exception is recorded"),
        (lambda e: e.update(error_type="builtins.TimeoutError", error=""), "an exception is recorded"),
        (lambda e: e.update(result=dict(SUCCESS_RESULT, subtype="error_max_turns", is_error=True)),
         "no success result"),
        (lambda e: e.update(result=None), "no success result"),
        (lambda e: e.update(result=dict(SUCCESS_RESULT, num_turns=25)), "num_turns is 25"),
    ])
    def test_completed_needs_its_evidence(self, official_key, mutate, expect):
        execs = perfect_executions(official_key)
        mutate(execs[0])
        scored = score(official_key, execs, CONFIG)
        assert scored["result"] == INVALID_LABEL
        assert any(expect in p for p in scored["record_problems"]), scored["record_problems"]
        assert _page(scored, execs[0]["case_id"])["rules"] == ["E"]  # kept, as an agent failure

    def test_agent_failure_with_infra_evidence(self, official_key):
        execs = perfect_executions(official_key)
        mark_invalid(execs[0])["status"] = AGENT_FAILURE
        scored = score(official_key, execs, CONFIG)
        assert scored["result"] == INVALID_LABEL
        assert any("classifies as infrastructure" in p for p in scored["record_problems"])

    def test_recorded_category_must_match(self, official_key):
        execs = perfect_executions(official_key)
        mark_invalid(execs[0])["infra_category"] = "auth"
        scored = score(official_key, execs, CONFIG)
        assert any("recorded infra_category 'auth'" in p for p in scored["record_problems"])

    def test_supported_invalid_run_is_consistent(self, official_key):
        execs = rerun_page(perfect_executions(official_key), official_key["cases"][0], invalid_run=2)
        scored = score(official_key, execs, CONFIG)
        assert scored["result"] == "PASS" and scored["record_problems"] == []

    @pytest.mark.parametrize("field", ["error_type", "error", "result"])
    def test_evidence_fields_are_required(self, official_key, field):
        execs = perfect_executions(official_key)
        del execs[0][field]
        scored = score(official_key, execs, CONFIG)
        assert scored["result"] == INVALID_LABEL
        assert any(field in p for p in scored["record_problems"])


class TestEarlierAttemptRuns:
    def test_lost_run_of_an_earlier_attempt(self, official_key):
        """Re-check RT1R-02: losing a bad run's record from an earlier attempt
        is refused, so its event cannot disappear."""
        execs, hit = _rule_a_on_run1(official_key)
        for case in hit:
            execs = rerun_page(execs, case, invalid_run=2)
        full = score(official_key, execs, CONFIG)
        assert full["result"] == "FAIL" and full["gates"]["gate_1_page_reliability"]["passing_pages"] == 58
        lost = [e for e in execs if not (e["attempt"] == 1 and e["run_index"] == 1
                                         and e["case_id"] in {c["case_id"] for c in hit})]
        scored = score(official_key, lost, CONFIG)
        assert scored["result"] == INVALID_LABEL
        assert sum("holds runs [2]" in p for p in scored["attempt_problems"]) == 3

    @pytest.mark.parametrize("run_index", [7, 2])
    def test_run_index_out_of_range_or_duplicate(self, official_key, run_index):
        case = official_key["cases"][0]
        execs = rerun_page(perfect_executions(official_key), case, invalid_run=2)
        by_id(execs)[f"fx-{case['case_id']}-r1"]["run_index"] = run_index
        scored = score(official_key, execs, CONFIG)
        assert scored["result"] == INVALID_LABEL
        assert any("an earlier attempt needs runs 1 to k" in p for p in scored["attempt_problems"])

    def test_invalid_run_must_end_the_attempt(self, official_key):
        case = official_key["cases"][0]
        execs = perfect_executions(official_key)
        mark_invalid(by_id(execs)[f"fx-{case['case_id']}-r1"])
        execs = rerun_page(execs, case, invalid_run=None)  # runs 2 and 3 kept after the INVALID RUN
        scored = score(official_key, execs, CONFIG)
        assert scored["result"] == INVALID_LABEL
        assert any("continued after an INVALID RUN" in p for p in scored["attempt_problems"])

    def test_events_of_earlier_valid_runs_are_kept(self, official_key):
        execs, hit = _rule_a_on_run1(official_key, pages=1)
        execs = rerun_page(execs, hit[0], invalid_run=2)
        scored = score(official_key, execs, CONFIG)
        assert _page(scored, hit[0]["case_id"])["rules"] == ["A"]
        assert scored["attempt_problems"] == [] and scored["result"] == "PASS"


CRASH_TEXT = "Command failed with exit code 1 (exit code: 1)\nError output: Check stderr output for details"


class TestCliCrashProvenance:
    """Re-check RT1R-03: the SDK re-raises a mid-run ProcessError as a plain
    Exception (claude_agent_sdk 0.2.110 _internal/query.py receive_messages);
    its text is the ProcessError message built in subprocess_cli.py."""

    def _classify(self, error_type, text, origin, result=None):
        return classify_execution(error_type, text, result, [], [], "case_01", 20, 60, CONFIG,
                                  error_origin=origin)

    @pytest.mark.parametrize("text", [CRASH_TEXT, CRASH_TEXT.replace("1", "-1")])
    def test_crash_at_the_sdk_boundary_is_invalid_run(self, text):
        out = self._classify("builtins.Exception", text, "sdk_call")
        assert out["status"] == INVALID_RUN and out["infra_category"] == "cli_process"

    @pytest.mark.parametrize("error_type,text,origin,result", [
        ("builtins.Exception", CRASH_TEXT, None, None),  # origin not established
        ("builtins.Exception", CRASH_TEXT, "runner", None),
        ("builtins.Exception", "Command failed with exit code 1: oops", "sdk_call", None),
        ("builtins.Exception", CRASH_TEXT.replace("(exit code: 1)", "(exit code: 2)"), "sdk_call", None),
        ("builtins.Exception", CRASH_TEXT + "\nmore", "sdk_call", None),
        ("builtins.RuntimeError", CRASH_TEXT, "sdk_call", None),
        ("builtins.Exception", CRASH_TEXT, "sdk_call", error_result("", subtype="error_max_turns")),
    ])
    def test_without_provenance_or_signature_it_stays_rule_e(self, error_type, text, origin, result):
        out = self._classify(error_type, text, origin, result)
        assert out["status"] == AGENT_FAILURE and out["infra_category"] is None

    def test_crash_record_is_consistent(self, official_key):
        case = official_key["cases"][0]
        execs = rerun_page(perfect_executions(official_key), case, invalid_run=2)
        e = by_id(execs)[f"fx-{case['case_id']}-r2"]
        e.update(infra_category="cli_process", error_type="builtins.Exception", error=CRASH_TEXT,
                 error_origin="sdk_call", result=None)
        assert score(official_key, execs, CONFIG)["result"] == "PASS"
        e["error_origin"] = None
        scored = score(official_key, execs, CONFIG)
        assert scored["result"] == INVALID_LABEL
        assert any("show no infrastructure category" in p for p in scored["record_problems"])


class TestEmptyException:
    """Re-check RT1R-04: an exception with an empty message is an exception."""

    @pytest.mark.parametrize("error_type,text", [
        ("builtins.TimeoutError", ""),
        ("asyncio.exceptions.CancelledError", ""),
        ("asyncio.exceptions.CancelledError", None),
    ])
    def test_empty_message_after_a_good_run(self, error_type, text):
        out = classify_execution(error_type, text, TestCompletion.RESULT, TestCompletion.FINDINGS,
                                 [], "case_01", 20, 60, CONFIG)
        assert out["status"] == AGENT_FAILURE
        assert out["failures"][0].startswith(f"exception: {error_type}")

    def test_empty_sdk_exception_is_still_classified(self):
        out = classify_execution(CLI_NOT_FOUND, "", None, [], [], "case_01", 20, 60, CONFIG)
        assert out["status"] == INVALID_RUN and out["infra_category"] == "cli_spawn"


class TestKeyHashForm:
    """Re-check RT1R-05: the answer-key hash ignores the checkout's line endings."""

    def test_lf_and_crlf_give_one_hash(self, tmp_path):
        lf, crlf = tmp_path / "lf.json", tmp_path / "crlf.json"
        lf.write_bytes(b'{\n  "set": "dev",\n  "cases": []\n}\n')
        crlf.write_bytes(lf.read_bytes().replace(b"\n", b"\r\n"))
        assert key_sha256(lf) == key_sha256(crlf) == hashlib.sha256(lf.read_bytes()).hexdigest()
        assert key_sha256(crlf) != hashlib.sha256(crlf.read_bytes()).hexdigest()

    def test_rescore_accepts_another_checkout(self, tmp_path):
        key_path = tmp_path / "key.json"
        key_path.write_bytes(b'{"set": "dev",\n"cases": []}\n')
        recorded = {"answer_key_sha256": key_sha256(key_path)}
        key_path.write_bytes(key_path.read_bytes().replace(b"\n", b"\r\n"))
        assert rescore_key_problem(recorded, key_path) is None


class TestNotScoredObservations:
    """Re-check RT1R-06: findings of an INVALID RUN are reported, never scored."""

    def test_false_assurance_inside_an_invalid_run(self, official_key):
        execs = perfect_executions(official_key)
        case, claim = claim_of(official_key, "CONTRADICTED")
        set_verdict(execs, case, claim, 2, "SUPPORTED")
        execs = rerun_page(execs, case, invalid_run=2)
        scored = score(official_key, execs, CONFIG)
        assert scored["result"] == "PASS" and _page(scored, case["case_id"])["passed"]
        assert scored["events"] == []
        assert [(o["rule"], o["label"]) for o in scored["not_scored_observations"]] == [("A", "NOT SCORED")]
        assert "NOT SCORED A" in format_report(scored)

    def test_no_rule_b_and_malformed_findings(self, official_key):
        case = official_key["cases"][0]
        execs = rerun_page(perfect_executions(official_key), case, invalid_run=2)
        e = by_id(execs)[f"fx-{case['case_id']}-r2"]
        e["findings"] = []
        assert score(official_key, execs, CONFIG)["not_scored_observations"] == []
        e["findings"] = ["x"]
        obs = score(official_key, execs, CONFIG)["not_scored_observations"]
        assert obs[0]["rule"] is None and obs[0]["label"] == "NOT SCORED"


class TestVerificationFollowUps:
    """Findings of the focused verification of the second amendment."""

    def test_completed_with_empty_report_is_refused(self, official_key):
        """V-01: a completed record needs at least one finding, so an empty
        report cannot hide its rule E by a status relabel."""
        execs = perfect_executions(official_key)
        case = next(c for c in official_key["cases"]
                    if all(cl["verdict"] != "CONTRADICTED" for cl in c["claims"]))
        e = by_id(execs)[f"fx-{case['case_id']}-r1"]
        e["findings"] = []
        del e["failures"]
        scored = score(official_key, execs, CONFIG)
        assert scored["result"] == INVALID_LABEL
        assert any("empty report" in p for p in scored["record_problems"])
        assert _page(scored, case["case_id"])["rules"] == ["E"]

    def test_execution_id_must_match_run_and_attempt(self, official_key):
        """V-02: deleting a bad earlier run and renumbering the INVALID RUN is
        caught by the frozen execution_id format."""
        execs, hit = _rule_a_on_run1(official_key, pages=1)
        cid = hit[0]["case_id"]
        execs = rerun_page(execs, hit[0], invalid_run=2)
        execs = [e for e in execs if e["execution_id"] != f"fx-{cid}-r1"]
        by_id(execs)[f"fx-{cid}-r2"]["run_index"] = 1
        scored = score(official_key, execs, CONFIG)
        assert scored["result"] == INVALID_LABEL
        assert any(f"fx-{cid}-r2: execution_id does not end with '-r1'" in p
                   for p in scored["record_problems"])

    @pytest.mark.parametrize("execution_id", ["fx-h001-r1-a2", "fx-h001-r2"])
    def test_execution_id_suffix(self, official_key, execution_id):
        execs = perfect_executions(official_key)
        execs[0]["execution_id"] = execution_id  # h001, run 1, attempt 1
        if execution_id == "fx-h001-r2":
            del execs[1]
        scored = score(official_key, execs, CONFIG)
        assert any("execution_id does not end with '-r1'" in p for p in scored["record_problems"])

    def test_missing_num_turns_fails_completion(self):
        """V-03: the runner's classification and the scorer's integrity check
        agree that a missing num_turns is not a complete run."""
        result = dict(TestCompletion.RESULT, num_turns=None)
        out = classify_execution(None, None, result, TestCompletion.FINDINGS, [], "case_01", 20, 60, CONFIG)
        assert out["status"] == AGENT_FAILURE
        assert any("num_turns None is not an integer" in f for f in out["failures"])


# --- make_dataset ------------------------------------------------------------------------------

def _spec(n_cases: int = 61) -> dict:
    cases = []
    for i in range(1, n_cases + 1):
        cid = f"h{i:03d}"
        cases.append({
            "case_id": cid,
            "product": f"Fixture Gadget {i}",
            "title": f"Fixture Gadget {i} Review",
            "claims": [
                {"text": f"Fixture Gadget {i} weighs {100 + i} grams.", "verdict": "SUPPORTED",
                 "scenario": "exact_spec", "evidence_sources": ["source_1_spec_sheet.html"]},
                {"text": f"Fixture Gadget {i} costs ${i}9.99.", "verdict": "CONTRADICTED",
                 "scenario": "exact_spec", "evidence_sources": ["source_2_retailer.html"]},
                {"text": f"Fixture Gadget {i} has a {i}-year warranty.", "verdict": "UNVERIFIABLE",
                 "scenario": "missing_coverage", "evidence_sources": []},
            ],
            "sources": [
                {"file": "source_1_spec_sheet.html", "title": f"Fixture Gadget {i} - Specifications",
                 "paragraphs": [f"Weight: {100 + i} grams."]},
                {"file": "source_2_retailer.html", "title": f"Fixture Gadget {i} - Buy Now",
                 "paragraphs": [f"Price: ${i}8.99."]},
            ],
        })
    return {"set": "official", "cases": cases}


class TestMakeDataset:
    @pytest.fixture
    def rendered(self, tmp_path):
        spec_path = tmp_path / "spec.json"
        spec_path.write_text(json.dumps(_spec()), encoding="utf-8")
        root, key = tmp_path / "dataset", tmp_path / "keys" / "official.json"
        assert make_dataset.render(spec_path, root, key) == []
        return root, key

    def test_render_is_deterministic(self, tmp_path, rendered):
        root, key = rendered
        spec_path = tmp_path / "spec.json"
        root2, key2 = tmp_path / "again" / "dataset", tmp_path / "again" / "keys" / "official.json"
        assert make_dataset.render(spec_path, root2, key2) == []
        files = sorted(p.relative_to(root) for p in root.rglob("*") if p.is_file())
        assert files == sorted(p.relative_to(root2) for p in root2.rglob("*") if p.is_file())
        for rel in files:
            assert (root / rel).read_bytes() == (root2 / rel).read_bytes()
        assert key.read_bytes() == key2.read_bytes()

    def test_render_writes_nothing_on_failure(self, tmp_path):
        spec = _spec()
        spec["cases"][0]["claims"][0]["verdict"] = "MAYBE"
        spec_path = tmp_path / "bad.json"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        root, key = tmp_path / "out", tmp_path / "keys" / "official.json"
        assert make_dataset.render(spec_path, root, key)
        assert not root.exists() and not key.exists()

    def test_render_refuses_overwrite(self, tmp_path, rendered):
        root, key = rendered
        assert make_dataset.render(tmp_path / "spec.json", root, key)

    def test_clean_set_passes(self, rendered):
        root, key = rendered
        assert make_dataset.check(root, key, against=[DEV_KEY_PATH]) == []
        assert make_dataset.main(["check", str(root), str(key), "--against", str(DEV_KEY_PATH)]) == 0

    def _plant(self, rendered, mutate, expect: str, against=None):
        root, key_path = rendered
        key = json.loads(key_path.read_text(encoding="utf-8"))
        mutate(root, key)
        key_path.write_text(json.dumps(key), encoding="utf-8")
        problems = make_dataset.check(root, key_path, against=[DEV_KEY_PATH] if against is None else against)
        assert any(expect in p for p in problems), problems
        argv = ["check", str(root), str(key_path), "--against", str(DEV_KEY_PATH)]
        assert make_dataset.main(argv) == 1

    def test_duplicate_case_id(self, rendered):
        def m(root, key):
            key["cases"][1]["case_id"] = "h001"
        self._plant(rendered, m, "duplicate case_id")

    def test_duplicate_claim_id(self, rendered):
        def m(root, key):
            key["cases"][0]["claims"][1]["id"] = key["cases"][0]["claims"][0]["id"]
        self._plant(rendered, m, "duplicate claim id")

    def test_dev_official_id_collision(self, rendered):
        def m(root, key):
            key["cases"][0]["claims"][0]["id"] = "case_01_c1"
        self._plant(rendered, m, "collides with another key")

    def test_claim_not_a_paragraph(self, rendered):
        def m(root, key):
            key["cases"][0]["claims"][0]["text"] = "Fixture Gadget 1 weighs"
        self._plant(rendered, m, "not exactly one target paragraph")

    def test_paragraph_not_a_claim(self, rendered):
        def m(root, key):
            t = root / "h001" / "target.html"
            t.write_text(t.read_text(encoding="utf-8").replace(
                "</body>", "    <p>An extra unclaimed paragraph.</p>\n</body>"), encoding="utf-8")
        self._plant(rendered, m, "target paragraph is not a claim")

    def test_too_few_claims(self, rendered):
        def m(root, key):
            key["cases"][0]["claims"] = key["cases"][0]["claims"][:1]
            t = root / "h001" / "target.html"
            text = t.read_text(encoding="utf-8")
            t.write_text("\n".join(l for l in text.split("\n") if "costs" not in l and "warranty" not in l),
                         encoding="utf-8")
        self._plant(rendered, m, "1 claims, needs 2 to 4")

    def test_too_many_claims(self, rendered):
        def m(root, key):
            extra = [{"id": f"h001_c{j}", "text": f"Extra fact {j}.", "verdict": "UNVERIFIABLE",
                      "scenario": "missing_coverage", "evidence_sources": []} for j in (4, 5)]
            key["cases"][0]["claims"] += extra
            t = root / "h001" / "target.html"
            t.write_text(t.read_text(encoding="utf-8").replace(
                "</body>", "    <p>Extra fact 4.</p>\n    <p>Extra fact 5.</p>\n</body>"), encoding="utf-8")
        self._plant(rendered, m, "5 claims, needs 2 to 4")

    def test_bad_verdict(self, rendered):
        def m(root, key):
            key["cases"][0]["claims"][0]["verdict"] = "PARTLY"
        self._plant(rendered, m, "bad verdict")

    def test_missing_evidence_file(self, rendered):
        def m(root, key):
            key["cases"][0]["claims"][0]["evidence_sources"] = ["source_3_press_release.html"]
        self._plant(rendered, m, "missing evidence file")

    def test_extra_file(self, rendered):
        def m(root, key):
            (root / "h001" / "notes.html").write_text("<p>x</p>", encoding="utf-8")
        self._plant(rendered, m, "extra file")

    def test_evidence_on_unverifiable(self, rendered):
        def m(root, key):
            key["cases"][0]["claims"][2]["evidence_sources"] = ["source_1_spec_sheet.html"]
        self._plant(rendered, m, "evidence on an UNVERIFIABLE claim")

    def test_verdict_word_in_name(self, rendered):
        def m(root, key):
            src = root / "h001" / "source_2_retailer.html"
            src.rename(root / "h001" / "source_2_contradicted_retailer.html")
            key["cases"][0]["claims"][1]["evidence_sources"] = ["source_2_contradicted_retailer.html"]
        self._plant(rendered, m, "verdict word in file name")

    def test_product_reused_across_pages(self, rendered):
        def m(root, key):
            key["cases"][1]["product"] = key["cases"][0]["product"]
        self._plant(rendered, m, "product reused")

    def test_product_reused_from_dev(self, rendered):
        def m(root, key):
            key["cases"][0]["product"] = "SoundWave Pro X2"
        self._plant(rendered, m, "product reused")

    def test_identical_normalized_paragraphs(self, rendered):
        def m(root, key):
            t = root / "h001" / "target.html"
            t.write_text(t.read_text(encoding="utf-8").replace(
                "</body>", "    <p>Fixture Gadget 1 weighs 101 grams</p>\n</body>"), encoding="utf-8")
        self._plant(rendered, m, "normalize to the same text")

    @pytest.mark.parametrize("bad_id", ["h000", "h062", "h099", "case_99"])
    def test_official_case_id_range(self, rendered, bad_id):
        def m(root, key):
            (root / "h001").rename(root / bad_id)
            key["cases"][0]["case_id"] = bad_id
            for cl in key["cases"][0]["claims"]:
                cl["id"] = cl["id"].replace("h001", bad_id)
        self._plant(rendered, m, "official case_id must be h001 to h061")

    def test_wrong_case_count(self, rendered):
        def m(root, key):
            key["cases"] = key["cases"][:60]
        self._plant(rendered, m, "needs 61")

    def test_format_character_in_claim(self, rendered):
        """RT1-11: a zero-width space in a claim and its paragraph is refused."""
        def m(root, key):
            t = root / "h001" / "target.html"
            t.write_text(t.read_text(encoding="utf-8").replace("weighs", "wei​ghs"), encoding="utf-8")
            key["cases"][0]["claims"][0]["text"] = key["cases"][0]["claims"][0]["text"].replace(
                "weighs", "wei​ghs")
        self._plant(rendered, m, "format character(s) U+200B in claim text")

    def test_nfkc_difference_is_not_refused(self, rendered):
        root, key_path = rendered
        t = root / "h001" / "target.html"
        t.write_text(t.read_text(encoding="utf-8").replace("weighs 101 grams", "weighs 10¹ grams"),
                     encoding="utf-8")
        key = json.loads(key_path.read_text(encoding="utf-8"))
        key["cases"][0]["claims"][0]["text"] = "Fixture Gadget 1 weighs 10¹ grams."
        key_path.write_text(json.dumps(key), encoding="utf-8")
        assert make_dataset.check(root, key_path, against=[DEV_KEY_PATH]) == []

    @pytest.mark.parametrize("name,is_dir,expect", [
        ("notes.html", False, "unknown file at the dataset root"),
        ("spec_draft", True, "case directory has no answer-key entry"),
        ("h062", True, "case directory has no answer-key entry"),
    ])
    def test_unknown_dataset_root_entry(self, rendered, name, is_dir, expect):
        """RT1-13: the agent's tools can read anything under the dataset root."""
        def m(root, key):
            if is_dir:
                (root / name).mkdir()
            else:
                (root / name).write_text("x", encoding="utf-8")
        self._plant(rendered, m, expect)

    def test_dev_and_official_share_the_dataset_root(self, tmp_path):
        """Both sets live under evals/dataset: each check accounts for the
        other set's folders through the default comparison with every other
        key in the key directory (the existing --against mechanism)."""
        root, keys = tmp_path / "dataset", tmp_path / "keys"
        shutil.copytree(DATASET_ROOT, root)
        keys.mkdir()
        shutil.copyfile(DEV_KEY_PATH, keys / "dev.json")
        spec_path = tmp_path / "spec.json"
        spec_path.write_text(json.dumps(_spec()), encoding="utf-8")
        assert make_dataset.render(spec_path, root, keys / "official.json") == []
        assert make_dataset.check(root, keys / "dev.json") == []
        assert make_dataset.check(root, keys / "official.json") == []
        # without the comparison, the other set's folders are unknown entries
        unknown = make_dataset.check(root, keys / "dev.json", against=[])
        assert len(unknown) == 61 and all("no answer-key entry" in p for p in unknown)
