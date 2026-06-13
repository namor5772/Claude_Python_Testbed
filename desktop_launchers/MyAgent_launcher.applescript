-- My Agent.app — Desktop launcher for MyAgent.py
-- Per-machine artifact (absolute repo path); rebuild.sh rewrites the path for
-- whatever clone it runs from before compiling, so the no-hardcoded-paths rule
-- holds on any machine. Launches a NEW MyAgent instance each time: MyAgent is
-- multi-instance by design (each instance claims the lowest free lock number),
-- so — unlike the CSVEditor launcher — there is deliberately no launch-or-focus.

on run
	set repoDir to "/Users/roman/projects/Claude_Python_Testbed"
	try
		do shell script "cd " & quoted form of repoDir & " && nohup .venv/bin/python MyAgent.py > /dev/null 2>&1 &"
	on error errMsg number errNum
		display dialog "My Agent launch failed (" & errNum & "): " & errMsg buttons {"OK"} default button 1 with icon stop with title "My Agent"
	end try
end run
