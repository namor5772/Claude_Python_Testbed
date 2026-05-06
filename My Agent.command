#!/bin/bash
# macOS GUI launches (Finder, Dock, double-click of .command files) start with
# a bare PATH from /etc/paths that excludes Homebrew. Augment so child
# processes spawned by MyAgent (e.g. `npx` for MCP servers) can be found.
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:$PATH"
cd "$(dirname "$0")"
source .venv/bin/activate
python MyAgent.py "$@"
