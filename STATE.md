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
| Standard eval v2 official series (Sonnet 4.6, 2026-11-09; local branch) | 61/61 pages, 0/504 unnecessary UNVERIFIABLE, 504/504 useful verification, 183/183 executions (61 sealed synthetic pages x 3), gate PASS | ev2-b67b4f30 | evals/EVAL_RESULTS.md; evals/results/v2/official/ev2-b67b4f30.json |

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

## Plain-language README and eval-note correction (2026-10-29)

README gains an "In plain words" paragraph under the title (v1.0.4). `evals/EVAL_RESULTS.md`: the Known Limitations note that called the path-scoping and answer-key checks manual now says `tests/test_bounds.py` has run them as regression tests since 2026-07-06 (dated correction mark); the two references to the retired `SPEC.md` now point to the README and `evals/ground_truth.json`. No eval number, threshold or test changed.

## Standard eval v2 (official PASS 2026-11-09, local branch)

A second evaluation was prepared on a local branch: 61 sealed synthetic pages, 3 runs each, gate `evals/eval_config_v2.yaml`. On 2026-11-05 the official series had not run. Development evidence, DEV ONLY (`evals/EVAL_RESULTS.md`): run `ev2-2046a87c`, 36/36 executions completed, 12/12 pages, 96/96 useful verification, 0/96 unnecessary UNVERIFIABLE. The gate was amended on 2026-11-05 so a subscription limit stops the series instead of failing pages; the dev records keep the gate they ran under, `615302b`.

2026-11-09: the agent is frozen at `e9b4b96`; a 2-case dev dry run there (`ev2-8f2712a4`, DEV ONLY) completed with `apiKeySource` none and exactly the 4 tools. An independent session sealed the 61-page hold-out outside this repository; its seal and answer-key hashes and the checks still required are in `evals/PREFLIGHT.md`. Later that day a second session that never builds verified the seal and extracted the hold-out (`evals/dataset/h001` to `h061`, `evals/answer_keys/official.json`); extraction checks E1 to E4 passed. The preflight ran the same day at `681da69`, recorded at `60a9135`.

2026-11-09, official series `ev2-b67b4f30` at `60a9135`: **PASS**, runner exit 0 (operator's terminal); numbers in the eval-numbers table above. All 183 executions had `apiKeySource` none and exactly the 4 tools. Gate and answer-key hashes equal the frozen values; a re-score of the saved record gives the same result. Synthetic pages only, not live web. The hold-out is now exposed: a later run on it is a regression run, never a new first result. Entry and limits: `evals/EVAL_RESULTS.md`.

## Open loops
- Demo assets (recording/GIF): in-scope, not shipped until they
  exist (adr/0002); not a publication precondition.
- Standard eval v2: official PASS recorded on the local branch; README,
  Version Log, review and publication not done. A live
  PostToolUseFailure row has never been seen; Kristian accepted that as
  a limit at preflight.
