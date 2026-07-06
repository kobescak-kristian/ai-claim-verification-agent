"""SQLite audit trail. A PreToolUse/PostToolUse hook writes every tool call
here before its result is used by the model, per SPEC.md bounds."""
import json
import sqlite3
from datetime import datetime, timezone

from .config import AUDIT_DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    tool_use_id TEXT,
    tool_name TEXT,
    event_type TEXT,
    timestamp TEXT,
    payload TEXT
)
"""


def init_audit_db() -> None:
    conn = sqlite3.connect(AUDIT_DB_PATH)
    try:
        conn.execute(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _insert(session_id, tool_use_id, tool_name, event_type, payload: dict) -> None:
    conn = sqlite3.connect(AUDIT_DB_PATH)
    try:
        conn.execute(
            "INSERT INTO tool_calls (session_id, tool_use_id, tool_name, event_type, "
            "timestamp, payload) VALUES (?, ?, ?, ?, ?, ?)",
            (
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
