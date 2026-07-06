"""The 4 in-process MCP tools available to the claim-verification agent.

Read-only by construction: every tool either reads a local HTML file under
evals/dataset/ or appends to an in-memory findings list. None can write,
edit, execute shell commands, or reach beyond evals/dataset/.
"""
from claude_agent_sdk import tool

from .config import MAX_TOOL_CALLS
from .pages import PathOutsideDatasetError, read_page

VALID_VERDICTS = {"SUPPORTED", "CONTRADICTED", "UNVERIFIABLE"}

# Populated by log_finding during a run; harness.py reads it after query()
# completes to build the verdict table. Reset by harness.py before each run.
findings: list[dict] = []

_tool_call_count = 0


def _circuit_breaker_tripped() -> dict | None:
    """Hard backstop on total tool calls for this run, independent of max_turns."""
    global _tool_call_count
    _tool_call_count += 1
    if _tool_call_count > MAX_TOOL_CALLS:
        return {
            "content": [{
                "type": "text",
                "text": (
                    f"Circuit breaker tripped: tool-call ceiling ({MAX_TOOL_CALLS}) "
                    "reached for this run. No further tool calls will be served. "
                    "Log findings for whatever claims you have already investigated "
                    "and stop."
                ),
            }],
            "is_error": True,
        }
    return None


def reset_run_state() -> None:
    """Call before each run: clears findings and the circuit-breaker counter."""
    global _tool_call_count
    findings.clear()
    _tool_call_count = 0


def _error(message: str) -> dict:
    return {"content": [{"type": "text", "text": message}], "is_error": True}


@tool(
    "fetch_page",
    "Fetch a local HTML page by path relative to evals/dataset/ (e.g. "
    "'case_01_supported_wireless_earbuds/target.html'). Returns the page "
    "title and its paragraphs. Rejects any path outside evals/dataset/.",
    {"path": str},
)
async def fetch_page(args):
    tripped = _circuit_breaker_tripped()
    if tripped:
        return tripped
    try:
        title, paragraphs = read_page(args["path"])
    except PathOutsideDatasetError as e:
        return _error(str(e))
    except FileNotFoundError as e:
        return _error(str(e))
    body = "\n".join(f"- {p}" for p in paragraphs)
    return {"content": [{"type": "text", "text": f"# {title}\n\n{body}"}]}


@tool(
    "extract_claims",
    "Extract candidate factual claims from the target page (one per "
    "paragraph) given its path relative to evals/dataset/. Returns a "
    "numbered list; you decide which are claims worth verifying.",
    {"path": str},
)
async def extract_claims(args):
    tripped = _circuit_breaker_tripped()
    if tripped:
        return tripped
    try:
        _title, paragraphs = read_page(args["path"])
    except PathOutsideDatasetError as e:
        return _error(str(e))
    except FileNotFoundError as e:
        return _error(str(e))
    if not paragraphs:
        return {"content": [{"type": "text", "text": "No paragraphs found on this page."}]}
    numbered = "\n".join(f"{i + 1}. {p}" for i, p in enumerate(paragraphs))
    return {"content": [{"type": "text", "text": f"Found {len(paragraphs)} candidate claim(s):\n{numbered}"}]}


@tool(
    "compare_source",
    "Fetch a source page (path relative to evals/dataset/) framed for "
    "comparison against a specific claim. Returns the source's full content "
    "so you can judge SUPPORTED / CONTRADICTED / UNVERIFIABLE yourself, per "
    "the comparison policy in your system prompt.",
    {"claim_text": str, "source_path": str},
)
async def compare_source(args):
    tripped = _circuit_breaker_tripped()
    if tripped:
        return tripped
    try:
        title, paragraphs = read_page(args["source_path"])
    except PathOutsideDatasetError as e:
        return _error(str(e))
    except FileNotFoundError as e:
        return _error(str(e))
    body = "\n".join(f"- {p}" for p in paragraphs)
    text = (
        f"Claim under review: \"{args['claim_text']}\"\n"
        f"Source: {args['source_path']} ({title})\n\n"
        f"Source content:\n{body}\n\n"
        "Decide whether this source SUPPORTS, CONTRADICTS, or does not "
        "address (UNVERIFIABLE) the claim, applying the comparison policy."
    )
    return {"content": [{"type": "text", "text": text}]}


@tool(
    "log_finding",
    "Record the final verdict for one claim. verdict must be exactly one "
    "of SUPPORTED, CONTRADICTED, UNVERIFIABLE. evidence_source is the "
    "source path that determined the verdict, or 'none' if UNVERIFIABLE. "
    "Call this once per claim after you have finished investigating it.",
    {"claim_text": str, "verdict": str, "evidence_source": str, "evidence_note": str},
)
async def log_finding(args):
    tripped = _circuit_breaker_tripped()
    if tripped:
        return tripped
    verdict = args["verdict"].strip().upper()
    if verdict not in VALID_VERDICTS:
        return _error(
            f"Invalid verdict '{args['verdict']}'. Must be one of: "
            f"{', '.join(sorted(VALID_VERDICTS))}."
        )
    findings.append({
        "claim_text": args["claim_text"],
        "verdict": verdict,
        "evidence_source": args["evidence_source"],
        "evidence_note": args["evidence_note"],
    })
    return {"content": [{"type": "text", "text": f"Logged: {verdict} — {args['claim_text']!r}"}]}


ALL_TOOLS = [fetch_page, extract_claims, compare_source, log_finding]
