# Preflight: Standard eval v2 official series

**Status: preflight run 2026-11-09 at `681da69`.** Checks 5 to 9
PASS. Checks 1 to 4 PASS on the machine state a session can read;
their launch conditions are Kristian's to confirm in the plain terminal
before the series. Check 10 is an accepted limit by Kristian's ruling.
The official series has not started. The preflight is valid for a
series started on 2026-11-09 only.

## 1. Frozen references

| Item | Value | How it is verified |
|---|---|---|
| Frozen agent commit | `e9b4b96201af9c37655d4bb75c9b43df2ce2d220` | `git diff` from it on the frozen paths is empty (check 8) |
| Gate | `evals/eval_config_v2.yaml` at `0f0c320`, config SHA-256 `4f1050772b9fabb96bf48e9f70067011f06ac74b907fa6be57e2507c1445676f` | Check 8 (empty diff from `0f0c320`); the runner records `config_sha256` (LF-normalized) in every results file for comparison |
| Hold-out seal | SHA-256 over the ZIP's raw bytes: `d60d0c14465a896ef85f64245114086c0dfb36188e824e956d78dc4169140e37` | Plain SHA-256 of the ZIP file as it is on disk, before it is opened |
| Official answer key | `evals/answer_keys/official.json`, LF-normalized SHA-256: `d98f92bfb366c36d4bdee933698395772ed851c106b8b60d13a4574f2d67114b` | `score_v2.key_sha256` (every CR LF becomes LF, then SHA-256), after extraction; the runner refuses a key that differs from `--key-sha256` |

The two hashes cover different things and are never compared with each
other (`evals/eval_config_v2.yaml`, `results.integrity`).

## 2. Already verified by the writer session (2026-11-09)

An independent session wrote and sealed the hold-out at `e9b4b96`. It
never builds. Its report, not re-checked by any build session:

- 61 pages and 197 claims; verdict mix 121 SUPPORTED, 47 CONTRADICTED,
  29 UNVERIFIABLE; 222 files in the ZIP.
- Blind relabel (pages only, no answer key): 197/197 agreement on
  verdicts and sources.
- `make_dataset.py check` passed on the official and the dev key;
  `pytest tests/` 387 passed, 2 skipped, 0 real process starts; Tier 0
  PASS; the public-safe patterns found nothing.
- The ZIP was verified against its checked set and against a backup
  copy. The backup is on the same disk: it protects against accidental
  loss, not disk failure.

None of this replaces the checks below.

## 3. Still required

### At extraction (writer session, before the hold-out commit, which the gate calls the freeze commit)

| # | Check | Result |
|---|---|---|
| E1 | Seal SHA-256 of the ZIP equals the value in section 1, computed before the ZIP is opened. A mismatch stops the step | PASS 2026-11-09: primary and backup ZIP both equal the section 1 value |
| E2 | Only `evals/dataset/h001` to `h061` and `evals/answer_keys/official.json` are extracted | PASS 2026-11-09: 222 entries, all inside the allowed set (221 page files in exactly 61 folders `h001` to `h061`, 1 key); no unsafe path, duplicate or symlink; CRC test OK; 222 files written, no other file changed |
| E3 | `score_v2.key_sha256` of the extracted key equals the value in section 1 | PASS 2026-11-09: equal |
| E4 | `make_dataset.py check` on both keys, `pytest tests/` and Tier 0 pass at the repository root | PASS 2026-11-09: official check exit 0, 61 cases, 197 claims; dev check exit 0, 12 cases, 35 claims; `pytest tests/` 388 passed, exit 0; `Tier 0: PASS` |

### Preflight (run session, same day as the official series)

| # | Check | Result |
|---|---|---|
| 1 | Test setup is ours: no Claude Code session, including the one that ran this preflight, makes tool calls on this machine during the series; nothing else uses this checkout or `audit.db`; nothing opens the results file or `audit.db` while the series runs; one runner process only | PASS (machine state): no Python process running; `audit.db` last written 2026-11-06 11:55, the dry run; `evals/results/v2/official/` absent. Open at the preflight: 7 Claude Code CLI processes (6 under the desktop app, this preflight session included; 1 standalone `.local\bin\claude.exe` since 2026-11-06 14:08). LAUNCH: Kristian closes all of them, then starts one runner |
| 2 | Credentials: subscription login works; the guarded variables are absent; `CLAUDECODE` is unset; `CLAUDE_CODE_USE_FOUNDRY`, `CLAUDE_CODE_USE_MANTLE`, `CLAUDE_CODE_USE_ANTHROPIC_AWS` and `CLAUDE_CODE_USE_GATEWAY` are unset in the plain terminal | PASS: the SDK's bundled CLI, with `CLAUDECODE` and `ANTHROPIC_BASE_URL` removed, reports `loggedIn` true, `authMethod` claude.ai, `apiProvider` firstParty, subscription max. None of the 9 guarded and provider names, nor `CLAUDECODE`, is set at User or Machine scope. A terminal opened inside the Claude desktop app inherits `CLAUDECODE` and `ANTHROPIC_BASE_URL`; the runner refuses there (check 6). LAUNCH: Kristian runs the section 7 name check in the plain terminal |
| 3 | Subscription limits allow 183 executions plus one page rerun (186). Estimate, inferred from `ev2-2046a87c` (36 executions, 19m 12s, $1.3120): about 1h 40m and $6.80 SDK API-equivalent, not a charge. Hold-out pages may differ | PASS (login): subscription max, logged in. A session cannot read the remaining limit. LAUNCH: Kristian checks the usage page shows room for about 1h 40m of runs; a limit mid-series stops it as INVALID RUN |
| 4 | No change during the series: no deploy of machine-wide managed settings or hooks, no Claude CLI or SDK upgrade; AC power on, Windows Update paused, no sleep or hibernate, no other work in that terminal | PASS (machine state): on AC, battery 100%; sleep and hibernate on AC: never; Windows Update paused until 2026-11-11 07:59 UTC; no `core/kos_core.py` commit in the governance repository since 2026-11-06, and the planned hook repair has no work folder yet. LAUNCH: no deploy or upgrade starts; the terminal is used only for the series |
| 5 | Dry run: see section 4. No Claude CLI, SDK or managed-settings change since that run; if there was one, a new 2-case dev dry run first | PASS, no new dry run needed. The runner starts the SDK's bundled `claude.exe` 2.1.191 (the SDK picks the bundled binary first; the harness sets no `cli_path`), SHA-256 `83397a6a029c7da663fb1ce27211e05174a3546d8b151e42451bf4590b8343d7`, file date 2026-07-06. `claude-agent-sdk` 0.2.110 installed 2026-07-06; no site-packages folder changed after 2026-11-06 10:00; Python 3.12.0. `managed-settings.json` SHA-256 `6e39ca73164ff66810d8bb7642915a53ef17258029ee1b76ac2f084adabe07ae` (2026-11-02); its hook script `kos_core.py` SHA-256 `b087edbc227bd20d2d68b579b74b099dbe121964bb327e06031829dfc750fb0b` (2026-11-04). All predate the dry run (2026-11-06 10:54 UTC). Changed since: the standalone CLI on PATH, 2.1.317 to 2.1.319 at 2026-11-06 14:09; the runner does not start it |
| 6 | No blueprint exists, so blueprint defaults are not applicable; the env-guard refusal is shown by hand; the dependency pins are exact but not hashed (a limit) | PASS: no blueprint, not applicable. Refusal shown 2026-11-09 with `--set dev --cases case_01 --runs 1`: inside Claude Code `Refused: CLAUDECODE is set`, exit 2; with `CLAUDECODE` removed `Refused: AuthGuardError ... ANTHROPIC_BASE_URL`, exit 2; no results file written. Pins exact, not hashed: limit |
| 7 | Hashes: E1 and E3 passed and are recorded; the official command passes the section 1 key hash as `--key-sha256` | PASS: E1 and E3 recorded above; `score_v2.key_sha256` of the official key recomputed 2026-11-09 equals section 1; section 7 passes that value. Rerun of E4 on 2026-11-09: official check 61 cases, 197 claims, exit 0; dev check 12 cases, 35 claims, exit 0; `pytest tests/` 388 passed, exit 0; `Tier 0: PASS` |
| 8 | Frozen state: both commands print nothing: `git diff --stat 0f0c320 HEAD -- evals/eval_config_v2.yaml evals/score_v2.py evals/run_eval.py evals/answer_keys/dev.json` and `git diff --stat e9b4b96 HEAD -- agent evals/run_eval_v2.py evals/score_v2.py evals/make_dataset.py evals/eval_config_v2.yaml evals/eval_config.yaml evals/ground_truth.json tests requirements.txt` (`evals/eval_config.yaml` holds the comparison policy the agent's prompt loads); `git status` shows only `.claude/` | PASS: both diffs empty at `681da69` (tree `889a180`); `git status` shows only `.claude/`; LF-normalized SHA-256 of the gate equals section 1 |
| 9 | No earlier official series: `evals/results/v2/official/` holds no file; on a redone preflight before `--resume`, it holds only the series being resumed. Any other file means the hold-out is exposed, and any PASS must be labelled "regression PASS on exposed hold-out" by hand (limit RT2-04) | PASS: `evals/results/v2/official/` does not exist; no tracked file under it |
| 10 | Live PostToolUseFailure row: never seen live; deterministic tests cover the row shape. Kristian rules here whether a live check is required before the official series | ACCEPTED LIMIT, Kristian 2026-11-09: "I approve proceeding without a live PostToolUseFailure demonstration." No frozen code change, no manufactured tool error. Support: the deterministic tests, and `audit.db` across 39 v2 runs: 333 PreToolUse rows, each closed by PostToolUse, 0 PostToolUseFailure rows, 0 error-like responses. A missing closing row makes the run incomplete and fails the page under rule E: toward FAIL, never a false PASS |

## 4. Development dry run (evidence)

Run `ev2-8f2712a4` (`evals/results/v2/dev/`), 2026-11-06, at `e9b4b96`,
before any hold-out existed: state complete, 2/2 executions, both start-up
messages with `apiKeySource` none and exactly the 4 tools, model
`claude-sonnet-4-6`, `git_dirty` false. The record has no exit-code field;
the operator's terminal printed `runner exit: 0`.

## 5. Named limits from the second red team

These were found before any official result and accepted without a code
change. The safeguard is a preflight check where one exists.

| Finding | What can happen | Safeguard |
|---|---|---|
| RT2-02 | Any process end during an execution (Ctrl+C, closed terminal, power loss, reboot) abandons the whole series; a new series is an exposed rerun. Ruled: the first official run is not redefined | Checks 1 and 4 |
| RT2-03 | A file lock on the results file or a busy `audit.db` crashes the runner after a good execution, which then ends as RT2-02 | Check 1 |
| RT2-04 | A second official series prints PASS with no sign that the hold-out was exposed | Check 9 |
| RT2-05 | A failed audit write inside a hook is silent: the tool still runs, the page fails under rule E, and nothing pauses | Check 1 |
| RT2-08 | Editing this tracked file after the series starts blocks `--resume` (dirty tree or changed `git_head`) | Section 6 |
| RT2-12 | SDK, CLI, Python and managed-settings versions are not recorded or compared | Checks 4 and 5 |
| RT2-13 | A machine-wide record hook runs on every tool call and can block a tool when its lock is busy, which fails the page under rule E | Check 1 |
| RT2-15 | The runner does not check that case folders exist; a missing folder fails its page under rule E | E4 |
| RT2-16 | No single-writer lock: a second terminal's `--recover` is silently overwritten | Check 1 |
| RT2-17 | The 20-turn bound means 19 tool calls, because the CLI's `num_turns` counts tool results plus 1. The largest dev pages used at most 13 calls | None; part of the test scope |
| Sweep | Real SDK process cleanup is proven live for 36 runs only; a hung CLI has no wall-clock timeout and needs a manual kill; tools can read other page folders, and a bare file name from another page passes as evidence; four provider variables are outside the env guard | Check 2 for the provider variables; none for the others |
| RT2-09 | The live PostToolUseFailure row is unproven; deterministic tests only | Check 10 |

## 6. After an INVALID RUN

The gate requires a redone preflight before `--resume` (decision 2A). Its
record goes to the private work record, not this file, until the series
ends: changing a tracked file blocks the resume (RT2-08).

## 7. Official command (only after Kristian's go)

From a plain PowerShell terminal at the repository root. First the
name check for check 2; it must print nothing:

```powershell
'CLAUDECODE','ANTHROPIC_API_KEY','ANTHROPIC_AUTH_TOKEN','ANTHROPIC_BASE_URL','CLAUDE_CODE_USE_BEDROCK','CLAUDE_CODE_USE_VERTEX','CLAUDE_CODE_USE_FOUNDRY','CLAUDE_CODE_USE_MANTLE','CLAUDE_CODE_USE_ANTHROPIC_AWS','CLAUDE_CODE_USE_GATEWAY' | Where-Object { Test-Path "env:$_" }
```

Then the series:

```powershell
python evals/run_eval_v2.py --set official --key-sha256 d98f92bfb366c36d4bdee933698395772ed851c106b8b60d13a4574f2d67114b; "runner exit: $LASTEXITCODE"
```

After the first official execution the hold-out counts as exposed,
whatever the result.
