-- SelfBot.app — Desktop launcher for SelfBot.py
-- Per-machine artifact (absolute repo path); rebuild.sh rewrites the path for
-- whatever clone it runs from, then recompiles with osacompile. Launches a NEW
-- SelfBot instance each time (detached). SelfBot is a two-instance app by design —
-- the second instance chats with the first (self-chat) — so, like My Agent.app and
-- unlike CSVEditor.app, there is deliberately no launch-or-focus. SelfBot.py itself
-- cascades a manually-opened second instance so the two windows don't stack on the
-- same saved geometry. For the auto-positioned side-by-side duo layout, use
-- LaunchSelfBot.bat (Windows) / launch it twice by hand.

on run
	set repoDir to "/Users/roman/projects/Claude_Python_Testbed"
	try
		do shell script "cd " & quoted form of repoDir & " && nohup .venv/bin/python SelfBot.py > /dev/null 2>&1 &"
	on error errMsg number errNum
		display dialog "SelfBot launch failed (" & errNum & "): " & errMsg buttons {"OK"} default button 1 with icon stop with title "SelfBot"
	end try
end run
