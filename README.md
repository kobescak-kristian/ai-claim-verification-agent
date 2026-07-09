# ai-claim-verification-agent

Bounded claim-verification agent — verifies factual claims on content pages against source pages; read-only by harness enforcement.

**Status:** v1.0 — eval gate green (Sonnet 4.6, 12 cases / 35 claims, precision 1.00, recall 1.00 — see Outcome).

## Problem

Affiliate reviews, comparisons, and product pages make factual claims — prices, specs, availability, release dates — that should match their sources but often go unchecked. Doing that check by hand is slow, and it's the first thing skipped when a deadline is close. An AI agent could do the checking instead, but an agent that can act freely near live content is a liability, not a fix: one bad decision and it can publish, edit, or send something it shouldn't.

## Solution

This agent takes one target page and a set of source pages, extracts the target's factual claims, investigates each one against the sources, and logs a verdict — SUPPORTED, CONTRADICTED, or UNVERIFIABLE — with the evidence that produced it. It can provably do nothing else: no writing, no editing, no shell, no network — only four read-only tools over local HTML.

![architecture](ai-claim-verification-agent_architecture.png)

## System

### Input

- One **target page** (the content being checked) and a set of **source pages** (what it's checked against). v1 scope: local, seeded HTML files under `evals/dataset/` — see [Known Limitations](evals/EVAL_RESULTS.md#known-limitations) for what that does and doesn't prove about live web content.
- A **comparison policy** — how to treat numeric mismatches, rephrasing, source authority, and no-coverage claims — loaded from [`evals/eval_config.yaml`](evals/eval_config.yaml) at *runtime*, not copied into the agent's prompt file. The policy lives in exactly one place.

### The Cage

Built on the Claude Agent SDK (Python). The harness — not a README promise — enforces every bound shown in the diagram above:

- `allowed_tools` is exactly the 4 custom in-process MCP tools below; `tools=[]` disables every built-in (no Write, Bash, Edit, Read).
- `max_turns` caps each run at 20 turns.
- A per-run cost ceiling (`max_budget_usd`) plus a tool-call-count circuit breaker in the tool wrapper itself — two independent backstops, one SDK-level, one in-process.
- An SDK `PreToolUse`/`PostToolUse` hook pair writes every tool call to a SQLite audit trail (`audit.db`) *before* its result is used by the model — see [`agent/audit.py`](agent/audit.py). [`tests/test_bounds.py`](tests/test_bounds.py) checks this mechanically against a real run's audit log: every tool-call path resolves inside `evals/dataset/` (with explicit escape-attempt cases asserted rejected), and the fixed eval answer key never appears in any tool input or output.
- Models: Haiku 4.5 for dev iterations, Sonnet 4.6 for eval and demo runs. Billed to Claude subscription (Max plan) auth, not a per-token API key — see [`SPEC.md`](SPEC.md) → Models & cost for why.

### Output

A verdict table (claim → verdict → evidence source), one summary line, and a final line reporting run cost and turns used — one screen, no scrollback. Every tool call behind that output is recorded in `audit.db`, independent of what the printed report shows.

### Value

Turns a manual, easy-to-skip check into a single bounded command whose failure mode is "wastes one read-only tool call," never "does something irreversible." The four-tool whitelist and the audit trail mean the bound is checkable after the fact, not just promised up front.

### Tools

The model gets exactly these four in-process MCP tools ([`agent/tools.py`](agent/tools.py)) — all read local HTML under `evals/dataset/` or append to an in-memory findings list; none can write, edit, execute, or reach outside that directory:

| Tool | What it does |
|---|---|
| `fetch_page` | Fetch one local HTML page by path, return its title and paragraphs. Rejects any path outside `evals/dataset/`. |
| `extract_claims` | Return the target page's paragraphs as a numbered list of candidate claims. Deterministic segmentation — the model decides which are worth verifying. |
| `compare_source` | Fetch a source page, framed against a specific claim, and return its full content. Does **not** judge the claim itself — see Key Logic. |
| `log_finding` | Record one claim's final verdict (SUPPORTED / CONTRADICTED / UNVERIFIABLE), the evidence source, and a short note. The only way the agent can "output" anything. |

### Key Logic (high-level)

See [`docs/architecture_detailed.png`](docs/architecture_detailed.png) for the full harness-bounds diagram (tool whitelist, path validator, audit hooks, verification loop).

1. `extract_claims` on the target page produces the candidate claims.
2. For each claim, the model calls `compare_source` (and `fetch_page` as needed) against the relevant source page(s).
3. **The model decides the verdict — no tool computes it.** `extract_claims` does no semantic judgment and `compare_source` does no matching or scoring; both are deterministic fetch-and-frame operations. The model reads the returned page content, applies the comparison policy from its system prompt (numeric claims need exact matches, rephrasing of the same fact is equivalent, authoritative sources outrank forum chatter, no source coverage means UNVERIFIABLE not CONTRADICTED), and reasons to a verdict itself.
4. The model calls `log_finding` once per claim with that verdict, its evidence source, and a note.
5. This is Bounded-AI v2 (ADR-001): the model chooses its next *read-only investigative* action from the fixed whitelist, within step/turn/cost limits — it never publishes, edits, sends, or makes an irreversible change. A bad decision costs at most one wasted tool call.

## Outcome

**Official gate run — Sonnet 4.6, 2026-07-06** ([full report](evals/EVAL_RESULTS.md)):

| Metric | Value | Threshold | Result |
|---|---|---|---|
| Precision (positive class: CONTRADICTED) | 1.0000 (8/8) | ≥ 0.95 | PASS |
| Recall (positive class: CONTRADICTED) | 1.0000 (8/8) | ≥ 0.90 | PASS |
| Overall verdict accuracy (secondary, not gated) | 1.0000 (35/35) | — | — |

12 cases / 35 claims, $0.9047 total run cost, no case hit `max_turns` or the budget ceiling.

All 12 eval cases are synthetic, hand-seeded HTML rather than live web pages, so this gate proves the harness and the model's reasoning against controlled, known-truth claims, not robustness to real-world markup noise — see [Known Limitations](evals/EVAL_RESULTS.md#known-limitations) for what that does and doesn't cover.

**Out of scope (production upgrades)** — per [`SPEC.md`](SPEC.md), deliberately not built and with no trigger yet: live-web crawling at scale, multi-agent teams, subagents, dashboards, scheduled runs, auto-fixing content.

## Run It Yourself

```bash
python -m venv .venv
.venv/Scripts/activate   # or: source .venv/bin/activate  (macOS/Linux)
pip install -r requirements.txt
```

No API key is needed for a Claude subscription (Max plan) login via the Claude CLI — the harness bills to that auth by default (see The Cage, above). To use a per-token API key instead, copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY`; only needed for live (non-bounds-test) runs.

**Bounds tests** — 17 total. 15 need no prior run and no API key; the other 2 (`test_every_audit_path_resolves_inside_dataset`, `test_ground_truth_never_appears_in_audit_payloads`) read a real `audit.db` and are skipped until one exists — run any case or the eval once, then re-run pytest for 17/17:

```bash
pytest tests/test_bounds.py -v
```

**Single-case demo** (calls the API — this one case cost $0.13 on a live run):

```bash
python run_case.py case_09_mixed_multi_claim_router
```

Expected output shape: a claim-by-claim verdict table (SUPPORTED / CONTRADICTED / UNVERIFIABLE + evidence source), a one-line summary, and a final line with run cost and turns used.

**Full eval** (all 12 cases / 35 claims — calls the API, ~$0.90 at current pricing per the committed reference run):

```bash
python evals/run_eval.py
```

The committed reference result is [`evals/results/eval-05fbe4ee.json`](evals/results/eval-05fbe4ee.json), summarized in [`evals/EVAL_RESULTS.md`](evals/EVAL_RESULTS.md) — compare a fresh run against it rather than trusting either run in isolation.

## Version Log

| Version | Date | Change |
|---|---|---|
| v1.0 | 2026-07-06 | Initial release: bounded harness + 4 MCP tools, eval-gated (Sonnet 4.6 — precision 1.00, recall 1.00 on 12 cases / 35 claims), audit-trail regression tests, architecture diagram. |
| v1.0.1 | 2026-07-09 | Repro instructions added pre-flip: Run It Yourself section (proven from a fresh clone), SPEC.md and detailed-diagram links. |
