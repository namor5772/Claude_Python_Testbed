# MyAgent tools in Claude Code

`server.py` publishes MyAgent's whole tool catalogue to Claude Code as a
Model Context Protocol server. It is its own folder of the repository and
changes nothing in MyAgent: it imports the `myagent` package read-only, builds a
headless host from the same mixins MyAgent's `App` class is made of, and
routes every call through MyAgent's own dispatcher (`_run_tool`). The Tk-bound
methods (the confirmation dialogs, the Agent Request dialog, the clipboard,
the Skills Manager refresh) are overridden on the subclass.

## Registered

From the repository root, once per machine:

    claude mcp add --scope user myagent -- "$PWD/.venv/bin/python" "$PWD/claude_code_mcp/server.py"

Tools appear in Claude Code as `mcp__myagent__<name>` (99 on the Mac Mini:
desktop, browser, Excel, camera + microphone, Gmail / Proton / Outlook,
read_document, the file tools, run_command, web_search / fetch_webpage,
csv_search, manage_instructions / manage_skills / run_instruction, get_skill).
Left out on purpose: `find_element` (Gemini's pointing API), `user_prompt`
and `upgrade_model` (controls of a MyAgent run), and MyAgent's own MCP
servers (register those in Claude Code directly).

## Confirmations

MyAgent asks before a confirm-pattern command (`rm`, `mv`, …) and before a
mail send / trash. No dialog can open here, and Claude Code may be running
with its own prompts bypassed, so the DEFAULT is to refuse those calls with a
result that says why. To let the server grant them (Claude Code's per-call
prompt then being the gate), re-register with the variable:

    claude mcp remove myagent -s user
    claude mcp add --scope user -e MYAGENT_MCP_CONFIRM=allow myagent -- <python> <server.py>

`COMMAND_BLOCKED` patterns are refused either way, inside MyAgent's
`run_powershell`. Note that list is Windows-shaped; `rm -rf /` is a CONFIRM
pattern (`\brm\b`), not a blocked one.

## Environment

The server inherits Claude Code's environment. API keys for `microphone_listen`'s
transcription (Voice Setup's provider) and the mail credentials under
`~/.config/myagent-*` are read exactly as MyAgent reads them. `MYAGENT_REPO`
overrides the repository path; `MYAGENT_DATA_DIR` reaches the package as usual.

macOS permissions are per launching app: the first screenshot, camera or
microphone call from Claude Code's terminal raises that app's own prompt.

## Checks

    <venv python> smoke.py     # list + read-only calls, zero tokens
    <venv python> smoke2.py    # screenshot image, clipboard, the deny policy

Server log lines go to stderr (Claude Code's MCP log); the protocol owns the
real stdout, and fd 1 is pointed at stderr so child processes cannot corrupt it.

## Skills

MyAgent's 25 skills are Claude Code skills too, since 2026-11-07: one symlink
per skill folder directly under `~/.claude/skills/`, made by `link_skills.sh` (`link_skills.ps1` on Windows).
Claude Code follows a top-level symlink and tolerates the `mode` / `section` /
`order` frontmatter keys, but does NOT scan a nested folder (all three probed
with headless Haiku runs), so a single link to the whole tree does not work
and the per-skill links need a refresh when a skill is added or deleted:

    <repo>/claude_code_mcp/link_skills.sh      # macOS / Linux: symlinks; idempotent, adds / removes only its own links
    .\claude_code_mcp\link_skills.ps1          # Windows: directory JUNCTIONS (no Developer Mode or elevation needed)

On Windows the twin uses junctions because a symbolic link needs Developer
Mode or an elevated shell and Git Bash's `ln -s` silently copies without them;
`-Remove` drops every junction of its own. Never `Remove-Item -Recurse` one
of those junctions by hand — Windows PowerShell follows it into the shared
skills tree. The Windows twin is written to the same contract but has not
yet been run on a Windows machine; the first run there, followed by the
`/skills` check, is its test. The server registration on Windows, from the
repo root in PowerShell:

    claude mcp add --scope user myagent -- "$PWD\.venv\Scripts\python.exe" "$PWD\claude_code_mcp\server.py"

Claude Code picks changes up live (no restart). The skills appear in `/skills`
and as `/<name>` slash commands; the model loads one when its description
fits. MyAgent's `mode` means nothing here — a disabled skill is as available
as an enabled one. The skill text itself is the OneDrive file, so an edit in
MyAgent's Skills Manager, or by `manage_skills`, is live for Claude Code at
once. The skills themselves stay in OneDrive, outside git, as they always have;
nothing of them enters this folder.
