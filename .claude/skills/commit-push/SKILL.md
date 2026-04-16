---
name: commit-push
description: Stage modified tracked files, commit with a descriptive message, and push to the current branch's origin. Fast — no file exploration beyond git diff.
disable-model-invocation: true
allowed-tools: Bash
---

# Commit and push

Commit the current modifications and push to origin. Act fast — this is a simple Git ceremony, not a review pass.

## Steps

1. Run `git status --short` and `git diff --stat` in parallel to see what's changed. Do NOT `Read` any files unless the diff is so small that cat-ing it confirms the change intent. Normally the diff-stat is enough to infer a message.
2. Run `git log -5 --oneline` to match the repo's commit-message style (the recent commits are the canonical style reference).
3. Stage modified tracked files explicitly by name (not `git add -A` — that would pick up unrelated untracked files like `.DS_Store` or scratch experiments). Only include files actually involved in the change set. Skip `agent_instructions.json` and `skills.json` unless the user explicitly asks to include them (they're GUI-auto-modified state files that churn constantly).
4. Draft a one-line subject (under 70 chars, imperative present tense matching repo style) and optional 2-4 line body. Focus on the *why* — the diff shows the *what*.
5. Commit via HEREDOC to preserve formatting, ending with:
   ```
   Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
   ```
6. Push to the current branch's origin: `git push origin $(git rev-parse --abbrev-ref HEAD)`.
7. Report: commit hash, subject, and the `<old>..<new>` range that was pushed.

## Guardrails

- Do NOT run the test suite, lint, or any build step. This is commit + push only.
- Do NOT amend an existing commit unless the user explicitly asks.
- Do NOT force-push. If push fails due to non-fast-forward, stop and ask the user.
- If there are no staged or unstaged changes, say so and stop — do not create an empty commit.
