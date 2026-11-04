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


# --- Standard eval v2 runner (evals/run_eval_v2.py): no model calls; a fake
# executor stands in for harness.run_case_execution and writes audit rows to
# a throwaway database. ---------------------------------------------------------

from evals import run_eval_v2  # noqa: E402

DEV_KEY = json.loads((ANSWER_KEYS_DIR / "dev.json").read_text(encoding="utf-8"))
DEV_CLAIMS = {c["case_id"]: c["claims"] for c in DEV_KEY["cases"]}
OVERLOADED = "Claude Code returned an error result: API Error: 529 Overloaded"
USAGE_TEXT = "You have hit your usage limit. Please try again later."


def _hook(event, tool, tool_use_id, tool_input, response=None):
    data = {"hook_event_name": event, "session_id": "s-1", "tool_name": tool,
            "tool_input": tool_input, "tool_use_id": tool_use_id}
    if event == "PostToolUse":
        data["tool_response"] = [{"type": "text", "text": response}]
    # audit_hook never awaits; drive it directly so it also works inside the
    # runner's event loop.
    coro = agent_audit.audit_hook(data, tool_use_id, None)
    try:
        coro.send(None)
    except StopIteration:
        return
    raise AssertionError("audit_hook awaited unexpectedly")


def _perfect_findings(case_id):
    return [{"claim_text": c["text"], "verdict": c["verdict"],
             "evidence_source": c["evidence_sources"][0] if c["evidence_sources"] else "none",
             "evidence_note": "n"} for c in DEV_CLAIMS[case_id]]


def _write_perfect_audit(case_id, run_id, findings):
    agent_audit.init_audit_db()
    agent_audit.set_run_context(run_id, case_id)
    fetch, log = QUALIFIED_TOOL_NAMES[0], QUALIFIED_TOOL_NAMES[3]
    path = {"path": f"{case_id}/target.html"}
    _hook("PreToolUse", fetch, f"{run_id}-t0", path)
    _hook("PostToolUse", fetch, f"{run_id}-t0", path, "- page text")
    for i, f in enumerate(findings, start=1):
        _hook("PreToolUse", log, f"{run_id}-t{i}", f)
        _hook("PostToolUse", log, f"{run_id}-t{i}", f, f"Logged: {f['verdict']}")


def _sdk_result(is_error=False, text="done"):
    return ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=is_error,
                         num_turns=3, session_id="s-1", total_cost_usd=0.01, result=text)


def _outcome(spec, case_id, run_id):
    init = {"apiKeySource": "none", "tools": list(QUALIFIED_TOOL_NAMES),
            "mcp_servers": [], "model": "claude-sonnet-4-6"}
    if spec == "auth":
        return harness.ExecutionOutcome(
            error=harness.AuthGuardError("AuthGuardError: init apiKeySource is 'x', expected 'none'"))
    if spec == "overloaded":
        return harness.ExecutionOutcome(error=Exception(OVERLOADED),
                                        error_origin=harness.SDK_CALL_ORIGIN, init=init)
    if spec == "usage":
        return harness.ExecutionOutcome(result=_sdk_result(is_error=True, text=USAGE_TEXT), init=init)
    findings = _perfect_findings(case_id)
    _write_perfect_audit(case_id, run_id, findings)
    if spec == "ok":
        return harness.ExecutionOutcome(result=_sdk_result(), findings=findings, init=init)
    if spec == "is_error_none":
        return harness.ExecutionOutcome(result=_sdk_result(is_error=None), findings=findings, init=init)
    if spec == "boom":  # runner/agent-side exception: unclassified, an agent failure
        return harness.ExecutionOutcome(result=_sdk_result(), findings=findings, init=init,
                                        error=RuntimeError("boom"))
    raise AssertionError(spec)


@pytest.fixture
def runner(isolated, monkeypatch):
    """A fake executor that plays a script of outcome specs ("ok" once the
    script runs out), a temporary results folder and a fixed git state."""
    monkeypatch.delenv(run_eval_v2.CLAUDE_CODE_ENV, raising=False)
    monkeypatch.setattr(run_eval_v2, "RESULTS_ROOT", isolated / "results")
    monkeypatch.setattr(run_eval_v2, "git_state", lambda: ("a" * 40, False))

    class Runner:
        def __init__(self):
            self.calls, self.script = [], []

        def run(self, *argv):
            return run_eval_v2.main(list(argv))

        def files(self, set_name="dev"):
            return sorted((isolated / "results" / set_name).glob("*.json"))

        def series(self, set_name="dev"):
            (path,) = self.files(set_name)
            return path, json.loads(path.read_text(encoding="utf-8"))

        def rewrite(self, path, **fields):
            data = json.loads(path.read_text(encoding="utf-8"))
            data.update(fields)
            path.write_text(json.dumps(data), encoding="utf-8")

    r = Runner()

    async def fake(case_id, run_id, model=None, max_budget_usd=None):
        r.calls.append(run_id)
        return _outcome(r.script.pop(0) if r.script else "ok", case_id, run_id)

    monkeypatch.setattr(run_eval_v2.harness, "run_case_execution", fake)
    return r


SMOKE = ("--set", "dev", "--cases", "case_01", "--runs", "1")


class TestRunnerV2Refusals:
    def test_refuses_inside_claude_code(self, runner, monkeypatch):
        monkeypatch.setenv(run_eval_v2.CLAUDE_CODE_ENV, "1")
        assert runner.run("--set", "dev") == run_eval_v2.EXIT_REFUSED
        assert runner.calls == [] and runner.files() == []

    def test_refuses_a_guarded_env_name_without_printing_its_value(self, runner, monkeypatch, capsys):
        monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_SECRET_VALUE)
        assert runner.run("--set", "dev") == run_eval_v2.EXIT_REFUSED
        out = capsys.readouterr()
        assert "ANTHROPIC_API_KEY" in out.err
        assert FAKE_SECRET_VALUE not in out.out + out.err
        assert runner.calls == []

    def test_official_needs_the_frozen_key_hash_and_the_full_series(self, runner, monkeypatch, tmp_path):
        assert runner.run("--set", "official") == run_eval_v2.EXIT_REFUSED
        assert runner.run("--set", "official", "--key-sha256", "0" * 64,
                          "--cases", "h001") == run_eval_v2.EXIT_REFUSED
        key = tmp_path / "official.json"
        key.write_text(json.dumps({"set": "official", "cases": []}), encoding="utf-8")
        monkeypatch.setattr(run_eval_v2, "key_path_for", lambda s, c: key)
        assert runner.run("--set", "official", "--key-sha256", "0" * 64) == run_eval_v2.EXIT_REFUSED
        # right hash, but not an official set (0 cases)
        assert runner.run("--set", "official", "--key-sha256",
                          score_v2.key_sha256(key)) == run_eval_v2.EXIT_REFUSED
        assert runner.calls == [] and runner.files("official") == []

    def test_dev_subset_must_name_cases_of_the_key(self, runner):
        assert runner.run("--set", "dev", "--cases", "h001") == run_eval_v2.EXIT_REFUSED
        assert runner.run("--set", "dev", "--runs", "4") == run_eval_v2.EXIT_REFUSED
        assert runner.run("--set", "dev", "--recover") == run_eval_v2.EXIT_REFUSED
        assert runner.calls == []

    def test_runner_never_loads_a_dotenv_file_or_the_v1_runner(self):
        source = Path(run_eval_v2.__file__).read_text(encoding="utf-8")
        assert "dotenv" not in source
        assert "run_eval import" not in source and "import run_eval\n" not in source


class TestRunnerV2Records:
    def test_full_dev_series_with_a_perfect_agent(self, runner, monkeypatch, capsys):
        assert runner.run("--set", "dev") == run_eval_v2.EXIT_OK
        assert len(runner.calls) == 36
        path, series = runner.series()
        assert series["state"] == "complete" and series["pending"] is None
        assert series["kind"] == run_eval_v2.FULL_KIND
        assert series["summary"]["result"] == score_v2.DEV_ONLY
        assert series["answer_key"] == "evals/answer_keys/dev.json"
        good, problems = score_v2.record_problems(DEV_KEY, series["executions"])
        assert problems == [] and len(good) == 36
        config = score_v2.load_config()
        assert all(score_v2.status_problem(e, config) is None for e in series["executions"])
        assert {e["status"] for e in series["executions"]} == {score_v2.COMPLETED}
        assert all(e["result"]["is_error"] is False for e in series["executions"])
        assert run_eval_v2.SMOKE_LABEL not in capsys.readouterr().out
        monkeypatch.setattr(sys, "argv", ["score_v2.py", str(path)])
        score_v2.main()  # the rescore accepts the file (no SystemExit)
        assert score_v2.DEV_ONLY in capsys.readouterr().out

    def test_smoke_test_record_shape_label_and_init_output(self, runner, capsys):
        assert runner.run(*SMOKE) == run_eval_v2.EXIT_OK
        out = capsys.readouterr().out
        assert run_eval_v2.SMOKE_LABEL in out
        assert "apiKeySource 'none'" in out
        assert f"observed MCP tools: {list(QUALIFIED_TOOL_NAMES)!r}" in out
        _, series = runner.series()
        assert series["kind"] == run_eval_v2.SMOKE_KIND
        assert series["summary"]["kind"] == run_eval_v2.SMOKE_KIND
        assert series["subset"] == {"cases": ["case_01"], "runs": 1}
        (e,) = series["executions"]
        assert e["execution_id"] == f"{series['eval_id']}-case_01-r1"
        assert runner.calls == [e["execution_id"]]
        assert set(e["result"]) == {"subtype", "is_error", "num_turns", "result_text", "errors"}
        assert e["result"]["is_error"] is False
        assert e["error_type"] is None and e["error"] is None
        assert e["num_turns"] == 3 and e["total_cost_usd"] == 0.01
        assert e["init"]["apiKeySource"] == "none"
        assert e["status"] == score_v2.COMPLETED

    def test_a_non_boolean_is_error_is_never_read_as_false(self, runner):
        runner.script = ["is_error_none"]
        assert runner.run("--set", "dev", "--cases", "case_01,case_02",
                          "--runs", "1") == run_eval_v2.EXIT_STOPPED
        assert len(runner.calls) == 1
        _, series = runner.series()
        (e,) = series["executions"]
        assert e["result"]["is_error"] is None  # kept as sent, not converted
        assert e["status"] == score_v2.AGENT_FAILURE
        assert any("not a boolean" in f for f in e["failures"])
        assert series["state"] == "stopped"
        assert score_v2.status_problem(e, score_v2.load_config()) is None

    def test_exception_keeps_result_and_records_module_qualname(self, runner):
        runner.script = ["boom"]
        assert runner.run(*SMOKE) == run_eval_v2.EXIT_OK
        (e,) = runner.series()[1]["executions"]
        assert e["error_type"] == "builtins.RuntimeError" and e["error"] == "boom"
        assert e["result"] is not None
        assert e["status"] == score_v2.AGENT_FAILURE and e["infra_category"] is None

    def test_only_the_executions_own_audit_rows_are_used(self, runner):
        agent_audit.init_audit_db()
        agent_audit.set_run_context("someone-else", "case_01")
        _hook("PreToolUse", QUALIFIED_TOOL_NAMES[0], "x-1", {"path": "case_01/target.html"})
        assert runner.run(*SMOKE) == run_eval_v2.EXIT_OK
        (e,) = runner.series()[1]["executions"]
        assert e["status"] == score_v2.COMPLETED and e["audit_rows"] == 8


class TestRunnerV2StopPauseRerun:
    def test_auth_error_stops_the_series_at_once(self, runner):
        runner.script = ["auth"]
        assert runner.run("--set", "dev") == run_eval_v2.EXIT_STOPPED
        assert len(runner.calls) == 1
        _, series = runner.series()
        assert series["state"] == "stopped"
        (e,) = series["executions"]
        assert e["status"] == score_v2.INVALID_RUN and e["infra_category"] == "auth"
        assert e["error_type"] == score_v2.AUTH_GUARD_TYPE

    def test_usage_limit_stops_the_series(self, runner):
        runner.script = ["ok", "usage"]
        assert runner.run("--set", "dev") == run_eval_v2.EXIT_STOPPED
        assert len(runner.calls) == 2
        e = runner.series()[1]["executions"][-1]
        assert e["infra_category"] == "usage_or_rate_limit"

    def test_invalid_run_stops_the_page_and_resume_reruns_it_as_a_new_attempt(self, runner):
        runner.script = ["ok", "overloaded"]
        assert runner.run("--set", "dev", "--cases", "case_01,case_02",
                          "--runs", "3") == run_eval_v2.EXIT_STOPPED
        path, series = runner.series()
        eid = series["eval_id"]
        assert runner.calls == [f"{eid}-case_01-r1", f"{eid}-case_01-r2"]
        assert series["executions"][-1]["infra_category"] == "overloaded_or_5xx"

        runner.calls.clear()
        assert runner.run("--set", "dev", "--resume", str(path)) == run_eval_v2.EXIT_OK
        assert runner.calls == [f"{eid}-case_01-r1-a2", f"{eid}-case_01-r2-a2", f"{eid}-case_01-r3-a2",
                                f"{eid}-case_02-r1", f"{eid}-case_02-r2", f"{eid}-case_02-r3"]
        _, series = runner.series()
        assert series["eval_id"] == eid
        first = [e for e in series["executions"] if e["attempt"] == 1 and e["case_id"] == "case_01"]
        assert len(first) == 2 and all(e["superseded"] for e in first)
        assert not any(e["superseded"] for e in series["executions"] if e["attempt"] == 2)
        assert [ev["kind"] for ev in series["series_events"]] == ["stop", "resume", "rerun", "complete"]
        scored = score_v2.score(run_eval_v2.subset_key(DEV_KEY, series), series["executions"])
        assert scored["attempt_problems"] == [] and scored["record_problems"] == []

    def test_two_unclassified_exceptions_in_a_row_pause_and_resume_continues_the_attempt(self, runner):
        runner.script = ["boom", "boom"]
        assert runner.run("--set", "dev", "--cases", "case_01",
                          "--runs", "3") == run_eval_v2.EXIT_PAUSED
        path, series = runner.series()
        assert series["state"] == "paused" and len(runner.calls) == 2
        runner.calls.clear()
        assert runner.run("--set", "dev", "--resume", str(path)) == run_eval_v2.EXIT_OK
        assert runner.calls == [f"{series['eval_id']}-case_01-r3"]
        _, series = runner.series()
        assert [e["status"] for e in series["executions"]] == [
            score_v2.AGENT_FAILURE, score_v2.AGENT_FAILURE, score_v2.COMPLETED]
        assert not any(e["superseded"] for e in series["executions"])

    def test_unclassified_exceptions_not_in_a_row_do_not_pause(self, runner):
        runner.script = ["boom", "ok", "boom"]
        assert runner.run("--set", "dev", "--cases", "case_01", "--runs", "3") == run_eval_v2.EXIT_OK

    def test_an_execution_id_with_audit_rows_is_never_reused(self, runner):
        runner.script = ["boom", "boom"]
        assert runner.run("--set", "dev", "--cases", "case_01", "--runs", "3") == run_eval_v2.EXIT_PAUSED
        path, series = runner.series()
        agent_audit.set_run_context(f"{series['eval_id']}-case_01-r3", "case_01")
        _hook("PreToolUse", QUALIFIED_TOOL_NAMES[0], "y-1", {"path": "case_01/target.html"})
        runner.calls.clear()
        assert runner.run("--set", "dev", "--resume", str(path)) == run_eval_v2.EXIT_STOPPED
        assert runner.calls == []
        assert runner.series()[1]["state"] == "abandoned"

    @pytest.mark.parametrize("field,value", [
        ("eval_id", "ev2-00000000"), ("git_head", "b" * 40), ("answer_key_sha256", "0" * 64),
        ("config_sha256", "0" * 64), ("model", "other"), ("max_budget_usd", 9.0),
        ("max_turns", 99), ("max_tool_calls", 999), ("runs_per_page", 5),
        ("subset", {"cases": ["case_02"], "runs": 1}), ("subset", {"cases": ["case_01"], "runs": 9}),
        ("kind", run_eval_v2.FULL_KIND),
        ("state", "complete"), ("state", "abandoned"),
    ])
    def test_resume_refuses_a_changed_series(self, runner, field, value):
        runner.script = ["auth"]
        assert runner.run(*SMOKE) == run_eval_v2.EXIT_STOPPED
        path, _ = runner.series()
        runner.rewrite(path, **{field: value})
        runner.calls.clear()
        assert runner.run("--set", "dev", "--resume", str(path)) == run_eval_v2.EXIT_REFUSED
        assert runner.calls == []

    def test_resume_refuses_cases_or_runs_that_differ_from_the_stored_subset(self, runner):
        runner.script = ["auth"]
        assert runner.run(*SMOKE) == run_eval_v2.EXIT_STOPPED
        path, series = runner.series()
        assert runner.run("--set", "dev", "--resume", str(path), "--runs", "2") == run_eval_v2.EXIT_REFUSED
        assert len(runner.calls) == 1
        assert runner.run("--set", "dev", "--resume", str(path), "--cases", "case_01",
                          "--runs", "1") == run_eval_v2.EXIT_OK
        assert runner.calls[1:] == [f"{series['eval_id']}-case_01-r1-a2"]


class TestRunnerV2InterruptedSeries:
    def _interrupted(self, runner, pending):
        runner.script = ["boom", "boom"]
        assert runner.run("--set", "dev", "--cases", "case_01", "--runs", "3") == run_eval_v2.EXIT_PAUSED
        path, series = runner.series()
        runner.rewrite(path, state="running", pending=pending)
        runner.calls.clear()
        return path, series

    def test_a_running_series_is_not_resumed_without_recover(self, runner):
        path, before = self._interrupted(runner, pending=None)
        assert runner.run("--set", "dev", "--resume", str(path)) == run_eval_v2.EXIT_REFUSED
        assert runner.calls == []
        _, after = runner.series()
        assert after["executions"] == before["executions"] and after["state"] == "running"

    def test_recover_with_an_execution_in_flight_abandons_and_keeps_the_evidence(self, runner):
        path, before = self._interrupted(runner, pending=None)
        lost = f"{before['eval_id']}-case_01-r3"
        runner.rewrite(path, pending={"execution_id": lost, "started_at": "t"})
        agent_audit.set_run_context(lost, "case_01")
        _hook("PreToolUse", QUALIFIED_TOOL_NAMES[0], "z-1", {"path": "case_01/target.html"})
        assert runner.run("--set", "dev", "--resume", str(path), "--recover") == run_eval_v2.EXIT_STOPPED
        assert runner.calls == []
        _, after = runner.series()
        assert after["state"] == "abandoned"
        assert after["executions"] == before["executions"]
        assert after["series_events"][-1]["kind"] == "interrupted"
        assert lost in after["series_events"][-1]["detail"]
        assert len(score_v2.load_audit_rows(agent_audit.AUDIT_DB_PATH, lost)) == 1
        assert runner.run("--set", "dev", "--resume", str(path)) == run_eval_v2.EXIT_REFUSED

    def test_recover_with_nothing_in_flight_continues(self, runner):
        path, series = self._interrupted(runner, pending=None)
        assert runner.run("--set", "dev", "--resume", str(path), "--recover") == run_eval_v2.EXIT_OK
        assert runner.calls == [f"{series['eval_id']}-case_01-r3"]
        kinds = [ev["kind"] for ev in runner.series()[1]["series_events"]]
        assert kinds[-3:] == ["recover", "resume", "complete"]

    def test_recover_is_refused_for_a_series_that_is_not_running(self, runner):
        runner.script = ["auth"]
        assert runner.run(*SMOKE) == run_eval_v2.EXIT_STOPPED
        path, _ = runner.series()
        assert runner.run("--set", "dev", "--resume", str(path), "--recover") == run_eval_v2.EXIT_REFUSED


# --- Messages before init: the live smoke test (2026-11-04) saw a
# HookEventMessage before the init message. The CLI always streams
# SessionStart/Setup hook lifecycle messages, also from hooks in machine-wide
# policy settings. Only those may come before init, and only with no hook
# output (SessionStart output reaches the model). ------------------------------

from claude_agent_sdk import (  # noqa: E402
    AssistantMessage, HookEventMessage, TextBlock, ToolUseBlock, UserMessage,
)

KOS_LIKE_OUTPUT = json.dumps({"hookSpecificOutput": {
    "hookEventName": "SessionStart", "additionalContext": "context text from a hook"}})


def _hook_message(subtype, event="SessionStart", hook_id="h-1", output="", stdout=""):
    data = {"type": "system", "subtype": subtype, "hook_id": hook_id,
            "hook_name": "SessionStart:startup", "hook_event": event}
    if subtype == "hook_response":
        data.update({"output": output, "stdout": stdout, "stderr": "",
                     "exit_code": 0, "outcome": "success"})
    if subtype == "hook_progress":  # the SDK parses this one as a plain SystemMessage
        data.update({"stdout": stdout, "stderr": "", "output": output})
        return SystemMessage(subtype=subtype, data=data)
    return HookEventMessage(subtype=subtype, data=data, hook_event_name=event)


OBSERVED_ORDER = [_hook_message("hook_started"), _hook_message("hook_response")]


class TestPreInitMessages:
    def _run(self, monkeypatch, messages, closed=None):
        monkeypatch.setattr(harness, "query", _fake_query(messages, closed=closed))
        return asyncio.run(harness.run_case_execution("case_01", "test-run"))

    def test_observed_order_with_a_silent_session_start_hook_passes(self, isolated, monkeypatch):
        result = _result_message()
        outcome = self._run(monkeypatch, OBSERVED_ORDER + [_init_message(), result])
        assert outcome.error is None and outcome.result is result
        assert outcome.init["apiKeySource"] == "none"
        assert [e["subtype"] for e in outcome.lifecycle] == ["hook_started", "hook_response"]
        assert all(e["hook_event"] == "SessionStart" for e in outcome.lifecycle)
        assert "SessionStart:startup" not in json.dumps(outcome.lifecycle)  # no hook name kept

    def test_progress_message_without_output_is_allowed(self, isolated, monkeypatch):
        outcome = self._run(monkeypatch, [_hook_message("hook_started"), _hook_message("hook_progress"),
                                          _hook_message("hook_response"), _init_message(),
                                          _result_message()])
        assert outcome.error is None and len(outcome.lifecycle) == 3

    @pytest.mark.parametrize("field", ["output", "stdout"])
    def test_hook_output_before_init_is_refused_without_recording_it(self, isolated, monkeypatch, field):
        closed = []
        outcome = self._run(monkeypatch, [_hook_message("hook_started"),
                                          _hook_message("hook_response", **{field: KOS_LIKE_OUTPUT}),
                                          _init_message(), _result_message()], closed=closed)
        assert isinstance(outcome.error, harness.AuthGuardError)
        assert "printed" in str(outcome.error) and "context text" not in str(outcome.error)
        assert outcome.result is None and outcome.init is None and harness.last_init is None
        assert closed == [True]

    def test_progress_output_before_init_is_refused(self, isolated, monkeypatch):
        outcome = self._run(monkeypatch, [_hook_message("hook_started"),
                                          _hook_message("hook_progress", stdout="partial"),
                                          _hook_message("hook_response"), _init_message()])
        assert isinstance(outcome.error, harness.AuthGuardError)

    def test_hook_output_after_init_is_refused_too(self, isolated, monkeypatch):
        outcome = self._run(monkeypatch, [_init_message(),
                                          _hook_message("hook_response", output=KOS_LIKE_OUTPUT),
                                          _result_message()])
        assert isinstance(outcome.error, harness.AuthGuardError)
        assert outcome.result is None

    @pytest.mark.parametrize("event", ["PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop", "", None])
    def test_hook_messages_of_other_events_are_refused(self, isolated, monkeypatch, event):
        outcome = self._run(monkeypatch, [_hook_message("hook_started", event=event),
                                          _init_message(), _result_message()])
        assert isinstance(outcome.error, harness.AuthGuardError)
        assert outcome.init is None and outcome.result is None

    @pytest.mark.parametrize("message", [
        AssistantMessage(content=[TextBlock(text="hi")], model="m"),
        UserMessage(content="hi"),
        SystemMessage(subtype="status", data={}),
        SystemMessage(subtype="hook_started_x", data={"hook_event": "SessionStart"}),
    ], ids=["assistant", "user", "other-system", "look-alike-subtype"])
    def test_any_other_message_before_init_is_refused(self, isolated, monkeypatch, message):
        outcome = self._run(monkeypatch, OBSERVED_ORDER + [message, _init_message(), _result_message()])
        assert isinstance(outcome.error, harness.AuthGuardError)
        assert outcome.init is None and outcome.result is None

    def test_a_result_before_init_is_refused(self, isolated, monkeypatch):
        outcome = self._run(monkeypatch, OBSERVED_ORDER + [_result_message(), _init_message()])
        assert isinstance(outcome.error, harness.AuthGuardError)
        assert outcome.result is None

    def test_a_hook_started_without_response_before_init_is_refused(self, isolated, monkeypatch):
        outcome = self._run(monkeypatch, [_hook_message("hook_started"), _init_message(),
                                          _result_message()])
        assert isinstance(outcome.error, harness.AuthGuardError)
        assert "without a response" in str(outcome.error)

    def test_too_many_lifecycle_messages_before_init_are_refused(self, isolated, monkeypatch):
        many = [_hook_message("hook_started", hook_id=f"h-{i}")
                for i in range(harness.MAX_PRE_INIT_MESSAGES + 1)]
        outcome = self._run(monkeypatch, many + [_init_message()])
        assert isinstance(outcome.error, harness.AuthGuardError)

    @pytest.mark.parametrize("init", [
        _init_message(api_key_source="ANTHROPIC_API_KEY"),
        _init_message(tools=list(QUALIFIED_TOOL_NAMES) + ["Bash"]),
        _init_message(tools=list(QUALIFIED_TOOL_NAMES)[:3]),
    ], ids=["api-key-source", "extra-tool", "missing-tool"])
    def test_init_checks_stay_strict_after_lifecycle_messages(self, isolated, monkeypatch, init):
        outcome = self._run(monkeypatch, OBSERVED_ORDER + [init, _result_message()])
        assert isinstance(outcome.error, harness.AuthGuardError)
        assert outcome.result is None

    def test_a_tool_use_before_init_is_refused_and_leaves_no_audit_rows(self, isolated, monkeypatch):
        tool_use = AssistantMessage(content=[ToolUseBlock(
            id="tu-early", name=QUALIFIED_TOOL_NAMES[0], input={"path": "case_01/target.html"})],
            model="m")
        outcome = self._run(monkeypatch, OBSERVED_ORDER + [tool_use, _init_message(),
                                                           _result_message()])
        assert isinstance(outcome.error, harness.AuthGuardError)
        assert "AssistantMessage" in str(outcome.error)
        assert outcome.init is None and outcome.result is None and outcome.findings == []
        db = isolated / "audit.db"
        conn = sqlite3.connect(db)
        try:
            assert conn.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0] == 0
        finally:
            conn.close()

    def test_a_refused_hook_classifies_as_auth_and_stops_the_runner(self):
        error = harness.AuthGuardError("AuthGuardError: a SessionStart hook printed 9 characters "
                                       "(hook_response); hook output can reach the model, refusing")
        category = score_v2.infra_category(score_v2.AUTH_GUARD_TYPE, str(error), None,
                                           score_v2.load_config())
        assert category == "auth" and category in run_eval_v2.STOP_CATEGORIES
