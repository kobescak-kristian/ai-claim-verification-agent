"""Bounded harness: wires the 4 MCP tools, allowed_tools whitelist, max_turns,
cost ceiling, and SQLite audit hooks into a single ClaudeAgentOptions, then
runs one query against a single eval case."""
from claude_agent_sdk import (
    ClaudeAgentOptions,
    HookMatcher,
    ResultMessage,
    create_sdk_mcp_server,
    query,
)

from . import tools
from .audit import audit_hook, init_audit_db
from .config import (
    DATASET_ROOT,
    MAX_BUDGET_USD,
    MAX_TURNS,
    MCP_SERVER_NAME,
    MODEL,
    QUALIFIED_TOOL_NAMES,
)
from .prompts import build_system_prompt


def build_options() -> ClaudeAgentOptions:
    server = create_sdk_mcp_server(
        name=MCP_SERVER_NAME, version="1.0.0", tools=tools.ALL_TOOLS
    )
    return ClaudeAgentOptions(
        model=MODEL,
        system_prompt=build_system_prompt(),
        tools=[],  # disable all built-in tools (no Write, Bash, Edit, Read, ...)
        mcp_servers={MCP_SERVER_NAME: server},
        allowed_tools=list(QUALIFIED_TOOL_NAMES),  # exactly the 4 custom tools
        max_turns=MAX_TURNS,
        max_budget_usd=MAX_BUDGET_USD,  # SDK-level per-run cost ceiling
        hooks={
            "PreToolUse": [HookMatcher(hooks=[audit_hook])],
            "PostToolUse": [HookMatcher(hooks=[audit_hook])],
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


def format_report(case_id: str, result: ResultMessage) -> str:
    findings = tools.findings
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


async def run_case(case_id: str) -> str:
    init_audit_db()
    tools.reset_run_state()
    options = build_options()
    prompt = build_user_prompt(case_id)

    final_result = None
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage):
            final_result = message

    if final_result is None:
        raise RuntimeError("No ResultMessage received from the query.")
    return format_report(case_id, final_result)
