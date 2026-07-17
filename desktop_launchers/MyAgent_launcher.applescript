-- My Agent.app — Desktop launcher for MyAgent.py
-- Per-machine artifact (absolute repo path); rebuild.sh rewrites the path for
-- whatever clone it runs from before compiling, so the no-hardcoded-paths rule
-- holds on any machine. Launches a NEW MyAgent instance each time: MyAgent is
-- multi-instance by design (each instance claims the lowest free lock number),
-- so — unlike the CSVEditor launcher — there is deliberately no launch-or-focus.
--
-- IMPORTANT: the `. ~/.zshenv; . ~/.zshrc` is load-bearing — do NOT remove it, and
-- source BOTH files. macOS GUI apps (Finder / LaunchServices) start with a bare
-- environment, and `do shell script` runs /bin/sh — NOT zsh — so no zsh dotfile is
-- sourced automatically. MyAgent reads ANTHROPIC/OPENAI/GEMINI/XAI _API_KEY from
-- the environment to decide which providers to offer (MyAgent.py:83-86), and the
-- keys are split across both files: OPENAI_API_KEY moved to ~/.zshenv 2026-07 (for
-- zsh-invoked launchers in other repos — a move that silently dropped the OpenAI
-- provider from GUI launches here until .zshenv was sourced too), the rest live in
-- ~/.zshrc. .zshenv is sourced first, matching zsh's own order. A missing file is
-- harmless (errors suppressed, `;` continues). Windows needs no equivalent: its
-- env vars are system-wide and inherited by GUI processes.
--
-- IMPORTANT: the subshell parentheses in `&& (nohup ... &)` are load-bearing — do
-- NOT "simplify" to `cd X && nohup ... &`. Under `do shell script`, a trailing &
-- on a COMPOUND list does not detach: the spawned sh waits on the child until
-- MyAgent exits, so the applet never quits — and a still-running applet swallows
-- the next double-click (macOS sends reopen instead of relaunching, and an applet
-- blocked inside its run handler can't service events), breaking launch-a-fresh-
-- instance-per-press. A & on a SIMPLE command inside a foreground subshell
-- detaches for real (verified + fixed 2026-07-13, same fix as SelfBot.app — see
-- SelfBot_launcher.applescript for the full analysis). The reopen handler covers
-- a second press landing in the ~1s window while the applet is still alive.

on launchInstance()
	set repoDir to "/Users/roman/projects/Claude_Python_Testbed"
	try
		do shell script ". ~/.zshenv > /dev/null 2>&1; . ~/.zshrc > /dev/null 2>&1; cd " & quoted form of repoDir & " && (nohup .venv/bin/python MyAgent.py > /dev/null 2>&1 &)"
	on error errMsg number errNum
		display dialog "My Agent launch failed (" & errNum & "): " & errMsg buttons {"OK"} default button 1 with icon stop with title "My Agent"
	end try
end launchInstance

on run
	my launchInstance()
end run

on reopen
	my launchInstance()
end reopen
