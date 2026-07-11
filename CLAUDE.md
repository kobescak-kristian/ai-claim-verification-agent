# ai-claim-verification-agent

Documentation discipline: Tier 0 artifacts only unless an ADR in this
repo cites a trigger.
Do not create SYSTEM_WALKTHROUGH.md, CHANGELOG.md, RUNBOOK.md, or Tier 1/2 artifacts
without an explicit instruction citing the trigger.
ADR cap: 5. Version log lives in README, not a separate file.

## Session boot and governance (applies to every session here)
- Governance home: kristian-os (PRINCIPLES.md -> GOVERNANCE.md ->
  FAILURE_REGISTER.md). Read before any irreversible action.
- Boot: read this repo's STATE.md first; the operating contract
  (SPEC.md) loads globally.
- Before any write: environment fingerprint (pwd + git config
  user.email; /home/user/ path or noreply@anthropic.com = cloud
  sandbox = read-only, no pen). Pen check on main at open AND
  immediately before every commit.
- Eval discipline: gates and thresholds are never adjusted after a
  run; a FAIL ships honest or blocks. Scorers freeze before runs.
- Evidence: this repo's commits are hash-pinned by published case
  studies and reuse claims. NO history rewrites, ever.
- Close ritual: commit -> push origin main -> git log
  origin/main..HEAD empty -> report verbatim. A feature-branch
  push is not done.
- Work comes from the governance repo's queue (kristian-os,
  FABLE_QUEUE); do not invent tasks.
