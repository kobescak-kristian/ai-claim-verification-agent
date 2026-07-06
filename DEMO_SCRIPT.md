# DEMO_SCRIPT — ai-claim-verification-agent

Budget: 3–5 min (runs alongside the Reliability demo, not instead of it).
Register: concrete evidence. Default = replay local mp4;
live run = optional flourish ONLY if offline-rehearsed.

---

## Before leaving home (checklist)

- [ ] Local clone at public HEAD, venv ready, dependencies installed.
- [ ] Claude CLI logged in via subscription (`claude auth login` done;
      no ANTHROPIC_API_KEY anywhere in the environment).
- [ ] Test run completed on THIS laptop, THIS day:
      `python run_case.py case_09_mixed_multi_claim_router` → verdict table
      with 2 CONTRADICTED, 1 UNVERIFIABLE, 1 SUPPORTED.
- [ ] Backup mp4 (Win+G) of that exact run, saved locally, tested it plays.
- [ ] Terminal font large; notifications off.
- [ ] NOTE: live run needs internet + subscription auth. If either is
      uncertain in the room → mp4 only, no live attempt.

## Core demo (4 min)

### Step 1 — Frame (30 sec)
SAY: "The engines you saw are pipelines — fixed steps. This one is an
agent: the model chooses its own next step. That's exactly why it's the
most dangerous kind of AI system, so the point of this project is the cage
around it."

### Step 2 — The cage (1 min)
DO: open README, show the architecture diagram.
SAY: "The model gets exactly four read-only tools. It can't write, edit,
or reach outside the dataset folder — enforced by the harness, not by a
promise in the docs. Every tool call is logged to SQLite before the model
even sees the result."

### Step 3 — The run (1.5 min)
DO: play the mp4 (or live run if rehearsed + connectivity confirmed).
SAY while table appears: "It reads a product page, pulls the factual
claims, checks each against source pages, and logs a verdict. Here it
caught two false claims — a wrong price and a wrong port count — and
flagged one claim no source could confirm."
POINT AT LAST LINE: "Run cost and turn count printed on every run —
the caps are part of the design."

### Step 4 — The proof (1 min)
DO: open evals/EVAL_RESULTS.md, top table.
SAY: "The pass thresholds were committed to the repo before I wrote any
agent code — git timestamps prove I couldn't move the goalposts. Official
run: all 35 claims correct, including the traps. And the limitations
section says plainly what this does NOT prove — it's synthetic data,
not live web pages."
SAY (the line): "Done means checked, not claimed."

## Kill rules

- Live run fails once → mp4. No debugging in the room, no second attempt.
- Never claim "production-ready." If asked: name the gaps —
  live-web fetching, untrusted-input sanitisation, larger eval, monitoring.
- Over 5 min → jump to Step 4.
