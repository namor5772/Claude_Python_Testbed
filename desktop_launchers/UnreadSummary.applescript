-- UnreadSummary.app — Desktop launcher for UnreadSummary.py
-- Per-machine artifact (absolute repo path); rebuild with rebuild.sh if the
-- repo moves. Success -> chime + self-dismissing dialog with the run's log
-- line; failure -> dialog with the log tail (the script logs fatals rather
-- than printing). Deliberately TCC-free: `display notification` would need
-- a notifications consent (re-asked after every rebuild, since the new code
-- hash makes macOS treat it as a new app); a plain dialog + afplay need no
-- permission at all.

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
		try
			do shell script "afplay /System/Library/Sounds/Glass.aiff > /dev/null 2>&1 &"
		end try
		display dialog "Run complete:" & return & resultLine buttons {"OK"} default button 1 giving up after 6 with title "UnreadSummary"
	on error errMsg number errNum
		set logTail to ""
		try
			set logTail to do shell script "tail -n 3 " & quoted form of logFile
		end try
		display dialog "UnreadSummary failed (exit " & errNum & "): " & errMsg & return & return & "Log:" & return & logTail buttons {"OK"} default button 1 with icon stop with title "UnreadSummary"
	end try
end run
