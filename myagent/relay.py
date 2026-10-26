"""Agent Request relay — the file protocol MyAgent and Heartbeat's Telegram
listener share, so a run's Agent Request dialog can be answered from the
phone (2026-10-26).

A run the listener launched gets a folder of its own (MyAgent's --relay-dir).
MyAgent must never read Telegram itself — the listener owns the bot's
getUpdates connection, and a second reader would consume its messages — so
the two processes talk through three small files in that folder:

  request.json   {"seq": n, "text": "…", "written": ts}
      written by MyAgent when an Agent Request dialog opens; the listener's
      watcher posts the text to the chat and marks the chat as awaiting a
      reply for this run
  reply.json     {"seq": n, "text": "…"}
      written by the listener when the next message arrives from the chat;
      the dialog's poller reads it, drops the text in the reply box and
      sends it exactly as Enter would
  answered.json  {"seq": n}
      written by MyAgent when the request was answered AT THE PC (typed, or
      the dialog dismissed) so the listener can withdraw it in the chat

``seq`` counts the run's requests from 1, so a reply to an earlier question
(Convo mode asks again and again) is never taken for the current one. Every
write goes through a temp file and os.replace, so a reader never sees a
half-written file. Stdlib only: Heartbeat.py imports it.
"""

import json
import os
import tempfile
import time
from pathlib import Path

REQUEST = "request.json"
REPLY = "reply.json"
ANSWERED = "answered.json"


def _write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def _read(path):
    """The file's JSON object, or None when it is missing or unreadable."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _unlink(path):
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass


def write_request(relay_dir, seq, text):
    """MyAgent: a new Agent Request is open."""
    _write(Path(relay_dir) / REQUEST, {"seq": int(seq), "text": str(text),
                                       "written": time.time()})


def read_request(relay_dir):
    """The listener: the open request as {"seq", "text", "written"}, or None."""
    data = _read(Path(relay_dir) / REQUEST)
    if data is None or "seq" not in data:
        return None
    return data


def write_reply(relay_dir, seq, text):
    """The listener: the chat's answer to request ``seq``."""
    _write(Path(relay_dir) / REPLY, {"seq": int(seq), "text": str(text)})


def read_reply(relay_dir, seq):
    """MyAgent: the reply's text when one for request ``seq`` has landed,
    else None (no reply yet, or one for an earlier question)."""
    data = _read(Path(relay_dir) / REPLY)
    if data is None or data.get("seq") != int(seq):
        return None
    return str(data.get("text", ""))


def clear(relay_dir):
    """MyAgent: the request is over (answered either way) — both files go."""
    _unlink(Path(relay_dir) / REQUEST)
    _unlink(Path(relay_dir) / REPLY)


def mark_answered(relay_dir, seq):
    """MyAgent: request ``seq`` was answered at the PC."""
    _write(Path(relay_dir) / ANSWERED, {"seq": int(seq)})


def take_answered(relay_dir):
    """The listener: the seq of a request answered at the PC, consuming the
    marker, else None."""
    path = Path(relay_dir) / ANSWERED
    data = _read(path)
    if data is None:
        return None
    _unlink(path)
    return data.get("seq")
