#!/usr/bin/env python3
"""Dataset tool for Standard eval v2.

check  <dataset_root> <answer_key> [--against KEY ...]
    Self-check of the WHOLE set every time; exit 1 on any problem. By
    default the key is also compared against every other *.json key in its
    own directory (ID and product collisions between dev and official).

render <spec> <dataset_root> <answer_key>
    Turn a case spec (YAML or JSON) into HTML pages and an answer key.
    Renders into a temporary directory, runs check there, and writes
    nothing to the destination unless check passes. Output is byte-for-byte
    deterministic for the same spec. Refuses to overwrite an existing case.

Spec shape:
    set: official
    cases:
      - case_id: h001
        product: Example Phone 9
        title: Example Phone 9 Review
        claims:
          - {text: "...", verdict: SUPPORTED, scenario: exact_spec,
             evidence_sources: [source_1_spec_sheet.html]}
        sources:
          - {file: source_1_spec_sheet.html, title: "...", paragraphs: ["..."]}
"""
import argparse
import html
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agent.pages import _ParagraphExtractor  # noqa: E402
from evals.score_v2 import VERDICTS, load_config, normalize_claim  # noqa: E402

VERDICT_WORDS = ("supported", "contradicted", "unverifiable")
SOURCE_NAME = re.compile(r"^source_\d+_[a-z0-9_]+\.html$")


def _read_paragraphs(path: Path) -> tuple[str, list[str]]:
    parser = _ParagraphExtractor()
    parser.feed(path.read_text(encoding="utf-8"))
    return parser.title, parser.paragraphs


def _load(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        if path.suffix in (".yaml", ".yml"):
            return yaml.safe_load(f)
        return json.load(f)


def check(dataset_root: Path, key_path: Path, against: list[Path] | None = None) -> list[str]:
    """Return every problem found; empty list = PASS."""
    config = load_config()
    rules = config["case_rules"]
    allowed_scenarios = set(rules["scenarios"])
    key = _load(key_path)
    official = key.get("set") == "official"
    problems = []

    if against is None:
        against = sorted(p for p in key_path.parent.glob("*.json") if p.resolve() != key_path.resolve())
    other_cases, other_claims, other_products = set(), set(), set()
    for other in against:
        okey = _load(other)
        for c in okey.get("cases", []):
            other_cases.add(c["case_id"])
            other_products.add(str(c.get("product", "")).casefold())
            other_claims.update(cl["id"] for cl in c.get("claims", []))

    cases = key.get("cases", [])
    if official and len(cases) != config["official_case_count"]:
        problems.append(f"official key has {len(cases)} cases, needs {config['official_case_count']}")

    seen_cases, seen_claims, seen_products = set(), set(), set()
    for case in cases:
        cid = case.get("case_id", "")
        where = f"{cid}:"
        if cid in seen_cases:
            problems.append(f"{where} duplicate case_id")
        seen_cases.add(cid)
        if cid in other_cases:
            problems.append(f"{where} case_id collides with another key")
        if official and not re.match(config["sets"]["official"]["case_id_pattern"], cid):
            problems.append(f"{where} official case_id must be h001 to h061")
        if any(w in cid.lower() for w in VERDICT_WORDS):
            problems.append(f"{where} verdict word in case_id")

        product = str(case.get("product", "")).casefold()
        if not product:
            problems.append(f"{where} no product")
        elif product in seen_products or product in other_products:
            problems.append(f"{where} product reused across pages: {case.get('product')!r}")
        seen_products.add(product)

        case_dir = dataset_root / cid
        target = case_dir / "target.html"
        if not target.is_file():
            problems.append(f"{where} missing {cid}/target.html")
            continue
        names = sorted(p.name for p in case_dir.iterdir())
        sources = [n for n in names if SOURCE_NAME.match(n)]
        for n in names:
            if any(w in n.lower() for w in VERDICT_WORDS):
                problems.append(f"{where} verdict word in file name {n}")
            if n != "target.html" and n not in sources:
                problems.append(f"{where} extra file {n}")
        if official and not 2 <= len(sources) <= 4:
            problems.append(f"{where} {len(sources)} source pages, needs 2 to 4")

        _title, paragraphs = _read_paragraphs(target)
        norm_paragraphs = [normalize_claim(p) for p in paragraphs]
        if len(set(norm_paragraphs)) != len(norm_paragraphs):
            problems.append(f"{where} two target paragraphs normalize to the same text")

        claims = case.get("claims", [])
        if official and not 2 <= len(claims) <= 4:
            problems.append(f"{where} {len(claims)} claims, needs 2 to 4")
        claimed = set()
        for claim in claims:
            clid = claim.get("id", "")
            cwhere = f"{where}{clid}:"
            if clid in seen_claims:
                problems.append(f"{cwhere} duplicate claim id")
            seen_claims.add(clid)
            if clid in other_claims:
                problems.append(f"{cwhere} claim id collides with another key")
            if claim.get("verdict") not in VERDICTS:
                problems.append(f"{cwhere} bad verdict {claim.get('verdict')!r}")
            if claim.get("scenario") not in allowed_scenarios:
                problems.append(f"{cwhere} scenario {claim.get('scenario')!r} not in case_rules")
            norm = normalize_claim(claim.get("text", ""))
            hits = norm_paragraphs.count(norm)
            if hits != 1:
                problems.append(f"{cwhere} claim is not exactly one target paragraph ({hits} matches)")
            if norm in claimed:
                problems.append(f"{cwhere} two claims with the same normalized text")
            claimed.add(norm)
            evidence = claim.get("evidence_sources")
            if not isinstance(evidence, list):
                problems.append(f"{cwhere} evidence_sources must be a list")
                continue
            if claim.get("verdict") == "UNVERIFIABLE" and evidence:
                problems.append(f"{cwhere} evidence on an UNVERIFIABLE claim")
            if claim.get("verdict") in ("SUPPORTED", "CONTRADICTED") and not evidence:
                problems.append(f"{cwhere} no evidence_sources on a {claim['verdict']} claim")
            if len(set(evidence)) != len(evidence):
                problems.append(f"{cwhere} duplicate evidence source")
            for src in evidence:
                if src not in sources:
                    problems.append(f"{cwhere} missing evidence file {src}")
        for p, norm in zip(paragraphs, norm_paragraphs):
            if norm not in claimed:
                problems.append(f"{where} target paragraph is not a claim: {p!r}")

    on_disk = sorted(p.name for p in dataset_root.iterdir() if p.is_dir()) if dataset_root.is_dir() else []
    if official:
        for name in on_disk:
            if re.match(config["sets"]["official"]["case_id_pattern"], name) and name not in seen_cases:
                problems.append(f"{name}: case directory has no answer-key entry")
    return problems


_PAGE = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>{title}</title></head>
<body>
    <h1>{h1}</h1>
{paragraphs}
</body>
</html>
"""


def _page(title: str, paragraphs: list[str], h1: str | None = None) -> str:
    body = "\n".join(f"    <p>{html.escape(p, quote=False)}</p>" for p in paragraphs)
    return _PAGE.format(title=html.escape(title, quote=False),
                        h1=html.escape(h1 or title, quote=False), paragraphs=body)


def render(spec_path: Path, dataset_root: Path, key_path: Path) -> list[str]:
    spec = _load(spec_path)
    for case in spec["cases"]:
        if (dataset_root / case["case_id"]).exists():
            return [f"{case['case_id']}: destination exists, refusing to overwrite"]
    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp) / "dataset"
        tmp_key = Path(tmp) / "keys" / key_path.name
        tmp_key.parent.mkdir(parents=True)
        key_cases = []
        for case in spec["cases"]:
            cid = case["case_id"]
            case_dir = tmp_root / cid
            case_dir.mkdir(parents=True)
            (case_dir / "target.html").write_text(
                _page(case["title"], [c["text"] for c in case["claims"]]),
                encoding="utf-8", newline="\n")
            for src in case["sources"]:
                (case_dir / src["file"]).write_text(
                    _page(src["title"], src["paragraphs"]), encoding="utf-8", newline="\n")
            key_cases.append({
                "case_id": cid,
                "product": case["product"],
                "claims": [
                    {"id": f"{cid}_c{i}", "text": c["text"], "verdict": c["verdict"],
                     "evidence_sources": list(c.get("evidence_sources", [])),
                     "scenario": c["scenario"]}
                    for i, c in enumerate(case["claims"], start=1)
                ],
            })
        key = {"set": spec["set"], "key_version": "2.0", "case_count": len(key_cases),
               "claim_count": sum(len(c["claims"]) for c in key_cases), "cases": key_cases}
        tmp_key.write_text(json.dumps(key, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8", newline="\n")
        against = sorted(p for p in key_path.parent.glob("*.json")
                         if p.resolve() != key_path.resolve()) if key_path.parent.is_dir() else []
        problems = check(tmp_root, tmp_key, against)
        if problems:
            return problems
        dataset_root.mkdir(parents=True, exist_ok=True)
        for case in spec["cases"]:
            shutil.copytree(tmp_root / case["case_id"], dataset_root / case["case_id"])
        key_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(tmp_key, key_path)
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_check = sub.add_parser("check")
    p_check.add_argument("dataset_root", type=Path)
    p_check.add_argument("answer_key", type=Path)
    p_check.add_argument("--against", type=Path, nargs="*", default=None)
    p_render = sub.add_parser("render")
    p_render.add_argument("spec", type=Path)
    p_render.add_argument("dataset_root", type=Path)
    p_render.add_argument("answer_key", type=Path)
    args = parser.parse_args(argv)

    if args.command == "check":
        problems = check(args.dataset_root, args.answer_key, args.against)
    else:
        problems = render(args.spec, args.dataset_root, args.answer_key)
    if problems:
        for p in problems:
            print(f"FAIL {p}")
        print(f"{args.command}: {len(problems)} problem(s); nothing written" if args.command == "render"
              else f"check: {len(problems)} problem(s)")
        return 1
    key = _load(args.answer_key)
    print(f"{args.command} PASS: {key['case_count'] if 'case_count' in key else len(key['cases'])} cases, "
          f"{sum(len(c['claims']) for c in key['cases'])} claims")
    return 0


if __name__ == "__main__":
    sys.exit(main())
