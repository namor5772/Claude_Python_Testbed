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

Tools appear in Claude Code as `mcp__myagent__<name>` (99 on the Mac Mini and
on the Windows laptop alike: desktop, browser, Excel, camera + microphone,
Gmail / Proton / Outlook, read_document, the file tools, run_command,
web_search / fetch_webpage,
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

The refusal names the dialog MyAgent would have shown ("Send a Proton email
from roman1@ri.com.au?"), so the person who asked can be told exactly what
was declined.

### One call granted, the registration kept

The policy is read from the environment once, at import, so a separate client
process that spawns `server.py` with the variable set grants confirms in THAT
child alone — the registered server stays deny-by-default. Verified
2026-11-07: a `proton_send` the registered server had refused, sent this way
from the same Claude Code session once the user had asked for it explicitly.
From the venv, in the repo root:

    import asyncio, os, sys
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = dict(os.environ, MYAGENT_MCP_CONFIRM="allow")

    async def main():
        params = StdioServerParameters(command=sys.executable,
                                       args=["claude_code_mcp/server.py"], env=env)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                res = await s.call_tool("proton_send", {"account": "...", "to": "...",
                                                        "subject": "...", "body": "..."})
                print([c.text for c in res.content if c.type == "text"], res.isError)

    asyncio.run(main())

The grant is still logged by the server (`granted without MyAgent's dialog`)
and the result carries the same `MyAgent notes:` line, so the transcript
shows the gate was passed on purpose.

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

Both spawn `server.py` with this process's WHOLE environment
(`env=dict(os.environ)`), as Claude Code does. Left to the SDK's default the
child gets a dozen variables, and on Windows `OneDrive` is not one of them:
`datapaths` then falls back to the repo-root `skills/` folder and the server
serves 98 tools and `No skills defined.` — the first Windows run, 2026-11-07,
until the scripts passed the environment. They also set stdout to UTF-8, since
a cp1252 Windows console cannot print the `•` / `⚠` in tool results, and the
`run_command` check joins its two commands with `;`, which bash and PowerShell
share. Both pass on the Windows laptop since that day.

## Live use

First used from a real Claude Code session on the Mac Mini on 2026-11-07,
driven by the `/physical-interface` skill: `get_skill` (the skill text out of
the OneDrive tree), `camera_capture` with `save_path` (the 1920x1080 file in
`MyAppShare/general` plus the provider-sized inline image Claude Code
displays), `screenshot` of the display to verify a Preview window, and a
`proton_send` through the one-call client above — each through this server,
MyAgent itself idle. Two things seen on the way: Claude Code's `/mcp` warning
that `ANTHROPIC_API_KEY` in the environment disables the claude.ai connectors
does not touch this server (local stdio, no claude.ai login), and
`get_skill`'s `skill_name` enum lists the skills whose mode is `on_demand` at
server start — the index MyAgent's system prompt would show — so a mode change
in the Skills Manager reaches it at the next reconnect.

### A whole instruction, end to end (2026-11-07, later the same evening)

A MyAgent instruction pasted into Claude Code — the Westpac Mastercard top-up
("DRY_RUN=False": westpac-login, read the summary, transfer the shortfall with
westpac-transfer-funds, email the report from Gmail, sign out, close only the
task window) — ran to completion with no MyAgent window: the login helper's
snapshot / open / prepare / submit / verify over `run_command`-style Bash, a
$1,308.36 transfer with both local-date guards (receipt C08863), the
`gmail_send` through the one-call client above, sign-out verified, the task
target closed by id and the browser re-enumerated. The user's verdict:
"Claude Code can act just like my MyAgent harness". Two things differ from a
MyAgent run, and both are worth knowing before pasting the next instruction:

- **Confirmations.** MyAgent's dialog does not exist here. The two mail sends
  went through the one-call client because the instruction asked for them in
  so many words; Claude Code's own permission prompt is the only gate on that
  path, and a session with permissions bypassed has none. For unattended runs
  MyAgent's dialog, or the Telegram relay, remains the safer design.
- **Browser steps.** The skills are written for MyAgent's `browser_*` tools.
  In Claude Code the tighter route is a small Playwright script per checkpoint
  (form filled and read back; Continue and the confirm dialog; Confirm and the
  receipt; the re-read), each `connect_over_cdp` to port 9222 — MyAgent's
  automation Chrome, the one the login helper used — and bound to the helper's
  exact `target_id` through `Target.getTargetInfo`, never to "the Westpac tab".
  **Never call Playwright's `browser.close()` on that connection:** it closed
  the task page (the debug browser went to zero targets, the session not signed
  out); letting the `sync_playwright()` context exit only disconnects. The
  helper then rightly refused to recreate the page, and the instruction
  started with its own fresh baseline.

The physical-interface rules held as written — "tell me" was spoken through
`say`, the photos went to the share, the viewer was verified by screenshot —
because the skill text is the same file either way.

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
skills tree. First run on Windows 2026-11-07, on the laptop: 25 junctions
added under `~\.claude\skills`, a real folder already there left alone, a skill
readable through its junction, the second run `0 added, 0 removed, 25
unchanged`, and the open Claude Code session listed all 25 skills at once,
no restart. The server registration on Windows, from the
repo root in PowerShell:

    claude mcp add --scope user myagent -- "$PWD\.venv\Scripts\python.exe" "$PWD\claude_code_mcp\server.py"

Done on the laptop 2026-11-07: `claude mcp get myagent` reports Connected, and a
headless `claude -p` run calling `manage_skills` through the registered server
listed the 25 skills — Claude Code passes the whole environment, `OneDrive`
included (see Checks).

Claude Code picks changes up live (no restart). The skills appear in `/skills`
and as `/<name>` slash commands; the model loads one when its description
fits. MyAgent's `mode` means nothing here — a disabled skill is as available
as an enabled one. The skill text itself is the OneDrive file, so an edit in
MyAgent's Skills Manager, or by `manage_skills`, is live for Claude Code at
once. The skills themselves stay in OneDrive, outside git, as they always have;
nothing of them enters this folder.
