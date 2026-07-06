# SPEC — ai-claim-verification-agent

Classification: PROJECT. Named reader: recruiters/hiring managers from active
applications. Trigger: P4 agent slot (locked 2026-07-05). Timebox: ~1 week.

## Problem
Content pages (affiliate reviews, comparisons, product pages) make factual
claims — prices, specs, availability, dates. Checking them against source
pages is manual, slow, and skipped under deadline. An unbounded agent could
check them but can't be trusted near live content. This agent verifies claims
and can provably do nothing else.

## What it does
Input: one target page + a set of source pages (v1: local/seeded HTML).
The agent extracts factual claims from the target, investigates each against
the sources, and logs a verdict per claim: SUPPORTED / CONTRADICTED /
UNVERIFIABLE, with the evidence reference.
Output: verdict table + one summary line to stdout (one screen, no scrollback;
final line reports run cost + turns used) and a full SQLite audit trail.

## Bounds (enforced by harness, not by README promise)
- Claude Agent SDK (Python 3.10+); `allowed_tools` whitelist of exactly 4
  custom in-process MCP tools: fetch_page, extract_claims, compare_source,
  log_finding. No Write, no Bash, no Edit.
- max_turns cap ~20; per-run cost ceiling + circuit breaker in tool wrapper.
- SDK hooks log every tool call to SQLite before the result is used.
- Bounded-AI rule v2 (ADR-001): the model chooses the next read-only
  investigative action from the fixed whitelist within step/time/cost limits;
  it may never publish, edit, send, or make irreversible changes.

## Eval (committed before any agent code)
Synthetic seeded pages with known-truth claim sets (deliberate choice,
documented in README + eval doc). Gate: precision >= 0.95 AND recall >= 0.90,
hard PASS/FAIL in eval config. Repo goes public only when the gate is green.

## Models & cost
Haiku 4.5 dev iterations; Sonnet 4.6 eval + demo runs. Runs on Claude
subscription auth (Max plan limits) — the previously assumed Agent SDK
credit pool was paused by Anthropic before activation (2026-06-15); no plan
change without one week of dev-limit evidence.

## Stop conditions (done means all four)
Demo run works + README up + one eval result + one recording.

## Out of scope (README "production upgrades" only)
Live-web crawling at scale, multi-agent teams, subagents, dashboards,
scheduled runs, auto-fixing content.
