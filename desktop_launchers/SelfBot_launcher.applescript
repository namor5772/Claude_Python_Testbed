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
-- IMPORTANT: the `. ~/.zshrc` is load-bearing — do NOT remove it. macOS GUI apps
-- (Finder / LaunchServices) start with a bare environment and do NOT source the
-- shell profile. SelfBot is Anthropic-only and reads ANTHROPIC_API_KEY from the
-- environment at startup (SelfBot.py:958); that key lives in ~/.zshrc. Without
-- sourcing it, a GUI launch sees no key and SelfBot aborts with the "set the
-- ANTHROPIC_API_KEY environment variable" dialog (it has no keyless fallback, so
-- unlike My Agent this is fatal, not a degraded-provider list). Windows needs no
-- equivalent: its env vars are system-wide and inherited by GUI processes.

on run
	set repoDir to "/Users/roman/projects/Claude_Python_Testbed"
	try
		do shell script ". ~/.zshrc > /dev/null 2>&1; cd " & quoted form of repoDir & " && nohup .venv/bin/python SelfBot.py > /dev/null 2>&1 &"
	on error errMsg number errNum
		display dialog "SelfBot launch failed (" & errNum & "): " & errMsg buttons {"OK"} default button 1 with icon stop with title "SelfBot"
	end try
end run
