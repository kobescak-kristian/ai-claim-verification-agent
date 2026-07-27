"""Regression test for the two bounds SPEC.md and ADR-001 promise are
enforced in the harness, not just in documentation:

(a) every path any tool call touched (per audit.db) stays inside
    evals/dataset/ — resolving fully for the pinned eval run, or passing a
    strict lexical containment check for orphan rows from ephemeral cases
    (see test docstring, 2026-07-24 finding) — plus an explicit
    escape-attempt case asserting rejection at both the path-resolver and
    the tool level.
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
from pathlib import Path, PurePosixPath, PureWindowsPath

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

# The one run whose dataset cases are committed with the repo: every path it
# touched must still resolve on disk, with no orphan tolerance.
PINNED_EVAL_RUN_ID = "eval-05fbe4ee"


def _lexical_containment_ok(path_value: str) -> bool:
    """Pure-lexical containment check for an audit-trail path value — no
    filesystem existence required. True only for a relative path with no
    ``..`` segment, no backslash, and no drive/absolute form, whose join
    onto DATASET_ROOT resolves inside DATASET_ROOT."""
    if not path_value or "\\" in path_value:
        return False
    windows_view = PureWindowsPath(path_value)
    if windows_view.is_absolute() or windows_view.drive:
        return False
    posix_view = PurePosixPath(path_value)
    if posix_view.is_absolute() or ".." in posix_view.parts:
        return False
    return (DATASET_ROOT / path_value).resolve().is_relative_to(DATASET_ROOT.resolve())


def _orphan_violation(path_value: str, case_id: str) -> str | None:
    """Decide whether a path that raised FileNotFoundError qualifies as a
    tolerated orphan (a row from an ephemeral case whose dataset dir is
    gone). Returns None if tolerated, else the violated condition:

    - "lexical-containment" — the raw value fails _lexical_containment_ok;
    - "case-id-mismatch"    — first path segment != the row's own case_id;
    - "case-dir-exists"     — the case dir is present but the file is not
                              (a real defect, not an orphan).
    """
    if not _lexical_containment_ok(path_value):
        return "lexical-containment"
    first_segment = PurePosixPath(path_value).parts[0]
    if first_segment != case_id:
        return "case-id-mismatch"
    if (DATASET_ROOT / first_segment).exists():
        return "case-dir-exists"
    return None


class TestPathsStayInsideDataset:
    def test_every_audit_path_resolves_inside_dataset(self):
        """(a) Historical check over the full audit trail: every path any
        tool call actually used stays inside evals/dataset/.

        2026-07-24 finding: local audit trails legitimately accumulate rows
        from ephemeral dev/smoke cases whose dataset dirs no longer exist.
        Orphan rows are containment-checked lexically but not required to
        resolve. Audit rows are never deleted. A row from the pinned eval
        run (PINNED_EVAL_RUN_ID) gets no orphan tolerance: its cases are
        committed, so every one of its paths must still resolve."""
        rows = _audit_rows()
        rows_by_id = {row["id"]: row for row in rows}
        checked = 0
        resolved_count = 0
        pinned_resolved = 0
        orphan_count = 0
        orphan_run_ids = set()
        for row_id, path_value in _iter_path_values(rows):
            checked += 1
            row = rows_by_id[row_id]
            try:
                resolved = resolve_dataset_path(path_value)
            except PathOutsideDatasetError:
                pytest.fail(f"row {row_id}: path {path_value!r} resolved OUTSIDE evals/dataset/")
            except FileNotFoundError:
                if row["run_id"] == PINNED_EVAL_RUN_ID:
                    pytest.fail(
                        f"row {row_id}: path {path_value!r} belongs to pinned eval run "
                        f"{PINNED_EVAL_RUN_ID} and must resolve, but does not exist "
                        f"under evals/dataset/"
                    )
                violation = _orphan_violation(path_value, row["case_id"])
                if violation == "case-dir-exists":
                    pytest.fail(f"row {row_id}: path {path_value!r} does not exist under evals/dataset/")
                if violation is not None:
                    pytest.fail(
                        f"row {row_id}: orphan path {path_value!r} rejected — "
                        f"violated condition: {violation}"
                    )
                orphan_count += 1
                orphan_run_ids.add(row["run_id"])
                continue
            resolved_count += 1
            if row["run_id"] == PINNED_EVAL_RUN_ID:
                pinned_resolved += 1
            assert resolved.is_relative_to(DATASET_ROOT)
        print(
            f"\naudit path values checked: {checked} "
            f"({resolved_count} resolved, of which {pinned_resolved} from pinned "
            f"eval run; {orphan_count} orphaned); "
            f"orphan run_ids: {sorted(orphan_run_ids)}"
        )
        assert checked > 0, "No path-bearing tool calls found in audit.db to verify against."
        assert pinned_resolved > 0, (
            f"no resolved path values from pinned eval run "
            f"{PINNED_EVAL_RUN_ID} — historical check is vacuous"
        )

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


class TestLexicalContainmentNegativeProof:
    """Permanent negative proof for the orphan-tolerance seam: the lexical
    containment helper and the case_id rule, exercised directly with
    synthetic values — no audit.db, no fixtures. Asserts pin the exact
    boolean return, so a helper that starts answering True for
    escape-shaped input turns these red."""

    def test_parent_traversal_rejected(self):
        assert _lexical_containment_ok("../SPEC.md") is False
        assert _lexical_containment_ok("../../etc/passwd") is False
        assert _lexical_containment_ok("case_x/../../SPEC.md") is False

    def test_absolute_path_rejected(self):
        assert _lexical_containment_ok("/etc/passwd") is False
        assert _lexical_containment_ok(str(REPO_ROOT / "SPEC.md")) is False

    def test_backslash_rejected(self):
        assert _lexical_containment_ok("..\\..\\SPEC.md") is False
        assert _lexical_containment_ok("case_x\\target.html") is False

    def test_drive_letter_form_rejected(self):
        assert _lexical_containment_ok("C:/evil/target.html") is False
        assert _lexical_containment_ok("C:evil.html") is False

    def test_plain_dataset_relative_path_accepted(self):
        assert _lexical_containment_ok("some_case_dir/target.html") is True

    def test_orphan_case_id_mismatch_detected(self):
        # Contained and nonexistent, but filed under a different case_id:
        # not a tolerable orphan.
        assert _orphan_violation("case_aaaa/target.html", "case_bbbb") == "case-id-mismatch"

    def test_orphan_matching_nonexistent_case_tolerated(self):
        assert _orphan_violation("nonexistent_case_dir/target.html", "nonexistent_case_dir") is None


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
