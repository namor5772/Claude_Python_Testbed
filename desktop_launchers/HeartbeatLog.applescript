-- Heartbeat Log.app — Desktop launcher that opens Heartbeat.py's log for examination.
-- Per-machine artifact (absolute repo path); rebuild with rebuild.sh if the repo
-- moves. Opens a Terminal pager (the bundled view_heartbeat.command) showing the
-- meaningful events first, then the full log — scrollable & searchable.
--
-- Deliberately TCC-free: `open -a Terminal` of a .command launches Terminal with
-- no "control Terminal" Apple Events consent — which a `tell application
-- "Terminal"` would otherwise re-ask after every rebuild, since the new code hash
-- makes macOS treat the app as new (same reasoning as UnreadSummary's chime+dialog).
on run
	set cmdFile to "/Users/roman/projects/Claude_Python_Testbed/desktop_launchers/view_heartbeat.command"
	try
		do shell script "open -a Terminal " & quoted form of cmdFile
	on error errMsg number errNum
		display dialog "Couldn't open the Heartbeat log viewer (" & errNum & "): " & errMsg buttons {"OK"} default button 1 with icon stop with title "Heartbeat Log"
	end try
end run
