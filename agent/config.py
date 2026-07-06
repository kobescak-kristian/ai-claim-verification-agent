from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATASET_ROOT = (REPO_ROOT / "evals" / "dataset").resolve()
EVAL_CONFIG_PATH = REPO_ROOT / "evals" / "eval_config.yaml"
AUDIT_DB_PATH = REPO_ROOT / "audit.db"

MODEL = "claude-haiku-4-5-20251001"  # dev iterations (locked models decision, SPEC.md)
EVAL_MODEL = "claude-sonnet-4-6"  # eval + demo runs (locked models decision, SPEC.md)
MAX_TURNS = 20
MAX_BUDGET_USD = 0.25  # per-case ceiling for Haiku dev runs
EVAL_MAX_BUDGET_USD = 1.50  # per-case ceiling for Sonnet eval runs (higher token cost/turn)
MAX_TOOL_CALLS = 60  # circuit breaker: hard backstop independent of max_turns

MCP_SERVER_NAME = "claimverify"
TOOL_NAMES = ["fetch_page", "extract_claims", "compare_source", "log_finding"]
QUALIFIED_TOOL_NAMES = [
    f"mcp__{MCP_SERVER_NAME}__{name}" for name in TOOL_NAMES
]
