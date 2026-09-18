# STATE — ai-claim-verification-agent

Bounded claim-verification agent: 4-tool whitelist, turn cap, cost
ceiling, SQLite audit trail; eval gate committed before agent code.
**Classification:** PROJECT · core artifact set, plus DEMO_SCRIPT as a
recorded exception (adr/0002).
**Status:** PUBLIC since 2026-07-10 (v1.0.1).
Publication gate PASS at this HEAD; post-publication verification:
HTTP 200 unauthenticated, fresh public clone matched, pin commit
reachable in public history.

## Cross-repo pin
A downstream private system pins this repo at d444b13c as a reused
verification node. Pin validity = that hash reachable in this repo's
public history after flip. All commits here are forward of the pin —
pin unaffected.

## Eval numbers (committed sources only)
| Quantity | Value | Run ID | Source |
|---|---|---|---|
| Official gate run (Sonnet 4.6) | precision 1.00 / recall 1.00 (synthetic eval set), 35/35, gate PASS | eval-05fbe4ee | evals/EVAL_RESULTS.md; evals/results/eval-05fbe4ee.json |
| Haiku shakedown (pre-official) | precision 1.00 / recall 1.00, 35/35, PASS | eval-33d91861 | evals/results/eval-33d91861.json; named in EVAL_RESULTS.md |

Gate-before-result: eval_config.yaml first committed b041702
(2026-07-05); results bc9f41a (2026-07-06). Threshold predates result.

## Post-publication changes (Q-72(f) reconciliation, 2026-09-19)

Frozen at 2026-07-10 (v1.0.1), this file was silent on four
substantive commits landed since — none docs-only:
- **c3633e6** (2026-07-24) — cross-OS path-escape hardening: the
  dataset resolver now rejects any backslash-bearing path before
  resolution (a `..\..\SPEC.md`-style string traverses on Windows but
  read as a literal filename on POSIX, failing a fresh-clone Linux
  bounds-suite run). Test-portability hardening, not a sandbox fix —
  the cage held on both OS before this change.
- **76145d5** (2026-07-24) — canonical pre-commit local-path guard
  added (Q-48 wave 1).
- **ad28f32** (2026-07-25) — keyless CI added: 6-leg matrix
  (ubuntu/macos/windows x Python 3.12/3.14) running the bounds suite;
  a demo job stays dormant without an API-key secret this repo does
  not set.
- **700e0bf** (2026-08-04, Q-35 hook rollout) — validator now accepts
  `decisions/` as an alternate to `adr/`; pre-commit regex widened for
  `/c/`-style paths; pre-push freshness guard added.
- **a55c3b2** (2026-07-27) — bounds-suite rewrite: pinned-run
  non-vacuousness checking (`PINNED_EVAL_RUN_ID`) added alongside
  lexical-containment tolerance for ephemeral dev/smoke rows; the
  pinned eval run above gets zero orphan tolerance.
- **7ddf34e** (2026-09-15, Q-93) — canonical AGENTS.md router adopted;
  validator extended with the AGENTS.md v2.7 heading check.

None of the above changes the publication facts or eval numbers
recorded above, which remain accurate.

## Six-name validator convergence (Q-72(f), 2026-09-19) — SKIPPED

This repo carries a live root `SPEC.md`. No existing decision record
in `adr/` cites it (checked directly: no match for the string
"SPEC.md" in any `adr/*.md` file). Per Q-72(f)'s own instruction, a
decision record is not fabricated merely to satisfy the validator —
propagating the six-name BANNED_WITHOUT_TRIGGER list to this repo's
validator is skipped. This is a named Q-72(f) residual, not a silent
gap. STATE.md-existence enforcement and the obsolete decision-cap
removal are independently authorized and proceed regardless (see
validator diff, same commit).

## Open loops
- Demo assets (recording/GIF): in-scope, not shipped until they
  exist (adr/0002); not a publication precondition.
- Six-name validator convergence, skipped above — resolvable only by
  an owner-authorized decision record for the live SPEC.md, or an
  owner ruling accepting the gap permanently.
