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
ANSWER_KEYS_DIR = REPO_ROOT / "evals" / "answer_keys"


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
PINNED_EVAL_RUN_ID = "eval-199f77c6"  # renamed-dataset re-run, 2026-10-13 (was eval-05fbe4ee)


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
        secrets = {"ground_truth.json", "ground_truth", "answer_keys"}
        for case in gt["cases"]:
            for claim in case["claims"]:
                secrets.add(claim["id"])  # e.g. "case_09_c3" — never shown to the model
        # Every v2 answer key is covered the same way: its file name
        # and its claim IDs.
        for key_path in sorted(ANSWER_KEYS_DIR.glob("*.json")):
            secrets.add(key_path.name)
            with open(key_path, encoding="utf-8") as f:
                key = json.load(f)
            for case in key["cases"]:
                for claim in case["claims"]:
                    secrets.add(claim["id"])
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


class TestDatasetNamesCarryNoVerdict:
    def test_no_dataset_path_component_names_a_verdict(self):
        """No directory or file name under evals/dataset/ contains a verdict
        word. Case paths reach the model in the user prompt and in the
        fetch_page tool description, so a directory name
        that carries a verdict word hints at the expected verdict.
        Checks names only: page text such as "HDR10 supported." is product
        content, not a hint."""
        verdict_tokens = ("supported", "contradicted", "unverifiable")
        names = list(DATASET_ROOT.rglob("*"))
        assert names, "evals/dataset/ is empty"
        offenders = sorted(
            p.relative_to(DATASET_ROOT).as_posix()
            for p in names
            if any(t in p.name.lower() for t in verdict_tokens)
        )
        assert not offenders, f"dataset names carry a verdict word: {offenders}"


# --- Harness isolation (no model calls: query() is replaced by a fake) ---

from claude_agent_sdk import ResultMessage, SystemMessage  # noqa: E402

from agent import audit as agent_audit  # noqa: E402
from agent import harness  # noqa: E402
from agent.config import QUALIFIED_TOOL_NAMES  # noqa: E402
from evals import score_v2  # noqa: E402

FAKE_SECRET_VALUE = "sk-fake-value-never-printed"


def _init_message(api_key_source="none", tools=None):
    data = {
        "type": "system",
        "subtype": "init",
        "apiKeySource": api_key_source,
        "tools": list(QUALIFIED_TOOL_NAMES) if tools is None else tools,
        "mcp_servers": [{"name": "claimverify", "status": "connected"}],
        "model": "claude-haiku-4-5-20251001",
        "cwd": "/machine/local/path",
        "session_id": "s-1",
    }
    return SystemMessage(subtype="init", data=data)


def _result_message(subtype="success", is_error=False):
    return ResultMessage(
        subtype=subtype, duration_ms=1, duration_api_ms=1, is_error=is_error,
        num_turns=3, session_id="s-1", total_cost_usd=0.01, result="done",
    )


def _fake_query(messages, raise_after=None, closed=None, called=None):
    async def fake(prompt, options):
        if called is not None:
            called.append(options)
        try:
            for message in messages:
                yield message
            if raise_after is not None:
                raise raise_after
        finally:
            if closed is not None:
                closed.append(True)
    return fake


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """Clean guarded env and a throwaway audit database; the real audit.db
    is never touched."""
    for name in harness.GUARDED_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(agent_audit, "AUDIT_DB_PATH", tmp_path / "audit.db")
    return tmp_path


class TestHarnessIsolation:
    def test_options_load_no_settings_and_only_the_four_tools(self):
        options = harness.build_options()
        assert options.setting_sources == []
        assert options.tools == []
        assert sorted(options.allowed_tools) == sorted(QUALIFIED_TOOL_NAMES)
        assert len(options.allowed_tools) == 4

    def test_audit_hook_registered_on_all_three_tool_events(self):
        hooks = harness.build_options().hooks
        for event in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
            assert event in hooks
            assert any(agent_audit.audit_hook in m.hooks for m in hooks[event])

    def test_names_match_the_frozen_gate(self):
        guard = harness.AuthGuardError
        assert f"{guard.__module__}.{guard.__qualname__}" == score_v2.AUTH_GUARD_TYPE
        assert harness.SDK_CALL_ORIGIN == score_v2.SDK_CALL_ORIGIN

    @pytest.mark.parametrize("name", harness.GUARDED_ENV_NAMES)
    def test_env_guard_rejects_each_name_without_reading_the_value(self, name):
        with pytest.raises(harness.AuthGuardError) as info:
            harness.check_env({name: FAKE_SECRET_VALUE, "PATH": "x"})
        assert name in str(info.value)
        assert FAKE_SECRET_VALUE not in str(info.value)

    def test_env_guard_rejects_an_empty_value_too(self):
        with pytest.raises(harness.AuthGuardError):
            harness.check_env({"ANTHROPIC_API_KEY": ""})

    def test_env_guard_accepts_a_clean_env(self):
        harness.check_env({"PATH": "x", "HOME": "y"})

    def test_init_check_accepts_and_records_only_named_fields(self):
        recorded = harness.check_init(_init_message())
        assert set(recorded) == set(harness.INIT_FIELDS)
        assert recorded["apiKeySource"] == "none"
        assert "/machine/local/path" not in json.dumps(recorded)

    @pytest.mark.parametrize("source", ["ANTHROPIC_API_KEY", "apiKeyHelper", None, ""])
    def test_init_check_rejects_api_key_source(self, source):
        with pytest.raises(harness.AuthGuardError):
            harness.check_init(_init_message(api_key_source=source))

    def test_init_check_rejects_missing_api_key_source(self):
        message = _init_message()
        del message.data["apiKeySource"]
        with pytest.raises(harness.AuthGuardError):
            harness.check_init(message)

    @pytest.mark.parametrize("tools", [
        list(QUALIFIED_TOOL_NAMES) + ["Bash"],
        list(QUALIFIED_TOOL_NAMES)[:3],
        list(QUALIFIED_TOOL_NAMES)[:3] + ["Read"],
        [],
        None,
    ])
    def test_init_check_rejects_any_other_tool_set(self, tools):
        message = _init_message()
        message.data["tools"] = tools
        with pytest.raises(harness.AuthGuardError):
            harness.check_init(message)

    def test_init_check_rejects_a_first_message_that_is_not_init(self):
        with pytest.raises(harness.AuthGuardError):
            harness.check_init(_result_message())
        with pytest.raises(harness.AuthGuardError):
            harness.check_init(SystemMessage(subtype="status", data={}))


class TestHarnessExecution:
    def _run(self, case_id="case_01", run_id="test-run"):
        return asyncio.run(harness.run_case_execution(case_id, run_id))

    def test_clean_run_keeps_result_and_init(self, isolated, monkeypatch):
        result = _result_message()
        monkeypatch.setattr(harness, "query", _fake_query([_init_message(), result]))
        outcome = self._run()
        assert outcome.error is None and outcome.error_origin is None
        assert outcome.result is result
        assert outcome.init["apiKeySource"] == "none"
        assert harness.last_init == outcome.init

    def test_sdk_error_after_result_keeps_result_and_sets_origin(self, isolated, monkeypatch):
        result = _result_message()
        crash = Exception("Command failed with exit code 1 (exit code: 1)\n"
                          "Error output: Check stderr output for details")
        monkeypatch.setattr(harness, "query",
                            _fake_query([_init_message(), result], raise_after=crash))
        outcome = self._run()
        assert outcome.result is result
        assert outcome.error is crash
        assert outcome.error_origin == harness.SDK_CALL_ORIGIN

    def test_sdk_error_before_any_message_sets_origin(self, isolated, monkeypatch):
        crash = RuntimeError("")
        monkeypatch.setattr(harness, "query", _fake_query([], raise_after=crash))
        outcome = self._run()
        assert outcome.result is None and outcome.init is None
        assert outcome.error is crash
        assert outcome.error_origin == harness.SDK_CALL_ORIGIN
        assert harness.last_init is None

    def test_bad_init_aborts_closes_stream_and_has_no_sdk_origin(self, isolated, monkeypatch):
        closed = []
        monkeypatch.setattr(harness, "query", _fake_query(
            [_init_message(api_key_source="ANTHROPIC_API_KEY"), _result_message()],
            closed=closed,
        ))
        outcome = self._run()
        assert isinstance(outcome.error, harness.AuthGuardError)
        assert outcome.error_origin is None
        assert outcome.result is None  # nothing after the first message is used
        assert closed == [True]

    def test_wrong_tool_set_aborts(self, isolated, monkeypatch):
        tools = list(QUALIFIED_TOOL_NAMES) + ["Bash"]
        monkeypatch.setattr(harness, "query", _fake_query(
            [_init_message(tools=tools), _result_message()]))
        outcome = self._run()
        assert isinstance(outcome.error, harness.AuthGuardError)
        assert outcome.result is None

    def test_env_guard_runs_before_anything_is_spawned(self, isolated, monkeypatch):
        called = []
        monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_SECRET_VALUE)
        monkeypatch.setattr(harness, "query", _fake_query([_init_message()], called=called))
        outcome = self._run()
        assert isinstance(outcome.error, harness.AuthGuardError)
        assert FAKE_SECRET_VALUE not in str(outcome.error)
        assert outcome.error_origin is None
        assert called == []
        assert not (isolated / "audit.db").exists()

    def test_last_init_is_reset_for_each_execution(self, isolated, monkeypatch):
        monkeypatch.setattr(harness, "query", _fake_query([_init_message(), _result_message()]))
        self._run()
        assert harness.last_init is not None
        monkeypatch.setattr(harness, "query", _fake_query([], raise_after=RuntimeError("x")))
        self._run()
        assert harness.last_init is None

    def test_run_case_result_still_raises_on_error(self, isolated, monkeypatch):
        crash = RuntimeError("boom")
        monkeypatch.setattr(harness, "query", _fake_query([_init_message()], raise_after=crash))
        with pytest.raises(RuntimeError, match="boom"):
            asyncio.run(harness.run_case_result("case_01", "test-run"))

    def test_run_case_result_returns_result_on_success(self, isolated, monkeypatch):
        result = _result_message()
        monkeypatch.setattr(harness, "query", _fake_query([_init_message(), result]))
        got, findings = asyncio.run(harness.run_case_result("case_01", "test-run"))
        assert got is result and findings == []


class TestAuditFailureRows:
    def _rows(self, db):
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute("SELECT * FROM tool_calls ORDER BY id").fetchall()
        finally:
            conn.close()

    def test_failure_row_closes_the_pre_row_with_error_payload(self, isolated):
        agent_audit.init_audit_db()
        agent_audit.set_run_context("test-run", "case_01")
        tool = QUALIFIED_TOOL_NAMES[0]
        base = {"session_id": "s-1", "tool_name": tool, "tool_input": {"path": "x"},
                "tool_use_id": "tu-1"}
        asyncio.run(agent_audit.audit_hook(
            {**base, "hook_event_name": "PreToolUse"}, "tu-1", None))
        asyncio.run(agent_audit.audit_hook(
            {**base, "hook_event_name": "PostToolUseFailure",
             "error": "Circuit breaker tripped"}, "tu-1", None))
        rows = self._rows(isolated / "audit.db")
        assert [r["event_type"] for r in rows] == ["PreToolUse", score_v2.POST_FAILURE]
        assert {r["tool_use_id"] for r in rows} == {"tu-1"}
        assert {r["run_id"] for r in rows} == {"test-run"}
        assert json.loads(rows[1]["payload"])["error"] == "Circuit breaker tripped"

    def test_failure_row_takes_tool_use_id_from_input_when_argument_missing(self, isolated):
        agent_audit.init_audit_db()
        agent_audit.set_run_context("test-run", "case_01")
        asyncio.run(agent_audit.audit_hook(
            {"hook_event_name": "PostToolUseFailure", "session_id": "s-1",
             "tool_name": QUALIFIED_TOOL_NAMES[0], "tool_input": {},
             "tool_use_id": "tu-9", "error": "e"}, None, None))
        rows = self._rows(isolated / "audit.db")
        assert rows[0]["tool_use_id"] == "tu-9"
