# ai-claim-verification-agent

@AGENTS.md

## Session mechanics (Claude-specific)
- Boot: read STATE.md before writing to this tree.
- Environment fingerprint before any write (pwd + git config user.email).
- Close: commit on a branch -> push the branch -> open a PR to main -> merge after CI passes.
