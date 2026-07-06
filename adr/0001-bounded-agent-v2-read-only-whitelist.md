# ADR 0001: Bounded Agent v2 — Read-Only Whitelist, and Why This Repo Is Fully Public

## Status
Accepted (2026-07-06, on first agent commit `a420efb`)

## Date: 2026-07-05

## Context
Prior engines follow Bounded-AI v1: deterministic code executes, AI only
recommends. An agent breaks that frame — the model must choose its own next
investigative step or it is just a pipeline. The risk to bound is not "AI
decides" but "AI decides something irreversible."

## Decision
Bounded-AI rule v2: the model may choose the next read-only investigative
action from a fixed whitelist (fetch_page, extract_claims, compare_source,
log_finding) within hard step, time, and cost limits. It may never publish,
edit, send, or make any irreversible change. The bounds are enforced in the
harness (Agent SDK `allowed_tools`, `max_turns`, cost circuit breaker,
SQLite hook logging) — not promised in documentation.

Second decision recorded here: this repo is fully public, prompts included —
a deliberate one-repo exception to the public/private standard (public =
signal, private = advantage). Reason: for an agent, the prompts and bounds
ARE the engineering signal; hiding them would hide the work.

## Consequences
- A bad model decision costs at most one wasted read-only step, bounded in
  count and cost; it can never become a state change.
- Every step is auditable after the fact from SQLite, logged by hooks the
  model cannot bypass.
- Trade-off: the agent cannot fix what it finds — verdicts require a human
  acting on them. Accepted as the cost of trustworthiness.
- Trade-off: public prompts can be copied. Accepted — the signal value to a
  named reader outweighs the advantage cost for this one repo.
