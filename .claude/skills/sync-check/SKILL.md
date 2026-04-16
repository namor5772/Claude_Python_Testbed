---
name: sync-check
description: Check if the current local branch is in sync with its origin remote. Always does a fresh fetch — never relies on stale data.
disable-model-invocation: true
allowed-tools: Bash
---

# Sync check

Verify the current local branch matches `origin/<branch>`. Fast and deterministic — no file exploration.

## Steps

1. Run these commands in a single Bash call:
   - `git rev-parse --abbrev-ref HEAD` — current branch
   - `git fetch origin <branch>` — always fresh, never stale cache
   - `git rev-list --left-right --count origin/<branch>...<branch>` — divergence count
   - `git log -1 --oneline <branch>` — local tip
   - `git log -1 --oneline origin/<branch>` — remote tip
   - `git status -sb | head -5` — working tree state

2. Report the result in this exact shape:
   - `0 0` → **✅ In sync** at `<hash>`
   - `0 N` (N > 0) → **⬆ Local is N ahead** — run `git push origin <branch>` to publish
   - `N 0` (N > 0) → **⬇ Local is N behind** — run `git pull origin <branch>` to catch up
   - `M N` (both > 0) → **⚠ Diverged** (M behind, N ahead) — needs rebase or merge
   - Always show both tip hashes
   - Mention uncommitted changes if `git status` shows any (separate from the sync status)

Do NOT read files, do NOT explore the repo. This is a 5-second status check.
