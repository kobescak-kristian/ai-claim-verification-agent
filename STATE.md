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
| Re-run, renamed dataset (Sonnet 4.6, 2026-10-13) | precision 1.00 / recall 1.00, 35/35, gate PASS | eval-199f77c6 | evals/rerun_2026-10-13.md; evals/results/eval-199f77c6.json |

Gate-before-result: eval_config.yaml first committed b041702
(2026-07-05); results bc9f41a (2026-07-06). Threshold predates result.

## Post-publication changes (reconciliation, 2026-09-19)

Frozen at 2026-07-10 (v1.0.1), this file was silent on four
substantive commits landed since — none docs-only:
- **c3633e6** (2026-07-24) — cross-OS path-escape hardening: the
  dataset resolver now rejects any backslash-bearing path before
  resolution (a `..\..\SPEC.md`-style string traverses on Windows but
  read as a literal filename on POSIX, failing a fresh-clone Linux
  bounds-suite run). Test-portability hardening, not a sandbox fix —
  the cage held on both OS before this change.
- **76145d5** (2026-07-24) — canonical pre-commit local-path guard
  added.
- **ad28f32** (2026-07-25) — keyless CI added: 6-leg matrix
  (ubuntu/macos/windows x Python 3.12/3.14) running the bounds suite;
  a demo job stays dormant without an API-key secret this repo does
  not set.
- **700e0bf** (2026-08-04, hook rollout) — validator now accepts
  `decisions/` as an alternate to `adr/`; pre-commit regex widened for
  `/c/`-style paths; pre-push freshness guard added.
- **a55c3b2** (2026-07-27) — bounds-suite rewrite: pinned-run
  non-vacuousness checking (`PINNED_EVAL_RUN_ID`) added alongside
  lexical-containment tolerance for ephemeral dev/smoke rows; the
  pinned eval run (`eval-05fbe4ee` then; `eval-199f77c6` since
  2026-10-13) gets zero orphan tolerance.
- **7ddf34e** (2026-09-15) — canonical AGENTS.md router adopted;
  validator extended with the AGENTS.md v2.7 heading check.

None of the above changes the publication facts or eval numbers
recorded above, which remain accurate.

## Six-name validator convergence — RESOLVED 2026-09-19

Root `SPEC.md` was retired as redundant rather than justified with a
new decision record: every substantive statement in it was already
carried by `README.md` (problem, solution, cage, models, eval gate,
out-of-scope), `adr/0001` (bounded-agent v2 rule) and `adr/0002`
(DEMO_SCRIPT exception and its demo-asset consequence). The deleted
file remains in git history; nothing at runtime read it. With no
root file left under any of the six trigger-gated names, the
canonical six-name BANNED_WITHOUT_TRIGGER list is propagated to this
repo's validator — closing the residual recorded here on
2026-09-19 without fabricating a citation.

## Eval dataset rename (2026-10-13)

8 of the 12 eval case directory names contained a verdict word (`supported`, `contradicted` or `unverifiable`), and the directory name reaches the model in the user prompt and in the `fetch_page` tool description. The directories are now `case_01` to `case_12`; `evals/ground_truth.json` changed by name substitution only, verdicts unchanged (35 claims, 8 CONTRADICTED). The eval numbers above were produced under the old names. Re-run on the renamed dataset, same scorer, thresholds, model and per-case ceiling (2026-10-13, run `eval-199f77c6`): precision 1.00 (8/8), recall 1.00 (8/8), 35/35, gate PASS; recorded in `evals/rerun_2026-10-13.md` alongside the official gate run. The bounds suite now pins this run.

## Open loops
- Demo assets (recording/GIF): in-scope, not shipped until they
  exist (adr/0002); not a publication precondition.
