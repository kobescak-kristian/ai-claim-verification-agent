"""Bounded harness: wires the 4 MCP tools, allowed_tools whitelist, max_turns,
cost ceiling, and SQLite audit hooks into a single ClaudeAgentOptions, then
runs one query against a single eval case.

Isolation: no filesystem settings are loaded (setting_sources=[]), an env
guard refuses to start when an API-key or provider-routing variable is set
(names only, values are never read), and the CLI's init message must show
apiKeySource "none" and exactly the 4 MCP tools before the run continues."""
import os
from dataclasses import dataclass, field

from claude_agent_sdk import (
    ClaudeAgentOptions,
    HookMatcher,
    ResultMessage,
    SystemMessage,
    create_sdk_mcp_server,
    query,
)

from . import tools
from .audit import audit_hook, init_audit_db, set_run_context
from .config import (
    DATASET_ROOT,
    MAX_BUDGET_USD,
    MAX_TURNS,
    MCP_SERVER_NAME,
    MODEL,
    QUALIFIED_TOOL_NAMES,
)
from .prompts import build_system_prompt

# Environment variables that would route the CLI away from the Claude
# subscription login (API key, token, other endpoint or cloud provider).
GUARDED_ENV_NAMES = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
)

# Recorded as error_origin when an exception is raised while iterating the
# SDK's query(), i.e. at the SDK call boundary (evals/eval_config_v2.yaml).
SDK_CALL_ORIGIN = "sdk_call"

# Init fields kept in last_init. Only these: the full init message also
# carries machine-local values (working directory, session id).
INIT_FIELDS = ("apiKeySource", "tools", "mcp_servers", "model")

# The init check of the most recent execution: the INIT_FIELDS of the CLI's
# init message when it passed the check; None when no init message arrived
# or the check failed (the AuthGuardError text then names the bad value).
last_init: dict | None = None


class AuthGuardError(Exception):
    """The harness refuses to run outside the isolated, subscription-only
    setup: a guarded env variable is set, the init message is missing, its
    apiKeySource is not "none", or its tool list is not exactly the 4 MCP
    tools. Infrastructure by design; the runner stops the series on it."""


def check_env(environ=None) -> None:
    """Raise AuthGuardError if any guarded variable is present. Checks names
    only; values are never read, printed or recorded."""
    env = os.environ if environ is None else environ
    present = [name for name in GUARDED_ENV_NAMES if name in env]
    if present:
        raise AuthGuardError(
            "AuthGuardError: refusing to start, guarded environment "
            f"variable(s) set: {', '.join(present)}"
        )


def check_init(message) -> dict:
    """Validate the CLI's first message. Returns the recorded INIT_FIELDS or
    raises AuthGuardError."""
    if not (isinstance(message, SystemMessage) and message.subtype == "init"):
        raise AuthGuardError(
            "AuthGuardError: the first message was not the init message: "
            f"{type(message).__name__}"
        )
    data = message.data or {}
    recorded = {k: data.get(k) for k in INIT_FIELDS}
    if recorded["apiKeySource"] != "none":
        raise AuthGuardError(
            "AuthGuardError: init apiKeySource is "
            f"{recorded['apiKeySource']!r}, expected 'none'"
        )
    tools_seen = recorded["tools"]
    if not isinstance(tools_seen, list) or sorted(tools_seen) != sorted(QUALIFIED_TOOL_NAMES):
        raise AuthGuardError(
            f"AuthGuardError: init tools are {tools_seen!r}, expected exactly "
            f"{sorted(QUALIFIED_TOOL_NAMES)!r}"
        )
    return recorded


@dataclass
class ExecutionOutcome:
    """Everything one execution leaves behind. `result` is kept even when an
    exception was raised. `error_origin` is SDK_CALL_ORIGIN only when the
    exception was raised while iterating the SDK's query(); None otherwise."""
    result: ResultMessage | None = None
    findings: list[dict] = field(default_factory=list)
    error: Exception | None = None
    error_origin: str | None = None
    init: dict | None = None


def build_options(
    model: str = MODEL, max_budget_usd: float = MAX_BUDGET_USD
) -> ClaudeAgentOptions:
    server = create_sdk_mcp_server(
        name=MCP_SERVER_NAME, version="1.0.0", tools=tools.ALL_TOOLS
    )
    return ClaudeAgentOptions(
        model=model,
        system_prompt=build_system_prompt(),
        tools=[],  # disable all built-in tools (no Write, Bash, Edit, Read, ...)
        mcp_servers={MCP_SERVER_NAME: server},
        allowed_tools=list(QUALIFIED_TOOL_NAMES),  # exactly the 4 custom tools
        setting_sources=[],  # load no user, project or local settings
        max_turns=MAX_TURNS,
        max_budget_usd=max_budget_usd,  # SDK-level per-run cost ceiling
        hooks={
            "PreToolUse": [HookMatcher(hooks=[audit_hook])],
            "PostToolUse": [HookMatcher(hooks=[audit_hook])],
            "PostToolUseFailure": [HookMatcher(hooks=[audit_hook])],
        },
    )


def case_paths(case_id: str) -> tuple[str, list[str]]:
    """Return (target_rel_path, [source_rel_paths]) for a case, relative to
    evals/dataset/, discovered from the directory listing (not ground truth)."""
    case_dir = DATASET_ROOT / case_id
    if not case_dir.is_dir():
        raise FileNotFoundError(f"No such case under evals/dataset/: {case_id}")
    target = case_dir / "target.html"
    if not target.exists():
        raise FileNotFoundError(f"Case {case_id} has no target.html")
    sources = sorted(
        p.name for p in case_dir.glob("source_*.html")
    )
    return f"{case_id}/target.html", [f"{case_id}/{s}" for s in sources]


def build_user_prompt(case_id: str) -> str:
    target, sources = case_paths(case_id)
    source_lines = "\n".join(f"- {s}" for s in sources)
    return (
        f"Target page: {target}\n"
        f"Source pages:\n{source_lines}\n\n"
        "Verify every factual claim on the target page against the source "
        "pages, then log a finding for each claim."
    )


def format_report(case_id: str, result: ResultMessage, findings: list[dict]) -> str:
    lines = [f"Claim Verification Report — {case_id}", "=" * 64]
    for i, f in enumerate(findings, start=1):
        claim = f["claim_text"]
        if len(claim) > 55:
            claim = claim[:52] + "..."
        lines.append(f"{i} | {f['verdict']:<12} | {claim:<55} | {f['evidence_source']}")

    counts = {"SUPPORTED": 0, "CONTRADICTED": 0, "UNVERIFIABLE": 0}
    for f in findings:
        counts[f["verdict"]] += 1

    lines.append("")
    lines.append(
        f"Summary: {len(findings)} claim(s) verified "
        f"({counts['SUPPORTED']} SUPPORTED, {counts['CONTRADICTED']} CONTRADICTED, "
        f"{counts['UNVERIFIABLE']} UNVERIFIABLE)."
    )
    cost = result.total_cost_usd if result.total_cost_usd is not None else 0.0
    lines.append(f"Run cost: ${cost:.4f} | Turns used: {result.num_turns}/{MAX_TURNS}")
    return "\n".join(lines)


async def run_case_execution(
    case_id: str, run_id: str, model: str = MODEL, max_budget_usd: float = MAX_BUDGET_USD
) -> ExecutionOutcome:
    """Fresh agent invocation scoped to one case: own ClaudeAgentOptions (own
    max_turns/max_budget_usd allowance, own circuit-breaker counter), own
    findings list. Never raises an Exception: the exception, the
    ResultMessage (if one arrived) and the exception's origin are returned
    together, so a caller can record all of them."""
    global last_init
    last_init = None
    outcome = ExecutionOutcome()
    try:
        check_env()
        init_audit_db()
        set_run_context(run_id, case_id)
        tools.reset_run_state()
        options = build_options(model=model, max_budget_usd=max_budget_usd)
        prompt = build_user_prompt(case_id)
    except Exception as exc:
        outcome.error = exc
        return outcome

    stream = query(prompt=prompt, options=options).__aiter__()
    first = True
    try:
        while True:
            try:
                message = await stream.__anext__()
            except StopAsyncIteration:
                break
            except Exception as exc:  # raised by the SDK while iterating
                outcome.error = exc
                outcome.error_origin = SDK_CALL_ORIGIN
                break
            try:
                if first:
                    first = False
                    outcome.init = last_init = check_init(message)
                if isinstance(message, ResultMessage):
                    outcome.result = message
            except Exception as exc:  # harness check, not the SDK
                outcome.error = exc
                break
    finally:
        aclose = getattr(stream, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Exception as exc:  # closing, not iterating: origin not established
                if outcome.error is None:
                    outcome.error = exc
    outcome.findings = list(tools.findings)
    return outcome


async def run_case_result(
    case_id: str, run_id: str, model: str = MODEL, max_budget_usd: float = MAX_BUDGET_USD
) -> tuple[ResultMessage, list[dict]]:
    """Single-case convenience over run_case_execution: returns
    (ResultMessage, findings) and raises on any error. Callers (run_case.py,
    run_eval.py) build their own report from this."""
    outcome = await run_case_execution(
        case_id, run_id, model=model, max_budget_usd=max_budget_usd
    )
    if outcome.error is not None:
        raise outcome.error
    if outcome.result is None:
        raise RuntimeError("No ResultMessage received from the query.")
    return outcome.result, outcome.findings


async def run_case(case_id: str) -> str:
    result, findings = await run_case_result(case_id, run_id=f"single-{case_id}")
    return format_report(case_id, result, findings)
