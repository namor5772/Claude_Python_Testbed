#!/bin/bash
# Make every MyAgent skill a Claude Code skill: one symlink per skill folder
# directly under ~/.claude/skills (Claude Code follows a top-level symlink but
# does not scan a nested folder — probed 2026-11-07). Idempotent: adds links
# for new skills, removes links of ours whose skill is gone, touches nothing
# else in the folder. Re-run after adding or deleting a MyAgent skill.
set -e
SK="${MYAGENT_SKILLS_DIR:-$HOME/Library/CloudStorage/OneDrive-Personal/MyAppShare/skills}"
S="$HOME/.claude/skills"
mkdir -p "$S"
added=0; removed=0; kept=0
# stale links of ours: a symlink into $SK whose SKILL.md is gone
for l in "$S"/*; do
  [ -L "$l" ] || continue
  t=$(readlink "$l")
  case "$t" in "$SK"/*) [ -f "$t/SKILL.md" ] || { rm "$l"; removed=$((removed+1)); };; esac
done
for d in "$SK"/*/; do
  n=$(basename "$d"); [ -f "$d/SKILL.md" ] || continue
  if [ -L "$S/$n" ] && [ "$(readlink "$S/$n")" = "${d%/}" ]; then kept=$((kept+1)); continue; fi
  if [ -e "$S/$n" ] && [ ! -L "$S/$n" ]; then echo "skip $n: a real entry of that name exists in $S" >&2; continue; fi
  ln -sfn "${d%/}" "$S/$n"; added=$((added+1))
done
echo "MyAgent skills linked into $S: $added added, $removed removed, $kept unchanged"
