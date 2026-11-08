"""The OS credential-vault readers are confirm patterns (2026-11-09).

The Westpac login's Customer ID and password moved out of the westpac-login
skill's text into Windows Credential Manager / the macOS Keychain (keyring
service `myagent-westpac`), read by the skill's own helper inside its process,
so a run never holds them; the ANZ login's Customer Registration Number and
password followed the same day (service `myagent-anz`, anz-login's new
`scripts/anz_login_support.py`). A model that went for them itself — keyring's CLI
or API, cmdkey, vaultcmd, PowerShell's CredentialManager module, WinRT's
PasswordVault, a CredRead / CredEnumerate P/Invoke; `security
find-generic-password` / `dump-keychain` or keyring on the Mac — now meets the
confirmation dialog (and the Claude Code MCP server's deny-by-default refusal),
while the helper's own command lines name none of these words.
"""

import unittest

from myagent import constants as C
from myagent.safety_mixin import SafetyMixin
from tests._util import stub

# The exact shapes the skill's runbooks have a run type.
HELPER_COMMANDS = (
    r'& "C:\x\.venv\Scripts\python.exe" -B "C:\x\skills\westpac-login\scripts\windows_dependency_preflight.py" --install-missing',
    r'& "C:\x\.venv\Scripts\python.exe" -B "C:\x\skills\westpac-login\scripts\windows_login_support.py" check-credentials',
    r'& "C:\x\.venv\Scripts\python.exe" -B "C:\x\skills\westpac-login\scripts\windows_login_support.py" snapshot --login-url "https://banking.westpac.com.au/"',
    r'& "C:\x\.venv\Scripts\python.exe" -B "C:\x\skills\westpac-login\scripts\windows_login_support.py" enter-credentials --state "C:\Users\x\AppData\Local\Temp\westpac-login-abc\ownership.json"',
    r'& "C:\x\.venv\Scripts\python.exe" -B "C:\x\skills\westpac-login\scripts\windows_login_support.py" verify --state "C:\Users\x\AppData\Local\Temp\westpac-login-abc\ownership.json" --timeout 20',
    '"/Users/x/.venv/bin/python" -B "/Users/x/skills/westpac-login/scripts/macos_login_support.py" preflight',
    '"/Users/x/.venv/bin/python" -B "/Users/x/skills/westpac-login/scripts/macos_login_support.py" check-credentials',
    '"/Users/x/.venv/bin/python" -B "/Users/x/skills/westpac-login/scripts/macos_login_support.py" prepare --state "/var/folders/x/westpac-macos-abc/state.json"',
    # anz-login's helper (the same day), one script for both platforms
    r'& "C:\x\.venv\Scripts\python.exe" -B "C:\x\skills\anz-login\scripts\anz_login_support.py" preflight',
    r'& "C:\x\.venv\Scripts\python.exe" -B "C:\x\skills\anz-login\scripts\anz_login_support.py" check-credentials',
    r'& "C:\x\.venv\Scripts\python.exe" -B "C:\x\skills\anz-login\scripts\anz_login_support.py" targets',
    r'& "C:\x\.venv\Scripts\python.exe" -B "C:\x\skills\anz-login\scripts\anz_login_support.py" enter-credentials --target-id "0123456789ABCDEF0123456789ABCDEF"',
    '"/Users/x/.venv/bin/python" -B "/Users/x/skills/anz-login/scripts/anz_login_support.py" enter-credentials --target-id "0123456789ABCDEF0123456789ABCDEF" --port 9222',
)

WINDOWS_READERS = (
    ('python -c "import keyring; print(keyring.get_password(\'myagent-westpac\', \'password\'))"', r"\bkeyring\b"),
    ("keyring get myagent-westpac password", r"\bkeyring\b"),
    ("cmdkey /list", r"\bcmdkey\b"),
    ('vaultcmd /listcreds:"Windows Credentials" /all', r"\bvaultcmd\b"),
    ("Get-StoredCredential -Target myagent-westpac -AsCredentialObject", r"\bGet-StoredCredential\b"),
    ("(New-Object Windows.Security.Credentials.PasswordVault).RetrieveAll()", r"\bPasswordVault\b"),
    ('[DllImport("advapi32.dll", CharSet=CharSet.Unicode)] public static extern bool CredReadW(string t, int y, int f, out IntPtr c);',
     r"\bCred(?:Read|Enumerate)\w*"),
    ("[Win32.Cred]::CredEnumerate($null, 0, [ref]$count, [ref]$creds)", r"\bCred(?:Read|Enumerate)\w*"),
)

MAC_READERS = (
    ('python3 -c "import keyring; print(keyring.get_password(\'myagent-westpac\', \'password\'))"', r"\bkeyring\b"),
    ("security find-generic-password -s myagent-westpac -a password -w", r"\bsecurity\s+(?:find|dump)-\S+"),
    ("security find-internet-password -s banking.westpac.com.au -g", r"\bsecurity\s+(?:find|dump)-\S+"),
    ("security dump-keychain -d login.keychain", r"\bsecurity\s+(?:find|dump)-\S+"),
)


def classify(command):
    host = stub(SafetyMixin, _disabled_confirm_patterns=set())
    return host._check_command_safety(command)


class VaultPatternTests(unittest.TestCase):

    def test_the_helpers_own_command_lines_run_unasked(self):
        for command in HELPER_COMMANDS:
            with self.subTest(command=command):
                self.assertEqual(classify(command), ("safe", ""))

    def test_the_vault_patterns_are_appended_to_this_platforms_list(self):
        expected = [pattern for _, pattern in (WINDOWS_READERS if C.IS_WINDOWS else MAC_READERS)]
        for pattern in expected:
            self.assertIn(pattern, C.COMMAND_CONFIRM)
        # Appended, never inserted: a saved bypass list names patterns by text,
        # and the Safety dialog lists them in this order.
        self.assertEqual(C.COMMAND_CONFIRM[-len(set(expected)):], list(dict.fromkeys(expected)))

    @unittest.skipUnless(C.IS_WINDOWS, "the PowerShell list")
    def test_windows_vault_readers_are_confirmed(self):
        for command, pattern in WINDOWS_READERS:
            with self.subTest(command=command):
                self.assertEqual(classify(command), ("confirm", pattern))

    @unittest.skipIf(C.IS_WINDOWS, "the bash list")
    def test_mac_vault_readers_are_confirmed(self):
        for command, pattern in MAC_READERS:
            with self.subTest(command=command):
                self.assertEqual(classify(command), ("confirm", pattern))

    def test_ordinary_words_do_not_trip_the_new_patterns(self):
        for command in ("Get-ChildItem C:\\Users\\x\\Documents\\credentials-policy.md",
                        "python -c \"print('security review of the keyrings/ folder')\"",
                        "Write-Output 'Credentials and incredible things'",
                        "git log --oneline -- security.md"):
            with self.subTest(command=command):
                self.assertEqual(classify(command), ("safe", ""))


if __name__ == "__main__":
    unittest.main()
