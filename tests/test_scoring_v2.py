"""Fixture tests for the Standard eval v2 scorer and dataset tool.

No model calls and no Agent SDK: every execution record here is built in
the test code. The synthetic 61-page fixture is generated from a fixed seed
(synthetic claim records only, no HTML, no link to the hold-out).
"""
import copy
import json
import random
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
    load_audit_rows,
    load_config,
    load_key,
    normalize_claim,
    score,
)

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


def perfect_executions(key: dict, runs: int = 3) -> list[dict]:
    return [
        {
            "execution_id": f"fx-{case['case_id']}-r{r}",
            "case_id": case["case_id"],
            "run_index": r,
            "status": COMPLETED,
            "failures": [],
            "num_turns": 8,
            "total_cost_usd": 0.1,
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
        execs[0]["status"] = INVALID_RUN
        assert score(official_key, execs, CONFIG)["result"] == INVALID_LABEL

    def test_superseded_invalid_run_after_page_rerun(self, official_key):
        execs = perfect_executions(official_key)
        old = copy.deepcopy(execs[:3])
        for e in old:
            e["superseded"] = True
        old[1]["status"] = INVALID_RUN
        assert score(official_key, old + execs, CONFIG)["result"] == "PASS"

    def test_superseded_without_invalid_run_is_refused(self, official_key):
        """2A allows only a whole-page rerun after an INVALID RUN; a bad run
        marked superseded and replaced on its own must not reach PASS."""
        execs = perfect_executions(official_key)
        case = official_key["cases"][0]
        bad = copy.deepcopy(by_id(execs)[f"fx-{case['case_id']}-r2"])
        bad["execution_id"] += "-old"
        bad["status"] = AGENT_FAILURE
        bad["superseded"] = True
        scored = score(official_key, [bad] + execs, CONFIG)
        assert scored["result"] == INVALID_LABEL
        assert scored["improper_supersede"] == [bad["execution_id"]]
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

class TestClassification:
    def _classify(self, error=None, result=None, **kw):
        return classify_execution(error, result, kw.get("findings", []), kw.get("rows", []),
                                  "case_01", 20, 60, CONFIG)

    @pytest.mark.parametrize("text,category", [
        ("Not logged in · Please run /login", "auth"),
        ("Invalid API key · Fix external API key", "auth"),
        ("AuthGuardError: apiKeySource is 'ANTHROPIC_API_KEY', expected 'none'", "auth"),
        ("API Error: 429 {\"type\":\"rate_limit_error\"}", "usage_or_rate_limit"),
        ("Claude AI usage limit reached", "usage_or_rate_limit"),
        ("API Error: 529 overloaded_error", "overloaded_or_5xx"),
        ("CLINotFoundError: Claude Code not found", "cli_spawn"),
    ])
    def test_infra_is_invalid_run(self, text, category):
        out = self._classify(error=text)
        assert out["status"] == INVALID_RUN and out["infra_category"] == category

    @pytest.mark.parametrize("text", [
        "Command failed with exit code 1: max budget exceeded",
        "Reached max_turns limit",
        "KeyError: 'claim_text'",
    ])
    def test_other_exceptions_are_agent_failures(self, text):
        out = self._classify(error=text)
        assert out["status"] == AGENT_FAILURE
        assert text in out["failures"][0]

    def test_error_result_with_infra_text_is_invalid(self):
        result = {"subtype": "error_during_execution", "is_error": True, "num_turns": 2,
                  "result_text": "API Error: 529 overloaded"}
        assert self._classify(result=result)["status"] == INVALID_RUN

    def test_max_turns_result_is_agent_failure(self):
        result = {"subtype": "error_max_turns", "is_error": True, "num_turns": 20, "result_text": ""}
        assert self._classify(result=result)["status"] == AGENT_FAILURE


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
        return classify_execution(None, result or self.RESULT, self.FINDINGS if findings is None else findings,
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
        assert any("without PostToolUse" in f for f in out["failures"])

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
