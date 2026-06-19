---
description: Fetch and hard-reset the local refultra branch to exactly match origin/refultra, discarding local changes.
allowed-tools: Bash(git fetch:*), Bash(git status:*), Bash(git log:*), Bash(git diff:*), Bash(git stash:*), Bash(git reset:*), Bash(git rev-parse:*)
---

Overwrite the local `refultra` branch so it exactly matches `origin/refultra` on GitHub. The remote is the source of truth; local uncommitted changes and local-only commits on `refultra` are to be discarded.

Follow these steps:

1. Fetch the latest: `git fetch origin refultra`.
2. Show what is about to change and what will be lost:
   - `git status -sb` (ahead/behind + dirty tracked files)
   - `git log --oneline -3 origin/refultra` (incoming commits)
   - If there are uncommitted tracked changes, run `git diff --stat` so the user can see the scope of what the reset will discard.
3. If — and only if — the working tree has substantial uncommitted work that looks like it was NOT meant to be thrown away (more than a trivial diff, or changes to source files the user has been actively editing this session), pause and confirm before resetting. Otherwise proceed: the explicit intent of this command is "remote wins."
4. Run `git reset --hard origin/refultra`.
5. Confirm the result: `git status -sb` should show `## refultra...origin/refultra` with no ahead/behind, and a clean working tree. Report the new HEAD commit hash + subject.

Notes:
- `git reset --hard` only touches tracked files. Untracked / gitignored runtime state (`agent_state.json`, `saved_chats/`, lock files) is intentionally left alone — do not delete it.
- If `refultra` is not the current branch, check it out first (`git checkout refultra`), or tell the user which branch they're actually on before doing anything destructive.
- If MyAgent is running, remind the user that it may rewrite `agent_instructions.json` from memory after the reset; suggest a restart to keep the file pinned to the remote.

$ARGUMENTS
