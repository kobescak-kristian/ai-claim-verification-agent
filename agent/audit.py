"""SQLite audit trail. A PreToolUse/PostToolUse hook writes every tool call
here before its result is used by the model, per SPEC.md bounds.

Shared across an eval run: each row is keyed by run_id (one per run_eval.py
invocation, or one per single-case run_case.py invocation) and case_id (the
eval case that invocation was scoped to), so a multi-case eval run can be
queried per case from the one audit.db."""
import json
import sqlite3
from datetime import datetime, timezone

from .config import AUDIT_DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    case_id TEXT,
    session_id TEXT,
    tool_use_id TEXT,
    tool_name TEXT,
    event_type TEXT,
    timestamp TEXT,
    payload TEXT
)
"""

# Set via set_run_context() before each fresh agent invocation. The hook
# callback signature is fixed by the SDK (input_data, tool_use_id, context),
# so it can't take run_id/case_id as arguments directly — module-level state
# is safe here because invocations run sequentially, never concurrently.
_current_run_id: str | None = None
_current_case_id: str | None = None


def set_run_context(run_id: str, case_id: str) -> None:
    global _current_run_id, _current_case_id
    _current_run_id = run_id
    _current_case_id = case_id


def init_audit_db() -> None:
    conn = sqlite3.connect(AUDIT_DB_PATH)
    try:
        conn.execute(_SCHEMA)
        existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(tool_calls)")}
        for col in ("run_id", "case_id"):
            if col not in existing_cols:
                conn.execute(f"ALTER TABLE tool_calls ADD COLUMN {col} TEXT")
        conn.commit()
    finally:
        conn.close()


def _insert(session_id, tool_use_id, tool_name, event_type, payload: dict) -> None:
    conn = sqlite3.connect(AUDIT_DB_PATH)
    try:
        conn.execute(
            "INSERT INTO tool_calls (run_id, case_id, session_id, tool_use_id, "
            "tool_name, event_type, timestamp, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _current_run_id,
                _current_case_id,
                session_id,
                tool_use_id,
                tool_name,
                event_type,
                datetime.now(timezone.utc).isoformat(),
                json.dumps(payload, default=str),
            ),
        )
        conn.commit()
    finally:
        conn.close()


async def audit_hook(input_data, tool_use_id, context):
    """Registered on both PreToolUse and PostToolUse. Writes to SQLite
    before the result is used by the model — that write happens here,
    synchronously, before this hook returns control to the SDK."""
    event = input_data.get("hook_event_name")
    if event == "PreToolUse":
        _insert(
            input_data.get("session_id"),
            tool_use_id,
            input_data.get("tool_name"),
            "PreToolUse",
            {"tool_input": input_data.get("tool_input")},
        )
    elif event == "PostToolUse":
        _insert(
            input_data.get("session_id"),
            tool_use_id,
            input_data.get("tool_name"),
            "PostToolUse",
            {
                "tool_input": input_data.get("tool_input"),
                "tool_response": input_data.get("tool_response"),
            },
        )
    return {}
