#!/usr/bin/env python3
"""check_controls.py -- control package member: the CI half of the
control check. Installed as
.githooks/check_controls.py; run by the kos-controls CI job.

CI cannot read the private governance repository, so this checks the
repository against the package manifest it carries
(.kos/controls-manifest.sha256, a byte copy of the release manifest):
.kos/controls.json pins the manifest's version, and every package member
the repository lists is present at its installed path with the manifest
sha256 (line endings normalized to LF). The check against the release
itself (required version, manifest equal to the release) runs locally in
pre-push (`kos_core.py controls check`) and in the drift scan.

Stdlib only. Exit 0 = OK; exit 1 = BLOCK (fail-closed on any doubt).
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
MANIFEST = ROOT / ".kos" / "controls-manifest.sha256"
CONTROLS = ROOT / ".kos" / "controls.json"


def block(msg):
    print("CONTROLS BLOCK (CI): " + msg)
    sys.exit(1)


def member_path(member):
    return (".github/workflows/" if member.endswith(".yml") else ".githooks/") + member


def lf_sha(path):
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


if not CONTROLS.is_file():
    block(".kos/controls.json missing")
if not MANIFEST.is_file():
    block(".kos/controls-manifest.sha256 missing")
try:
    controls = json.loads(CONTROLS.read_text(encoding="utf-8"))
except ValueError as ex:
    block(".kos/controls.json is not valid JSON (%s)" % ex)

version, digests = None, {}
for line in MANIFEST.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line.startswith("# version "):
        version = line.split()[-1]
    elif line and not line.startswith("#"):
        parts = line.split()
        if len(parts) != 2:
            block("malformed manifest line: %r" % line)
        digests[parts[1].lstrip("*")] = parts[0]
if not version or not digests:
    block("manifest has no version line or no members")
if controls.get("control_version") != version:
    block("control_version %s in .kos/controls.json != manifest version %s"
          % (controls.get("control_version"), version))
members = controls.get("package")
if not isinstance(members, list) or not members:
    block(".kos/controls.json lists no package members")
for member in members:
    if member not in digests:
        block("member %s is not in the manifest at %s" % (member, version))
    path = ROOT / member_path(member)
    if not path.is_file():
        block("member %s missing at %s" % (member, member_path(member)))
    if lf_sha(path) != digests[member]:
        block("member %s at %s does not match the manifest at %s (local edit or tamper)"
              % (member, member_path(member), version))
print("CONTROLS OK (CI): control standard %s; %d package member(s) verified against "
      ".kos/controls-manifest.sha256" % (version, len(members)))
