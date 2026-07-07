# ADR 0002: DEMO_SCRIPT.md Tier-1 Exception

## Status
Accepted (2026-07-07)

## Context
ARTIFACT_STANDARD (house standard) reserves DEMO_SCRIPT.md for the Tier-1
flagship engine. This repo is an agent, not an engine, and is the live-demo
candidate for interviews.

## Decision
This repo carries DEMO_SCRIPT.md as a deliberate Tier-1 exception. Trigger:
locked decision 2026-07-05 (recorded in the house portfolio state): agent
gets its own DEMO_SCRIPT plus the offline-rehearsal rule.

## Alternatives rejected
- Delete the file — erases a locked decision to satisfy tooling.
- Move demo to the flagship engine — the agent is the demo candidate.

## Consequences
Demo assets (recording, GIF) become in-scope for this repo; the repo does
not ship them until they exist.

## Reopening condition
Flagship demo policy changes, or the agent is dropped as interview demo
candidate.

## Code-verified
DEMO_SCRIPT.md exists at HEAD (`ls DEMO_SCRIPT.md`).
