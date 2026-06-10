"""Heartbeat.py — zero-API-cost replacement for the Heartbeat_Instruction.

Polls namor5772@gmail.com for an unread Inbox email with Subject "AGENT PROMPT".
If found:
  line 1 of the body  -> {Instruction}  (name of a saved agent instruction)
  lines 3+ of the body -> {PromptCore}
The named instruction's text is rewritten: everything below its "*****" marker
line is replaced with {PromptCore}. The instruction is then launched headless
(python MyAgent.py -l "{Instruction}" --headless) and the email is marked read.

No LLM is involved — every step is deterministic, so the 10-minute poll costs
nothing but a Gmail API call. Designed to be run by launchd (StartInterval 600);
exits silently when there is nothing to do.

Reuses MyAgent's Google config: token at ~/.config/myagent-google/
{account}_token.json with the gmail.modify scope. Never starts the interactive
OAuth flow — if the stored token is missing or unrefreshable it logs and exits,
leaving the email unread so the next tick (or a manual MyAgent run, which CAN
do the browser dance) recovers.
"""

import base64
import json
import os
import platform
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
from myagent.helpers import extract_text_from_html  # noqa: E402

ACCOUNT = "namor5772"
SUBJECT = "AGENT PROMPT"
MARKER = "*****"
GOOGLE_CONFIG_DIR = Path.home() / ".config" / "myagent-google"
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
INSTRUCTIONS_FILE = BASE_DIR / "agent_instructions.json"

if platform.system() == "Darwin":
    LOG_FILE = Path.home() / "Library" / "Logs" / "myagent" / "heartbeat.log"
else:
    LOG_FILE = BASE_DIR / "heartbeat.log"


def log(msg):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")


def gmail_service():
    token_path = GOOGLE_CONFIG_DIR / f"{ACCOUNT}_token.json"
    if not token_path.exists():
        raise RuntimeError(f"no token at {token_path} — run MyAgent once to authorize")
    creds = Credentials.from_authorized_user_file(str(token_path), GMAIL_SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_path.write_text(creds.to_json(), encoding="utf-8")
            os.chmod(token_path, 0o600)
        else:
            raise RuntimeError("stored token invalid and not refreshable")
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def extract_body(payload):
    """Depth-first search of the MIME tree: prefer text/plain, fall back to HTML."""
    plain, html = None, None
    stack = [payload]
    while stack:
        part = stack.pop()
        stack.extend(part.get("parts", []))
        data = part.get("body", {}).get("data")
        if not data:
            continue
        text = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        mime = part.get("mimeType", "")
        if mime == "text/plain" and plain is None:
            plain = text
        elif mime == "text/html" and html is None:
            html = text
    if plain is not None:
        return plain
    if html is not None:
        return extract_text_from_html(html)
    return ""


def rewrite_instruction(name, prompt_core):
    """Replace everything below the ***** marker line; atomic write."""
    instructions = json.loads(INSTRUCTIONS_FILE.read_text(encoding="utf-8"))
    if name not in instructions:
        raise LookupError(f"instruction {name!r} not found")
    lines = instructions[name]["text"].split("\n")
    marker_idx = next(
        (i for i, l in enumerate(lines) if l.strip().startswith(MARKER)), None
    )
    if marker_idx is None:
        raise LookupError(f"instruction {name!r} has no {MARKER!r} marker line")
    instructions[name]["text"] = "\n".join(lines[: marker_idx + 1] + [prompt_core])
    fd, tmp = tempfile.mkstemp(dir=str(BASE_DIR), suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(instructions, f, indent=2, ensure_ascii=False)
    os.replace(tmp, INSTRUCTIONS_FILE)


def launch_instruction(name):
    if platform.system() == "Windows":
        python = BASE_DIR / ".venv" / "Scripts" / "python.exe"
    else:
        python = BASE_DIR / ".venv" / "bin" / "python"
    if not python.exists():
        python = Path(sys.executable)
    proc = subprocess.Popen(
        [str(python), "MyAgent.py", "-l", name, "--headless"],
        cwd=str(BASE_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return proc.pid


def main():
    service = gmail_service()
    resp = (
        service.users()
        .messages()
        .list(userId="me", q=f'in:inbox is:unread subject:"{SUBJECT}"', maxResults=10)
        .execute()
    )
    candidates = resp.get("messages", [])
    if not candidates:
        log("checked — nothing found")
        return

    # Gmail subject: search is word-based, so verify the exact header. Process
    # the OLDEST exact match (list is newest-first); any others stay unread and
    # are picked up on subsequent ticks — one spawn per tick by design.
    msg = None
    for ref in reversed(candidates):
        full = (
            service.users()
            .messages()
            .get(userId="me", id=ref["id"], format="full")
            .execute()
        )
        headers = {h["name"].lower(): h["value"] for h in full["payload"]["headers"]}
        if headers.get("subject", "").strip() == SUBJECT:
            msg = full
            break
    if msg is None:
        log(f"checked — {len(candidates)} candidate(s), none with exact subject")
        return

    def mark_read():
        service.users().messages().modify(
            userId="me", id=msg["id"], body={"removeLabelIds": ["UNREAD"]}
        ).execute()

    body = extract_body(msg["payload"])
    lines = body.splitlines()
    name = lines[0].strip() if lines else ""
    prompt_core = "\n".join(lines[2:])

    # Malformed emails are poison messages: mark them read so a permanently bad
    # email can't retry every 10 minutes forever, log why, and skip the launch.
    if not name:
        log(f"POISON msg {msg['id']}: empty first line — marked read, no launch")
        mark_read()
        return
    try:
        rewrite_instruction(name, prompt_core)
    except LookupError as e:
        log(f"POISON msg {msg['id']}: {e} — marked read, no launch")
        mark_read()
        return

    pid = launch_instruction(name)
    mark_read()
    log(f"launched {name!r} headless (PID {pid}) from msg {msg['id']}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Transient failures (network, auth) leave the email unread — the next
        # 10-minute tick retries naturally.
        log(f"ERROR {type(e).__name__}: {e}")
        sys.exit(1)
