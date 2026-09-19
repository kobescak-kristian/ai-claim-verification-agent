# AGENTS.md

Context router for coding agents working in this repository. It points to
the file that owns each topic. It does not restate current status, results
or configuration values; read the owning file instead.

## Repository purpose

ai-claim-verification-agent is a bounded claim-verification agent built on
the Claude Agent SDK (Python).

For one case from the local seeded dataset under `evals/dataset/`, it takes
a target page plus that case's source pages, extracts candidate factual
claims from the target, and lets the model investigate each claim through a
fixed whitelist of custom in-process MCP tools. The model records one
verdict per claim (SUPPORTED, CONTRADICTED or UNVERIFIABLE) with an evidence
source and a short note. The harness prints a verdict report and records
every tool call in a local SQLite audit trail.

The model chooses its investigative steps and its verdicts. Its tool surface
cannot publish, edit, send, run shell commands or make external network
calls. The writes that do happen are narrow and owned by the program, not
chosen freely by the model: `log_finding` appends to the run's in-memory
findings list, the harness audit hooks write the local audit database, and
`evals/run_eval.py` writes a result file under `evals/results/` when a full
evaluation is run.

## Authority and conflict handling

Each topic has one owning source:

| Source | Owns |
|---|---|
| `STATE.md` | Current repository state, classification, current evidence and status, open loops |
| `README.md` | Public system description, usage, claims, limitations, Version Log |
| `adr/0001-bounded-agent-v2-read-only-whitelist.md` | Accepted bounded-agent architecture and the decision to keep prompts public |
| `adr/0002-demo-script-tier-exception.md` | Legitimacy of, and trigger for, `DEMO_SCRIPT.md` in this repository |
| `agent/harness.py` | Actual Agent SDK options: model-accessible tool whitelist, built-in tool disabling, turn and cost bound wiring, audit hook registration |
| `agent/tools.py` | Model-accessible tool definitions, verdict validation, findings buffer, independent tool-call circuit breaker |
| `agent/pages.py` | Dataset path containment and local HTML reads |
| `agent/audit.py` | Audit persistence and PreToolUse/PostToolUse logging |
| `agent/prompts.py` | System prompt construction and runtime loading of the comparison policy |
| `agent/config.py` | Current runtime configuration values and tool names |
| `evals/eval_config.yaml` | Binding comparison policy, metric definitions, eval thresholds |
| `evals/EVAL_RESULTS.md` and `evals/results/` | Recorded evaluation evidence |
| `tests/test_bounds.py` and `.github/workflows/ci.yml` | Mechanically asserted bounds and current automated verification |

When sources disagree:

- An accepted ADR governs the material decision it records.
- `STATE.md` holds current state. The bounds contract is the accepted
  ADR plus the harness that enforces it.
- Implementation and config state actual runtime behaviour.
- `evals/eval_config.yaml` owns policy and threshold definitions. Committed
  eval artifacts state observed results.
- Tests and CI state what is mechanically asserted.
- Surface the disagreement. Do not silently reconcile it.
- If the requested work materially depends on an unresolved conflict, stop
  for owner review.

## Task routing

| Task | Go to |
|---|---|
| Current state, open loops | `STATE.md` |
| Product contract, scope, bounds | `README.md`, `adr/0001-bounded-agent-v2-read-only-whitelist.md` |
| Public description, usage, limitations, version history | `README.md` |
| Local setup and dependencies | `README.md` (Run It Yourself), `requirements.txt`, `.env.example` |
| Bounded-agent architecture decision | `adr/0001-bounded-agent-v2-read-only-whitelist.md` |
| Demo artifact exception | `adr/0002-demo-script-tier-exception.md`, `DEMO_SCRIPT.md` |
| Harness, Agent SDK options | `agent/harness.py` |
| Tool surface, circuit breaker, findings | `agent/tools.py` |
| Dataset path containment, HTML reads | `agent/pages.py` |
| Audit trail, hooks | `agent/audit.py` |
| Prompt and comparison-policy loading | `agent/prompts.py`, `evals/eval_config.yaml` |
| Runtime constants, tool names | `agent/config.py` |
| Evaluation policy, thresholds | `evals/eval_config.yaml` |
| Seeded case pages | `evals/dataset/` |
| Ground truth | `evals/ground_truth.json` |
| Recorded evaluation evidence | `evals/EVAL_RESULTS.md`, `evals/results/` |
| Single-case runner | `run_case.py` |
| Full evaluation | `evals/run_eval.py` |
| Bounds regression tests | `tests/test_bounds.py` |
| CI | `.github/workflows/ci.yml` |
| Documentation and artifact validation | the affected artifact, `.githooks/validate_artifacts.py` |
| Publishing | `.githooks/`, `.publicgate-allow` |

## Always-on constraints

1. **Model-accessible actions stay bounded.** The model acts only through
   the custom MCP tool whitelist named in `agent/config.py` and wired in
   `agent/harness.py`. Built-in tools stay disabled there. Do not expose
   Write, Edit, Bash or shell, unrestricted Read, or any other
   state-changing or unbounded capability without a separately authorized
   architectural decision.
2. **Investigative tools stay local and non-destructive.** `fetch_page`,
   `extract_claims` and `compare_source` read local HTML under
   `evals/dataset/`. `log_finding` records a verdict in the run's in-memory
   findings list. Do not add publishing, editing, sending, shell execution
   or external network side effects to the model-accessible tool surface
   as part of an unrelated task.
3. **Dataset path containment is an enforced bound.** Paths passed to the
   page tools must resolve inside `evals/dataset/` (`agent/pages.py`). Keep
   cross-platform escape rejection: parent traversal, absolute and
   drive-letter forms, and backslash-shaped paths. Do not weaken
   containment for convenience.
4. **Bounds are implementation controls, not documentation promises.** The
   tool whitelist and built-in disabling, the turn and cost limits, the
   tool-call circuit breaker and the audit hooks live in code and config.
   Numeric limits live in `agent/config.py`; do not copy them into this
   file. If docs and code disagree about a bound, surface it. README prose
   is not enforcement.
5. **Audit hooks remain part of the harness.** `agent/harness.py` registers
   the `agent/audit.py` hook on PreToolUse and PostToolUse, and each event
   is written to the local SQLite audit database. Do not bypass or remove
   that registration for model-accessible tool calls. The audit database
   is a harness-owned local write, separate from the read-only,
   non-destructive tool surface the model uses.
6. **The verdict is a model judgment, not a tool-computed score.**
   `extract_claims` segments candidate text. `compare_source` returns
   source content framed against a claim. Neither decides truth. The model
   applies the comparison policy and records SUPPORTED, CONTRADICTED or
   UNVERIFIABLE. Do not move hidden semantic judging into the tools without
   an authorized redesign.
7. **The comparison policy has one runtime source.** It lives in
   `evals/eval_config.yaml` and is loaded at runtime by `agent/prompts.py`.
   Do not copy policy text into this file or hard-code a second copy
   anywhere else.
8. **Ground truth stays out of the agent's evidence path.**
   `evals/ground_truth.json` is evaluation truth, not model evidence. Case
   inputs are discovered from the dataset directory, not from ground truth.
   Do not expose the file, its claim identifiers or its answer-key content
   through tool inputs, tool responses or prompt construction. Keep the
   leakage regression checks in `tests/test_bounds.py`.
9. **Eval policy and observed results are different things.**
   `evals/eval_config.yaml` defines metrics and gate thresholds; committed
   result artifacts record observed runs. Do not change thresholds,
   comparison policy, ground truth or scoring after seeing results to make
   a run pass, and do not raise caps or re-run single cases mid-evaluation.
   A deliberate eval-policy change needs its own authorized task and must
   stay distinguishable from the result it is judged against.
10. **Eval limitations remain visible.** Results come from a seeded
    synthetic dataset. Do not present them as evidence of real-world or
    live-web robustness unless that is separately evidenced. Current
    evidence and limitations live in `evals/EVAL_RESULTS.md` and
    `README.md`.
11. **`DEMO_SCRIPT.md` is a recorded exception.** ADR 0002 authorizes it.
    Do not delete or demote it as extra documentation. Do not copy demo or
    status content into this file.
12. **Public prompts are deliberate.** ADR 0001 records that the prompt
    implementation is intentionally public in this repository. Do not hide
    or remove it as generic cleanup. Reversing that needs an authorized
    material decision.
13. **Local runtime artifacts stay local.** `.env`, `audit.db`, virtual
    environments and caches are gitignored. Do not commit credentials,
    audit databases, virtual environments, caches or machine-local absolute
    paths unless a separately authorized task changes that policy.
14. **No pushed-history rewrite.** Published evidence and downstream
    references pin commit hashes. Do not amend or rebase pushed commits,
    and do not force-push.
15. **Artifact rules.** ADRs have no hard maximum; write one only for a
    genuine material decision. Version history stays in the README Version
    Log. Existing recorded exceptions such as `DEMO_SCRIPT.md` stay. If
    `.githooks/validate_artifacts.py` disagrees with current artifact
    policy, surface the disagreement. Do not merge or delete decision
    records, or change other artifacts, only to satisfy the validator
    without a separately authorized validator task.
16. **This file is guidance, not enforcement.** Enforcement lives in the
    implementation, path checks, hooks, tests, CI and publishing gates.

## Verification

Routine checks:

```bash
python .githooks/validate_artifacts.py .
python -m pytest tests/test_bounds.py -v
```

- The artifact validator must print `Tier 0: PASS`. The pre-push hook in
  `.githooks/` runs it as well.
- Most of the bounds suite runs without any agent invocation. The
  audit-history checks read an existing local `audit.db` and skip when none
  exists. Report skips honestly. Do not create or edit an audit database to
  remove them.
- `python run_case.py <case_id>` and `python evals/run_eval.py` invoke a
  live model and cost money. They are not routine verification; run them
  only when the task explicitly calls for a live run.
- CI (`.github/workflows/ci.yml`) is the authoritative cross-platform run
  of the bounds suite. Its demo job stays dormant unless an API key secret
  is configured; do not add one as part of routine work.
