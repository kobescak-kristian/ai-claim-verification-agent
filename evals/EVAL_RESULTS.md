# Eval Results — Official Gate Run

- **Model:** `claude-sonnet-4-6` (locked models decision, SPEC.md — Haiku 4.5 is dev-only)
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

- **Synthetic dataset, not live web content.** All 12 cases are seeded local HTML pages written by hand for this eval (`evals/dataset/`), not real affiliate/review pages. This was a deliberate choice — see `SPEC.md` and `evals/ground_truth.json` — because it gives exact, disputable-free ground truth. It also means the eval cannot yet speak to real-world HTML noise: ads, nested markup, JS-rendered content, inconsistent phrasing across genuinely independent sources, or pages that mix several claims in one paragraph.
- **Small n.** 12 cases / 35 claims is enough to catch gross failures and validate the three adversarial patterns, not enough to produce a statistically tight precision/recall estimate. A single misclassified CONTRADICTED claim would drop recall from 1.00 to 0.875 — still comfortably above the 0.90 gate, but the margin is thin given the sample size.
- **A perfect 35/35 on the first official run is a good sign, not proof of robustness.** The adversarial cases were designed by the same person who wrote the comparison policy the agent is prompted with — there's a shared-author risk that the eval and the prompt are tuned to each other rather than to the general problem. Real-world claim types not represented here (ranges, conditional claims, claims spanning multiple paragraphs, claims about the same fact stated differently across 3+ sources with partial disagreement) haven't been tested.
- **Path-scoping and audit-trail verification was manual, not automated.** The "all fetch/compare paths stayed inside `evals/dataset/`" and "no ground-truth leakage into tool calls" checks (done before this run, on the earlier Haiku shakedown) were ad hoc SQL queries against `audit.db`, not a repeatable test. They should be a real regression test before this repo goes public.
- **Cost ceiling was miscalibrated on the first attempt at this gate run.** `MAX_BUDGET_USD` (0.25) was tuned for the cheap Haiku dev runs; the first Sonnet attempt hit that ceiling mid-turn on case_01 and the SDK raised a hard exception rather than a graceful capped result — a real gap in the harness (now handled by catching the exception and scoring the case's claims as unresolved, and by giving eval runs their own higher ceiling, `EVAL_MAX_BUDGET_USD = 1.50`). Worth noting since it's exactly the kind of gap this eval is supposed to catch, and it wasn't caught by the (also newly-written) eval runner itself — it was caught by the run crashing.

## Raw run artifacts

- Official gate run: `evals/results/eval-05fbe4ee.json`
  (claude-sonnet-4-6, 2026-07-06) — source of every number above.
- Haiku shakedown: `evals/results/eval-33d91861.json`
  (claude-haiku-4-5, 2026-07-06, ran 20 minutes before the official
  run; same per-claim verdicts, gate PASS). This is the run named in
  Known Limitations.
