"""Heartbeat.py — zero-API-cost dispatcher: a Telegram message starts a MyAgent run.

Each machine owns ONE Telegram bot (the desktop's, the laptop's, the Mac's),
because the Bot API's getUpdates confirms updates globally — one bot polled by
three machines would have each machine's confirmation swallow the others'
messages, where one bot per machine needs no routing code at all. A message
to a machine's bot:
  line 1          -> {Instruction}  (name of a saved agent instruction)
  the lines below -> {PromptCore}   (leading blank lines dropped)
The named instruction's text is rewritten: it must contain at least TWO
"*****" marker lines, and everything between the first and the LAST is
replaced with {PromptCore} (the header above the first marker and the footer
below the last are preserved — a PromptCore may hold ***** lines of its own).
The update is then CONFIRMED (getUpdates with offset = update_id + 1 — the
consume step, done BEFORE the launch so a tick that dies cannot start the
same run twice) and the instruction launched headless with a --result-file
(python MyAgent.py -l "{Instruction}" --headless --result-file …); a later
pass posts the run's outcome back to the chat — its status and final text,
or a note that it ended without a result (the watchdog, a crash) or has run
a day without one (a dialog). The bot answers at once: ▶ Started, ⚠ Not run
with the reason for a poison trigger, the usage for /start or /help, and
what it received for a message without text (a voice note, a sticker).
A run the listener launched also has its Agent Request dialogs RELAYED
(2026-10-26, myagent/relay.py): MyAgent writes each request to the run's
relay folder (--relay-dir), the watcher posts "❓ <name> asks: …" to the
chat and marks the chat as awaiting that run's reply, the chat's next text
message is written back as the reply and the dialog sends it as if typed —
so a whole Convo-mode conversation can be held from the phone; a request
answered at the PC first is withdrawn in the chat.
Only messages from the chat ids in ~/.config/myagent-telegram/config.json
are obeyed; anything else is confirmed, logged and ignored. Set up with
    python Heartbeat.py --telegram-pair <bot token from BotFather>
(saves the token, waits for the first message to the bot, records that chat)
and check a machine with --telegram-test. Telegram keeps an unconfirmed
message 24 hours, so a machine off for longer misses it.

No LLM is involved — every step is deterministic, so a poll costs nothing
but one Bot API call. Two ways to run it:
  python Heartbeat.py --listen   the RESIDENT LISTENER (since 2026-10-26, the
      way it is deployed): one process that long-polls the bot — getUpdates
      holds the connection up to LONG_POLL_SECONDS and answers the moment a
      message arrives — so a trigger is picked up within a second, and a
      watcher thread per run posts the outcome the instant the child exits.
      Started at logon by Task Scheduler (MyAgent_Heartbeat_Listener, via
      desktop_launchers/Heartbeat_Listener_Win.ps1 -Register) or a launchd
      KeepAlive job on the Mac; a lock file keeps it to one instance (two
      would consume each other's updates); a network failure backs off and
      retries; an hourly 'listener alive' line is its liveness record.
  python Heartbeat.py            ONE PASS: post finished runs' outcomes, drain
      one trigger, exit — the shape the scheduled job had (every 3 minutes on
      the laptop, 8 on the desktop) and still the hand-run check.

Until 2026-10-26 the trigger was an unread Gmail message whose SUBJECT was the
machine's code — APD (the Windows desktop, DESKTOP-NAMOR), APL (any other
Windows box, the laptop) or APM (macOS) — polled from one inbox by every
machine. Retired the day Telegram proved itself live, so a pass costs one
poll and this file no longer needs the Google libraries (UnreadSummary.py,
the mail digest, still does). The codes survive as MACHINE_CODE, the label
the bot's messages carry.
"""

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
from myagent.helpers import rotate_log_if_needed as _rotate_log  # noqa: E402
from myagent.datapaths import resolve_store as _resolve_store  # noqa: E402
from myagent import relay as _relay  # noqa: E402

# The machine's code, the label the bot's messages carry: the desktop answers
# as APD, any other Windows machine (the laptop) as APL, the Mac as APM. They
# were the email trigger's per-machine subjects until 2026-10-26 (a single
# Windows-wide "APW" until 2026-08-04).
if platform.system() != "Windows":
    MACHINE_CODE = "APM"
elif platform.node().upper() == "DESKTOP-NAMOR":
    MACHINE_CODE = "APD"
else:
    MACHINE_CODE = "APL"
MARKER = "*****"
# Same resolution as MyAgent itself: <OneDrive>/MyAppShare/agent_instructions.json
# when a OneDrive client is present, repo root otherwise — the marker rewrite
# below must edit the same store the spawned MyAgent will read.
INSTRUCTIONS_FILE = Path(_resolve_store("agent_instructions.json"))

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
# The mechanics live in myagent.helpers.rotate_log_if_needed, shared with
# the APICostLog.txt writers in MyAgent and SelfBot.
LOG_MAX_BYTES = 100_000


def rotate_log_if_needed():
    _rotate_log(LOG_FILE, LOG_MAX_BYTES)


# ── Telegram transport ────────────────────────────────────────────────────
# Per machine, outside OneDrive like the Google tokens: each machine's bot
# token is its own. {"bot_token": "...", "allowed_chat_ids": [123456789]}.
TELEGRAM_CONFIG_DIR = Path.home() / ".config" / "myagent-telegram"
TELEGRAM_CONFIG_FILE = TELEGRAM_CONFIG_DIR / "config.json"
# One record per run a Telegram trigger started, until its outcome is posted.
TELEGRAM_PENDING_DIR = TELEGRAM_CONFIG_DIR / "pending"
# Where those runs write their --result-file.
TELEGRAM_RESULTS_DIR = TELEGRAM_CONFIG_DIR / "results"
TELEGRAM_API_BASE = "https://api.telegram.org"
TELEGRAM_HTTP_TIMEOUT = 30
# Telegram caps a message at 4096 characters; replies are cut into chunks
# under that, at a line break where one is near.
TELEGRAM_CHUNK_CHARS = 3900
# A run with no result file after this long gets a last reply and is dropped
# from the pending list — it is parked on a dialog or was closed by hand.
TELEGRAM_RESULT_WAIT_SECONDS = 24 * 3600
# How long --telegram-pair waits for the first message to the new bot.
TELEGRAM_PAIR_WAIT_SECONDS = 120
HOST = platform.node()


def telegram_config():
    """The bot token and the chat ids this machine obeys, or None when it has
    no Telegram setup (no config file — the common case on a machine that
    uses email alone). A file that exists but is unusable raises, so the
    pass logs it instead of silently polling nothing."""
    if not TELEGRAM_CONFIG_FILE.exists():
        return None
    data = json.loads(TELEGRAM_CONFIG_FILE.read_text(encoding="utf-8"))
    token = str(data.get("bot_token", "")).strip()
    ids = data.get("allowed_chat_ids", [])
    if not token:
        raise RuntimeError(f"{TELEGRAM_CONFIG_FILE} has no bot_token")
    if (not isinstance(ids, list)
            or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids)):
        raise RuntimeError(f"{TELEGRAM_CONFIG_FILE}: allowed_chat_ids must be a "
                           "list of integers")
    return {"bot_token": token, "allowed_chat_ids": ids}


def telegram_write_config(cfg):
    TELEGRAM_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TELEGRAM_CONFIG_FILE.write_text(
        json.dumps({"bot_token": cfg["bot_token"],
                    "allowed_chat_ids": cfg["allowed_chat_ids"]}, indent=2),
        encoding="utf-8")
    os.chmod(TELEGRAM_CONFIG_FILE, 0o600)


def telegram_api(token, method, **params):
    """One Bot API call; the ``result`` field, or a RuntimeError that names
    the method and Telegram's description — never the URL, which carries
    the token (nothing here may put the token in a log line)."""
    url = f"{TELEGRAM_API_BASE}/bot{token}/{method}"
    req = urllib.request.Request(
        url, data=json.dumps(params).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    # A long poll (getUpdates timeout=N) holds the connection N seconds on
    # purpose, so the socket timeout sits above it.
    timeout = TELEGRAM_HTTP_TIMEOUT + int(params.get("timeout", 0) or 0)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8"))
        except Exception:
            raise RuntimeError(f"Telegram {method}: HTTP {e.code}") from None
    if not isinstance(payload, dict) or not payload.get("ok"):
        desc = payload.get("description", "not ok") if isinstance(payload, dict) else payload
        raise RuntimeError(f"Telegram {method}: {desc}")
    return payload.get("result")


def telegram_chunks(text, limit=TELEGRAM_CHUNK_CHARS):
    """``text`` as a list of sendMessage-sized pieces, cut at the last line
    break in each window when one sits past its midpoint, else hard."""
    text = (text or "").strip()
    if not text:
        return ["(empty)"]
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks


def telegram_send(token, chat_id, text):
    """Post ``text`` to the chat as plain text (no parse mode — a run's
    final text may hold Markdown that Telegram would refuse), in chunks."""
    for chunk in telegram_chunks(text):
        telegram_api(token, "sendMessage", chat_id=chat_id, text=chunk)


def telegram_updates(token):
    """Every unconfirmed message update, oldest first — a plain poll, no
    long wait, because the scheduler owns the cadence."""
    return telegram_api(token, "getUpdates", timeout=0,
                        allowed_updates=["message"]) or []


def telegram_confirm(token, update_id):
    """Consume one update: Telegram confirms every update below the offset,
    so offset = id + 1 confirms this one and the ones before it — the oldest
    is always the one processed, so nothing newer is lost."""
    telegram_api(token, "getUpdates", offset=update_id + 1, limit=1, timeout=0)


# What a message without text was, for the hint that answers it — a voice
# note is the likely one on a phone whose owner dictates everywhere else.
TELEGRAM_MESSAGE_KINDS = (
    ("voice", "a voice message"), ("audio", "an audio file"), ("photo", "a photo"),
    ("video_note", "a video message"), ("video", "a video"), ("sticker", "a sticker"),
    ("animation", "an animation"), ("document", "a file"), ("location", "a location"),
    ("contact", "a contact"), ("poll", "a poll"),
)


def telegram_message_kind(msg):
    """'a voice message', 'a photo', 'a sticker (👍)' … for a message with no
    text, else 'a message without text'. A sticker names its emoji: the big
    animated thumbs-up a phone offers above the keyboard IS a sticker, and
    the user who sent one deserves to learn which tap did it."""
    for key, kind in TELEGRAM_MESSAGE_KINDS:
        if msg.get(key):
            if key == "sticker" and isinstance(msg[key], dict) and msg[key].get("emoji"):
                kind += f" ({msg[key]['emoji']})"
            return kind
    return "a message without text"


# The chat's START button sends /start; /help is what a person types.
TELEGRAM_HELP_COMMANDS = ("/start", "/help")


def telegram_usage():
    return (f"This is Heartbeat on {HOST} ({MACHINE_CODE}). Send a text message: line 1 the "
            f"name of a saved MyAgent instruction, the prompt below it. The prompt "
            f"replaces the instruction's {MARKER} block; an instruction without one "
            f"gets it put in front of its text for that run; no prompt runs the "
            f"instruction as saved. Start the name with {SHOW_WINDOW_PREFIX} to watch "
            f"the run in its window on the PC (otherwise it runs headless). I confirm "
            f"the start and post the result when the run ends.")


SHOW_WINDOW_PREFIX = "@"


def parse_telegram_trigger(text):
    """(instruction name, prompt core, show window): line 1 the name, then
    the rest with leading blank lines dropped — the email body's layout, with
    or without its blank second line, since a phone keyboard makes that line
    easy to miss. A name that starts with "@" (2026-10-28, the user's
    request) asks for the run's window to be shown — the "@" is not part of
    the name — where a Telegram run is otherwise headless."""
    lines = (text or "").splitlines()
    name = lines[0].strip() if lines else ""
    show_window = name.startswith(SHOW_WINDOW_PREFIX)
    if show_window:
        name = name[len(SHOW_WINDOW_PREFIX):].strip()
    rest = lines[1:]
    while rest and not rest[0].strip():
        rest.pop(0)
    return name, "\n".join(rest), show_window


def pid_alive(pid):
    """Whether a process with this id is still running (a stale id reused by
    another process reads as alive; the 24-hour rule then ends the wait)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if platform.system() == "Windows":
        import ctypes
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259                    # STILL_ACTIVE
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def format_result_reply(name, result):
    """The message a finished run is reported with: a status line, the
    error if any, then the run's final text."""
    status = str(result.get("status", "unknown"))
    icon = {"completed": "✅", "stopped": "⏹", "error": "❌"}.get(status, "ℹ")
    head = f"{icon} {name} {status} on {HOST}"
    error = str(result.get("error") or "").strip()
    if error:
        head += f"\n{error}"
    body = str(result.get("final_text") or "").strip()
    return head + (f"\n\n{body}" if body else "")


def telegram_pending_write(update_id, chat_id, name, pid, result_file, relay_dir=None):
    """Record a launched run until its outcome is posted; the record's path."""
    TELEGRAM_PENDING_DIR.mkdir(parents=True, exist_ok=True)
    record = {"update_id": update_id, "chat_id": chat_id, "instruction": name,
              "pid": pid, "started": time.time(), "result_file": str(result_file),
              "relay_dir": str(relay_dir) if relay_dir else None}
    path = TELEGRAM_PENDING_DIR / f"{update_id}.json"
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return path


def telegram_deliver_one(cfg, pending):
    """Post one pending run's outcome if it is known: the result file the run
    wrote, else — when its process is gone — that it ended without one, else
    — after TELEGRAM_RESULT_WAIT_SECONDS — that no result is coming. True when
    a reply went out (the record and the result file are then removed)."""
    if not pending.exists():
        return False            # delivered by another caller meanwhile
    try:
        record = json.loads(pending.read_text(encoding="utf-8"))
        chat_id = record["chat_id"]
        result_file = Path(record["result_file"])
    except Exception as e:
        log(f"telegram: unreadable pending record {pending.name} "
            f"({type(e).__name__}: {e}) — removed")
        pending.unlink(missing_ok=True)
        return False
    name = record.get("instruction", "?")
    alive = pid_alive(record.get("pid"))
    if result_file.exists():
        try:
            result = json.loads(result_file.read_text(encoding="utf-8"))
        except Exception as e:
            if alive:
                return False    # still being written — next time
            result = {"status": "error", "final_text": "",
                      "error": f"result file unreadable ({type(e).__name__}: {e})"}
        text = format_result_reply(name, result)
        what = f"result ({result.get('status', '?')})"
    elif not alive:
        text = (f"⚠ {name} on {HOST} ended without a result — killed by the "
                f"watchdog, crashed, or closed from its window.")
        what = "no-result note"
    elif time.time() - float(record.get("started") or 0) > TELEGRAM_RESULT_WAIT_SECONDS:
        text = (f"⚠ {name} on {HOST} has run for over 24 hours without a "
                f"result — it may be waiting on a dialog. No further reply "
                f"will come for this run.")
        what = "timeout note"
    else:
        return False
    telegram_send(cfg["bot_token"], chat_id, text)
    log(f"telegram: sent {what} of {name!r} to chat {chat_id} "
        f"(update {record.get('update_id')})")
    pending.unlink(missing_ok=True)
    if result_file.exists():
        result_file.unlink()
    prepend_file_for(result_file).unlink(missing_ok=True)   # the run read it at start
    return True


def telegram_deliver_results(cfg):
    """Post the outcome of every pending run whose outcome is known. Returns
    the number of replies sent."""
    if not TELEGRAM_PENDING_DIR.exists():
        return 0
    with _DELIVER_LOCK:
        return sum(telegram_deliver_one(cfg, p)
                   for p in sorted(TELEGRAM_PENDING_DIR.glob("*.json")))


def telegram_handle_update(cfg, upd):
    """One update, start to finish. Returns (status, info): "ignored" (a
    stranger, or no message), "skipped" (no text, or /start — answered),
    "poison" (answered with the reason), "deferred" (left pending behind a
    live run), or "launched" with info = (pid, pending record path). A launch
    that fails AFTER the confirm raises, the user having been told to resend;
    a failure before it raises with the update left pending, so the next
    poll retries by itself."""
    token, allowed = cfg["bot_token"], cfg["allowed_chat_ids"]
    uid = upd.get("update_id")
    msg = upd.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    label = f"telegram update {uid}"
    if chat_id not in allowed:
        # A stranger (or a non-message update): consumed so it cannot sit at
        # the head of the queue forever, logged, never answered.
        telegram_confirm(token, uid)
        log(f"telegram: ignored update {uid} from chat {chat_id} — not in "
            f"allowed_chat_ids")
        return "ignored", None
    text = msg.get("text") or msg.get("caption")
    if not text:
        # Consumed and answered, then on to the next update: a sticker or a
        # voice note must not use up a pass's one launch.
        kind = telegram_message_kind(msg)
        telegram_confirm(token, uid)
        telegram_send(token, chat_id,
                      f"I received {kind}, which I cannot read. Send text: line 1 "
                      f"the instruction name, the prompt below it.")
        log(f"telegram: {kind} in update {uid} — consumed, no launch")
        return "skipped", None
    if text.strip().lower() in TELEGRAM_HELP_COMMANDS:
        telegram_confirm(token, uid)
        telegram_send(token, chat_id, telegram_usage())
        log(f"telegram: {text.strip()} in update {uid} — answered with the usage")
        return "skipped", None
    # A run of this chat's is waiting on an Agent Request: this message is
    # its reply, not a new trigger (the oldest open request first).
    with _RELAY_LOCK:
        waiting = AWAITING.get(chat_id) or []
        item = waiting.pop(0) if waiting else None
        if not waiting:
            AWAITING.pop(chat_id, None)
    if item is not None:
        relay_dir, seq, name = item
        telegram_confirm(token, uid)
        open_request = _relay.read_request(relay_dir)
        if open_request is None or open_request.get("seq") != seq:
            # Answered at the PC between the question and this reply.
            telegram_send(token, chat_id,
                          f"⚠ {name} on {HOST} had already been answered at the PC "
                          f"— your message was not used.")
            log(f"telegram: late reply to {name!r} request #{seq} in update {uid} "
                f"— already answered at the PC")
            return "skipped", None
        _relay.write_reply(relay_dir, seq, text)
        telegram_send(token, chat_id, f"↩ Sent to {name}.")
        log(f"telegram: reply to {name!r} request #{seq} from update {uid}")
        return "skipped", None
    name, prompt_core, show_window = parse_telegram_trigger(text)
    TELEGRAM_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    result_file = TELEGRAM_RESULTS_DIR / f"{uid}.json"
    relay_dir = TELEGRAM_RELAY_DIR / str(uid)
    consumed = []

    def consume():
        telegram_confirm(token, uid)
        consumed.append(uid)

    try:
        status, detail = dispatch_trigger(label, name, prompt_core, consume,
                                          result_file=result_file, relay_dir=relay_dir,
                                          show_window=show_window)
    except Exception as e:
        # Consumed already means the launch itself failed — an update cannot
        # be un-confirmed, so the user is told to resend.
        if consumed:
            telegram_send(token, chat_id,
                          f"❌ Could not start {name} on {HOST}: "
                          f"{type(e).__name__}: {e}\nResend the message to retry.")
        raise
    if status == "poison":
        # Answered and skipped: a mistyped name must not cost a pass its one
        # launch when a good trigger waits behind it.
        telegram_send(token, chat_id, f"⚠ Not run on {HOST}: {detail}")
        return "poison", None
    if status == "deferred":
        return "deferred", None
    pending = telegram_pending_write(uid, chat_id, name, detail, result_file, relay_dir)
    telegram_send(token, chat_id,
                  f"▶ Started {name} on {HOST}{' with its window' if show_window else ''}"
                  f" — the result will follow when it finishes.")
    return "launched", (detail, pending)


def process_telegram(cfg):
    """One PASS (the scheduled shape): deliver finished runs' outcomes, then
    drain the pending messages from the oldest up to and including the first
    launch — or the first trigger deferred behind a live run. Returns True
    when the pass did or found anything worth a log line of its own."""
    activity = telegram_deliver_results(cfg) > 0
    updates = telegram_updates(cfg["bot_token"])
    for upd in sorted(updates, key=lambda u: u.get("update_id", 0)):
        status, _info = telegram_handle_update(cfg, upd)
        if status in ("launched", "deferred"):
            return True         # one trigger per pass
        activity = True
    return activity


# ── Resident listener (--listen) ──────────────────────────────────────────
# getUpdates holds the connection this long and answers the moment a message
# arrives: one request a minute when idle, a second's latency when not.
LONG_POLL_SECONDS = 50
# A trigger deferred behind a live run is left unconfirmed, so the next poll
# would return it at once — the listener waits this long before asking again.
LISTEN_DEFER_RETRY_SECONDS = 30
# A failing poll (network down, Telegram down, the laptop asleep) backs off
# 5 → 10 → … → this ceiling, and repeats of one error are logged this often.
LISTEN_BACKOFF_MAX_SECONDS = 60
LISTEN_ERROR_LOG_EVERY_SECONDS = 600
# The listener's liveness record: one line this often while idle.
LISTEN_ALIVE_LINE_SECONDS = 3600
# A run this process did not launch (a record from an earlier life) is
# watched by polling its pid.
WATCH_POLL_SECONDS = 5
TELEGRAM_LOCK_FILE = TELEGRAM_CONFIG_DIR / "listener.lock"
# One folder per launched run for its Agent Request relay (myagent/relay.py),
# named by the update id like the result file; removed when the run ends.
TELEGRAM_RELAY_DIR = TELEGRAM_CONFIG_DIR / "relay"
# How often a watcher looks at its run's relay folder while the run lives.
RELAY_POLL_SECONDS = 1.0
# chat id → [(relay folder, request seq, instruction name), …] — the Agent
# Requests posted to that chat and not yet answered, oldest first: the chat's
# next text message answers the first of them. Watcher threads add and
# remove entries, the poll loop takes them, hence the lock.
AWAITING = {}
_RELAY_LOCK = threading.Lock()
# pid → Popen of every run THIS process launched, so a watcher can wait on the
# child instead of polling (launch_instruction fills it, watch_run takes).
LAUNCHED = {}
_DELIVER_LOCK = threading.Lock()


def listener_lock_acquire():
    """Claim the one-listener lock: False when another live listener holds
    it (two would confirm each other's updates). A stale lock — its pid dead
    — is taken over."""
    other = 0
    if TELEGRAM_LOCK_FILE.exists():
        try:
            other = int(TELEGRAM_LOCK_FILE.read_text(encoding="utf-8").strip() or 0)
        except (ValueError, OSError):
            other = 0
    if other and other != os.getpid() and pid_alive(other):
        return False
    TELEGRAM_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TELEGRAM_LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")
    return True


def listener_lock_release():
    try:
        if (TELEGRAM_LOCK_FILE.exists()
                and TELEGRAM_LOCK_FILE.read_text(encoding="utf-8").strip() == str(os.getpid())):
            TELEGRAM_LOCK_FILE.unlink()
    except OSError:
        pass


def relay_tick(cfg, record, relay_dir, sent_seq):
    """One look at a live run's relay folder: a request answered at the PC is
    withdrawn in the chat; a request not yet posted (its seq above
    ``sent_seq``) is posted and the chat marked as awaiting its reply.
    Returns the seq of the last request posted."""
    chat_id, name = record["chat_id"], record.get("instruction", "?")
    answered = _relay.take_answered(relay_dir)
    if answered is not None:
        with _RELAY_LOCK:
            waiting = AWAITING.get(chat_id) or []
            entry = (str(relay_dir), answered, name)
            withdrawn = entry in waiting
            if withdrawn:
                waiting.remove(entry)
            if not waiting:
                AWAITING.pop(chat_id, None)
        if withdrawn:
            telegram_send(cfg["bot_token"], chat_id,
                          f"✔ {name} was answered at the PC — no reply needed.")
            log(f"telegram: {name!r} request #{answered} answered at the PC — withdrawn")
    request = _relay.read_request(relay_dir)
    if request is not None and int(request.get("seq", 0)) > sent_seq:
        seq = int(request["seq"])
        with _RELAY_LOCK:
            AWAITING.setdefault(chat_id, []).append((str(relay_dir), seq, name))
        telegram_send(cfg["bot_token"], chat_id,
                      f"❓ {name} asks:\n{request.get('text', '')}\n\n"
                      f"Reply here and it goes to the run.")
        log(f"telegram: relayed {name!r} request #{seq} to chat {chat_id}")
        sent_seq = seq
    return sent_seq


def relay_forget(chat_id, relay_dir):
    """The run is over: whatever it was still asking is dropped."""
    with _RELAY_LOCK:
        waiting = [w for w in (AWAITING.get(chat_id) or []) if w[0] != str(relay_dir)]
        if waiting:
            AWAITING[chat_id] = waiting
        else:
            AWAITING.pop(chat_id, None)


def watch_run(cfg, pid, pending):
    """Wait for one run to end, relaying its Agent Requests meanwhile, then
    post its outcome at once. Waits on the Popen when this process launched
    the run, else polls the pid; the wait is capped at the record's
    TELEGRAM_RESULT_WAIT_SECONDS so the timeout note still goes out for a
    run parked on a dialog nobody answers."""
    try:
        record = json.loads(pending.read_text(encoding="utf-8"))
    except Exception:
        record = {}
    started = float(record.get("started") or 0) or time.time()
    deadline = started + TELEGRAM_RESULT_WAIT_SECONDS
    relay_dir = record.get("relay_dir")
    proc = LAUNCHED.pop(pid, None)
    step = RELAY_POLL_SECONDS if relay_dir else WATCH_POLL_SECONDS
    sent_seq = 0
    while time.time() < deadline:
        if proc is not None:
            try:
                proc.wait(timeout=step)
                break
            except subprocess.TimeoutExpired:
                pass
        else:
            if not pid_alive(pid):
                break
            time.sleep(step)
        if relay_dir:
            try:
                sent_seq = relay_tick(cfg, record, relay_dir, sent_seq)
            except Exception as e:
                log(f"ERROR relay {type(e).__name__}: {e}")
    if relay_dir:
        relay_forget(record.get("chat_id"), relay_dir)
    with _DELIVER_LOCK:
        telegram_deliver_one(cfg, pending)
    if relay_dir:
        shutil.rmtree(relay_dir, ignore_errors=True)


def start_watcher(cfg, pid, pending):
    threading.Thread(target=watch_run, args=(cfg, pid, pending),
                     name=f"watch-{pid}", daemon=True).start()


def listen():
    """--listen: the resident listener. Long-polls the bot for as long as the
    process lives, handles every update as it arrives, and gives each run a
    watcher that posts its outcome the moment it ends. Ctrl+C (or the task
    being ended) stops it; the exit code is what the entry block returns."""
    cfg = telegram_config()
    if cfg is None:
        raise RuntimeError(f"no Telegram setup on this machine ({TELEGRAM_CONFIG_FILE} "
                           f"is missing) — run: python Heartbeat.py --telegram-pair <bot token>")
    if not listener_lock_acquire():
        log(f"listener already running (lock {TELEGRAM_LOCK_FILE}) — this one exits")
        return 1
    log(f"listener started (PID {os.getpid()}) on {HOST} ({MACHINE_CODE}) — "
        f"long-polling the bot")
    token = cfg["bot_token"]
    # Runs an earlier life launched and never reported on: watch them too.
    if TELEGRAM_PENDING_DIR.exists():
        for pending in sorted(TELEGRAM_PENDING_DIR.glob("*.json")):
            try:
                pid = json.loads(pending.read_text(encoding="utf-8")).get("pid")
            except Exception:
                continue        # telegram_deliver_one drops it when it is next seen
            start_watcher(cfg, pid, pending)
    polls = 0
    backoff = 0
    last_alive = time.time()
    last_error = (None, 0.0)
    try:
        while True:
            try:
                updates = telegram_api(token, "getUpdates", timeout=LONG_POLL_SECONDS,
                                       allowed_updates=["message"]) or []
                polls += 1
                backoff = 0
                deferred = False
                for upd in sorted(updates, key=lambda u: u.get("update_id", 0)):
                    status, info = telegram_handle_update(cfg, upd)
                    if status == "launched":
                        start_watcher(cfg, *info)
                    elif status == "deferred":
                        deferred = True
                        break   # the rest waits behind it, in order
                if deferred:
                    time.sleep(LISTEN_DEFER_RETRY_SECONDS)
                if time.time() - last_alive >= LISTEN_ALIVE_LINE_SECONDS:
                    log(f"listener alive — {polls} polls since start")
                    last_alive = time.time()
            except KeyboardInterrupt:
                raise
            except Exception as e:
                backoff = min(LISTEN_BACKOFF_MAX_SECONDS, backoff * 2 if backoff else 5)
                text = f"{type(e).__name__}: {e}"
                if (text != last_error[0]
                        or time.time() - last_error[1] >= LISTEN_ERROR_LOG_EVERY_SECONDS):
                    log(f"ERROR listener {text} — retrying in {backoff} s")
                    last_error = (text, time.time())
                time.sleep(backoff)
    except KeyboardInterrupt:
        log("listener stopped")
    finally:
        listener_lock_release()
    return 0


def telegram_pair(token):
    """--telegram-pair: save the token, wait for the first message to the
    bot and record that chat as the one this machine obeys."""
    me = telegram_api(token, "getMe")
    username = me.get("username", "?")
    cfg = {"bot_token": token, "allowed_chat_ids": []}
    try:
        old = telegram_config()
        if old:
            cfg["allowed_chat_ids"] = list(old["allowed_chat_ids"])
    except Exception:
        pass
    telegram_write_config(cfg)
    print(f"Bot @{username} saved to {TELEGRAM_CONFIG_FILE}")
    print(f"Now send any message to @{username} from your phone - waiting up to "
          f"{TELEGRAM_PAIR_WAIT_SECONDS} s ...")
    deadline = time.time() + TELEGRAM_PAIR_WAIT_SECONDS
    while time.time() < deadline:
        updates = telegram_api(token, "getUpdates", timeout=20,
                               allowed_updates=["message"]) or []
        if not updates:
            continue
        # Everything that arrived is consumed — the pairing message must not
        # be read as an instruction by the next tick.
        telegram_confirm(token, max(u.get("update_id", 0) for u in updates))
        for upd in updates:
            chat = (upd.get("message") or {}).get("chat") or {}
            if chat.get("id") is None:
                continue
            chat_id = chat["id"]
            if chat_id not in cfg["allowed_chat_ids"]:
                cfg["allowed_chat_ids"].append(chat_id)
                telegram_write_config(cfg)
            telegram_send(token, chat_id,
                          f"Paired with {HOST} ({MACHINE_CODE}). Send me the instruction "
                          f"name on line 1 and the prompt below it; I reply when "
                          f"the run ends.")
            who = " ".join(p for p in (chat.get("first_name"), chat.get("last_name")) if p)
            print(f"Paired with chat {chat_id} ({who or 'no name'}). Done.")
            log(f"telegram: paired with chat {chat_id} on bot @{username}")
            return 0
    print("No message arrived. Run the command again and message the bot while it waits.")
    return 1


def telegram_test():
    """--telegram-test: one message to every allowed chat, so a machine's
    setup can be checked from the phone."""
    cfg = telegram_config()
    if not cfg:
        print(f"No Telegram setup on this machine ({TELEGRAM_CONFIG_FILE} is missing) "
              f"- run --telegram-pair first.")
        return 1
    if not cfg["allowed_chat_ids"]:
        print("No allowed chat yet - run --telegram-pair and message the bot.")
        return 1
    for chat_id in cfg["allowed_chat_ids"]:
        telegram_send(cfg["bot_token"], chat_id,
                      f"Heartbeat on {HOST} ({MACHINE_CODE}) can reach you.")
        print(f"Sent a test message to chat {chat_id}.")
    return 0


def rewrite_instruction(name, prompt_core):
    """Put the trigger's prompt into the saved instruction where it has a
    place for it, and say what the launch must do: "replaced" — the text
    between the first and the LAST ***** marker line is now prompt_core,
    written atomically; "prepend" — the instruction has no such block (fewer
    than two markers), nothing is written, and the launch puts the prompt in
    front of the saved text for that run (`--prepend-file`); "unchanged" —
    the prompt is empty, nothing is written, the instruction runs as saved.
    An unknown name is a LookupError (the trigger is poison-pilled).

    The marker form keeps the header (above the first marker) and the footer
    (below the last marker); only the middle is swapped. The LAST, not the
    second: a prompt may contain a ***** line of its own, and with the second
    marker the next trigger replaced only the part above it — the rest of the
    old core survived under the new one, growing with every trigger. Until
    2026-10-28 fewer than two markers was poison too; the user asked that
    such an instruction run with the prompt prepended — for the RUN, never
    written into the store, which would grow by one prompt per trigger."""
    instructions = json.loads(INSTRUCTIONS_FILE.read_text(encoding="utf-8"))
    if name not in instructions:
        raise LookupError(f"instruction {name!r} not found")
    if not prompt_core.strip():
        return "unchanged"
    lines = instructions[name]["text"].split("\n")
    marker_idxs = [i for i, l in enumerate(lines) if l.strip().startswith(MARKER)]
    if len(marker_idxs) < 2:
        return "prepend"
    first, last = marker_idxs[0], marker_idxs[-1]
    instructions[name]["text"] = "\n".join(
        lines[: first + 1] + [prompt_core] + lines[last:]
    )
    # Temp file must live beside the store: os.replace across filesystems
    # (repo volume → OneDrive's File Provider volume) raises EXDEV.
    fd, tmp = tempfile.mkstemp(dir=str(INSTRUCTIONS_FILE.parent), suffix=".json.tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(instructions, f, indent=2, ensure_ascii=False)
    os.replace(tmp, INSTRUCTIONS_FILE)
    return "replaced"


def prepend_file_for(result_file):
    """Where a run's prepended prompt is written: beside its result file,
    `<update id>.prepend.txt`, so the delivery that removes the result file
    removes it too; a caller without a result file gets a temp file in the
    results folder."""
    if result_file:
        return Path(result_file).with_suffix(".prepend.txt")
    TELEGRAM_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(TELEGRAM_RESULTS_DIR), suffix=".prepend.txt")
    os.close(fd)
    return Path(tmp)


# A previous spawn younger than this blocks a new one (skip + retry next tick);
# older means it is hung — kill it and proceed, so a stuck run can never block
# new triggers forever. Matches the AI heartbeat's always-spawn semantics with
# at most this much delay, while still preventing instance pile-up.
WATCHDOG_SECONDS = 900

# The Windows process query normally answers in 2-3 s; a wedged WMI must not
# hang the pass (seen once: a single query stalled for minutes). On timeout
# subprocess.run kills PowerShell and raises, so the pass fails BEFORE the
# trigger is marked read — the email stays unread and the next tick retries,
# rather than launching a run that may duplicate a live one.
WATCHDOG_QUERY_TIMEOUT = 60


def watchdog_pattern(name):
    """The regex that finds a headless run of ``name`` in a Windows process
    command line. There the name appears as subprocess.list2cmdline wrote it
    for launch_instruction's Popen — QUOTED when it holds a space ("Balance
    Westpac Mastercard account"), its own quotes backslash-escaped — so the
    pattern is built from that form, with the surrounding quotes optional (a
    run started by hand may quote a name that needs none). A bare
    re.escape(name) matched only names without a space: for every other
    instruction the watchdog saw nothing running, let a second run start
    beside the first, and never reaped a hung one. (The same regex serves
    Python's re and PowerShell's .NET -match.)"""
    arg = subprocess.list2cmdline([name])
    if len(arg) >= 2 and arg[0] == arg[-1] == '"':
        arg = arg[1:-1]
    return (re.escape("MyAgent.py -l ") + '"?' + re.escape(arg) + '"?'
            + re.escape(" --headless"))


def running_instances(name):
    """[(pid, age_seconds)] for live headless runs of this instruction."""
    if platform.system() == "Windows":
        # No pgrep/ps on Windows — query process command lines via CIM.
        # PowerShell single-quoted strings escape ' by doubling it.
        pattern = watchdog_pattern(name).replace("'", "''")
        script = (
            "Get-CimInstance Win32_Process -Filter \"Name LIKE 'python%'\" | "
            f"Where-Object {{ $_.CommandLine -match '{pattern}' }} | "
            "ForEach-Object { \"$($_.ProcessId) "
            "$([int]((Get-Date) - $_.CreationDate).TotalSeconds)\" }"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True, text=True, timeout=WATCHDOG_QUERY_TIMEOUT,
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


def launch_instruction(name, result_file=None, relay_dir=None, prepend_file=None,
                       show_window=False):
    """Spawn the headless run, detached. ``result_file``, ``relay_dir``,
    ``prepend_file`` and ``show_window`` (the Telegram transport's) are
    appended AFTER --headless so the watchdog's command-line pattern, which
    ends there, still matches — a shown window is still a --headless run
    (MyAgent's --show-window keeps the window and nothing else)."""
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
    cmd = [str(python), "MyAgent.py", "-l", name, "--headless"]
    if result_file:
        cmd += ["--result-file", str(result_file)]
    if relay_dir:
        cmd += ["--relay-dir", str(relay_dir)]
    if prepend_file:
        cmd += ["--prepend-file", str(prepend_file)]
    if show_window:
        cmd.append("--show-window")
    proc = subprocess.Popen(
        cmd,
        cwd=str(BASE_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **detach,
    )
    LAUNCHED[proc.pid] = proc       # the listener's watcher waits on it
    return proc.pid


def dispatch_trigger(label, name, prompt_core, consume, result_file=None, relay_dir=None,
                     show_window=False):
    """The tail every trigger goes through: rewrite the instruction, run the
    watchdog, CONSUME the trigger, launch. ``consume`` is the transport's
    "mark read" — the Telegram update confirmed (the email transport's was
    the UNREAD label off). Returns ("poison", reason) for a trigger consumed
    without a launch, ("deferred", reason) for one left pending behind a
    live run, ("launched", pid) otherwise.

    The order is the safety rule: the trigger is consumed BEFORE the launch.
    A consume that fails costs nothing — nothing ran, the trigger stays
    pending, the next tick retries — where consuming after the launch left
    the trigger pending beside a live run, and a later tick launched the
    same command AGAIN (killing the first as "hung" once it passed
    WATCHDOG_SECONDS). A launch that fails after the consume raises; an
    update cannot be un-confirmed, so the caller tells the user to resend."""
    # Malformed triggers are poison messages: consumed so a permanently bad
    # one can't retry on every tick forever, logged, no launch.
    if not name:
        log(f"POISON {label}: empty first line — consumed, no launch")
        consume()
        return "poison", "the first line must name a saved instruction"

    # Rewrite FIRST, unconditionally — mirroring the AI Heartbeat_Instruction's
    # order (modify text field, then run_instruction). The command must land in
    # agent_instructions.json immediately even if the spawn below is deferred.
    # Idempotent, so a deferred trigger re-rewriting on the next tick is harmless.
    # The verdict says how the prompt reaches the run (2026-10-28): "replaced"
    # — it is in the store's ***** block; "prepend" — the instruction has no
    # block, so the prompt goes in a file the launch hands over as
    # --prepend-file, put in front of the saved text for that run only;
    # "unchanged" — no prompt, the instruction runs as saved. (A fake rewrite
    # in the tests returns None: the old, replaced, behaviour.)
    try:
        verdict = rewrite_instruction(name, prompt_core) or "replaced"
    except LookupError as e:
        log(f"POISON {label}: {e} — consumed, no launch")
        consume()
        return "poison", str(e)
    prepend_file = None
    if verdict == "prepend":
        prepend_file = prepend_file_for(result_file)
        prepend_file.parent.mkdir(parents=True, exist_ok=True)
        prepend_file.write_text(prompt_core, encoding="utf-8")
        log(f"{name!r} has no {MARKER!r} block: {len(prompt_core)} chars "
            f"prepended for this run ({label})")
    elif verdict == "unchanged":
        log(f"no prompt under the name: {name!r} runs as saved ({label})")
    else:
        log(f"rewrote {name!r}: {len(prompt_core)} chars below marker ({label})")

    # Watchdog: a recent run defers the new spawn (the trigger stays pending so
    # the next tick retries the launch). A run older than WATCHDOG_SECONDS is
    # presumed hung — kill it and fall through to the new spawn.
    for pid, age in running_instances(name):
        if age < WATCHDOG_SECONDS:
            log(f"spawn deferred for {label}: {name!r} still running "
                f"(PID {pid}, {age}s old)")
            return "deferred", f"{name!r} still running (PID {pid}, {age}s old)"
        os.kill(pid, 15)
        log(f"WATCHDOG: killed hung {name!r} run (PID {pid}, {age}s old)")

    consume()
    launch_kwargs = {"result_file": result_file, "relay_dir": relay_dir}
    if prepend_file:
        launch_kwargs["prepend_file"] = prepend_file
    if show_window:
        launch_kwargs["show_window"] = True
    try:
        pid = launch_instruction(name, **launch_kwargs)
    except Exception:
        if prepend_file:        # no run will read it, no delivery will remove it
            prepend_file.unlink(missing_ok=True)
        raise
    how = "with its window" if show_window else "headless"
    log(f"launched {name!r} {how} (PID {pid}) from {label}")
    return "launched", pid


def main():
    """One pass. A machine without a Telegram setup is an error every tick —
    nothing can reach it until it is paired — and an idle pass writes the one
    'nothing found' line the log viewers hide."""
    cfg = telegram_config()
    if cfg is None:
        raise RuntimeError(f"no Telegram setup on this machine ({TELEGRAM_CONFIG_FILE} "
                           f"is missing) — run: python Heartbeat.py --telegram-pair <bot token>")
    if not process_telegram(cfg):
        log("checked — nothing found")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Heartbeat: a Telegram message to this machine's bot starts a MyAgent run. "
                    "Without --listen, one pass: post finished runs' results, drain one trigger.")
    parser.add_argument("--listen", action="store_true",
                        help="Run as the resident listener: long-poll the bot until stopped, "
                             "handle every message as it arrives, post each run's result "
                             "the moment it ends")
    parser.add_argument("--telegram-pair", metavar="BOT_TOKEN",
                        help="Save this machine's bot token, then wait for the first "
                             "message to the bot and record that chat as allowed")
    parser.add_argument("--telegram-test", action="store_true",
                        help="Send a test message to every allowed chat and exit")
    args = parser.parse_args()
    if args.telegram_pair:
        sys.exit(telegram_pair(args.telegram_pair))
    if args.telegram_test:
        sys.exit(telegram_test())
    rotate_log_if_needed()
    try:
        if args.listen:
            sys.exit(listen())
        main()
    except SystemExit:
        raise
    except Exception as e:
        # A transient failure (network, Telegram down) leaves the update
        # unconfirmed — the next tick retries by itself.
        log(f"ERROR {type(e).__name__}: {e}")
        sys.exit(1)
