#!/usr/bin/env python3
"""check_write_set.py — declared repository write set vs actual git-changed paths.

Compares the paths git reports as changed in the CURRENT repository (the
repo the command runs inside — the PEN repo) against a declared write set
and prints a reviewable report. Adopted 2026-09-15 (executor scope
discipline: declared writes are checked, not trusted). Pattern:
.githooks/check_map.py (declared set vs actual set, fail-closed).

Usage (canonical file; a machine alias `git check-write-set` may run it):
  python <path-to>/check_write_set.py [--exact] [--range <base>..<head>] (--none | <entry> ...)

Entries:
  path/to/file   repo-relative file (relative to the PEN repo root, '/' separators)
  path/to/dir/   trailing slash = the whole subtree
  '~/some/path'  governed write outside any repo: declared and printed as
                 JUDGMENT, never git-compared, exempt from --exact. Quote it —
                 an unquoted ~/ is expanded by bash into an absolute path.
  --none         no git-visible changed path is permitted
Malformed (exit 2, never guessed): absolute or drive paths, backslashes,
'.'/'..'/empty segments, commas, whitespace, --none combined with entries.

Actual set: staged mode (default) = git diff --cached --name-only --no-renames;
range mode = git diff --name-only --no-renames <base>..<head>. Renames show
both paths; deletions count as changes.

Match: P == E, or E ends with '/' and P starts with E.
--exact: every declared repo FILE entry must also appear in the actual set
(directory entries and ~/ entries are never required).

Exit 0 = RESULT: PASS. Exit 1 = RESULT: BLOCK (unauthorized path, or with
--exact a missing declared file). Exit 2 = fail-closed: malformed or absent
declaration, git failure, not inside a worktree, or self-integrity failure —
this file untracked or modified in ITS OWN repository (checked independently
of the repo under test; the CHECKER line is this file's last commit there).
No bootstrap exception exists: an uncommitted checker always exits 2.
"""
import os
import subprocess
import sys


def fail_closed(msg):
    print(f"WRITE-SET CHECK BLOCK (fail-closed): {msg}")
    sys.exit(2)


def git(args, cwd):
    try:
        out = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)
    except OSError as e:
        fail_closed(f"cannot run git: {e}")
    return out.returncode, out.stdout, out.stderr.strip()


def checker_identity():
    """Self-integrity: this file must be committed and unmodified in its own repo."""
    path = os.path.abspath(__file__)
    rc, top, _ = git(["rev-parse", "--show-toplevel"], os.path.dirname(path))
    if rc != 0:
        fail_closed("checker file is not inside a git repository")
    top = top.strip()
    rel = os.path.relpath(path, top).replace(os.sep, "/")
    rc, status, err = git(["status", "--porcelain", "--untracked-files=all", "--", rel], top)
    if rc != 0:
        fail_closed(f"git status failed in checker repository: {err}")
    if status.strip():
        fail_closed(f"checker {rel} is untracked or modified in its own repository "
                    f"({os.path.basename(top)}); commit it first")
    rc, h, _ = git(["log", "-1", "--format=%h", "--", rel], top)
    if rc != 0 or not h.strip():
        fail_closed(f"checker {rel} has no committed history")
    return f"{os.path.basename(top)}/{rel}", h.strip()


def parse_args(argv):
    exact, rng, none, entries = False, None, False, []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--exact":
            exact = True
        elif a == "--none":
            none = True
        elif a == "--range":
            if i + 1 >= len(argv):
                fail_closed("--range needs <base>..<head>")
            rng = argv[i + 1]
            i += 1
        elif a.startswith("--"):
            fail_closed(f"unknown option {a}")
        else:
            entries.append(a)
        i += 1
    if none and entries:
        fail_closed("--none cannot be combined with entries")
    if not none and not entries:
        fail_closed("no declaration given: pass --none or at least one entry")
    if rng is not None and (".." not in rng or rng.startswith("..") or rng.endswith("..")):
        fail_closed(f"--range must be <base>..<head>, got {rng!r}")
    return exact, rng, none, entries


def classify(entries):
    """Split declarations into repo entries (git-checked) and ~/ entries (JUDGMENT)."""
    repo, home, seen = [], [], set()
    for e in entries:
        if e in seen:
            continue
        seen.add(e)
        if not e or "," in e or "\\" in e or any(ch.isspace() for ch in e):
            fail_closed(f"malformed entry {e!r}: no commas, backslashes or whitespace")
        if e.startswith("~/"):
            if len(e) < 3:
                fail_closed(f"malformed entry {e!r}: empty home path")
            home.append(e)
            continue
        if e.startswith("/") or e.startswith("~") or (len(e) > 1 and e[1] == ":"):
            fail_closed(f"malformed entry {e!r}: absolute, drive or non-repo path "
                        "(use a repo-relative path, or quote a '~/' home entry)")
        if any(p in ("", ".", "..") for p in e.rstrip("/").split("/")):
            fail_closed(f"malformed entry {e!r}: empty, '.' or '..' segment")
        repo.append(e)
    return repo, home


def actual_paths(rng):
    rc, top, _ = git(["rev-parse", "--show-toplevel"], os.getcwd())
    if rc != 0:
        fail_closed("not inside a git worktree")
    top = top.strip()
    if rng is None:
        args, mode = ["diff", "--cached", "--name-only", "--no-renames"], "staged"
    else:
        base, head = rng.split("..", 1)
        for r in (base, head):
            rc, _, _ = git(["rev-parse", "--verify", "-q", r + "^{commit}"], top)
            if rc != 0:
                fail_closed(f"range endpoint {r!r} is not a commit")
        args, mode = ["diff", "--name-only", "--no-renames", f"{base}..{head}"], f"range {rng}"
    rc, out, err = git(args, top)
    if rc != 0:
        fail_closed(f"git diff failed: {err}")
    return top, mode, [l.strip() for l in out.splitlines() if l.strip()]


def join(xs):
    return ", ".join(xs) if xs else "-"


def main(argv):
    exact, rng, none, entries = parse_args(argv)
    repo_entries, home_entries = ([], []) if none else classify(entries)
    checker, chash = checker_identity()
    top, mode, actual = actual_paths(rng)

    files = [e for e in repo_entries if not e.endswith("/")]
    dirs = [e for e in repo_entries if e.endswith("/")]
    unauthorized = [p for p in actual if p not in files and not any(p.startswith(d) for d in dirs)]
    missing = [e for e in files if e not in actual] if exact else []

    print(f"CHECKER: {checker} @ {chash}")
    print(f"REPO: {os.path.basename(top)}")
    print(f"MODE: {mode}")
    print(f"DECLARED git-checked ({len(repo_entries)}): {'NONE' if none else join(repo_entries)}")
    print(f"DECLARED not git-checked, JUDGMENT ({len(home_entries)}): {join(home_entries)}")
    print(f"ACTUAL ({len(actual)}): {join(actual)}")
    print(f"UNAUTHORIZED ({len(unauthorized)}): {join(unauthorized)}")
    if exact:
        print(f"MISSING ({len(missing)}): {join(missing)}")
    ok = not unauthorized and not missing
    print(f"RESULT: {'PASS' if ok else 'BLOCK'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
