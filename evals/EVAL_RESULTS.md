# Eval Results — Official Gate Run

Latest result: PASS, 2026-11-09, Standard eval v2 official series: 61 pages, 197 claims, 183/183 executions (61 pages x 3 runs), run `ev2-b67b4f30`.

This file records two official evaluations with different gates and
metrics: the v1 gate run of July 2026 (below) and the
[Standard eval v2 official series](#standard-eval-v2-official-series)
of November 2026.

## Standard eval v2: development runs (DEV ONLY)

These runs are development and smoke evidence. **None is an official
evaluation result**, and nothing above this section comes from them. The
official v2 gate had not run when they were recorded; its first run is in
the next section (updated 2026-11-09). Gate 1 needs 59 passing pages, so the 12-page
development set cannot meet it, whatever its score.

| Run | Date (UTC) | Commit | Type | Executions | Outcome | Label |
|---|---|---|---|---|---|---|
| `ev2-8c95e58c` | 2026-11-04 | `02ea27f` | Smoke test | 1 | Stopped: a session hook message arrived before the start-up message. No result, no findings | DEV ONLY, INVALID RUN |
| `ev2-08510ead` | 2026-11-05 | `142485a` | Smoke test | 1 | Stopped: the start-up message showed tools beyond the 4 approved. No result, no findings | DEV ONLY, INVALID RUN |
| `ev2-5486b40d` | 2026-11-05 | `2bd694e` | Smoke test | 1 | Completed: 1/1 page, 0/3 unnecessary UNVERIFIABLE, 3/3 useful verification, $0.0405 | DEV ONLY — NOT ENOUGH CASES |
| `ev2-2046a87c` | 2026-11-05 | `2bd694e` | Development series | 36 | Completed: 12/12 pages, 0/96 unnecessary UNVERIFIABLE, 96/96 useful verification, $1.3120 | DEV ONLY — NOT ENOUGH CASES |
| `ev2-8f2712a4` | 2026-11-06 | `e9b4b96` | Smoke test | 2 | Completed: 2/2 pages, 0/6 unnecessary UNVERIFIABLE, 6/6 useful verification, $0.0847 | DEV ONLY — NOT ENOUGH CASES |

Dollar figures are SDK API-equivalent, not a charge.

The 12 development cases are visible, synthetic and were used while the
system was built. A perfect score here says the runner and setup work. It
does not predict the result on the hold-out cases.

Source of every number in this section: `evals/results/v2/dev/`.

## Standard eval v2: official series

The first complete, valid official run on the sealed hold-out. This is
the Standard eval v2 result under the gate `evals/eval_config_v2.yaml`.

| Field | Value |
|---|---|
| Run | `ev2-b67b4f30`, 2026-11-09, 11:25:36 to 12:55:08 UTC |
| Result | **PASS** (first official series; runner exit 0, from the operator's terminal: the record has no exit-code field) |
| Commit | `60a9135`; the agent, runner, scorer, gate, tests and pins are unchanged since the freeze at `e9b4b96`; `git_dirty` false |
| Gate | config SHA-256 `4f1050772b9fabb96bf48e9f70067011f06ac74b907fa6be57e2507c1445676f` (LF-normalized), unchanged since `0f0c320` |
| Model | `claude-sonnet-4-6`; bundled CLI 2.1.191, `claude-agent-sdk` 0.2.110; subscription login, `apiKeySource` none and exactly the 4 tools on all 183 executions |
| Dataset | 61 sealed synthetic hold-out pages `h001` to `h061`, 197 claims, added at `681da69`; answer key `evals/answer_keys/official.json`, SHA-256 `d98f92bfb366c36d4bdee933698395772ed851c106b8b60d13a4574f2d67114b` (LF-normalized), equal to the value frozen before the run |
| Gate 1, page reliability | 61/61 pages passed (needs at least 59) |
| Gate 2, unnecessary UNVERIFIABLE | 0/504 = 0.00% (at most 5%) |
| Gate 3, useful verification | 504/504 = 100.00% (at least 80%) |
| Executions | 183/183 completed (61 pages x 3 runs), all on attempt 1; no INVALID RUN event, no error, no rerun |
| Miss analysis | No failed page and no page-fail event; no unmatched finding |
| Secondary, not gated | verdict accuracy 100.00%; verdict and evidence accuracy 100.00%; CONTRADICTED precision and recall 100.00%; genuine UNVERIFIABLE identified 87/87; verdict flips between runs 0; turns per run 6 to 14, mean 10.25 |
| Cost | $7.5558 in total, at most $0.0618 per run (SDK API-equivalent, not a charge) |
| Newly broken cases | Not applicable: first official result of this version |
| Changed since the previous entry (`ev2-8f2712a4`) | Set dev to official; commit `e9b4b96` to `60a9135`, which adds only the hold-out, its answer key, the dry-run record and the preflight and state records. Same model, CLI and SDK |

Re-scoring the saved record with `python evals/score_v2.py
evals/results/v2/official/ev2-b67b4f30.json` gives the same result and
gate numbers, with no record or attempt problems.

What this result supports: the agent gave the expected verdict, with
valid evidence, on every claim of 61 unseen synthetic pages, 3 times
each, inside its tool, turn and cost bounds. It does not show behaviour
on live web pages, on page shapes outside the case rules, or on other
models. The hold-out was written from the same case rules as the
development set, by a session that did not build the agent.

This series was the agent's first and only run on the hold-out. Its
pages and answer key are now part of this repository, so a later run on
them is a regression test of known pages, not a fresh unseen
evaluation; it is labelled "regression PASS on exposed hold-out" (or
FAIL). Known limits accepted before the run are in `evals/PREFLIGHT.md`.

Source of every number in this section:
`evals/results/v2/official/ev2-b67b4f30.json`, except the CLI and SDK
versions and the claim count, which come from `evals/PREFLIGHT.md`.

## Standard eval v2: scope and limits

The result covers the page shapes and scenarios that `case_rules` in
`evals/eval_config_v2.yaml` define; the rules were fixed before any
hold-out page was written.

- **Page shape.** Every hold-out page is generated local HTML
  (`evals/make_dataset.py render`): a title and plain paragraphs, with
  no ads, navigation, nested markup or scripts; 2 to 4 claims and 2 to 4
  sources per page; each claim is exactly one paragraph.
- **Scenarios.** Seven types: exact specifications, small numeric
  discrepancies, reworded statements or worded-out numbers, date
  mismatches, missing coverage, an authoritative source against forum
  content, and availability status. Excluded: conflicts between equally
  authoritative sources, chronology, partial or compound claims, tables,
  lists, and several claims in one paragraph.
- **Exact text matching.** A finding counts only when its claim text
  matches the expected paragraph after light normalization; the scorer
  never judges meaning.

## v1 gate run (July 2026)

- **Model:** `claude-sonnet-4-6` (locked models decision, see README "The Cage": Haiku 4.5 is dev-only)
- **Date:** 2026-07-06
- **Run ID:** `eval-05fbe4ee`
- **Total run cost:** $0.9047 across 12 cases (per-case ceiling: $1.50; none hit it)
- **Gate:** **PASS**

## Metrics (per `metric_definition`, `evals/eval_config.yaml`)

Dataset: synthetic seeded pages with known-truth claims (see Known Limitations).

| Metric | Value | Threshold | Result |
|---|---|---|---|
| Precision (positive class: CONTRADICTED) | 1.0000 (8/8) | ≥ 0.95 | PASS |
| Recall (positive class: CONTRADICTED) | 1.0000 (8/8) | ≥ 0.90 | PASS |
| Overall verdict accuracy (secondary, not gated) | 1.0000 (35/35) | — | — |

Cases that hit `max_turns` or the budget ceiling: **none**.

## Per-case verdict table

| Case | Claim | True | Predicted | Match |
|---|---|---|---|---|
| case_01_supported_wireless_earbuds | The SoundWave Pro X2 is priced at $129.99. | SUPPORTED | SUPPORTED | Y |
| | It features 30 hours of total battery life with the charging case. | SUPPORTED | SUPPORTED | Y |
| | Released on March 1, 2024. | SUPPORTED | SUPPORTED | Y |
| case_02_contradicted_price_laptop | Priced at $899. | CONTRADICTED | CONTRADICTED | Y |
| | Comes with 16GB RAM. | SUPPORTED | SUPPORTED | Y |
| | Ships with Windows 11 Home. | SUPPORTED | SUPPORTED | Y |
| case_03_contradicted_storage_phone | Comes with 256GB of storage. | CONTRADICTED | CONTRADICTED | Y |
| | Has a 6.5-inch display. | SUPPORTED | SUPPORTED | Y |
| | Weighs 189 grams. | SUPPORTED | SUPPORTED | Y |
| case_04_contradicted_release_date_camera | Released on September 10, 2023. | CONTRADICTED | CONTRADICTED | Y |
| | Features a 24MP sensor. | SUPPORTED | SUPPORTED | Y |
| | Supports 4K video recording at 60fps. | SUPPORTED | SUPPORTED | Y |
| case_05_unverifiable_warranty_monitor | Comes with a 3-year manufacturer warranty. | UNVERIFIABLE | UNVERIFIABLE | Y |
| | Has a 27-inch 4K display. | SUPPORTED | SUPPORTED | Y |
| | Supports HDR10. | SUPPORTED | SUPPORTED | Y |
| case_06_unverifiable_availability_speaker | Currently in stock at all major retailers. | UNVERIFIABLE | UNVERIFIABLE | Y |
| | Weighs 540 grams. | SUPPORTED | SUPPORTED | Y |
| | Waterproof rating of IPX7. | SUPPORTED | SUPPORTED | Y |
| case_07_adversarial_near_match_battery_earbuds | Battery life of 12 hours per charge. | CONTRADICTED | CONTRADICTED | Y |
| | Bluetooth 5.3 connectivity. | SUPPORTED | SUPPORTED | Y |
| case_08_adversarial_rephrased_price_tablet | Starts at $499 for the base model. | SUPPORTED | SUPPORTED | Y |
| | Has a 10.9-inch display. | SUPPORTED | SUPPORTED | Y |
| case_09_mixed_multi_claim_router | Supports Wi-Fi 6 (802.11ax). | SUPPORTED | SUPPORTED | Y |
| | Priced at $249.99. | CONTRADICTED | CONTRADICTED | Y |
| | Includes a 2-year warranty. | UNVERIFIABLE | UNVERIFIABLE | Y |
| | Has 8 LAN ports. | CONTRADICTED | CONTRADICTED | Y |
| case_10_supported_specs_bundle_keyboard | Uses hot-swappable mechanical switches. | SUPPORTED | SUPPORTED | Y |
| | Connects via USB-C and Bluetooth 5.0. | SUPPORTED | SUPPORTED | Y |
| | Priced at $89.99. | SUPPORTED | SUPPORTED | Y |
| | Weighs approximately 950 grams. | SUPPORTED | SUPPORTED | Y |
| case_11_contradicted_availability_gpu | Currently in stock and shipping immediately. | CONTRADICTED | CONTRADICTED | Y |
| | Has 12GB of GDDR6X memory. | SUPPORTED | SUPPORTED | Y |
| | TDP rated at 200 watts. | SUPPORTED | SUPPORTED | Y |
| case_12_adversarial_authoritative_conflict_smartwatch | Battery lasts up to 7 days on a single charge. | CONTRADICTED | CONTRADICTED | Y |
| | Water resistant up to 50 meters (5 ATM). | SUPPORTED | SUPPORTED | Y |

## Per-case cost and turns

| Case | Turns | Cost |
|---|---|---|
| case_01_supported_wireless_earbuds | 8 | $0.0708 |
| case_02_contradicted_price_laptop | 8 | $0.0684 |
| case_03_contradicted_storage_phone | 8 | $0.0673 |
| case_04_contradicted_release_date_camera | 11 | $0.0935 |
| case_05_unverifiable_warranty_monitor | 8 | $0.0691 |
| case_06_unverifiable_availability_speaker | 8 | $0.0694 |
| case_07_adversarial_near_match_battery_earbuds | 7 | $0.0890 |
| case_08_adversarial_rephrased_price_tablet | 7 | $0.0648 |
| case_09_mixed_multi_claim_router | 10 | $0.0769 |
| case_10_supported_specs_bundle_keyboard | 10 | $0.0731 |
| case_11_contradicted_availability_gpu | 8 | $0.0695 |
| case_12_adversarial_authoritative_conflict_smartwatch | 9 | $0.0929 |
| **Total** | — | **$0.9047** |

## Known Limitations

- **Synthetic dataset, not live web content.** All 12 cases are seeded local HTML pages written by hand for this eval (`evals/dataset/`), not real affiliate/review pages. This was a deliberate choice (see `evals/ground_truth.json`) because it gives exact, disputable-free ground truth. It also means the eval cannot yet speak to real-world HTML noise: ads, nested markup, JS-rendered content, inconsistent phrasing across genuinely independent sources, or pages that mix several claims in one paragraph.
- **Small n.** 12 cases / 35 claims is enough to catch gross failures and validate the three adversarial patterns, not enough to produce a statistically tight precision/recall estimate. A single misclassified CONTRADICTED claim would drop recall from 1.00 to 0.875, below the 0.90 gate, so a single miss fails it. (Corrected 2026-10-13: this sentence originally said 0.875 was "still comfortably above the 0.90 gate", which was wrong.)
- **A perfect 35/35 on the first official run is a good sign, not proof of robustness.** The adversarial cases were designed by the same person who wrote the comparison policy the agent is prompted with — there's a shared-author risk that the eval and the prompt are tuned to each other rather than to the general problem. Real-world claim types not represented here (ranges, conditional claims, claims spanning multiple paragraphs, claims about the same fact stated differently across 3+ sources with partial disagreement) haven't been tested.
- **Path-scoping and audit-trail verification is now a regression test.** The "all fetch/compare paths stayed inside `evals/dataset/`" and "no ground-truth leakage into tool calls" checks were first done by hand, before this run, as ad hoc SQL queries against `audit.db` on the earlier Haiku shakedown. Later the same day (2026-07-06), before the repo went public, `tests/test_bounds.py` turned both into repeatable tests that read a real run's `audit.db`. These two tests are skipped when no `audit.db` exists, as in CI. (Corrected 2026-10-29: this bullet originally said the checks were manual and should become a real regression test before this repo goes public. That test already existed.)
- **Cost ceiling was miscalibrated on the first attempt at this gate run.** `MAX_BUDGET_USD` (0.25) was tuned for the cheap Haiku dev runs; the first Sonnet attempt hit that ceiling mid-turn on case_01 and the SDK raised a hard exception rather than a graceful capped result — a real gap in the harness (now handled by catching the exception and scoring the case's claims as unresolved, and by giving eval runs their own higher ceiling, `EVAL_MAX_BUDGET_USD = 1.50`). Worth noting since it's exactly the kind of gap this eval is supposed to catch, and it wasn't caught by the (also newly-written) eval runner itself — it was caught by the run crashing.

## v1 gate: commit order and the October re-run

- The v1 gate thresholds (`evals/eval_config.yaml`) were first committed at `b041702` (2026-07-05). The July result was committed at `bc9f41a` (2026-07-06).
- Re-run on the renamed dataset, 2026-10-13, run `eval-199f77c6`: precision 1.00 (8/8), recall 1.00 (8/8), 35/35, gate PASS. Record: `evals/rerun_2026-10-13.md`.

## Raw run artifacts

- Official gate run: `evals/results/eval-05fbe4ee.json`
  (claude-sonnet-4-6, 2026-07-06) — source of every number above.
- Haiku shakedown: `evals/results/eval-33d91861.json`
  (claude-haiku-4-5, 2026-07-06, ran 20 minutes before the official
  run; same per-claim verdicts, gate PASS). This is the run named in
  Known Limitations.
