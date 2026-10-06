#!/usr/bin/env python3
"""check_work.py — contract-bound work checks.

Subcommands:
  scope  changed paths vs the approved contract's allowed writes.
         Reuses check_write_set.py (same folder) unchanged: this file
         only reads the contract and passes its entries through.
  close  acceptance evidence + close invariants for a CLOSED item
         (reference/CONTRACT_TEMPLATE.md, "Close invariants").
  publicsafe  scan a PUBLIC-DESTINATION repository's staged diff, a
         commit message, or its whole tracked tree and history messages
         against a private pattern list (LOCAL-ONLY).
  index  QUEUE.md is a pointer-only work index: table rows only
         (ID, band, title, home), excepted record blocks only for the IDs
         listed in .githooks/queue_excepted.txt of the judged tree
         (until quiescent), every home exists, no ID both active and
         archived, no orphan work/ home (indexed, archived or named in
         BACKLOG.md).

Contracts: work/Q-NNN/CONTRACT.md (template: reference/CONTRACT_TEMPLATE.md).
A contract branch is named work/Q-NNN or work/Q-NNN-<suffix>; any other
branch has no contract and `scope` reports NOT APPLICABLE (exit 0). A
branch under work/ that does not match that shape fails closed. In a
project repository that does not hold work/Q-NNN/CONTRACT.md, the
contract is read from the governance checkout (KOS_ROOT, .kos/local.env
or `git config kos.root`) and this repository's ```writes <name> block
applies, as with --contract-repo; neither found = refused.

The contract is always read from --contract-ref (default origin/main),
never from the working tree or the branch: the approved, merged
contract is the ruler, and a branch cannot widen its own scope. The
contract file itself may never be an allowed write (frozen once
approved).

Close model (owner-approved correction): two parts.
  A. Acceptance evidence: one close-table row per contract A#; each row
     has at least one checked reference and every reference exists before
     the commit being judged:
       commit:<sha>      resolves; strict ancestor of HEAD
       run:<id>@<sha>    GitHub Actions run: conclusion success and
                         head_sha == <sha>; <sha> strict ancestor of HEAD
       file:<path>       exists in the judged tree
     A JUDGMENT: note may sit next to a checked reference, never alone.
     With --worktree the judged commit does not exist yet (HEAD will be
     its parent), so HEAD itself may be cited.
  B. Close invariants I1-I7, asserted directly against the judged tree;
     they never cite commits or runs.
Run lookups use `gh api` (KOS_GH_CMD overrides the command, for
fixtures). In CI, GH_TOKEN comes from the workflow token (actions:
read). gh missing, auth missing or unusable, a failed call or bad JSON
= FAIL (SKIPPED != PASS).

Unit home (cross-repository close rule): a
contract whose section 2 Home line reads
`- Home: <unit-repo>:<path>/PROGRESS.md` keeps PROGRESS and its close
evidence in that project repository (the unit); the contract, I3, I4
and I5 stay in this repository. A unit-home close reads the unit at a
REMOTE ref (default origin/main; --fetch runs `git fetch origin` in
the unit first), never a local HEAD: I1, I2, I6, I7 and every commit:
/ run: / file: reference resolve in the unit tree at that ref (strict
ancestors of the ref's commit); run lookups resolve against the unit
repository (owner/name from the Home line, else from the unit clone's
origin URL). The unit clone is the sibling folder ../<name> of this
repository unless --unit names it. One home per item: a unit-home
contract with a work/Q-NNN/PROGRESS.md here fails I1. In CI (--all)
the workflow token cannot read another repository, so unit-home items
get the governance-side invariants I3-I5 only; the unit side is verified on
the owner's machine at close time and the report says so.

Usage:
  python .githooks/check_work.py scope (--staged | --range auto | --range <base>..<head>)
         [--branch NAME] [--contract-ref REF]
  python .githooks/check_work.py close Q-NNN [--worktree]
         [--fetch] [--unit <path>] [--unit-ref origin/<branch>]   (unit home only)
  python .githooks/check_work.py close --all --base <sha|"">
         (CI: every CLOSED item gets B; A only where its PROGRESS.md changed
         in <base>..HEAD; an empty or unresolvable base = A for all;
         unit-home items: I3-I5 only)
  python .githooks/check_work.py index [--worktree]
  python .githooks/check_work.py scope --staged --contract-repo <governance-repo> --contract Q-NNN [--target NAME]
  python .githooks/check_work.py publicsafe --patterns <file> [--at <sha>] (--staged | --tree [--messages] | --message-file F)
     --at <sha> binds --tree and --messages to that commit (its tree; the
     history reachable from it), never the index or the working tree
     (exact-snapshot binding); the result line names the full SHA.

Exit 0 = PASS or NOT APPLICABLE. Exit 1 = BLOCK / FAIL. Exit 2 =
fail-closed (missing / unapproved / malformed contract, git failure,
bad arguments).
"""
import json
import os
import re
import shlex
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WRITE_SET = os.path.join(HERE, "check_write_set.py")
BRANCH_RE = re.compile(r"^work/(Q-\d{3})(-[A-Za-z0-9._-]+)?$")
APPROVED_RE = re.compile(r"^- APPROVED: \d{4}-\d{2}-\d{2} by Kristian\b")
QID_RE = re.compile(r"^Q-\d{3}$")
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
REF_RE = re.compile(r"\b(commit|run|file):(\S+)")
ID_PREFIX = "Q-"  # work-item ID prefix (IDs are ID_PREFIX + digits)
PLACEHOLDER_RE = re.compile(r"\bTBD\b|\bTODO\b|<[^>]*>", re.IGNORECASE)
# Unit home: "Home: <unit-repo>:<path>/PROGRESS.md"; unit-repo is a name or
# owner/name. The governance-repository form "Home: `work/Q-NNN/`" has no colon.
UNIT_HOME_RE = re.compile(r"Home:\s*`?([A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)?):([^`\s]+/PROGRESS\.md)`?")
REPO_LINE_RE = re.compile(r"^- Repository:\s*`?([A-Za-z0-9._/-]+?)`?(?:[.,;:\s]|$)", re.MULTILINE)
GITHUB_URL_RE = re.compile(r"github\.com[:/]([A-Za-z0-9._-]+/[A-Za-z0-9._-]+?)(?:\.git)?/?$")


def fail_closed(msg):
    print(f"WORK CHECK BLOCK (fail-closed): {msg}")
    sys.exit(2)


def git(args, cwd=None):
    try:
        p = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    except OSError as e:
        fail_closed(f"cannot run git: {e}")
    return p.returncode, p.stdout, p.stderr.strip()


def repo_top():
    rc, out, _ = git(["rev-parse", "--show-toplevel"])
    if rc != 0:
        fail_closed("not inside a git worktree")
    return out.strip()


def current_branch(top):
    rc, out, err = git(["rev-parse", "--abbrev-ref", "HEAD"], top)
    if rc != 0:
        fail_closed(f"cannot read the current branch: {err}")
    return out.strip()


def contract_id_for(branch):
    """Q-NNN for a contract branch, None for a non-contract branch."""
    if not branch.startswith("work/"):
        return None
    m = BRANCH_RE.match(branch)
    if not m:
        fail_closed(f"branch {branch!r} is under work/ but is not work/Q-NNN[-suffix]")
    return m.group(1)


def governance_root(top):
    """The governance checkout, found as the package pre-push finds it: KOS_ROOT, else the
    git-ignored .kos/local.env of this repository, else `git config kos.root`. None if absent."""
    v = os.environ.get("KOS_ROOT", "").strip()
    env = os.path.join(top, ".kos", "local.env")
    if not v and os.path.isfile(env):
        with open(env, encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("KOS_ROOT="):
                    v = line.strip().split("=", 1)[1].strip().strip("'\"")
    if not v:
        rc, out, _ = git(["config", "--get", "kos.root"], top)
        v = out.strip() if rc == 0 else ""
    return v if v and os.path.isfile(os.path.join(v, "core", "kos_core.py")) else None


def read_at(top, ref, path):
    rc, out, err = git(["show", f"{ref}:{path}"], top)
    if rc != 0:
        fail_closed(f"cannot read {path} at {ref}: {err or 'not found'}")
    return out


def parse_contract(text, qid, path, target=None):
    """Allowed writes of an approved contract. target=None reads the plain
    ```writes block (writes in the contract's own repository); a target
    name reads the ```writes <target> block (writes in another repository,
    checked with scope --contract-repo)."""
    lines = text.splitlines()
    ids = [l for l in lines if l.startswith("- ID: ")]
    if ids != [f"- ID: {qid}"]:
        fail_closed(f"{path}: needs exactly one '- ID: {qid}' line, found {ids or 'none'}")
    if not any(APPROVED_RE.match(l) for l in lines):
        fail_closed(f"{path}: not approved (needs '- APPROVED: YYYY-MM-DD by Kristian ...')")
    fence = "```writes" + (f" {target}" if target else "")
    opens = [i for i, l in enumerate(lines) if l.strip() == fence]
    if len(opens) != 1:
        fail_closed(f"{path}: needs exactly one {fence} block, found {len(opens)}")
    entries = []
    for l in lines[opens[0] + 1:]:
        if l.strip() == "```":
            break
        s = l.strip()
        if not s:
            continue
        if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
            s = s[1:-1]
        entries.append(s)
    else:
        fail_closed(f"{path}: {fence} block is not closed")
    if not entries:
        fail_closed(f"{path}: {fence} block is empty")
    for e in entries:
        if target is None and (e == path or (e.endswith("/") and path.startswith(e))):
            fail_closed(f"{path}: allowed write {e!r} covers the contract itself (contracts are frozen)")
    return entries


def cmd_scope(argv):
    mode, rng, branch, ref = None, None, None, "origin/main"
    crepo, cid, target, base_ref = None, None, None, "origin/main"
    opts = ("--range", "--branch", "--contract-ref", "--contract-repo", "--contract", "--target", "--base-ref")
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--staged":
            mode = "staged"
        elif a in opts:
            if i + 1 >= len(argv):
                fail_closed(f"{a} needs a value")
            v = argv[i + 1]
            i += 1
            if a == "--range":
                mode, rng = "range", v
            elif a == "--branch":
                branch = v
            elif a == "--contract-ref":
                ref = v
            elif a == "--contract-repo":
                crepo = v
            elif a == "--contract":
                cid = v
            elif a == "--target":
                target = v
            else:
                base_ref = v
        else:
            fail_closed(f"unknown argument {a!r}")
        i += 1
    if mode is None:
        fail_closed("scope needs --staged or --range")
    top = repo_top()
    if crepo is None and cid is None and target is None:
        # A contract branch in a project repository whose approved contract lives in the
        # governance repository (unit-home work): check this repository's block of that
        # contract, exactly as --contract-repo does. No contract findable = refused.
        qid = contract_id_for(branch if branch is not None else current_branch(top))
        path = f"work/{qid}/CONTRACT.md" if qid else None
        if qid is not None and git(["cat-file", "-e", f"{ref}:{path}"], top)[0] != 0:
            gov = governance_root(top)
            if gov is None:
                fail_closed(f"{path} is not in this repository at {ref} and the governance checkout was not found "
                            "(KOS_ROOT, .kos/local.env or git config kos.root): a contract branch needs its contract")
            if os.path.normcase(os.path.abspath(gov)) != os.path.normcase(os.path.abspath(top)):
                crepo, cid, branch = gov, qid, None
    if crepo is not None:
        # Cross-repository mode: the contract lives in a private
        # governance repository; the writes being checked are in this one.
        # LOCAL-ONLY: it runs where both clones exist, never in this repo's CI.
        if branch is not None:
            fail_closed("--branch does not apply with --contract-repo (pass --contract)")
        if cid is None or not QID_RE.match(cid):
            fail_closed("--contract-repo needs --contract Q-NNN")
        rc, ctop, err = git(["rev-parse", "--show-toplevel"], crepo)
        if rc != 0:
            fail_closed(f"--contract-repo {crepo!r} is not a git worktree: {err}")
        ctop = ctop.strip()
        if os.path.normcase(os.path.abspath(ctop)) == os.path.normcase(os.path.abspath(top)):
            fail_closed("--contract-repo is this repository; use the plain branch mode")
        target = target or os.path.basename(top)
        path = f"work/{cid}/CONTRACT.md"
        entries = parse_contract(read_at(ctop, ref, path), cid, path, target)
        where, merge_ref = f"{os.path.basename(ctop)}:{path} @ {ref}, block writes {target}", base_ref
        label = f"repo {os.path.basename(top)}"
    else:
        if cid is not None or target is not None:
            fail_closed("--contract / --target need --contract-repo")
        if branch is None:
            branch = current_branch(top)
        qid = contract_id_for(branch)
        if qid is None:
            print(f"SCOPE: NOT APPLICABLE (branch {branch!r} is not a contract branch work/Q-NNN)")
            return 0
        path = f"work/{qid}/CONTRACT.md"
        entries = parse_contract(read_at(top, ref, path), qid, path)
        where, merge_ref, label = f"{path} @ {ref}", ref, f"branch {branch}"
    args = [sys.executable, WRITE_SET]
    if mode == "range":
        if rng == "auto":
            rc, base, err = git(["merge-base", merge_ref, "HEAD"], top)
            if rc != 0 or not base.strip():
                fail_closed(f"cannot find merge-base of {merge_ref} and HEAD: {err or 'none'}")
            rng = f"{base.strip()}..HEAD"
        args += ["--range", rng]
    args += entries
    print(f"SCOPE: {label} -> contract {where} ({len(entries)} allowed entries)")
    sys.stdout.flush()
    try:
        p = subprocess.run(args, cwd=top)
    except OSError as e:
        fail_closed(f"cannot run check_write_set.py: {e}")
    return p.returncode if p.returncode in (0, 1) else 2


# ---------------------------------------------------------------- close


class Tree:
    """The judged tree: HEAD (default), the working tree (--worktree), or a
    named ref (a unit's fetched remote ref)."""

    def __init__(self, top, worktree, ref="HEAD"):
        self.top, self.worktree, self.ref = top, worktree, ref

    def read(self, path):
        if self.worktree:
            p = os.path.join(self.top, path)
            if not os.path.isfile(p):
                return None
            with open(p, encoding="utf-8") as f:
                return f.read()
        rc, out, _ = git(["show", f"{self.ref}:{path}"], self.top)
        return out if rc == 0 else None

    def exists(self, path):
        if self.worktree:
            return os.path.exists(os.path.join(self.top, path))
        return git(["cat-file", "-e", f"{self.ref}:{path}"], self.top)[0] == 0


def fields(text, name):
    p = f"- {name}: "
    return [l[len(p):].strip() for l in (text or "").splitlines() if l.startswith(p)]


def close_rows(text):
    rows, inside = [], False
    for l in (text or "").splitlines():
        if l.startswith("## "):
            inside = l.strip().lower() == "## close table"
            continue
        m = re.match(r"^\|\s*(A\d+)\s*\|(.*)\|\s*$", l) if inside else None
        if m:
            rows.append((m.group(1), m.group(2).strip()))
    return rows


def unit_home(ctext):
    """(unit-repo, progress path) when the contract names a unit home, else
    None. Two different unit homes in one contract = fail-closed."""
    found = sorted(set(UNIT_HOME_RE.findall(ctext or "")))
    if len(found) > 1:
        fail_closed(f"contract names more than one unit home: {found}")
    return found[0] if found else None


def archived(tree, qid):
    """True when archive/queue/INDEX.md has a row for qid pointing to its home."""
    index, n = tree.read("archive/queue/INDEX.md"), int(qid[2:])
    if index is None:
        return False
    cells = [l.split("|") for l in index.splitlines() if re.match(rf"^\|\s*{ID_PREFIX}0*{n}\s*\|", l)]
    return any(len(c) > 5 and f"work/{qid}/" in c[4] for c in cells)


def check_kos_side(tree, qid):
    """I3-I5: the contract, the index row and the archive row live in this
    repository whichever home PROGRESS has. Returns (errors, contract text)."""
    errs, home, n = [], f"work/{qid}/", int(qid[2:])
    ctext = tree.read(home + "CONTRACT.md")
    if ctext is None:
        errs.append("I3 CONTRACT.md missing")
    elif not any(APPROVED_RE.match(l) for l in ctext.splitlines()):
        errs.append("I3 contract has no APPROVED line")
    queue = tree.read("QUEUE.md")
    if queue is None:
        errs.append("I4 QUEUE.md missing")
    else:
        pat = re.compile(rf"^(\|\s*{ID_PREFIX}0*{n}\s*\||{ID_PREFIX}0*{n} \[)")
        hits = [l for l in queue.splitlines() if pat.match(l)]
        if hits:
            errs.append(f"I4 QUEUE.md still has an index line for {qid}: {hits[0][:70]}")
    if tree.read("archive/queue/INDEX.md") is None:
        errs.append("I5 archive/queue/INDEX.md missing")
    elif not archived(tree, qid):
        errs.append(f"I5 archive/queue/INDEX.md has no row for {qid} pointing to {home}")
    return errs, ctext


def check_invariants(tree, qid, ptree=None, ppath=None):
    """B. I1-I7 against the judged tree. With ptree/ppath (unit home) the
    PROGRESS side is read there. Returns (errors, close rows)."""
    home = f"work/{qid}/"
    errs, ctext = check_kos_side(tree, qid)
    if ptree is None:
        ptree, ppath = tree, home + "PROGRESS.md"
        where = "PROGRESS.md"
    else:
        where = f"{ppath} in the unit at {ptree.ref}"
        if tree.exists(home + "PROGRESS.md"):
            errs.append(f"I1 two homes: {home}PROGRESS.md exists here and the contract names a unit home")
    ptext = ptree.read(ppath)
    if ptext is None:
        errs.append(f"I1 {where} missing")
    elif fields(ptext, "STATUS") != ["CLOSED"]:
        errs.append(f"I1 STATUS is {fields(ptext, 'STATUS') or 'missing'}, expected one CLOSED")
    cid, pid = fields(ctext, "ID"), fields(ptext, "ID")
    if cid != [qid] or pid != [qid]:
        errs.append(f"I2 IDs differ: folder {qid}, contract {cid or 'none'}, progress {pid or 'none'}")
    acc = re.findall(r"^- (A\d+): ", ctext or "", re.MULTILINE)
    rows = close_rows(ptext)
    ids = [a for a, _ in rows]
    if not acc:
        errs.append("I6 contract has no acceptance items (- A<n>: lines)")
    for a in sorted({a for a in acc if acc.count(a) > 1}):
        errs.append(f"I6 contract lists {a} more than once")
    for a in acc:
        if a not in ids:
            errs.append(f"I6 {a} has no close-table row")
    for a in sorted({a for a in ids if ids.count(a) > 1}):
        errs.append(f"I6 {a} has more than one close-table row")
    for a in ids:
        if a not in acc:
            errs.append(f"I6 close-table row {a} is not an acceptance item of the contract")
    for a, cell in rows:
        if not cell or cell in ("-", "—") or PLACEHOLDER_RE.search(cell):
            errs.append(f"I7 {a} evidence is empty or a placeholder: {cell!r}")
    return errs, rows


def gh_command():
    override = os.environ.get("KOS_GH_CMD")
    if override is not None:
        return shlex.split(override) or None
    p = shutil.which("gh")
    return [p] if p else None


def gh(args):
    cmd = gh_command()
    if not cmd:
        return None, "gh not available"
    try:
        p = subprocess.run(cmd + args, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    except OSError as e:
        return None, f"cannot run gh: {e}"
    if p.returncode != 0:
        last = (p.stderr.strip().splitlines() or ["no output"])[-1]
        return None, f"gh {args[0]} failed (exit {p.returncode}): {last}"
    return p.stdout, None


class Runs:
    def __init__(self, repo=None):
        self.repo, self.cache = repo or os.environ.get("GITHUB_REPOSITORY"), {}

    def get(self, run_id):
        if run_id in self.cache:
            return self.cache[run_id]
        res = (None, None)
        if not self.repo:
            out, err = gh(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
            if err:
                res = (None, f"cannot resolve the repository: {err}")
            else:
                self.repo = out.strip()
        if res[1] is None:
            out, err = gh(["api", f"repos/{self.repo}/actions/runs/{run_id}"])
            if err:
                res = (None, err)
            else:
                try:
                    data = json.loads(out)
                    res = (data, None) if isinstance(data, dict) else (None, "invalid JSON (not an object)")
                except ValueError:
                    res = (None, "invalid JSON")
        self.cache[run_id] = res
        return res


def check_sha(top, head, sha, worktree, label="HEAD"):
    """Full SHA if it may be cited, else (None, reason). `head` is the judged
    commit: HEAD here, or the unit's fetched remote ref (label names it)."""
    if not SHA_RE.match(sha):
        return None, "malformed SHA"
    rc, full, _ = git(["rev-parse", "--verify", "-q", f"{sha}^{{commit}}"], top)
    if rc != 0:
        return None, "commit not found" + ("" if label == "HEAD" else " in the unit")
    full = full.strip()
    if full == head:
        return (full, None) if worktree else (None, f"cites the commit being judged ({label})")
    if git(["merge-base", "--is-ancestor", full, head], top)[0] != 0:
        return None, f"not an ancestor of {label}"
    return full, None


def check_evidence(tree, top, rows, worktree, runs, head=None, label="HEAD"):
    """A. Every row: at least one checked reference; every reference valid.
    A row whose cell starts with "LOCAL-ONLY:" carries evidence
    from another repository that this check cannot re-verify: it must cite
    the governance record holding the raw evidence (file:), and it is reported as
    LOCAL-ONLY, never as verified. For a unit home, `tree`/`top` are the
    unit's and `head` is its fetched remote ref's commit (strict ancestry
    is judged against it). Returns (errors, local-only row IDs)."""
    errs, local = [], []
    if head is None:
        rc, head, _ = git(["rev-parse", "HEAD"], top)
        if rc != 0:
            fail_closed("cannot resolve HEAD")
        head = head.strip()
    for a, cell in rows:
        refs = REF_RE.findall(cell)
        if cell.startswith("LOCAL-ONLY:"):
            local.append(a)
            if not any(k == "file" for k, _ in refs):
                errs.append(f"{a} evidence: LOCAL-ONLY row must cite the record holding the raw evidence (file:)")
        if not refs:
            errs.append(f"{a} evidence: no checked reference (commit:, run:<id>@<sha>, file:)")
        for kind, val in refs:
            val = val.rstrip(",;.)")
            tag = f"{a} evidence {kind}:{val}"
            if kind == "commit":
                _, why = check_sha(top, head, val, worktree, label)
                if why:
                    errs.append(f"{tag}: {why}")
            elif kind == "file":
                if not tree.exists(val):
                    errs.append(f"{tag}: file not in the judged tree")
            else:
                m = re.match(r"^(\d+)@(\S+)$", val)
                if not m:
                    errs.append(f"{tag}: malformed (expected run:<id>@<sha>)")
                    continue
                full, why = check_sha(top, head, m.group(2), worktree, label)
                if why:
                    errs.append(f"{tag}: {why}")
                    continue
                data, why = runs.get(m.group(1))
                if why:
                    errs.append(f"{tag}: run lookup FAILED: {why}")
                elif data.get("conclusion") != "success":
                    errs.append(f"{tag}: run conclusion is {data.get('conclusion')!r}, not 'success'")
                elif data.get("head_sha") != full:
                    errs.append(f"{tag}: run head_sha {str(data.get('head_sha'))[:12]} != cited {full[:12]}")
    return errs, local


def report(qid, inv, ev, evidence_checked, rows, where=""):
    errs = inv + ev
    what = "invariants I1-I7" + (" + acceptance evidence" if evidence_checked else " (evidence unchanged in range)")
    local = [a for a, cell in rows if cell.startswith("LOCAL-ONLY:")]
    if local:
        what += (f"; {len(local)} LOCAL-ONLY row(s) {', '.join(local)}: record presence checked only,"
                 " the underlying project evidence is NOT re-verified by this check")
    print(f"CLOSE {qid}: {'PASS' if not errs else 'FAIL'} ({what}{where})")
    for e in errs:
        print(f"  - {e}")
    return not errs


def unit_clone(top, unit, path):
    """The unit's local clone: --unit <path> or the sibling ../<name> of this
    repository. Returns (unit top, owner/name for run lookups)."""
    name = unit.rsplit("/", 1)[-1]
    path = path or os.path.join(os.path.dirname(top), name)
    if not os.path.isdir(path):
        fail_closed(f"unit clone {path!r} not found (pass --unit <path>)")
    rc, utop, err = git(["rev-parse", "--show-toplevel"], path)
    if rc != 0:
        fail_closed(f"unit clone {path!r} is not a git worktree: {err}")
    utop = utop.strip()
    if os.path.normcase(os.path.abspath(utop)) == os.path.normcase(os.path.abspath(top)):
        fail_closed("the unit clone is this repository")
    if "/" in unit:
        return utop, unit
    rc, url, err = git(["remote", "get-url", "origin"], utop)
    m = GITHUB_URL_RE.search(url.strip()) if rc == 0 else None
    if not m:
        fail_closed(f"cannot derive owner/name for {unit!r} from the unit's origin URL "
                    f"({url.strip() if rc == 0 else err}); name the home as owner/name:<path>")
    if m.group(1).rsplit("/", 1)[-1] != name:
        fail_closed(f"unit {unit!r} but the clone's origin is {m.group(1)}")
    return utop, m.group(1)


def close_unit_home(top, tree, qid, unit, ppath, opts):
    """Unit-home close: governance side from `tree`; PROGRESS and
    evidence from the unit at a fetched remote ref. Returns PASS as bool."""
    ctext = tree.read(f"work/{qid}/CONTRACT.md") or ""
    m = REPO_LINE_RE.search(ctext)
    if not m:
        fail_closed(f"work/{qid}/CONTRACT.md names a unit home but has no '- Repository:' line")
    if m.group(1).rsplit("/", 1)[-1] != unit.rsplit("/", 1)[-1]:
        fail_closed(f"work/{qid}/CONTRACT.md: Repository line names {m.group(1)!r}, Home line names {unit!r}")
    ref = opts["--unit-ref"]
    if not ref.startswith("origin/") or ref == "origin/":
        fail_closed(f"--unit-ref {ref!r}: the unit is read at a remote ref origin/<branch>, never a local ref")
    utop, slug = unit_clone(top, unit, opts["--unit"])
    if opts["--fetch"]:
        rc, _, err = git(["fetch", "--quiet", "origin"], utop)
        if rc != 0:
            fail_closed(f"fetch failed in the unit {utop}: {err}")
    rc, uhead, _ = git(["rev-parse", "--verify", "-q", f"{ref}^{{commit}}"], utop)
    if rc != 0 or not uhead.strip():
        fail_closed(f"{ref} does not resolve in the unit clone {utop}" + ("" if opts["--fetch"] else " (try --fetch)"))
    uhead = uhead.strip()
    utree = Tree(utop, False, ref)
    inv, rows = check_invariants(tree, qid, utree, ppath)
    ev, _ = check_evidence(utree, utop, rows, False, Runs(slug), uhead, ref)
    where = (f"; unit home {slug}:{ppath} at {ref}@{uhead[:8]}"
             f" ({'fetched' if opts['--fetch'] else 'not fetched: ref as last fetched'}),"
             " evidence resolved in the unit")
    return report(qid, inv, ev, True, rows, where)


def cmd_close(argv):
    qid, all_items, worktree, base = None, False, False, None
    opts = {"--fetch": False, "--unit": None, "--unit-ref": "origin/main"}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--all":
            all_items = True
        elif a == "--worktree":
            worktree = True
        elif a == "--fetch":
            opts[a] = True
        elif a in ("--base", "--unit", "--unit-ref"):
            if i + 1 >= len(argv):
                fail_closed(f"{a} needs a value" + (" (may be empty)" if a == "--base" else ""))
            if a == "--base":
                base = argv[i + 1]
            else:
                opts[a] = argv[i + 1]
            i += 1
        elif a.startswith("--") or qid is not None:
            fail_closed(f"unexpected argument {a!r}")
        else:
            qid = a
        i += 1
    top, runs = repo_top(), Runs()
    unit_args = opts["--fetch"] or opts["--unit"] is not None or opts["--unit-ref"] != "origin/main"
    if not all_items:
        if qid is None or not QID_RE.match(qid) or base is not None:
            fail_closed("usage: close Q-NNN [--worktree] [--fetch] [--unit <path>] [--unit-ref origin/<branch>]"
                        "  |  close --all --base <sha|''>")
        tree = Tree(top, worktree)
        home = unit_home(tree.read(f"work/{qid}/CONTRACT.md"))
        if home is not None:
            return 0 if close_unit_home(top, tree, qid, home[0], home[1], opts) else 1
        if unit_args:
            fail_closed(f"--fetch / --unit / --unit-ref apply to a unit-home contract; work/{qid}/CONTRACT.md names none")
        inv, rows = check_invariants(tree, qid)
        ev, _ = check_evidence(tree, top, rows, worktree, runs)
        return 0 if report(qid, inv, ev, True, rows) else 1
    if qid is not None or worktree or base is None or unit_args:
        fail_closed("usage: close --all --base <sha|''> (HEAD tree only; no unit options)")
    rc, listing, err = git(["ls-tree", "-r", "--name-only", "HEAD", "--", "work/"], top)
    if rc != 0:
        fail_closed(f"cannot list work/ at HEAD: {err}")
    progress = [p for p in listing.splitlines() if p.endswith("/PROGRESS.md")]
    changed = None
    if base and git(["rev-parse", "--verify", "-q", f"{base}^{{commit}}"], top)[0] == 0:
        rc, out, err = git(["diff", "--name-only", "--no-renames", f"{base}..HEAD", "--", "work/"], top)
        if rc != 0:
            fail_closed(f"git diff failed: {err}")
        changed = set(out.splitlines())
        print(f"CLOSE: range {base[:12]}..HEAD; evidence re-checked where PROGRESS.md changed")
    else:
        print(f"CLOSE: base {base!r} unresolved; evidence checked for every CLOSED item")
    tree, ok, closed = Tree(top, False), True, 0
    for p in progress:
        m = re.match(r"^work/(Q-\d{3})/PROGRESS\.md$", p)
        if not m:
            print(f"CLOSE: FAIL {p}: PROGRESS.md outside a work/Q-NNN/ folder")
            ok = False
            continue
        status = fields(tree.read(p), "STATUS")
        if status not in (["OPEN"], ["CLOSED"], ["ABANDONED"]):
            print(f"CLOSE: FAIL {p}: STATUS must be one of OPEN / CLOSED / ABANDONED, found {status or 'none'}")
            ok = False
            continue
        if status != ["CLOSED"]:
            continue
        closed += 1
        inv, rows = check_invariants(tree, m.group(1))
        full = changed is None or p in changed
        ev = check_evidence(tree, top, rows, False, runs)[0] if full else []
        ok = report(m.group(1), inv, ev, full, rows) and ok
    # Unit-home items: PROGRESS lives in the unit, which this
    # repository's CI token cannot read. An item is closed here once its
    # archive row exists; the governance-side invariants I3-I5 are checked, the
    # unit side is not (verified on the owner's machine at close time).
    for p in listing.splitlines():
        m = re.match(r"^work/(Q-\d{3})/CONTRACT\.md$", p)
        home = unit_home(tree.read(p)) if m else None
        if home is None:
            continue
        qid = m.group(1)
        if f"work/{qid}/PROGRESS.md" in progress:
            print(f"CLOSE {qid}: FAIL (I1 two homes: work/{qid}/PROGRESS.md exists here and the contract names a unit home)")
            ok = False
            continue
        if not archived(tree, qid):
            continue
        closed += 1
        inv, _ = check_kos_side(tree, qid)
        print(f"CLOSE {qid}: {'PASS' if not inv else 'FAIL'} (unit home {home[0]}:{home[1]}; governance-side invariants I3-I5 only;"
              " I1, I2, I6, I7 and the acceptance evidence are verified in the unit on the owner's machine, NOT here)")
        for e in inv:
            print(f"  - {e}")
        ok = ok and not inv
    print(f"CLOSE: {closed} CLOSED item(s); RESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


# ---------------------------------------------------------------- index

# Records excepted from the pointer-only rule (ADR-2026-10-03 Decision 4):
# kept word for word in QUEUE.md until quiescent. The set is data of the
# judged tree, one ID per line in EXCEPTED_FILE ("#" comments); changing it
# is a governed edit of that file. Missing file = empty set (an excepted
# block then fails).
EXCEPTED_FILE = ".githooks/queue_excepted.txt"


def load_excepted(tree):
    text = tree.read(EXCEPTED_FILE) or ""
    return {l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith("#")}


ACTIVE_ROW = re.compile(r"^\| (Q-\d+) \| (B[1-5]|-) \| ([^|]+?) \| ([^|]+?) \|$")
BLOCKED_ROW = re.compile(r"^\| (Q-\d+) \| (B[1-5]|-) \| ([^|]+?) \| ([^|]+?) \| ([^|]+?) \|$")
TABLE_FIXED = {"ACTIVE": ("| ID | Band | Title | Home |", "|---|---|---|---|"),
               "BLOCKED": ("| ID | Band | Title | Blocker | Home |", "|---|---|---|---|---|")}


def home_of(qid):
    return "work/Q-%03d/" % int(qid[2:])


def list_homes(tree):
    if tree.worktree:
        d = os.path.join(tree.top, "work")
        return sorted(n for n in os.listdir(d) if os.path.isdir(os.path.join(d, n))) if os.path.isdir(d) else []
    rc, out, _ = git(["ls-tree", "-d", "--name-only", "HEAD", "work/"], tree.top)
    return sorted(p.split("/", 1)[1] for p in out.splitlines() if "/" in p) if rc == 0 else []


def cmd_index(argv):
    if argv not in ([], ["--worktree"]):
        fail_closed("usage: index [--worktree]")
    tree = Tree(repo_top(), argv == ["--worktree"])
    EXCEPTED = load_excepted(tree)
    text = tree.read("QUEUE.md")
    if text is None:
        print("INDEX: FAIL QUEUE.md missing")
        return 1
    errs, rows, section, block = [], {}, None, None
    for no, l in enumerate(text.splitlines(), 1):
        if l.startswith("## "):
            section, block = l[3:].strip(), None
            if section not in TABLE_FIXED:
                errs.append(f"line {no}: unexpected section {l!r} (only ## ACTIVE, ## BLOCKED)")
            continue
        if section is None:
            if l.startswith("| Q-"):
                errs.append(f"line {no}: index row before ## ACTIVE")
            continue
        if l.startswith("### "):
            m = re.match(r"^### (Q-\d+) — excepted record$", l)
            if section != "ACTIVE" or not m or m.group(1) not in EXCEPTED:
                errs.append(f"line {no}: {l!r} is not an allowed excepted block ({', '.join(sorted(EXCEPTED))} in ACTIVE)")
                block = "?"
            else:
                block = m.group(1)
                rows.setdefault(("block", block), no)
            continue
        if block:
            h = re.match(r"^(Q-\d+) \[", l)
            if h and h.group(1) != block:
                errs.append(f"line {no}: record header {h.group(1)} inside the {block} excepted block")
            if h and h.group(1) == block:
                rows[("header", block)] = no
            continue
        if not l.strip() or l in TABLE_FIXED.get(section, ()):
            continue
        m = (ACTIVE_ROW if section == "ACTIVE" else BLOCKED_ROW).match(l)
        if not m:
            errs.append(f"line {no}: not an index row of {section} (pointer-only rule): {l[:60]!r}")
            continue
        qid, title, home = m.group(1), m.group(3), m.group(m.lastindex)
        if qid in rows:
            errs.append(f"line {no}: {qid} listed twice")
        rows[qid] = (section, home, no)
        if len(title) > 100:
            errs.append(f"line {no}: {qid} title longer than 100 characters")
        if qid in EXCEPTED:
            if home != "QUEUE.md":
                errs.append(f"line {no}: excepted {qid} home must be QUEUE.md")
        elif home != home_of(qid):
            errs.append(f"line {no}: {qid} home {home!r} must be {home_of(qid)}")
        elif not (tree.exists(home + "RECORD.md") or tree.exists(home + "CONTRACT.md")):
            errs.append(f"line {no}: {qid} home {home} has no RECORD.md or CONTRACT.md")
    ids = {k for k in rows if isinstance(k, str)}
    for q in EXCEPTED:
        has_row, has_block = q in ids, ("block", q) in rows
        if has_row != has_block or (has_block and ("header", q) not in rows):
            errs.append(f"excepted {q}: row, '### {q} — excepted record' block and its record header must exist together")
    archive = tree.read("archive/queue/INDEX.md") or ""
    archived = {int(m) for m in re.findall(rf"^\|\s*{ID_PREFIX}0*(\d+)\s*\|", archive, re.MULTILINE)}
    for q in sorted(ids):
        if int(q[2:]) in archived:
            errs.append(f"{q} is in the index and also closed in archive/queue/INDEX.md")
    backlog = tree.read("BACKLOG.md") or ""
    indexed = {int(q[2:]) for q in ids}
    for name in list_homes(tree):
        if not QID_RE.match(name):
            errs.append(f"work/{name}/: folder name is not Q-NNN")
            continue
        n = int(name[2:])
        if n not in indexed and n not in archived and not re.search(rf"\b{ID_PREFIX}0*{n}\b", backlog):
            errs.append(f"work/{name}/: orphan home (not indexed, not archived, not in BACKLOG.md)")
    act = sum(1 for q in ids if rows[q][0] == "ACTIVE")
    print(f"INDEX: {act} ACTIVE, {len(ids) - act} BLOCKED; RESULT: {'PASS' if not errs else 'FAIL'}")
    for e in errs:
        print(f"  - {e}")
    return 0 if not errs else 1


# ----------------------------------------------------------- publicsafe

def load_patterns(path):
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        fail_closed(f"cannot read pattern file {path!r}: {e}")
    pats = []
    for no, l in enumerate(text.splitlines(), 1):
        s = l.strip()
        if not s or s.startswith("#"):
            continue
        if ": " not in s:
            fail_closed(f"{path}:{no}: expected 'label: regex'")
        label, rx = s.split(": ", 1)
        try:
            pats.append((label, re.compile(rx)))
        except re.error as e:
            fail_closed(f"{path}:{no}: bad regex: {e}")
    if not pats:
        fail_closed(f"{path}: no patterns")
    return pats


def staged_items(top):
    rc, names, err = git(["diff", "--cached", "--name-only", "--no-renames"], top)
    if rc != 0:
        fail_closed(f"git diff failed: {err}")
    items = [(f"path {n}", n) for n in names.splitlines() if n]
    rc, diff, err = git(["diff", "--cached", "-U0", "--no-color", "--no-renames"], top)
    if rc != 0:
        fail_closed(f"git diff failed: {err}")
    cur = "?"
    for l in diff.splitlines():
        if l.startswith("+++ "):
            cur = l[6:] if l.startswith("+++ b/") else l[4:]
        elif l.startswith("+") and not l.startswith("+++"):
            items.append((f"staged {cur}", l[1:]))
    return items


def tree_items(top, at=None):
    """Tracked paths and their content: the index (default) or, with
    at=<sha> (exact-snapshot binding), that commit's
    tree only, so an index or working-tree change cannot alter the
    verdict for a pushed SHA."""
    if at:
        rc, out, err = git(["ls-tree", "-r", "-z", "--name-only", at], top)
    else:
        rc, out, err = git(["ls-files", "-z"], top)
    if rc != 0:
        fail_closed(f"git {'ls-tree' if at else 'ls-files'} failed: {err}")
    items = []
    for n in [x for x in out.split("\0") if x]:
        items.append((f"path {n}", n))
        try:
            blob = subprocess.run(["git", "show", f"{at or ''}:{n}"], cwd=top, capture_output=True).stdout
        except OSError as e:
            fail_closed(f"cannot read {n}: {e}")
        if b"\0" in blob:
            continue
        for no, l in enumerate(blob.decode("utf-8", "replace").splitlines(), 1):
            items.append((f"{n}:{no}", l))
    return items


def message_items(top, at=None):
    """Commit messages of every ref (default) or, with at=<sha>, only the
    history reachable from that commit (exact-snapshot binding)."""
    rc, out, err = git(["log", at if at else "--all", "--format=%h%x1f%B%x1e"], top)
    if rc != 0:
        if "does not have any commits" in err:
            return []
        fail_closed(f"git log failed: {err}")
    items = []
    for rec in out.split("\x1e"):
        if "\x1f" not in rec:
            continue
        h, body = rec.strip("\n").split("\x1f", 1)
        items += [(f"commit {h} message", l) for l in body.splitlines()]
    return items


def cmd_publicsafe(argv):
    """Public-safe scan for a PUBLIC-DESTINATION repository.
    The pattern list is private (kept in the governance repository) and is
    passed by path; the scanned repository never holds it."""
    patterns, modes, msgfile, at = None, set(), None, None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--staged", "--tree", "--messages"):
            modes.add(a[2:])
        elif a in ("--patterns", "--message-file", "--at"):
            if i + 1 >= len(argv):
                fail_closed(f"{a} needs a value")
            if a == "--patterns":
                patterns = argv[i + 1]
            elif a == "--at":
                at = argv[i + 1]
            else:
                msgfile = argv[i + 1]
                modes.add("message-file")
            i += 1
        else:
            fail_closed(f"unknown argument {a!r}")
        i += 1
    if patterns is None or not modes:
        fail_closed("usage: publicsafe --patterns FILE [--at SHA] (--staged | --tree [--messages] | --message-file F)")
    if at and "staged" in modes:
        fail_closed("--at binds --tree/--messages to a commit; it does not apply to --staged")
    pats = load_patterns(patterns)
    top = repo_top()
    if at:
        # Exact-snapshot binding: resolve once, scan
        # that commit only, name it in the result line.
        rc, full, err = git(["rev-parse", "--verify", "-q", f"{at}^{{commit}}"], top)
        if rc != 0 or not full.strip():
            fail_closed(f"--at {at!r} does not resolve to a commit")
        at = full.strip()
    items = []
    if "staged" in modes:
        items += staged_items(top)
    if "tree" in modes:
        items += tree_items(top, at)
    if "messages" in modes:
        items += message_items(top, at)
    if "message-file" in modes:
        try:
            with open(msgfile, encoding="utf-8") as f:
                text = f.read()
        except OSError as e:
            fail_closed(f"cannot read message file {msgfile!r}: {e}")
        items += [("commit message", l) for l in text.splitlines() if not l.startswith("#")]
    hits = [(label, where, line) for where, line in items for label, rx in pats if rx.search(line)]
    print(f"PUBLICSAFE: {', '.join(sorted(modes))}{' at ' + at if at else ''}; "
          f"{len(items)} item(s) scanned, {len(pats)} pattern(s); "
          f"RESULT: {'PASS' if not hits else 'BLOCK'}")
    for label, where, line in hits[:50]:
        print(f"  - HIT [{label}] {where}: {line.strip()[:100]}")
    if len(hits) > 50:
        print(f"  - ... {len(hits) - 50} more")
    return 1 if hits else 0


def cmd_verify_auth(argv):
    """Owner-vs-agent check of an AUTH-introducing commit.
    Mechanical rule: the commit's SSH signature verifies against an OWNER-ONLY allowed-signers
    file AND the author email is the owner's. Without a signers file the check is INERT and says
    so: custody boundary absent (13 §8 D-2 is an owner decision), provenance documentary.
    Exit 0 VERIFIED or INERT; 1 REJECTED; 2 usage / unknown commit."""
    repo, owner, signers, commit = ".", None, None, None
    it = iter(argv)
    for a in it:
        if a == "--repo":
            repo = next(it, None)
        elif a == "--owner-email":
            owner = next(it, None)
        elif a == "--signers":
            signers = next(it, None)
        elif a.startswith("--"):
            fail_closed(f"verify-auth: unknown argument {a!r}")
        else:
            commit = a
    if not commit or not repo:
        fail_closed("usage: check_work.py verify-auth [--repo PATH] [--owner-email E] [--signers FILE] <commit>")
    rc, out, _ = git(["log", "-1", "--format=%H%n%an <%ae>%n%cn <%ce>%n%G?", commit], cwd=repo)
    if rc != 0 or not out.strip():
        fail_closed(f"verify-auth: commit {commit!r} not found in {repo}")
    sha, author, committer, sigstat = out.rstrip("\n").split("\n")[:4]
    if owner is None:
        rc, o, _ = git(["config", "--get", "kos.ownerEmail"], cwd=repo)
        owner = o.strip() if rc == 0 and o.strip() else None
    if signers is None:
        rc, o, _ = git(["config", "--get", "kos.ownerSignersFile"], cwd=repo)
        signers = o.strip() if rc == 0 and o.strip() else None
    facts = f"commit {sha[:8]}; author {author}; committer {committer}; signature status {sigstat or '-'}"
    if not signers or not owner:
        print(f"AUTH CHECK INERT: custody boundary absent (no owner-only signers file / owner email configured; "
              f"13 §8 D-2 is an owner decision); provenance is documentary. {facts}")
        return 0
    if not os.path.isfile(signers):
        fail_closed(f"verify-auth: signers file {signers!r} not found")
    rc, _, err = git(["-c", "gpg.format=ssh", "-c", f"gpg.ssh.allowedSignersFile={signers}", "verify-commit", sha], cwd=repo)
    author_email = author.rsplit("<", 1)[-1].rstrip(">")
    reasons = []
    if rc != 0:
        reasons.append(f"signature does not verify against the owner-only signers file ({err.splitlines()[-1] if err else 'no signature'})")
    if author_email.lower() != owner.lower():
        reasons.append(f"author email {author_email} is not the owner's ({owner})")
    if reasons:
        print(f"AUTH CHECK REJECTED: {'; '.join(reasons)}. {facts}")
        return 1
    print(f"AUTH CHECK VERIFIED: owner-signed AUTH commit (signers file + author email). {facts}")
    return 0


def main(argv):
    if not argv:
        fail_closed("usage: check_work.py (scope | close | index | publicsafe | verify-auth) ...")
    cmd, rest = argv[0], argv[1:]
    if cmd == "scope":
        return cmd_scope(rest)
    if cmd == "close":
        return cmd_close(rest)
    if cmd == "index":
        return cmd_index(rest)
    if cmd == "publicsafe":
        return cmd_publicsafe(rest)
    if cmd == "verify-auth":
        return cmd_verify_auth(rest)
    fail_closed(f"unknown subcommand {cmd!r}")


if __name__ == "__main__":
    # Record text carries non-ASCII (→, —, §); a cp1252 console must not crash the report.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(main(sys.argv[1:]))
