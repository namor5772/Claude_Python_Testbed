-- Bank Credentials.app — Desktop launcher that stores or updates the bank login details
-- MyAgent's westpac-login / anz-login / cba-login skills read from the macOS Keychain
-- (2026-11-09). Per-machine artifact (absolute repo path); rebuild with rebuild.sh if
-- the repo moves. Opens Terminal on the bundled store_bank_credentials.command — a menu
-- that runs the chosen skill's own helper in store-credentials mode, which asks for the
-- ID and the password twice with no echo and writes the Keychain itself (getpass needs
-- a real tty, which is why this is a Terminal window and not a dialog).
--
-- Deliberately TCC-free: `open -a Terminal` of a .command launches Terminal with no
-- "control Terminal" Apple Events consent — which a `tell application "Terminal"` would
-- otherwise re-ask after every rebuild (same reasoning as the Heartbeat Log launcher).
on run
	set cmdFile to "/Users/roman/projects/Claude_Python_Testbed/desktop_launchers/store_bank_credentials.command"
	try
		do shell script "open -a Terminal " & quoted form of cmdFile
	on error errMsg number errNum
		display dialog "Couldn't open Bank Credentials (" & errNum & "): " & errMsg buttons {"OK"} default button 1 with icon stop with title "Bank Credentials"
	end try
end run
