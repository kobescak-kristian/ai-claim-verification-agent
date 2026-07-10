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

## Open loops
- Demo assets (recording/GIF): in-scope, not shipped until they
  exist (adr/0002); not a publication precondition.
