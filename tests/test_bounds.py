"""Regression test for the two bounds SPEC.md and ADR-001 promise are
enforced in the harness, not just in documentation:

(a) every path any tool call touched (per audit.db) resolves inside
    evals/dataset/, plus an explicit escape-attempt case asserting rejection
    at both the path-resolver and the tool level.
(b) ground_truth.json content (the fixed eval target) never appears in any
    tool input or output the model saw — the agent must not be able to see
    the answer key it's being graded against.

Run against a real audit.db produced by run_case.py or run_eval.py:
    pytest tests/test_bounds.py -v
"""
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agent import tools as agent_tools  # noqa: E402
from agent.config import AUDIT_DB_PATH, DATASET_ROOT  # noqa: E402
from agent.pages import PathOutsideDatasetError, resolve_dataset_path  # noqa: E402

GROUND_TRUTH_PATH = REPO_ROOT / "evals" / "ground_truth.json"


def _audit_rows() -> list[sqlite3.Row]:
    if not AUDIT_DB_PATH.exists():
        pytest.skip(f"No audit.db at {AUDIT_DB_PATH} — run run_case.py or run_eval.py first.")
    conn = sqlite3.connect(AUDIT_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM tool_calls ORDER BY id").fetchall()
    finally:
        conn.close()
    if not rows:
        pytest.skip("audit.db has no tool_calls rows.")
    return rows


def _iter_path_values(rows: list[sqlite3.Row]):
    """Yield (row_id, path_value) for every path/source_path in tool_input
    across all logged tool calls."""
    for row in rows:
        payload = json.loads(row["payload"])
        tool_input = payload.get("tool_input") or {}
        for key in ("path", "source_path"):
            if key in tool_input:
                yield row["id"], tool_input[key]


ESCAPE_ATTEMPTS = [
    "../SPEC.md",
    "../../SPEC.md",
    "../../../etc/passwd",
    "..\\..\\SPEC.md",
    str(REPO_ROOT / "SPEC.md"),  # absolute path outside evals/dataset/
]


class TestPathsStayInsideDataset:
    def test_every_audit_path_resolves_inside_dataset(self):
        """(a) Historical check: every path any tool call actually used, per
        the audit trail of a real run, resolves inside evals/dataset/."""
        rows = _audit_rows()
        checked = 0
        for row_id, path_value in _iter_path_values(rows):
            checked += 1
            try:
                resolved = resolve_dataset_path(path_value)
            except PathOutsideDatasetError:
                pytest.fail(f"row {row_id}: path {path_value!r} resolved OUTSIDE evals/dataset/")
            except FileNotFoundError:
                pytest.fail(f"row {row_id}: path {path_value!r} does not exist under evals/dataset/")
            assert resolved.is_relative_to(DATASET_ROOT)
        assert checked > 0, "No path-bearing tool calls found in audit.db to verify against."

    @pytest.mark.parametrize("escape_path", ESCAPE_ATTEMPTS)
    def test_escape_attempt_rejected_by_resolver(self, escape_path):
        """(a) Explicit escape-attempt case: the path resolver itself must
        reject any path outside evals/dataset/, regardless of audit history."""
        with pytest.raises(PathOutsideDatasetError):
            resolve_dataset_path(escape_path)

    @pytest.mark.parametrize("escape_path", ESCAPE_ATTEMPTS)
    def test_escape_attempt_rejected_by_fetch_page_tool(self, escape_path):
        """(a) Same escape attempts, exercised through the actual fetch_page
        tool handler (not just the resolver), proving the bound holds at the
        boundary the model actually calls."""
        agent_tools.reset_run_state()
        result = asyncio.run(agent_tools.fetch_page.handler({"path": escape_path}))
        assert result.get("is_error") is True
        text = result["content"][0]["text"]
        assert "outside evals/dataset" in text or "rejected" in text.lower()

    @pytest.mark.parametrize("escape_path", ESCAPE_ATTEMPTS)
    def test_escape_attempt_rejected_by_compare_source_tool(self, escape_path):
        """(a) Same, through compare_source — the second path-accepting tool."""
        agent_tools.reset_run_state()
        result = asyncio.run(agent_tools.compare_source.handler({
            "claim_text": "irrelevant for this check",
            "source_path": escape_path,
        }))
        assert result.get("is_error") is True
        text = result["content"][0]["text"]
        assert "outside evals/dataset" in text or "rejected" in text.lower()


class TestGroundTruthNotLeaked:
    def _ground_truth_secrets(self) -> set[str]:
        """Tokens that could only appear in a tool payload if ground_truth.json
        itself was read. Deliberately NOT the free-text evidence_note strings:
        those are natural-language descriptions of an objective fact (e.g.
        "no source mentions warranty terms"), and a model reasoning correctly
        about the same evidence can converge on near-identical wording by
        coincidence — that's not a leak, it's two independent parties
        describing the same reality the same way. The claim `id` values are
        the reliable signal: they're constructed identifiers
        (f"{case_id}_{c['id']}") the model is never shown — extract_claims
        and compare_source only ever surface claim *text*, never these IDs —
        so their presence in a tool payload is unambiguous proof the file
        leaked, with no false-positive path via convergent reasoning."""
        with open(GROUND_TRUTH_PATH, encoding="utf-8") as f:
            gt = json.load(f)
        secrets = {"ground_truth.json", "ground_truth"}
        for case in gt["cases"]:
            for claim in case["claims"]:
                secrets.add(claim["id"])  # e.g. "case_09_..._c3" — never shown to the model
        return secrets

    def test_ground_truth_never_appears_in_audit_payloads(self):
        """(b) The fixed eval target — the file itself, and its
        model-never-sees-these claim IDs — must never appear in any
        tool_input/tool_response the model saw."""
        rows = _audit_rows()
        secrets = self._ground_truth_secrets()
        for row in rows:
            payload_text = row["payload"]
            for secret in secrets:
                assert secret not in payload_text, (
                    f"row {row['id']}: ground_truth.json content leaked into "
                    f"audit payload: {secret!r}"
                )
