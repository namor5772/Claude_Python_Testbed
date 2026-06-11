-- UnreadSummary.app — Desktop launcher for UnreadSummary.py
-- Per-machine artifact (absolute repo path); regenerate with osacompile if
-- the repo moves. Success -> notification with the run's log line; failure
-- -> dialog with the log tail (the script logs fatals rather than printing).

on run
	set repoDir to "/Users/roman/projects/Claude_Python_Testbed"
	set logFile to (POSIX path of (path to home folder)) & "Library/Logs/myagent/unread_summary.log"
	try
		with timeout of 900 seconds
			do shell script "cd " & quoted form of repoDir & " && .venv/bin/python UnreadSummary.py"
		end timeout
		set resultLine to ""
		try
			set resultLine to do shell script "tail -n 1 " & quoted form of logFile & " | cut -c 21-"
		end try
		display notification resultLine with title "UnreadSummary" subtitle "Run complete" sound name "Glass"
	on error errMsg number errNum
		set logTail to ""
		try
			set logTail to do shell script "tail -n 3 " & quoted form of logFile
		end try
		display dialog "UnreadSummary failed (exit " & errNum & "): " & errMsg & return & return & "Log:" & return & logTail buttons {"OK"} default button 1 with icon stop with title "UnreadSummary"
	end try
end run
