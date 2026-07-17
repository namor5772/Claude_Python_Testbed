-- SelfBot.app — Desktop launcher for SelfBot.py
-- Per-machine artifact (absolute repo path); rebuild.sh rewrites the path for
-- whatever clone it runs from, then recompiles with osacompile. Launches a NEW
-- SelfBot instance each time (detached). SelfBot is a two-instance app by design —
-- the second instance chats with the first (self-chat) — so, like My Agent.app and
-- unlike CSVEditor.app, there is deliberately no launch-or-focus. SelfBot.py itself
-- cascades a manually-opened second instance so the two windows don't stack on the
-- same saved geometry. For the auto-positioned side-by-side duo layout, use
-- LaunchSelfBot.bat (Windows) / launch it twice by hand.
--
-- IMPORTANT: the `. ~/.zshenv; . ~/.zshrc` is load-bearing — do NOT remove it, and
-- source BOTH files. macOS GUI apps (Finder / LaunchServices) start with a bare
-- environment, and `do shell script` runs /bin/sh — NOT zsh — so no zsh dotfile is
-- sourced automatically. SelfBot is Anthropic-only and reads ANTHROPIC_API_KEY
-- from the environment at startup (SelfBot.py:958); that key lives in ~/.zshrc
-- today, but the API keys are split across both files (OPENAI_API_KEY moved to
-- ~/.zshenv 2026-07, which silently dropped the OpenAI provider from My Agent.app
-- until its launcher sourced .zshenv too), so both launchers source both files —
-- .zshenv first, zsh's own order — to stay immune to future key moves. Without the
-- key, a GUI launch aborts with the "set the ANTHROPIC_API_KEY environment
-- variable" dialog (no keyless fallback, so unlike My Agent this is fatal, not a
-- degraded-provider list). A missing file is harmless (errors suppressed, `;`
-- continues). Windows needs no equivalent: its env vars are system-wide and
-- inherited by GUI processes.
--
-- IMPORTANT: the subshell parentheses in `&& (nohup ... &)` are load-bearing — do
-- NOT "simplify" to `cd X && nohup ... &`. Under `do shell script` (unlike a normal
-- shell), a trailing & on a COMPOUND list (`cd X && cmd &`) does not detach: the
-- spawned sh sits in wait4() on the child until SelfBot exits, do shell script
-- waits on sh, and the applet never quits. A still-running applet swallows the
-- next double-click — macOS sends the running app a reopen event instead of
-- launching it again, and an applet blocked inside its run handler can't service
-- events — so the second press that should open the self-chat peer did nothing
-- (verified + fixed 2026-07-13; Windows never had this problem because each
-- shortcut press starts a fresh pythonw). A trailing & on a SIMPLE command inside
-- a foreground subshell detaches for real: the applet quits ~1s after launch and
-- every press spawns a fresh instance, matching Windows. The reopen handler
-- covers the residual race — a second press landing in that ~1s window while the
-- applet is still alive — by launching the new instance directly.

on launchInstance()
	set repoDir to "/Users/roman/projects/Claude_Python_Testbed"
	try
		do shell script ". ~/.zshenv > /dev/null 2>&1; . ~/.zshrc > /dev/null 2>&1; cd " & quoted form of repoDir & " && (nohup .venv/bin/python SelfBot.py > /dev/null 2>&1 &)"
	on error errMsg number errNum
		display dialog "SelfBot launch failed (" & errNum & "): " & errMsg buttons {"OK"} default button 1 with icon stop with title "SelfBot"
	end try
end launchInstance

on run
	my launchInstance()
end run

on reopen
	my launchInstance()
end reopen
