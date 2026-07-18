-- TodoList.app — Desktop launcher for TodoList.py
-- Per-machine artifact (absolute repo path); rebuild.sh rewrites the path for
-- whatever clone it runs from before compiling. Launch-or-focus like the
-- CSVEditor launcher: TodoList is a personal single-window app whose todos.json
-- is shared via OneDrive — two local instances would race each other's 5-second
-- mtime poll and Overwrite/Reload dialogs, so a second press focuses the
-- running window instead of starting a second copy (needs a one-time
-- Automation consent for System Events).
--
-- The `. ~/.zshenv; . ~/.zshrc` sourcing exists so an optional TODOLIST_DATA_DIR
-- override set in the dotfiles is honoured (GUI apps start with a bare
-- environment and `do shell script` runs /bin/sh — no zsh dotfile is sourced
-- automatically). Without the override TodoList auto-locates OneDrive itself,
-- so a missing file is harmless (errors suppressed, `;` continues).
--
-- IMPORTANT: the subshell parentheses in `&& (nohup ... &)` are load-bearing — do
-- NOT "simplify" to `cd X && nohup ... &`. Under `do shell script`, a trailing &
-- on a COMPOUND list does not detach: the spawned sh waits on the child until the
-- app exits, so the applet never quits — and a still-running applet swallows the
-- next double-click (macOS sends reopen instead of relaunching). A & on a SIMPLE
-- command inside a foreground subshell detaches for real (verified + fixed
-- 2026-07-13 — see SelfBot_launcher.applescript for the full analysis). The
-- reopen handler covers a second press landing in the ~1s window while the
-- applet is still alive.

on launchOrFocus()
	set repoDir to "/Users/roman/projects/Claude_Python_Testbed"
	try
		set foundPid to do shell script "pgrep -f 'TodoList.py' | head -n 1; true"
		if foundPid is not "" then
			try
				tell application "System Events" to set frontmost of (first process whose unix id is (foundPid as integer)) to true
			on error
				display notification "Todo List is already running" with title "TodoList"
			end try
		else
			do shell script ". ~/.zshenv > /dev/null 2>&1; . ~/.zshrc > /dev/null 2>&1; cd " & quoted form of repoDir & " && (nohup .venv/bin/python TodoList.py > /dev/null 2>&1 &)"
		end if
	on error errMsg number errNum
		display dialog "TodoList launch failed (" & errNum & "): " & errMsg buttons {"OK"} default button 1 with icon stop with title "TodoList"
	end try
end launchOrFocus

on run
	my launchOrFocus()
end run

on reopen
	my launchOrFocus()
end reopen
