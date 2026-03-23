---
name: urp
description: Update README.md to reflect recent code changes, commit, and push to the current branch.
disable-model-invocation: true
allowed-tools: Bash, Read, Edit, Glob, Grep
---

# Update README and Push

Update README.md (and CLAUDE.md if needed) to reflect recent code changes, then commit and push.

## Steps

1. Run `git status` and `git log --oneline` to see uncommitted changes and recent commits
2. Run `git diff` to understand what code changed
3. Read the current README.md and identify sections that need updating
4. Make targeted edits — only update sections affected by recent changes. Do not rewrite unchanged sections
5. If architecture or workflow details changed, update CLAUDE.md too
6. Stage all changes: `git add -A` (code + docs together)
7. Commit with a concise message covering both code and doc changes
8. Push to the current branch: `git push origin $(git rev-parse --abbrev-ref HEAD)`

## Rules

- Do NOT modify any code files — only update documentation (README.md, CLAUDE.md)
- Stage and commit ALL pending changes (code + docs) together in one commit
- Keep doc updates concise and match the existing style
- Preserve the existing document structure
- Do it fast — don't over-explore, just read what's needed and update
