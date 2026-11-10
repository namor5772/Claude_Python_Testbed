# Bank credentials in the OS vault — 2026-11-09

The three bank-login skills MyAgent runs — `westpac-login`, `anz-login`,
`cba-login` (OneDrive, `<MyAppShare>/skills/`, outside this repository) — carried
their credentials as literal text: a Customer ID / Customer Registration Number /
client number and a password each. The user asked whether that was a security
vulnerability. It was, and this is the record of what replaced it, how it was
verified, and what a later session must know.

## Why it mattered

- Every run with a login skill ON sent the credential to the model provider as
  part of the prompt, and saved it in the run's chat file in the OneDrive share.
  A scan found the Westpac password in 104 files, the ANZ password in 56, the
  CBA password in 41 — saved chats, skill backups, the migrated `skills.json`,
  and Claude Code transcripts.
- The real risk was not the file but the context: a run that holds the password
  also reads mail and web pages, so a prompt injection could have asked a model
  that knew it to send it on. The skills' "never print the credentials" line is
  an instruction to the model, and instructions can be talked around.

## The design

**The model never sees a credential.** Each bank's values live in the computer's
credential vault — Windows Credential Manager on Windows, the login Keychain on
macOS — through the `keyring` package, and only that skill's own bundled helper
reads them, inside its own process, to type them into the bank's page.

| Bank | keyring service | entries | helper (in the skill's `scripts/`) |
|---|---|---|---|
| Westpac | `myagent-westpac` | `customer-id`, `password` | `windows_login_support.py` (Windows), `macos_login_support.py` (Mac) |
| ANZ | `myagent-anz` | `customer-registration-number`, `password` | `anz_login_support.py` (both platforms) |
| CBA | `myagent-cba` | `client-number`, `password` | `cba_login_support.py` (both platforms) |

Every helper has the same commands:

- `check-credentials` — booleans only (`CREDENTIALS_AVAILABLE` / `CREDENTIALS_MISSING`
  with `*_stored` flags). Run before any browser action.
- `store-credentials` — the user's command, interactive only: it prompts for the
  ID and the password twice with `getpass` (no echo), validates (digits for the
  ID; every password character one the key map can type), writes both entries,
  reads them back. A redirected stdin is refused, so no model, pipe or file ever
  carries the values. The **Bank Credentials** Desktop shortcut (below) is the
  front end.
- The entry step — Westpac Windows `enter-credentials --state <STATE>` (inside
  the existing ownership protocol), Westpac macOS `prepare --state <STATE>`,
  ANZ / CBA `enter-credentials --target-id <id>` — reads the vault, requires the
  bank's exact login origin in a secure context and the visible form, fills the
  ID field, focuses the password field, types the password key by key through
  CDP `Input.dispatchKeyEvent` (the focus and origin checked before every key),
  compares both fields inside the page and reports booleans. It never clicks
  the submit button; the model does that, once, as before.
- `preflight` (ANZ / CBA; Westpac's existing dependency preflight and the Mac
  `preflight`) — the imports and the vault backend, no installs.
- `targets` (ANZ / CBA) — the local debugging browser's page targets, id / url
  / title, so the task page is matched by its exact CDP target id, never by
  domain.

**Backend rule.** Only the platform's OS vault is accepted
(`keyring.backends.Windows.WinVaultKeyring` / `keyring.backends.macOS.Keyring`);
a plaintext, null or chained backend — `keyrings.alt`, a
`PYTHON_KEYRING_BACKEND` override — is a STOP. The Windows entries appear in
Control Panel → Credential Manager → Windows Credentials as
`<entry>@<service>` (e.g. `password@myagent-westpac`), encrypted by DPAPI under
the user's logon, so nothing synced through OneDrive ever contains them and a run
under another account has no credentials.

**What changed per bank beyond the source of the values.**

- Westpac kept its mechanisms: the Customer ID goes in by the focus + select +
  `Input.insertText` that the native `browser_fill` used, the password field is
  clicked through CDP mouse events as `browser_click` did, and the password is
  typed by the same keyDown / keyUp pairs the old stdin `type-password` sent.
  `type-password` is retired and STOPs with the advice. The dependency
  preflight's fixed allowlist gained `keyring` and the pure-Python modules its
  Windows backend imports (`jaraco.classes` / `jaraco.context` /
  `jaraco.functools`, `more-itertools`, `pywin32-ctypes`), one row each because
  the repair installs with `--no-deps`; an absent namespace parent now counts as
  missing, not broken.
- ANZ and CBA had no helper: the model typed with desktop keystrokes
  (`type_text`) after an OS-focus dance — `find_window`, a full-display
  screenshot, a `mouse_click` on Chrome's tab strip — and CBA on macOS also
  needed Accessibility trust for the Python running MyAgent. CDP input is
  delivered inside the renderer to the DOM-focused element, so both the dance
  and the Accessibility prerequisite are gone from those logins (the CBA skill
  keeps the Accessibility probe in its Gotchas for tasks that still desktop-type).
  Each field is clicked through CDP mouse events, its content selected (the old
  `ctrl+a`, so a remembered or half-typed value is replaced, never appended) and
  typed. CBA's helper touches only the visible `#clientNumber` / `#password` —
  never the hidden prefill / remember / passkey inputs, the Log on button or the
  MFA page — and reports whether *Remember my client number* is ticked.
- The callers were updated: `anz-pay-anyone` and `anz-transfer-funds` (their
  login step), `cba-pay-anyone` (preconditions and the login gotcha), the
  Westpac runbooks (the credential step, a *Storing or rotating* section, the
  retired here-string).

**Hardening in this repository** (`58ab1c2`). The OS credential-vault readers
are confirm patterns, appended to both `COMMAND_CONFIRM` lists in
`myagent/constants.py` and mirrored in `SelfBot.py`: `keyring`, `cmdkey`,
`vaultcmd`, `Get-StoredCredential`, `PasswordVault`, `CredRead` /
`CredEnumerate` on Windows; `keyring` and `security find-…` / `dump-…` on the
Mac. A run that goes for the vault itself meets the confirmation dialog — and
the Claude Code MCP server's deny-by-default refusal — while the helpers' own
command lines name none of these words. `tests/test_vault_confirm_patterns.py`
pins both halves.

## Verification

- Offline tests per skill, over a stand-in `keyring` module and a stand-in CDP
  page: Westpac 36 (login helper) + 24 (preflight) + 52 (macOS helper; 50 pass
  under a Windows shim, the two failures POSIX-only), ANZ 24, CBA 25. They cover
  the vault layer (both entries read, only the OS backend accepted,
  booleans-only status, the interactive-only store and its validations), the
  entry order (ID before the click, password after), origin / form / focus
  guards, no appending, no submit, and that the sources read no credential from
  stdin or argv.
- Each helper was run against its REAL login form in MyAgent's automation
  Chrome on the laptop without a submit: `CREDENTIALS_ENTERED`, both match flags
  true, the submit button enabled — then the page cleared and the task browser
  closed by its root process.
- The same day the user ran the Westpac, ANZ and CBA logins through MyAgent on
  the laptop with the new method: all three worked.
- **The Mac Mini, the same evening**, after a `/sync-refultra`: `keyring` and
  `websocket-client` installed in the venv (backend `keyring.backends.macOS.Keyring`),
  `rebuild.sh` run (the Bank Credentials app signed with the launcher cert, its
  Desktop alias created), the user stored all three from Bitwarden through the
  shortcut, every helper's `check-credentials` → `CREDENTIALS_AVAILABLE`. Then a
  Westpac login driven from Claude Code through the skill's own
  `macos_login_support.py`: snapshot → `open` (a dedicated launch, no debugging
  Chrome was running) → `prepare` (`FORM_PREPARED`) → `submit` → `LOGIN_VERIFIED`,
  first try. No keychain-access dialog interrupted any step — the grant given at
  store time carried to the login. ANZ and CBA pass preflight there but were not
  logged in: the skills forbid a bank login just to test a skill. The 77 saved instructions
  were scanned: none restates the old typing steps, none holds a credential.
- **The Desktop (DESKTOP-NAMOR), later the same evening**, after a `/sync-refultra`:
  `keyring` 25.7.0 and `websocket-client` 1.9.2 installed in the venv (backend
  `keyring.backends.Windows.WinVaultKeyring`), the **Bank Credentials** shortcut
  created from the README recipe, every helper's preflight OK, all three banks
  stored by the user through the shortcut (`-Status`: all `stored`) — and then the
  user ran the Westpac, ANZ and CBA logins through MyAgent on it: all three worked.
  So the vault method is live-confirmed on every machine. One observation: keyring's
  Windows backend files a service's FIRST entry under the bare service name and
  moves it to `<entry>@<service>` only when the second arrives, so Credential
  Manager shows `customer-id@myagent-westpac` beside a bare `myagent-westpac`
  (the password) — `get_password` reads either form, as the `stored` states show.
- Full repo suite 2345 tests OK; ruff at its 42-finding baseline.

## Cleanup

The old passwords were replaced by `[<BANK>-PASSWORD-REDACTED]` in the historical
files (JSON re-parsed after the rewrite, mtimes kept): Westpac 104 files / 178
occurrences, ANZ 53 / 123, CBA 39 / 79 — saved chats, `skills.json.migrated.bak`,
the skill backup copies (where the IDs were redacted too), closed Claude Code
transcripts and one transcript's tool-results file. The IDs stay in older chats
by the user's 2026-10-11 ruling (password only). Still holding the OLD passwords
and out of reach from here: OneDrive's version history and recycle bin of every
redacted file, the USB mirror until the next MyBackup run, this session's own
transcript, and every provider's API logs — which is why the passwords
themselves must be changed.

## Operating it

- **Rotation / first store:** the **Bank Credentials** Desktop shortcut
  (`desktop_launchers/BankCredentials_Win.ps1`; `BankCredentials.applescript` +
  `store_bank_credentials.command` on the Mac, built by `rebuild.sh`; `83d63d0`)
  opens a visible console listing each bank's vault state and runs the chosen
  helper's `store-credentials`. It is visible on purpose — `getpass` needs a real
  console — so it targets `powershell.exe` directly, never `conhost.exe
  --headless`. `-Status` prints the three states for a script.
- **A new machine:** `pip install keyring websocket-client` in the venv, run
  `rebuild.sh` for the launcher, then store all three; the first Keychain access
  by that Python may raise macOS's keychain-access dialog — *Always Allow*. Until
  then a run there stops at `CREDENTIALS_MISSING` and asks, by design. Done on
  the Mac Mini the same evening (below).
- **After editing a skill file directly,** restart any app that loaded the old
  text — MyAgent, SelfBot, the `claude_code_mcp` servers — because the
  whole-dict skills save writes a loaded skill back from memory.
- **Memory:** `westpac-credentials-locations.md` in the Claude Code memory
  folder is the running status (what is stored where, what is still open).
- **Backup:** on the Mac Mini `~/Library/Keychains/` is a MyBackup row since
  2026-11-09 — the login keychain with the three `myagent-*` entries and the
  launcher-signing key, both encrypted at rest, so the drive holds no clear
  secret (the bank passwords also live in Bitwarden, the source they were stored
  from). Claude Code's transcripts are deliberately NOT mirrored anywhere: the
  Mac Mini's 2026-11-07 session, which ran the Westpac top-up with the pre-vault
  skill, still holds the old Westpac password unredacted — the laptop's cleanup
  reached only its own transcripts. The same day three stray `~/.config/* copy`
  folders (duplicate Gmail / Outlook / Proton tokens from May–June) were deleted
  from the Mac and, by the next run, from its mirror.

## Open

- ~~Change the three bank passwords and re-store them (the user's step)~~ —
  done 2026-11-10: the user changed the Westpac, ANZ and CBA passwords at the
  banks, re-stored them through the Bank Credentials shortcut and ran each login
  instruction successfully. With that, the old passwords left in OneDrive's
  version history, the Mac Mini's 2026-11-07 transcript and the provider logs
  are dead values. Each machine has its own vault, so a machine whose vault
  still holds an old password stops at the bank's login error until it is
  re-stored there.
- ~~Store all three on the Mac Mini; build the Mac launcher~~ — done 2026-11-09
  (Verification). The first ANZ / CBA vault logins on the Mac will happen with
  real use.
- Two `Heartbeat.py --listen` processes were seen running at once on the laptop
  during this work (the lock should allow one) — unexplained, unrelated.
