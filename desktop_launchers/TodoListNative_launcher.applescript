-- TodoList (Native).app — Desktop launcher for TodoList.exe, the native
-- C++/Cocoa port of TodoList.py (build it with ./build_todolist_native.sh).
-- Per-machine artifact (absolute repo path); rebuild.sh rewrites the path for
-- whatever clone it runs from before compiling.
--
-- Launch-or-focus like the TodoList launcher, and for the same reason: BOTH
-- implementations poll the same OneDrive-synced todos.json every 5 seconds,
-- so a second instance — native or Python — would race the first's mtime
-- poll and Overwrite/Reload dialogs. A press therefore focuses a running
-- TodoList.exe, or failing that a running TodoList.py, before it ever
-- launches anything (needs a one-time Automation consent for System Events).
--
-- The `. ~/.zshenv; . ~/.zshrc` sourcing exists so an optional
-- TODOLIST_DATA_DIR override set in the dotfiles is honoured (GUI apps start
-- with a bare environment and `do shell script` runs /bin/sh). Without the
-- override TodoList.exe auto-locates OneDrive itself, so a missing file is
-- harmless (errors suppressed, `;` continues).
--
-- IMPORTANT: the subshell parentheses in `&& (nohup ... &)` are load-bearing —
-- do NOT "simplify" to `cd X && nohup ... &`. Under `do shell script`, a
-- trailing & on a COMPOUND list does not detach: the spawned sh waits on the
-- child until the app exits, so the applet never quits — and a still-running
-- applet swallows the next double-click (macOS sends reopen instead of
-- relaunching). A & on a SIMPLE command inside a foreground subshell detaches
-- for real (verified + fixed 2026-07-13 — see SelfBot_launcher.applescript
-- for the full analysis). The reopen handler covers a second press landing in
-- the ~1s window while the applet is still alive.

on focusPid(foundPid)
	try
		tell application "System Events" to set frontmost of (first process whose unix id is (foundPid as integer)) to true
	on error
		display notification "Todo List is already running" with title "TodoList (Native)"
	end try
end focusPid

on launchOrFocus()
	set repoDir to "/Users/roman/projects/Claude_Python_Testbed"
	try
		set nativePid to do shell script "pgrep -f 'TodoList.exe' | head -n 1; true"
		if nativePid is not "" then
			my focusPid(nativePid)
			return
		end if
		set pyPid to do shell script "pgrep -f 'TodoList.py' | head -n 1; true"
		if pyPid is not "" then
			my focusPid(pyPid)
			return
		end if
		if not my exeExists(repoDir) then
			display dialog "TodoList.exe has not been built on this machine yet." & return & return & "Run  ./build_todolist_native.sh  in the repo first." buttons {"OK"} default button 1 with icon caution with title "TodoList (Native)"
			return
		end if
		do shell script ". ~/.zshenv > /dev/null 2>&1; . ~/.zshrc > /dev/null 2>&1; cd " & quoted form of repoDir & " && (nohup ./TodoList.exe > /dev/null 2>&1 &)"
	on error errMsg number errNum
		display dialog "TodoList (Native) launch failed (" & errNum & "): " & errMsg buttons {"OK"} default button 1 with icon stop with title "TodoList (Native)"
	end try
end launchOrFocus

on exeExists(repoDir)
	try
		do shell script "test -x " & quoted form of (repoDir & "/TodoList.exe")
		return true
	on error
		return false
	end try
end exeExists

on run
	my launchOrFocus()
end run

on reopen
	my launchOrFocus()
end reopen
