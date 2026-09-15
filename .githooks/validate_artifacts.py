#!/usr/bin/env python3
"""Validate a repo against ARTIFACT_STANDARD.md Tier 0. Exit 1 = push blocked."""
import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
REQUIRED_README_SECTIONS = ["## Problem", "## Solution", "## System", "## Outcome", "## Version Log"]
BANNED_WITHOUT_TRIGGER = ["SYSTEM_WALKTHROUGH.md", "CHANGELOG.md", "RUNBOOK.md",
                          "PRODUCTION_READINESS.md", "THREAT_MODEL.md", "MONITORING.md",
                          "INCIDENT_RESPONSE.md", "TEST_MATRIX.md"]
errors = []

readme = ROOT / "README.md"
if not readme.exists():
    errors.append("README.md missing")
else:
    text = readme.read_text(encoding="utf-8")
    for section in REQUIRED_README_SECTIONS:
        if section not in text:
            errors.append(f"README missing section: {section}")

# AGENTS.md (ARTIFACT_STANDARD v2.7, Tier 0): root file + required H2 headings.
# Match is case-insensitive; "&" is accepted for "and". Optional
# "## Repository landmarks" is not checked.
AGENTS_REQUIRED_HEADINGS = ["Repository purpose", "Authority and conflict handling",
                            "Task routing", "Always-on constraints", "Verification"]
agents = ROOT / "AGENTS.md"
if not agents.exists():
    errors.append("AGENTS.md missing (ARTIFACT_STANDARD v2.7 Tier 0)")
else:
    agents_text = agents.read_text(encoding="utf-8")
    for heading in AGENTS_REQUIRED_HEADINGS:
        words = [r"(?:and|&)" if w == "and" else re.escape(w) for w in heading.split()]
        pattern = r"^##\s+" + r"\s+".join(words) + r"\s*$"
        if not re.search(pattern, agents_text, re.I | re.M):
            errors.append(f"AGENTS.md missing section: ## {heading}")

# Decision-record requirement: adr/ and decisions/ both satisfy it —
# a repo may use either name for its decision-record folder.
adr = ROOT / "adr"
decisions = ROOT / "decisions"
decision_dirs = [d for d in (adr, decisions) if d.is_dir()]
if not decision_dirs:
    errors.append("adr/ (or decisions/) folder missing")
else:
    decision_files = [f for d in decision_dirs for f in d.glob("*.md")
                       if "template" not in f.name.lower()]
    count = len(decision_files)
    if count == 0:
        errors.append("adr/ (or decisions/) has no decisions (need 1-5)")
    elif count > 5:
        errors.append(f"adr/ (or decisions/) has {count} decisions (cap is 5 - decisions were not decisions)")

for banned in BANNED_WITHOUT_TRIGGER:
    if (ROOT / banned).exists():
        # allowed only if a decision-record file mentions it (the trigger record)
        justified = any(
            re.search(re.escape(banned), f.read_text(encoding="utf-8"))
            for d in decision_dirs for f in d.glob("*.md"))
        if not justified:
            errors.append(f"{banned} exists without an ADR/decision record citing its trigger")

if errors:
    print("ARTIFACT_STANDARD violations:")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
print("Tier 0: PASS")
