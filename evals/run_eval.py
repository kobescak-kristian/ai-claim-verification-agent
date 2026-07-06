#!/usr/bin/env python3
"""Eval runner: iterates every case in evals/dataset/, one fresh agent
invocation per case (its own ClaudeAgentOptions — own max_turns allowance,
own max_budget_usd ceiling, own tool-call circuit breaker), and computes the
metrics defined in evals/eval_config.yaml's metric_definition block.

Rule (no exceptions, no reruns): if a case ends without a logged verdict for
one of its ground-truth claims — for any reason, including hitting max_turns
or the budget ceiling — that claim is scored UNRESOLVED, which never matches
the true verdict. Caps are never raised mid-eval and a case is never re-run.

Usage:
    python evals/run_eval.py
"""
import asyncio
import json
import sys
import uuid
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agent.config import MAX_BUDGET_USD, MAX_TURNS, MODEL  # noqa: E402
from agent.harness import run_case_result  # noqa: E402

GROUND_TRUTH_PATH = REPO_ROOT / "evals" / "ground_truth.json"
EVAL_CONFIG_PATH = REPO_ROOT / "evals" / "eval_config.yaml"

# Sentinel for a ground-truth claim with no matching log_finding call. Never
# equals a real verdict (SUPPORTED/CONTRADICTED/UNVERIFIABLE), so it always
# scores as a mismatch — this is what implements the "unresolved = wrong" rule.
UNRESOLVED = "UNRESOLVED"

POSITIVE_CLASS = "CONTRADICTED"


def load_ground_truth() -> dict:
    with open(GROUND_TRUTH_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_gate_thresholds() -> dict:
    with open(EVAL_CONFIG_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return {
        "precision_min": config["precision_min"],
        "recall_min": config["recall_min"],
    }


def match_predicted_verdict(claim_text: str, findings: list[dict]) -> tuple[str, str | None]:
    """Return (verdict, evidence_source) for the finding whose claim_text
    matches exactly (whitespace-stripped). If the model logged the same
    claim more than once, the last call wins. UNRESOLVED if no match."""
    match = None
    for f in findings:
        if f["claim_text"].strip() == claim_text.strip():
            match = f
    if match is None:
        return UNRESOLVED, None
    return match["verdict"], match["evidence_source"]


def detect_cap_hit(result) -> str | None:
    """Best-effort reporting only — which cases likely hit max_turns or the
    budget ceiling. Scoring never depends on this: any unresolved claim is
    wrong regardless of why it's unresolved."""
    reasons = []
    if result.subtype == "error_max_turns" or result.num_turns >= MAX_TURNS:
        reasons.append("max_turns")
    if result.subtype == "error_max_budget_usd" or (
        MAX_BUDGET_USD is not None
        and result.total_cost_usd is not None
        and result.total_cost_usd >= MAX_BUDGET_USD
    ):
        reasons.append("budget_ceiling")
    return "+".join(reasons) if reasons else None


async def run_eval() -> dict:
    ground_truth = load_ground_truth()
    run_id = f"eval-{uuid.uuid4().hex[:8]}"

    per_claim_rows = []
    capped_cases = []
    case_reports = []

    for case in ground_truth["cases"]:
        case_id = case["case_id"]
        print(f"Running {case_id} ...", file=sys.stderr)
        result, findings = await run_case_result(case_id, run_id=run_id)

        cap_reason = detect_cap_hit(result)
        if cap_reason:
            capped_cases.append({"case_id": case_id, "reason": cap_reason})

        case_reports.append({
            "case_id": case_id,
            "num_turns": result.num_turns,
            "total_cost_usd": result.total_cost_usd,
            "subtype": result.subtype,
            "is_error": result.is_error,
            "capped": cap_reason,
        })

        for claim in case["claims"]:
            predicted_verdict, predicted_source = match_predicted_verdict(claim["text"], findings)
            per_claim_rows.append({
                "case_id": case_id,
                "claim_id": claim["id"],
                "claim_text": claim["text"],
                "true_verdict": claim["verdict"],
                "predicted_verdict": predicted_verdict,
                "predicted_source": predicted_source,
                "match": predicted_verdict == claim["verdict"],
            })

    metrics = compute_metrics(per_claim_rows)
    return {
        "run_id": run_id,
        "model": MODEL,
        "rows": per_claim_rows,
        "case_reports": case_reports,
        "capped_cases": capped_cases,
        "metrics": metrics,
    }


def compute_metrics(rows: list[dict]) -> dict:
    thresholds = load_gate_thresholds()

    predicted_positive = [r for r in rows if r["predicted_verdict"] == POSITIVE_CLASS]
    true_positive = [r for r in predicted_positive if r["true_verdict"] == POSITIVE_CLASS]
    true_positive_total = [r for r in rows if r["true_verdict"] == POSITIVE_CLASS]

    # Zero-division convention: no positive predictions/no positive ground
    # truth means the fraction is undefined; score 0.0 rather than a vacuous
    # 1.0 so an eval that predicts nothing can't look artificially perfect.
    precision = len(true_positive) / len(predicted_positive) if predicted_positive else 0.0
    recall = len(true_positive) / len(true_positive_total) if true_positive_total else 0.0

    gate_pass = precision >= thresholds["precision_min"] and recall >= thresholds["recall_min"]

    overall_correct = sum(1 for r in rows if r["match"])
    overall_accuracy = overall_correct / len(rows) if rows else 0.0

    return {
        "positive_class": POSITIVE_CLASS,
        "precision": precision,
        "recall": recall,
        "precision_min": thresholds["precision_min"],
        "recall_min": thresholds["recall_min"],
        "gate": "PASS" if gate_pass else "FAIL",
        "overall_verdict_accuracy": overall_accuracy,
        "total_claims": len(rows),
        "predicted_contradicted_count": len(predicted_positive),
        "true_contradicted_count": len(true_positive_total),
        "true_positive_count": len(true_positive),
    }


def print_report(eval_result: dict) -> None:
    rows = eval_result["rows"]
    print()
    print(f"Eval run: {eval_result['run_id']}  model: {eval_result['model']}")
    print()
    print("Per-case verdict table")
    print("=" * 105)
    print(f"{'case_id':<46} {'claim':<38} {'true':<13} {'pred':<13} match")
    print("-" * 105)
    current_case = None
    for r in rows:
        case_label = r["case_id"] if r["case_id"] != current_case else ""
        current_case = r["case_id"]
        claim = r["claim_text"]
        if len(claim) > 36:
            claim = claim[:33] + "..."
        mark = "Y" if r["match"] else "N"
        print(f"{case_label:<46} {claim:<38} {r['true_verdict']:<13} {r['predicted_verdict']:<13} {mark}")

    print()
    print("Metrics (per metric_definition, evals/eval_config.yaml)")
    print("=" * 60)
    m = eval_result["metrics"]
    print(f"positive_class: {m['positive_class']}")
    print(
        f"precision: {m['precision']:.4f} "
        f"({m['true_positive_count']}/{m['predicted_contradicted_count']}) "
        f"[min {m['precision_min']}]"
    )
    print(
        f"recall: {m['recall']:.4f} "
        f"({m['true_positive_count']}/{m['true_contradicted_count']}) "
        f"[min {m['recall_min']}]"
    )
    print(f"gate: {m['gate']}")
    print(
        f"overall_verdict_accuracy (secondary, not gated): "
        f"{m['overall_verdict_accuracy']:.4f} "
        f"({sum(1 for r in rows if r['match'])}/{m['total_claims']})"
    )

    print()
    print("Cases that hit a cap (max_turns or budget ceiling)")
    print("=" * 60)
    if eval_result["capped_cases"]:
        for c in eval_result["capped_cases"]:
            print(f"- {c['case_id']}: {c['reason']}")
    else:
        print("(none)")


def write_results_file(eval_result: dict) -> Path:
    results_dir = REPO_ROOT / "evals" / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    out_path = results_dir / f"{eval_result['run_id']}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(eval_result, f, indent=2, default=str)
    return out_path


def main() -> None:
    eval_result = asyncio.run(run_eval())
    print_report(eval_result)
    out_path = write_results_file(eval_result)
    print(f"\nResults written to {out_path.relative_to(REPO_ROOT)}", file=sys.stderr)


if __name__ == "__main__":
    main()
