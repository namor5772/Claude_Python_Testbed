#!/bin/bash
# store_bank_credentials.command — opened by the "Bank Credentials" desktop launcher
# (the macOS twin of BankCredentials_Win.ps1).
#
# A Terminal menu that stores or updates the bank login details MyAgent's
# westpac-login / anz-login / cba-login skills read from the macOS Keychain
# (keyring services myagent-westpac / myagent-anz / myagent-cba, 2026-11-09):
# pick a bank and that skill's own helper runs in `store-credentials` mode,
# asking for the ID and the password twice with no echo and writing the
# Keychain itself. This script never sees a value, and Terminal is the right
# host for it: the helper's getpass needs a real tty and refuses a pipe.
#
# Machine-independent: the repo is this file's grandparent and the skills
# folder comes from myagent.datapaths.resolve_skills_dir (the OneDrive share),
# so only the .app that opens it stores an absolute path.
DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$DIR")"
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

SKILLS="$(cd "$REPO" && "$PY" -B -c 'from myagent.datapaths import resolve_skills_dir; print(resolve_skills_dir())' 2>/dev/null)"
if [ -z "$SKILLS" ]; then
  echo "Could not resolve the skills folder (myagent.datapaths.resolve_skills_dir) with $PY."
  read -r -p "Press Return to close this window… " _
  exit 1
fi

NAMES=("Westpac" "ANZ" "CBA (CommBank)")
WHATS=("Customer ID + password" "Customer Registration Number + password" "client number + password")
HELPERS=("$SKILLS/westpac-login/scripts/macos_login_support.py"
         "$SKILLS/anz-login/scripts/anz_login_support.py"
         "$SKILLS/cba-login/scripts/cba_login_support.py")

state() { # <helper> -> stored / MISSING / STOP …
  [ -f "$1" ] || { echo "helper not found"; return; }
  local out; out="$("$PY" -B "$1" check-credentials 2>/dev/null)"
  case "$out" in
    *CREDENTIALS_AVAILABLE*) echo "stored" ;;
    *CREDENTIALS_MISSING*)   echo "MISSING" ;;
    *) echo "STOP — run its preflight (is keyring installed in $PY?)" ;;
  esac
}

while true; do
  clear
  echo "Bank Credentials — store or update the login details MyAgent's bank skills use"
  echo
  echo "They go into this Mac's login Keychain. Nothing is written to a file, nothing you"
  echo "type is shown, and MyAgent reads them only inside the bank skill's helper at login"
  echo "time. Do this after changing a password on the bank's site. The first Keychain"
  echo "access by this Python may ask for permission — choose Always Allow."
  echo
  for i in 0 1 2; do
    printf '  %d) %-15s %-42s %s\n' $((i + 1)) "${NAMES[$i]}" "${WHATS[$i]}" "$(state "${HELPERS[$i]}")"
  done
  echo "  Q) close"
  echo
  read -r -p "Which bank? [1/2/3/Q] " choice
  case "$choice" in
    1|2|3) i=$((choice - 1)) ;;
    *) exit 0 ;;
  esac
  helper="${HELPERS[$i]}"
  if [ ! -f "$helper" ]; then
    echo; echo "The ${NAMES[$i]} helper is missing: $helper"
    read -r -p "Press Return to go back… " _
    continue
  fi
  echo
  echo "${NAMES[$i]}: the helper will ask for the ${WHATS[$i]/ + /, then the new } and the password again."
  echo "Type each answer and press Return — nothing appears as you type."
  echo
  if "$PY" -B "$helper" store-credentials; then
    echo; echo "Stored. MyAgent's next ${NAMES[$i]} login uses the new details."
  else
    echo; echo "Nothing stored (see the reason above). Run it again when ready."
  fi
  read -r -p "Press Return to go back to the menu… " _
done
