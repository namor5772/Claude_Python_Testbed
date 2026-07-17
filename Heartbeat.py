"""Heartbeat.py — zero-API-cost replacement for the Heartbeat_Instruction.

Polls namor5772@gmail.com for an unread Inbox email with the per-machine
Subject "APW" (Windows) or "APM" (macOS) — both machines poll the same inbox,
so the per-machine subject routes each trigger to exactly one executor instead
of racing for it.
If found:
  line 1 of the body  -> {Instruction}  (name of a saved agent instruction)
  lines 3+ of the body -> {PromptCore}
The named instruction's text is rewritten: it must contain TWO "*****" marker
lines, and everything between them is replaced with {PromptCore} (the header
above the first marker and the footer below the second are preserved). The
instruction is then launched headless (python MyAgent.py -l "{Instruction}"
--headless) and the email is marked read.

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
import re
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
# Per-machine trigger subject: the Windows box and the Mac mini poll the SAME
# inbox, so each answers only its own subject — deterministic routing, and no
# tick-timing race where both process (or one steals) the other's trigger.
SUBJECT = "APW" if platform.system() == "Windows" else "APM"
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


# One-slot log rotation: past this size the log is renamed to heartbeat.log.old
# (replacing the previous archive) and restarts with a marker line, so the
# 5-minute ticks can't grow it unbounded. ~100 KB is roughly 2,000 lines.
LOG_MAX_BYTES = 100_000


def rotate_log_if_needed():
    try:
        if LOG_FILE.stat().st_size <= LOG_MAX_BYTES:
            return
        LOG_FILE.replace(LOG_FILE.with_name(LOG_FILE.name + ".old"))
    except OSError:
        return  # no log yet, or the archive is locked open — try next tick
    log(f"log restarted (previous log archived to {LOG_FILE.name}.old)")


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
    """Replace everything between the two ***** marker lines; atomic write.

    The text keeps its header (above the first marker) and footer (below the
    second marker); only the middle is swapped for prompt_core. Fewer than two
    markers is a LookupError so the trigger email gets poison-pilled instead of
    silently mangling the instruction."""
    instructions = json.loads(INSTRUCTIONS_FILE.read_text(encoding="utf-8"))
    if name not in instructions:
        raise LookupError(f"instruction {name!r} not found")
    lines = instructions[name]["text"].split("\n")
    marker_idxs = [i for i, l in enumerate(lines) if l.strip().startswith(MARKER)]
    if len(marker_idxs) < 2:
        raise LookupError(
            f"instruction {name!r} needs two {MARKER!r} marker lines "
            f"(found {len(marker_idxs)})"
        )
    first, second = marker_idxs[0], marker_idxs[1]
    instructions[name]["text"] = "\n".join(
        lines[: first + 1] + [prompt_core] + lines[second:]
    )
    fd, tmp = tempfile.mkstemp(dir=str(BASE_DIR), suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(instructions, f, indent=2, ensure_ascii=False)
    os.replace(tmp, INSTRUCTIONS_FILE)


# A previous spawn younger than this blocks a new one (skip + retry next tick);
# older means it is hung — kill it and proceed, so a stuck run can never block
# new triggers forever. Matches the AI heartbeat's always-spawn semantics with
# at most this much delay, while still preventing instance pile-up.
WATCHDOG_SECONDS = 900


def running_instances(name):
    """[(pid, age_seconds)] for live headless runs of this instruction."""
    if platform.system() == "Windows":
        # No pgrep/ps on Windows — query process command lines via CIM.
        # PowerShell single-quoted strings escape ' by doubling it.
        pattern = re.escape(f"MyAgent.py -l {name} --headless").replace("'", "''")
        script = (
            "Get-CimInstance Win32_Process -Filter \"Name LIKE 'python%'\" | "
            f"Where-Object {{ $_.CommandLine -match '{pattern}' }} | "
            "ForEach-Object { \"$($_.ProcessId) "
            "$([int]((Get-Date) - $_.CreationDate).TotalSeconds)\" }"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True, text=True,
            # No console flash when run by Task Scheduler under pythonw.exe
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        out = []
        for line in result.stdout.split("\n"):
            parts = line.split()
            if len(parts) == 2 and all(p.isdigit() for p in parts):
                out.append((int(parts[0]), int(parts[1])))
        return out

    result = subprocess.run(
        ["pgrep", "-f", f"MyAgent.py -l {re.escape(name)} --headless"],
        capture_output=True, text=True,
    )
    out = []
    for pid in result.stdout.split():
        etime = subprocess.run(
            ["ps", "-o", "etime=", "-p", pid], capture_output=True, text=True
        ).stdout.strip()
        if not etime:
            continue  # exited between pgrep and ps
        # etime formats: [[dd-]hh:]mm:ss
        days, _, clock = etime.rpartition("-")
        parts = [int(p) for p in clock.split(":")]
        secs = 0
        for p in parts:
            secs = secs * 60 + p
        out.append((int(pid), secs + int(days or 0) * 86400))
    return out


def launch_instruction(name):
    if platform.system() == "Windows":
        python = BASE_DIR / ".venv" / "Scripts" / "python.exe"
        # start_new_session is POSIX-only; CREATE_NO_WINDOW detaches the child
        # from any console (same convention as _SUBPROCESS_NOWND in MyAgent).
        detach = {"creationflags": subprocess.CREATE_NO_WINDOW}
    else:
        python = BASE_DIR / ".venv" / "bin" / "python"
        detach = {"start_new_session": True}
    if not python.exists():
        python = Path(sys.executable)
    proc = subprocess.Popen(
        [str(python), "MyAgent.py", "-l", name, "--headless"],
        cwd=str(BASE_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **detach,
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

    # Rewrite FIRST, unconditionally — mirroring the AI Heartbeat_Instruction's
    # order (modify text field, then run_instruction). The command must land in
    # agent_instructions.json immediately even if the spawn below is deferred.
    # Idempotent, so a deferred email re-rewriting on the next tick is harmless.
    try:
        rewrite_instruction(name, prompt_core)
        log(f"rewrote {name!r}: {len(prompt_core)} chars below marker "
            f"(msg {msg['id']})")
    except LookupError as e:
        log(f"POISON msg {msg['id']}: {e} — marked read, no launch")
        mark_read()
        return

    # Watchdog: a recent run defers the new spawn (email stays unread so the
    # next tick retries the launch). A run older than WATCHDOG_SECONDS is
    # presumed hung — kill it and fall through to the new spawn.
    for pid, age in running_instances(name):
        if age < WATCHDOG_SECONDS:
            log(f"spawn deferred for msg {msg['id']}: {name!r} still running "
                f"(PID {pid}, {age}s old)")
            return
        os.kill(pid, 15)
        log(f"WATCHDOG: killed hung {name!r} run (PID {pid}, {age}s old)")

    pid = launch_instruction(name)
    mark_read()
    log(f"launched {name!r} headless (PID {pid}) from msg {msg['id']}")


if __name__ == "__main__":
    rotate_log_if_needed()
    try:
        main()
    except Exception as e:
        # Transient failures (network, auth) leave the email unread — the next
        # 10-minute tick retries naturally.
        log(f"ERROR {type(e).__name__}: {e}")
        sys.exit(1)
