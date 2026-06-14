-- API Cost Log.app — Desktop launcher that opens MyAgent's API cost log for examination.
-- Per-machine artifact (absolute repo path); rebuild with rebuild.sh if the repo
-- moves. Opens a Terminal pager (the bundled view_costlog.command) showing a spend
-- summary first (totals, today, this month, by provider, by model), then every
-- logged run — scrollable & searchable.
--
-- Deliberately TCC-free: `open -a Terminal` of a .command launches Terminal with
-- no "control Terminal" Apple Events consent — which a `tell application
-- "Terminal"` would otherwise re-ask after every rebuild (same as the Heartbeat
-- Log launcher).
on run
	set cmdFile to "/Users/roman/projects/Claude_Python_Testbed/desktop_launchers/view_costlog.command"
	try
		do shell script "open -a Terminal " & quoted form of cmdFile
	on error errMsg number errNum
		display dialog "Couldn't open the API cost-log viewer (" & errNum & "): " & errMsg buttons {"OK"} default button 1 with icon stop with title "API Cost Log"
	end try
end run
