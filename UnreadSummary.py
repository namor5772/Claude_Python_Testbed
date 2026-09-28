"""UnreadSummary.py — zero-API-cost replacement for Email_AllUnreadSummary_Mac3.

Scans every configured mail account (Gmail via the Google API, Proton Bridge /
WebCentral via IMAP, Outlook via Microsoft Graph) for unread email in the
Inbox and Spam/Junk folders and builds the COMPREHENSIVE LIST: one running
sequence numbered across all accounts, each entry showing Account, From,
Subject, Date, To (when forwarded) and a short summary. Emails matching the
SPECIFYING LIST (known bills/receipts, defined in SpecifyingList.csv —
kept in the OneDrive MyImportant/DeathFinances folder shared by all
machines, with a repo-root fallback) are
listed like any other email except for a bare "SPECIFYING LIST EMAIL"
marker line at the top of the entry, followed by one
"Type={Type}, Index={Index}" line echoing those rule columns verbatim,
and, at the bottom, the names of their downloaded PDF attachments plus a
"Determine:" line echoing the rule's Determine column verbatim (a
reference note — nothing is extracted from the email body). Their PDFs
are saved (idempotently) to the shared Attachments folder beside
SpecifyingList.csv — OneDrive MyImportant/DeathFinances/Attachments — and
they are marked read — but stay in place: the original move-to-Trash is
off by default behind the TRASH_MATCHES flag (each per-match action has
its own flag; see the constants below). The list is sent from
grobliro@outlook.com to namor5772@gmail.com, mirroring the AI-run
instruction's daily email.

No LLM is involved — the "summary" is the first ~45 words of the cleaned
body text — so a daily run costs $0.00 in API tokens (the AI run cost
~$0.57).

Safety properties, by construction rather than by prompt:
  * The listing phase uses only read-only primitives (IMAP EXAMINE +
    BODY.PEEK, Gmail messages.get, Graph GET) — it CANNOT mark, move, or
    delete anything.
  * Per-match actions are individually flag-gated: SAVE_MATCH_PDFS and
    MARK_MATCHES_READ (default True), TRASH_MATCHES (default False). The
    only default mailbox mutation is clearing the unread flag on matches.
  * Mailbox mutations run only AFTER the digest that lists those matches
    has been sent: a pass that fails before or during the send changes
    nothing, so the next pass lists the same mail again. (Saving PDFs is
    read-only and runs before the digest is built, so their names are in it.)
  * Even with all flags enabled, actions run only for emails matching a
    SPECIFYING entry, and Trash is recoverable from each provider's UI —
    nothing is permanently deleted (same boundary as MyAgent's mail mixins;
    an IMAP move's fallback expunges only the moved message by UID, never
    with a folder-wide EXPUNGE).

Reuses MyAgent's stored credentials and never starts an interactive flow:
  Gmail   ~/.config/myagent-google/{account}_token.json   (silent refresh)
  IMAP    ~/.config/myagent-protonmail/accounts.json      (Bridge/IMAP creds)
  Outlook ~/.config/myagent-msmail/{account}_token.json   (MSAL silent only)
If a token is missing/unrefreshable the account is reported as an ERROR line
in the sent summary (or the run fails if it's the sending account) — run
MyAgent once interactively to repair, as with Heartbeat.py.

Besides being emailed, the digest body is written to a text file beside the
log (unread_summary.txt) — laid out 80 columns wide, dividers and wrapping,
where the email keeps its 50-char dividers — and opened in a text viewer on
the machine that ran the pass — Notepad++ (else Notepad) on Windows, the
default text editor via `open -t` on macOS — so it can be read the moment the
run ends rather than when the email arrives. --no-view keeps the file and
skips the viewer.

Usage:
  python UnreadSummary.py            # real run: acts on matches, sends email
  python UnreadSummary.py --dry-run  # read-only: prints the email to stdout,
                                     # no downloads, no mark/trash, no send
  python UnreadSummary.py --no-view  # (either mode) don't open the viewer

Designed for launchd/Task Scheduler (e.g. daily at 07:00); exits 0 on a
normal pass (even with per-account errors — they're visible in the email AND
logged one per account as "ACCOUNT ERROR <account>: <reason>", e.g. Proton
Bridge not running), 1 on a fatal failure such as the summary send itself
failing.
"""

import argparse
import base64
import csv
import email
import email.header
import email.utils
import imaplib
import json
import os
import platform
import re
import shutil
import socket
import ssl
import subprocess
import sys
import textwrap
from datetime import datetime
from pathlib import Path

import msal
import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
from myagent.helpers import extract_text_from_html  # noqa: E402
from myagent.helpers import rotate_log_if_needed as _rotate_log  # noqa: E402

# ── Configuration ────────────────────────────────────────────────────────────

GOOGLE_CONFIG_DIR = Path.home() / ".config" / "myagent-google"
PROTON_CONFIG_DIR = Path.home() / ".config" / "myagent-protonmail"
OUTLOOK_CONFIG_DIR = Path.home() / ".config" / "myagent-msmail"
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
OUTLOOK_SCOPES = [
    "https://graph.microsoft.com/Mail.ReadWrite",
    "https://graph.microsoft.com/Mail.Send",
]
GRAPH_BASE = "https://graph.microsoft.com/v1.0"
# MSAL's own HTTP calls (the token refresh every run makes) take no timeout by
# default — it overrides socket.setdefaulttimeout — so a stalled connection
# to login.microsoftonline.com would hang the unattended job for good.
OUTLOOK_HTTP_TIMEOUT = 60

SEND_FROM_OUTLOOK_ACCOUNT = "outlook"  # account key in msmail accounts.json
SEND_TO = "namor5772@gmail.com"
SUBJECT_PREFIX = "Summary of Unread Emails"

# What to do with a SPECIFYING match beyond flagging it in the
# COMPREHENSIVE LIST. Each action is independent; the defaults download
# the bill PDFs and mark the email read, but leave it in place. Setting
# TRASH_MATCHES True as well restores the full Email_AllUnreadSummary_Mac3
# behaviour (save PDFs, mark read, move to Trash).
SAVE_MATCH_PDFS = True     # save pdf attachments to DOWNLOAD_DIR (idempotent)
MARK_MATCHES_READ = True   # mark the matched email as read
TRASH_MATCHES = False      # move the matched email to Trash/Bin

SUMMARY_MAX_WORDS = 45  # "under 50 word summary"
DIV = "=" * 50   # instruction: every divider EXACTLY 50 chars
SUB = "-" * 50
WRAP = 63        # entry text wraps to ~50 content chars past the label column

if platform.system() == "Darwin":
    LOG_FILE = Path.home() / "Library" / "Logs" / "myagent" / "unread_summary.log"
else:
    LOG_FILE = BASE_DIR / "unread_summary.log"


def log(msg):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")


# One-slot log rotation: past this size the log is renamed to
# unread_summary.log.old (replacing the previous archive) and restarts with a
# marker line. The mechanics are the shared myagent.helpers.rotate_log_if_needed,
# same as heartbeat.log and APICostLog.txt.
LOG_MAX_BYTES = 100_000


def rotate_log_if_needed():
    _rotate_log(LOG_FILE, LOG_MAX_BYTES)


# ── Digest file + viewer (2026-09-24) ────────────────────────────────────────
# The body that is emailed is also written to DIGEST_FILE, beside the log, and
# opened in a text viewer on the machine that ran the pass, so the digest is
# readable the moment the run ends. The viewer is chosen per platform by
# viewer_command: Notepad++ on Windows when it is installed (on PATH or in one
# of NOTEPADPP_DIRS), else Notepad; `open -t` on macOS — the user's default
# text editor, TextEdit unless changed; xdg-open elsewhere. Opening it is
# fire-and-forget and best-effort: a viewer that fails to launch is a log
# line, never a failed run, and --no-view skips it (the file is still written).
DIGEST_FILE = LOG_FILE.with_name("unread_summary.txt")
DIGEST_FILE_WIDTH = 80  # the file's line width — dividers and wrapping alike;
# the email keeps its narrow 50-char dividers, which read cramped in a viewer

# (environment variable, subfolder) pairs where a Notepad++ install lives:
# the machine-wide installer's folder and the per-user one.
NOTEPADPP_DIRS = (("ProgramFiles", "Notepad++"),
                  ("ProgramFiles(x86)", "Notepad++"),
                  ("ProgramW6432", "Notepad++"),
                  ("LOCALAPPDATA", os.path.join("Programs", "Notepad++")))


def viewer_command(path, system=None, which=shutil.which, exists=os.path.exists,
                   env=os.environ):
    """The argv that opens ``path`` in this platform's text viewer. Pure: the
    platform and the three lookups are injectable so the tests can pin every
    branch on any machine."""
    system = system or platform.system()
    if system == "Windows":
        exe = which("notepad++")
        if not exe:
            for var, sub in NOTEPADPP_DIRS:
                base = env.get(var)
                if base:
                    candidate = os.path.join(base, sub, "notepad++.exe")
                    if exists(candidate):
                        exe = candidate
                        break
        return [exe or "notepad.exe", path]
    if system == "Darwin":
        return ["open", "-t", path]
    return ["xdg-open", path]


def write_digest(body, path=None):
    """Write the digest body to ``path`` (default DIGEST_FILE), replacing the
    previous pass's file, and return the path."""
    path = Path(path or DIGEST_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body + "\n", encoding="utf-8")
    return path


def show_digest(body, view=True, launch=subprocess.Popen):
    """Write the digest file and, unless ``view`` is off, open it in the
    platform's text viewer. Returns the one-line outcome for the log. Never
    raises — the digest has already been built and (on a real run) is about
    to be sent, and a viewer problem must not turn that into a failed pass."""
    try:
        path = write_digest(body)
    except Exception as e:
        return f"digest file NOT written ({type(e).__name__}: {e})"
    if not view:
        return f"digest written to {path}"
    argv = viewer_command(str(path))
    try:
        launch(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
               stderr=subprocess.DEVNULL)
    except Exception as e:
        return (f"digest written to {path}; viewer {argv[0]} NOT opened "
                f"({type(e).__name__}: {e})")
    return f"digest written to {path}; opened with {argv[0]}"


# ── SPECIFYING LIST (loaded from SpecifyingList.csv) ─────────────────────────
# The rules live in SpecifyingList.csv in the shared OneDrive folder
# MyImportant/DeathFinances — one copy synced to all three machines (Mac mini,
# Windows laptop, Windows desktop), so an edit on any of them propagates via
# OneDrive rather than git. A repo-root copy is the fallback when no OneDrive
# copy is found (see _locate_specifying_csv). One row per email
# type, semicolon-delimited with every field double-quoted (same convention
# as APICostLog.txt: the data itself is full of commas) — with columns:
#   Type       the user's category for this bill type (e.g. "setup", "info").
#              Echoed verbatim in the "Type={Type}, Index={Index}" line
#              directly under the entry's "SPECIFYING LIST EMAIL" marker;
#              never used for matching.
#   Index      the user's reference index for this bill type. Echoed
#              verbatim in the same "Type=..., Index=..." line; never used
#              for matching.
#   To         optional substring the To header must contain (forwarded bills)
#   From       substring of the From header (display name or address)
#   Subject    START of the subject — always a prefix match, never exact,
#              because senders vary the tail across the billing cycle
#              ("... is now available" / "... is due soon."). A trailing
#              "..." is stripped. Matching is case-insensitive throughout.
#   Determine  the user's note of what matters about this bill type, e.g.
#              "Account number, Amount due, Due Date". Echoed VERBATIM as a
#              "Determine:" line under the matched entry in the emailed
#              list — never used for matching, and never extracted from the
#              email body (the per-field extraction engine was removed; see
#              git history before 2026-06-11 if it's ever wanted back).

def _locate_specifying_csv():
    """Return the path of SpecifyingList.csv: the shared OneDrive copy
    (MyImportant/DeathFinances) when one exists, else the repo root.
    OneDrive roots probed, in order: the Windows %OneDrive% /
    %OneDriveConsumer% env vars, ~/OneDrive (older clients on either OS),
    and macOS ~/Library/CloudStorage/OneDrive-* (current Mac client)."""
    roots = []
    for var in ("OneDrive", "OneDriveConsumer"):
        val = os.environ.get(var)
        if val:
            roots.append(Path(val))
    home = Path.home()
    roots.append(home / "OneDrive")
    cloud = home / "Library" / "CloudStorage"
    if cloud.is_dir():
        roots.extend(sorted(cloud.glob("OneDrive-*")))
    for root in roots:
        p = root / "MyImportant" / "DeathFinances" / "SpecifyingList.csv"
        if p.is_file():
            return p
    return BASE_DIR / "SpecifyingList.csv"


SPECIFYING_CSV = _locate_specifying_csv()

# Matched-bill PDFs are saved beside the rules that matched them: the
# Attachments subfolder of SpecifyingList.csv's home — normally the shared
# OneDrive MyImportant/DeathFinances/Attachments, so the PDFs sync to every
# machine (was the run machine's local ~/Downloads until 2026-08-05). On a
# OneDrive-less machine this degrades with the CSV to <repo>/Attachments,
# which is gitignored.
DOWNLOAD_DIR = SPECIFYING_CSV.parent / "Attachments"


def load_specifying():
    """Parse SpecifyingList.csv into match-rule dicts. A missing or empty
    file degrades to a plain summary run (logged, not fatal); malformed rows
    are skipped with a log line. utf-8-sig tolerates an Excel-written BOM."""
    specs = []
    if not SPECIFYING_CSV.exists():
        log(f"WARNING: {SPECIFYING_CSV} not found — no SPECIFYING matching this run")
        return specs
    try:
        with open(SPECIFYING_CSV, newline="", encoding="utf-8-sig") as f:
            for i, row in enumerate(csv.DictReader(f, delimiter=";"), start=1):
                frm = (row.get("From") or "").strip()
                subj = (row.get("Subject") or "").strip()
                to = (row.get("To") or "").strip()
                if not frm:
                    # A blank From would substring-match every email.
                    log(f"WARNING: {SPECIFYING_CSV.name} row {i}: empty From — skipped")
                    continue
                spec = {
                    "n": i,
                    "name": f"{frm} / {subj}" if subj else frm,
                    "from_has": frm.lower(),
                    "subject_pre": _norm_subject(subj),
                    "determine": (row.get("Determine") or "").strip(),
                    "type": (row.get("Type") or "").strip(),
                    "index": (row.get("Index") or "").strip(),
                }
                if to:
                    spec["to_has"] = to.lower()
                specs.append(spec)
    except Exception as e:
        log(f"WARNING: could not parse {SPECIFYING_CSV.name}: {e} — "
            f"no SPECIFYING matching this run")
        return []
    log(f"SpecifyingList: {len(specs)} rules from {SPECIFYING_CSV}")
    return specs


_SPECS = None


def get_specifying():
    global _SPECS
    if _SPECS is None:
        _SPECS = load_specifying()
    return _SPECS

# ── Text utilities ───────────────────────────────────────────────────────────

# Invisible characters used as preheader padding (Stripe uses U+034F runs).
_INVISIBLE = dict.fromkeys(map(ord, "͏​‌‍⁠﻿­"), None)
_BOILERPLATE = re.compile(
    r"^(view (this |in |it )?(email |message )?(in|on)?\s*(your )?(browser|web)|"
    r"view online|web version|having trouble|no images\?|unsubscribe|"
    r"add us to your address book|email not displaying)", re.I)

def clean_text(text):
    """Normalise extracted body text: strip invisible padding chars, NBSPs,
    CRs, and URLs (which would otherwise dominate first-words summaries)."""
    text = (text or "").translate(_INVISIBLE).replace("\xa0", " ").replace("\r", "")
    return re.sub(r"https?://\S+|\[https?://[^\]]*\]|\(https?://[^)]*\)", " ", text)


def body_lines(text):
    return [l.strip() for l in clean_text(text).splitlines() if l.strip()]


def summarize(lines, subject):
    """Deterministic stand-in for the AI's 50-word summary: the first
    SUMMARY_MAX_WORDS words of the body, skipping preheader boilerplate and a
    leading repeat of the subject line."""
    words = []
    subj_norm = re.sub(r"\s+", " ", subject or "").strip().lower()
    for l in lines:
        if _BOILERPLATE.match(l):
            continue
        if subj_norm and re.sub(r"\s+", " ", l).strip().lower() == subj_norm:
            continue
        words.extend(l.split())
        if len(words) >= SUMMARY_MAX_WORDS:
            break
    if not words:
        return "(no readable body text)"
    out = " ".join(words[:SUMMARY_MAX_WORDS])
    if len(words) > SUMMARY_MAX_WORDS:
        out += " ..."
    return out


# ── SPECIFYING matching ──────────────────────────────────────────────────────

def _norm_subject(subject):
    s = re.sub(r"\s+", " ", subject or "").strip()
    s = re.sub(r"^((fwd?|re):\s*)+", "", s, flags=re.I)
    return s.rstrip(". ").lower()


def match_specifying(entry):
    """Return the first SpecifyingList.csv rule matching this entry, or None.
    From/To are case-insensitive substring tests on the decoded headers; the
    Subject column is always a PREFIX of the normalised subject."""
    frm = (entry["from"] or "").lower()
    subj = _norm_subject(entry["subject"])
    to = (entry["to"] or "").lower()
    for spec in get_specifying():
        if spec["from_has"] not in frm:
            continue
        if spec["subject_pre"] and not subj.startswith(spec["subject_pre"]):
            continue
        if spec.get("to_has") and spec["to_has"] not in to:
            continue
        return spec
    return None


# ── Shared entry helpers ─────────────────────────────────────────────────────

def _decode_header_bytes(raw, charset):
    """One decoded header chunk's bytes as text. A raw 8-bit header (RFC 6532
    UTF-8 with no encoded-word) arrives labelled 'unknown-8bit' — a charset no
    codec has — so it, and any charset Python doesn't know, is read as UTF-8,
    else Latin-1. (Mirror of ProtonMailMixin._decode_header_bytes.)"""
    if charset and charset.lower() not in ("unknown-8bit", "x-unknown"):
        try:
            return raw.decode(charset, "replace")
        except LookupError:
            pass
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def decode_header(value):
    """A header as one line of plain text — ALWAYS a str. The email package
    hands a header holding raw 8-bit bytes back as an email.header.Header
    object, not a str, and one such Date header in any scanned folder crashed
    the digest build (textwrap on a Header), failing every pass until that
    message was gone."""
    if not value:
        return ""
    try:
        parts = []
        for t, cs in email.header.decode_header(value):
            parts.append(_decode_header_bytes(t, cs) if isinstance(t, bytes) else t)
        return re.sub(r"\s+", " ", "".join(parts)).strip()
    except Exception:
        return str(value)


def forwarded_to(entry, account_email):
    """The instruction's "If forwarded then To email address": show the To
    header when the owning account's address isn't among its recipients
    (i.e. the mail was auto-forwarded from elsewhere)."""
    addrs = [a.lower() for _, a in email.utils.getaddresses([entry["to"] or ""]) if a]
    if addrs and account_email.lower() not in addrs:
        return ", ".join(addrs)
    return None


def save_pdf(filename, data):
    """Write attachment bytes to DOWNLOAD_DIR (the shared Attachments
    folder). Idempotent: a matched email can be seen again on later runs —
    if a file with the same (cleaned) name and identical bytes is already
    there, reuse it instead of stacking up "name (1).pdf", "name (2).pdf"
    day after day. A same-named file with DIFFERENT content still gets a
    fresh suffix."""
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r'[\\/:*?"<>|]', "_", filename or "attachment.pdf").strip() or "attachment.pdf"
    target = DOWNLOAD_DIR / safe
    stem, suffix = target.stem, target.suffix
    i = 1
    while target.exists():
        try:
            if target.stat().st_size == len(data) and target.read_bytes() == data:
                return f"{target.name} (already in Attachments)"
        except OSError:
            pass
        target = DOWNLOAD_DIR / f"{stem} ({i}){suffix}"
        i += 1
    target.write_bytes(data)
    return target.name


def _is_pdf(filename, mime):
    return (mime or "").lower() == "application/pdf" or (filename or "").lower().endswith(".pdf")


# ── Gmail ────────────────────────────────────────────────────────────────────

def gmail_service(account):
    token_path = GOOGLE_CONFIG_DIR / f"{account}_token.json"
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


def _gmail_walk(part):
    yield part
    for p in part.get("parts", []):
        yield from _gmail_walk(p)


def _gmail_part_charset(part):
    """The charset a Gmail payload part declares in its Content-Type header,
    else utf-8. (Mirror of GmailMixin._gmail_part_charset.)"""
    for header in part.get("headers", []) or []:
        if (header.get("name") or "").lower() == "content-type":
            m = re.search(r'charset\s*=\s*"?([^";\s]+)', header.get("value") or "", re.I)
            if m:
                return m.group(1)
    return "utf-8"


def _gmail_body_text(payload):
    plain, html = None, None
    for part in _gmail_walk(payload):
        data = part.get("body", {}).get("data")
        if not data:
            continue
        # body.data is the part's bytes in its OWN charset (the API does not
        # transcode): a windows-1252 / ISO-8859-1 body read as UTF-8 put a
        # U+FFFD in the summary for every accented letter.
        raw = base64.urlsafe_b64decode(data)
        try:
            text = raw.decode(_gmail_part_charset(part), errors="replace")
        except LookupError:   # a charset name Python lacks
            text = raw.decode("utf-8", errors="replace")
        mime = part.get("mimeType", "")
        if mime == "text/plain" and plain is None:
            plain = text
        elif mime == "text/html" and html is None:
            html = text
    if plain:
        return plain
    if html:
        return extract_text_from_html(html)
    return ""


def gmail_collect(account, account_email):
    """Read-only: list unread in INBOX and SPAM, fetch each in full."""
    service = gmail_service(account)
    entries = []
    for label, tag in (("INBOX", ""), ("SPAM", "SPAM")):
        ids, page = [], None
        while True:
            resp = service.users().messages().list(
                userId="me", labelIds=["UNREAD", label], maxResults=100,
                pageToken=page).execute()
            ids.extend(m["id"] for m in resp.get("messages", []))
            page = resp.get("nextPageToken")
            if not page:
                break
        for mid in ids:
            full = service.users().messages().get(
                userId="me", id=mid, format="full").execute()
            headers = {h["name"].lower(): h["value"]
                       for h in full.get("payload", {}).get("headers", [])}
            lines = body_lines(_gmail_body_text(full.get("payload", {})))
            entries.append({
                "provider": "Gmail", "account": account,
                "account_email": account_email, "folder_tag": tag,
                "id": mid, "from": decode_header(headers.get("from", "")),
                "to": decode_header(headers.get("to", "")),
                "subject": decode_header(headers.get("subject", "")),
                "date": headers.get("date", ""), "lines": lines,
                "_service": service, "_payload": full.get("payload", {}),
            })
    return entries


def gmail_save_pdfs(entry):
    """The read-only half of a SPECIFYING match's actions: save its PDF
    attachments (messages.attachments.get changes nothing). Returns the
    saved file names."""
    service = entry["_service"]
    pdfs = []
    for part in _gmail_walk(entry["_payload"]):
        fname = part.get("filename", "")
        att_id = part.get("body", {}).get("attachmentId")
        if att_id and _is_pdf(fname, part.get("mimeType")):
            att = service.users().messages().attachments().get(
                userId="me", messageId=entry["id"], id=att_id).execute()
            pdfs.append(save_pdf(fname, base64.urlsafe_b64decode(att["data"])))
    return pdfs


def gmail_mark(entry):
    """The mailbox half, run only after the digest was sent: mark read and/or
    trash, as the flags say. Returns the actions taken."""
    service = entry["_service"]
    actions = []
    if MARK_MATCHES_READ:
        service.users().messages().modify(
            userId="me", id=entry["id"], body={"removeLabelIds": ["UNREAD"]}).execute()
        actions.append("marked read")
    if TRASH_MATCHES:
        service.users().messages().trash(userId="me", id=entry["id"]).execute()
        actions.append("moved to Trash")
    if not actions:
        actions.append("left unread in place")
    return actions


# ── IMAP (Proton Bridge / WebCentral) ────────────────────────────────────────

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0.0.0.0"})


def _is_loopback_host(host):
    """True for this machine's own addresses (Proton Bridge's case)."""
    return (host or "").strip().lower() in _LOOPBACK_HOSTS


def _imap_ssl_context(cfg):
    """Mirror of ProtonMailMixin._build_ssl_context: pinned cert > explicit
    opt-out > loopback CERT_NONE > system trust store."""
    ca = cfg.get("ca_cert_path")
    if ca and os.path.isfile(ca):
        return ssl.create_default_context(cafile=ca)
    if cfg.get("verify_tls") is False or _is_loopback_host(cfg.get("imap_host")):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return ssl.create_default_context()


def imap_connect(cfg):
    host = cfg.get("imap_host", "127.0.0.1")
    port = int(cfg.get("imap_port", 1143))
    ctx = _imap_ssl_context(cfg)
    if cfg.get("imap_ssl"):
        conn = imaplib.IMAP4_SSL(host, port, ssl_context=ctx)
    else:
        conn = imaplib.IMAP4(host, port)
        try:
            conn.starttls(ssl_context=ctx)
        except imaplib.IMAP4.error as e:
            # A Bridge quirk: Bridge on THIS machine gets the plain login,
            # which never leaves it. Any other host would receive the
            # password in clear — an attacker who strips STARTTLS reads it —
            # so there the login is refused instead (as ProtonMailMixin does).
            if not _is_loopback_host(host):
                try:
                    conn.shutdown()
                except Exception:
                    pass
                raise RuntimeError(
                    f"STARTTLS failed on {host}: {e} — refusing to send the password "
                    f"unencrypted (set imap_ssl: true if the server speaks implicit TLS)"
                ) from e
    conn.login(cfg.get("username") or cfg.get("email"), cfg["app_password"])
    return conn


def _quote_mailbox(name):
    if any(c in name for c in ' "\\'):
        return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return name or '""'


def imap_folders(conn):
    """LIST once; return (scan_folders, trash_folder). Scan = INBOX plus
    anything special-use-flagged \\Junk or leaf-named spam/junk (dovecot's
    INBOX.Junk carries no flag). Trash = \\Trash flag, else leaf name."""
    scan, trash = ["INBOX"], None
    typ, data = conn.list()
    if typ != "OK":
        return scan, "Trash"
    by_name_trash = None
    for raw in data or []:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        m = re.match(r'^\(([^)]*)\)\s+("[^"]*"|\S+)\s+(.*)$', line)
        if not m:
            continue
        flags, _delim, name = m.groups()
        name = name.strip().strip('"')
        if "\\Noselect" in flags or name.upper() == "INBOX":
            continue
        leaf = re.split(r"[./]", name)[-1].lower()
        if "\\Junk" in flags or leaf in ("spam", "junk", "junk e-mail", "junk email"):
            scan.append(name)
        if "\\Trash" in flags:
            trash = name
        elif leaf in ("trash", "bin", "deleted items", "deleted messages"):
            by_name_trash = by_name_trash or name
    return scan, trash or by_name_trash or "Trash"


def _imap_body_text(msg):
    plain, html = "", ""
    for part in msg.walk():
        ctype = part.get_content_type()
        if "attachment" in (part.get("Content-Disposition") or "").lower():
            continue
        try:
            payload = (part.get_payload(decode=True) or b"").decode(
                part.get_content_charset() or "utf-8", "replace")
        except Exception:
            continue
        if ctype == "text/plain" and not plain:
            plain = payload
        elif ctype == "text/html" and not html:
            html = payload
    return plain or (extract_text_from_html(html) if html else "")


def imap_collect(account, cfg, conn):
    """Read-only: EXAMINE each scan folder, UNSEEN search, BODY.PEEK fetch.
    PEEK is load-bearing — a plain BODY[] fetch would set \\Seen and violate
    the read-only guarantee."""
    entries = []
    scan, trash_folder = imap_folders(conn)
    for folder in scan:
        typ, _ = conn.select(_quote_mailbox(folder), readonly=True)
        if typ != "OK":
            continue
        typ, data = conn.uid("search", None, "UNSEEN")
        if typ != "OK" or not data or not data[0]:
            continue
        leaf = re.split(r"[./]", folder)[-1].lower()
        tag = "" if folder.upper() == "INBOX" else ("SPAM" if "spam" in leaf else "JUNK")
        for uid in data[0].split():
            typ, fd = conn.uid("fetch", uid, "(BODY.PEEK[])")
            if typ != "OK" or not fd or not isinstance(fd[0], tuple):
                continue
            msg = email.message_from_bytes(fd[0][1] or b"")
            entries.append({
                "provider": "IMAP", "account": account,
                "account_email": cfg.get("email", account), "folder_tag": tag,
                "id": uid.decode(), "folder": folder,
                "from": decode_header(msg.get("From", "")),
                "to": decode_header(msg.get("To", "")),
                "subject": decode_header(msg.get("Subject", "")),
                "date": decode_header(msg.get("Date", "")),
                "lines": body_lines(_imap_body_text(msg)),
                "_conn": conn, "_msg": msg, "_trash": trash_folder,
            })
    return entries


def imap_save_pdfs(entry):
    """The read-only half of a SPECIFYING match's actions. The PDFs come out
    of the message already fetched with BODY.PEEK during collection — saving
    them costs no IMAP traffic and cannot touch the \\Seen flag."""
    pdfs = []
    for part in entry["_msg"].walk():
        fname = decode_header(part.get_filename() or "")
        if fname and _is_pdf(fname, part.get_content_type()):
            payload = part.get_payload(decode=True) or b""
            if payload:
                pdfs.append(save_pdf(fname, payload))
    return pdfs


def imap_move(conn, uid, destination):
    """Move one message (by UID) out of the selected folder; returns the
    action line for the log.

    UID MOVE (RFC 6851) first. Only a server that does not KNOW the command —
    it answers BAD, which imaplib raises — gets the fallback: a CHECKED COPY,
    then \\Deleted and a UID EXPUNGE (RFC 4315) of this one message. Where UID
    EXPUNGE is refused too, the original stays flagged \\Deleted beside its
    copy: a plain EXPUNGE would also destroy every message another client had
    flagged \\Deleted in that folder. (The old fallback chose it from the
    PRE-login capability list — Dovecot names MOVE only after login — ran
    even when the COPY had failed, and expunged the whole folder.) A MOVE the
    server REFUSES (NO) is a failure, never a reason to fall back."""
    dest = _quote_mailbox(destination)
    try:
        typ, _ = conn.uid("move", uid, dest)
    except imaplib.IMAP4.abort:
        raise                                  # the connection died
    except imaplib.IMAP4.error:
        typ = None                             # BAD: this server has no MOVE
    if typ is not None:
        return f"moved to {destination}" if typ == "OK" else f"move to {destination} FAILED"
    typ, _ = conn.uid("copy", uid, dest)
    if typ != "OK":
        return f"move to {destination} FAILED (COPY refused)"
    conn.uid("store", uid, "+FLAGS", r"(\Deleted)")
    try:
        typ, _ = conn.uid("expunge", uid)
    except imaplib.IMAP4.abort:
        raise
    except imaplib.IMAP4.error:
        typ = None                             # BAD: no UIDPLUS
    if typ == "OK":
        return f"moved to {destination}"
    return (f"copied to {destination}; the original is flagged \\Deleted but not "
            f"expunged (this server cannot expunge a single message)")


def imap_mark(entry):
    """The mailbox half, run only after the digest was sent: a read-write
    SELECT of the entry's folder, then \\Seen and/or the move to Trash, as
    the flags say. Returns the actions taken."""
    conn, uid = entry["_conn"], entry["id"]
    actions = []
    if MARK_MATCHES_READ or TRASH_MATCHES:
        typ, _ = conn.select(_quote_mailbox(entry["folder"]))  # read-write select
        if typ != "OK":
            # (imaplib is then back in AUTH state, so no later command can
            # land on a UID of the previously selected folder.)
            return [f"SELECT {entry['folder']} FAILED — left as it was"]
    if MARK_MATCHES_READ:
        typ, _ = conn.uid("store", uid, "+FLAGS", r"(\Seen)")
        actions.append("marked read" if typ == "OK" else "mark-read FAILED")
    if TRASH_MATCHES:
        actions.append(imap_move(conn, uid, entry["_trash"]))
    if not actions:
        actions.append("left unread in place")
    return actions


# ── Outlook (Microsoft Graph) ────────────────────────────────────────────────

def _outlook_client_config():
    app_file = OUTLOOK_CONFIG_DIR / "msal_app.json"
    client_id, authority = None, "https://login.microsoftonline.com/common"
    if app_file.exists():
        data = json.loads(app_file.read_text(encoding="utf-8"))
        client_id = data.get("client_id")
        authority = data.get("authority", authority)
    client_id = client_id or os.environ.get("OUTLOOK_CLIENT_ID") or os.environ.get("MS_CLIENT_ID")
    if not client_id:
        raise RuntimeError(f"no Azure client_id in {app_file} or environment")
    return client_id, authority


_OUTLOOK_APPS = {}


def outlook_token(account, account_email):
    """Silent-only MSAL acquisition (Heartbeat rule: never start a browser
    flow from an unattended job — fail and let an interactive MyAgent run
    repair the cache)."""
    if account not in _OUTLOOK_APPS:
        client_id, authority = _outlook_client_config()
        cache_path = OUTLOOK_CONFIG_DIR / f"{account}_token.json"
        cache = msal.SerializableTokenCache()
        if cache_path.exists():
            cache.deserialize(cache_path.read_text(encoding="utf-8"))
        app = msal.PublicClientApplication(client_id, authority=authority, token_cache=cache,
                                           timeout=OUTLOOK_HTTP_TIMEOUT)
        _OUTLOOK_APPS[account] = (app, cache, cache_path)
    app, cache, cache_path = _OUTLOOK_APPS[account]
    accounts = app.get_accounts(username=account_email) or app.get_accounts()
    result = app.acquire_token_silent(OUTLOOK_SCOPES, account=accounts[0]) if accounts else None
    if not result or "access_token" not in result:
        raise RuntimeError(
            f"silent token acquisition failed for {account!r} — run MyAgent once to authorize")
    if cache.has_state_changed:
        cache_path.write_text(cache.serialize(), encoding="utf-8")
        os.chmod(cache_path, 0o600)
    return result["access_token"]


def graph(account, account_email, method, path, params=None, json_body=None):
    """One Graph call. ``path`` is relative to GRAPH_BASE — or a complete
    ``@odata.nextLink`` URL, which carries its own query and is accepted only
    on GRAPH_BASE itself, so the bearer token is never sent anywhere else."""
    if path.startswith(("https://", "http://")):
        if not path.startswith(GRAPH_BASE + "/"):
            raise RuntimeError(f"refusing a Graph link outside {GRAPH_BASE}: {path[:80]}")
        url = path
    else:
        url = GRAPH_BASE + path
    token = outlook_token(account, account_email)
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.request(method, url, headers=headers,
                            params=params, json=json_body, timeout=60)
    if resp.status_code >= 400:
        try:
            err = resp.json().get("error", {})
            detail = f"{err.get('code', '')}: {err.get('message', '')}"
        except ValueError:
            detail = resp.text[:200]
        raise RuntimeError(f"Graph {resp.status_code} {detail}")
    if resp.status_code == 204 or not resp.content:
        return {}
    return resp.json()


def _graph_date_local(iso):
    """Graph returns UTC ISO ("2026-06-11T07:00:09Z"); display like the
    other providers' Date headers, in local time."""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
        return dt.strftime("%a, %d %b %Y %H:%M:%S %z")
    except Exception:
        return iso


def _graph_pages(account, account_email, path, params):
    """Every item of a Graph listing: the first page, then each
    ``@odata.nextLink`` (which already carries the query) until there is
    none. One page only cut each folder off at $top unread messages, the rest
    silently missing from the digest and its TOTAL. A link seen twice ends
    the walk rather than looping."""
    resp = graph(account, account_email, "GET", path, params=params)
    seen = set()
    while True:
        yield from resp.get("value", [])
        link = resp.get("@odata.nextLink")
        if not link or link in seen:
            return
        seen.add(link)
        resp = graph(account, account_email, "GET", link)


def outlook_collect(account, account_email):
    entries = []
    select = "id,subject,from,sender,toRecipients,receivedDateTime,body,hasAttachments"
    for folder, tag in (("inbox", ""), ("junkemail", "JUNK")):
        url = f"/me/mailFolders/{folder}/messages"
        params = {"$filter": "isRead eq false", "$top": 100, "$select": select}
        for m in _graph_pages(account, account_email, url, params):
            addr = (m.get("from") or m.get("sender") or {}).get("emailAddress", {})
            frm = f"\"{addr.get('name', '')}\" <{addr.get('address', '')}>".strip()
            to = ", ".join(r.get("emailAddress", {}).get("address", "")
                           for r in m.get("toRecipients", []))
            body = m.get("body", {}) or {}
            content = body.get("content", "") or ""
            text = extract_text_from_html(content) if body.get("contentType", "").lower() == "html" else content
            entries.append({
                "provider": "Outlook", "account": account,
                "account_email": account_email, "folder_tag": tag,
                "id": m.get("id"), "from": frm, "to": to,
                "subject": m.get("subject", ""),
                "date": _graph_date_local(m.get("receivedDateTime", "")),
                "lines": body_lines(text),
                "_has_atts": m.get("hasAttachments", False),
            })
    return entries


def outlook_save_pdfs(entry):
    """The read-only half of a SPECIFYING match's actions (a Graph GET)."""
    account, account_email = entry["account"], entry["account_email"]
    pdfs = []
    if entry["_has_atts"]:
        resp = graph(account, account_email, "GET",
                     f"/me/messages/{entry['id']}/attachments")
        for a in resp.get("value", []):
            if a.get("contentBytes") and _is_pdf(a.get("name"), a.get("contentType")):
                pdfs.append(save_pdf(a.get("name"), base64.b64decode(a["contentBytes"])))
    return pdfs


def outlook_mark(entry):
    """The mailbox half, run only after the digest was sent: mark read and/or
    move to Deleted Items, as the flags say. Returns the actions taken."""
    account, account_email = entry["account"], entry["account_email"]
    actions = []
    if MARK_MATCHES_READ:
        graph(account, account_email, "PATCH", f"/me/messages/{entry['id']}",
              json_body={"isRead": True})
        actions.append("marked read")
    if TRASH_MATCHES:
        graph(account, account_email, "POST", f"/me/messages/{entry['id']}/move",
              json_body={"destinationId": "deleteditems"})
        actions.append("moved to Deleted Items")
    if not actions:
        actions.append("left unread in place")
    return actions


def outlook_send(account, account_email, subject, body):
    graph(account, account_email, "POST", "/me/sendMail", json_body={
        "message": {
            "subject": subject,
            "body": {"contentType": "text", "content": body},
            "toRecipients": [{"emailAddress": {"address": SEND_TO}}],
        },
        "saveToSentItems": True,
    })


# ── Output assembly ──────────────────────────────────────────────────────────

def _field(label, value, width=9, wrap=WRAP):
    """One wrapped 'Label: value' entry line with a hanging indent. ``width``
    widens the label column for labels that outgrow the default
    ("Determine:" is 10 chars); ``wrap`` is the column the line wraps at."""
    prefix = f"   {label:<{width}}"
    return textwrap.fill(value or "", width=wrap, initial_indent=prefix,
                         subsequent_indent=" " * len(prefix)) or prefix.rstrip()


def format_entry(n, entry, wrap=WRAP):
    """A SPECIFYING match renders like any other email; the only additions
    are the bare "SPECIFYING LIST EMAIL" marker line at the top, followed by
    one "Type={Type}, Index={Index}" line echoing those rule columns
    verbatim (blank value -> bare "Type=" / "Index="), and, at the bottom,
    the names of any downloaded PDF attachments plus a Determine: line
    echoing the rule's Determine column verbatim from SpecifyingList.csv."""
    tag = f" [{entry['folder_tag']}]" if entry["folder_tag"] else ""
    num = f"{n}. "
    lines = []
    if entry.get("spec"):
        lines.append(f"{num}SPECIFYING LIST EMAIL")
        lines.append(f"   Type={entry['spec'].get('type') or ''}, "
                     f"Index={entry['spec'].get('index') or ''}")
        lines.append(f"   Account:  {entry['account_email']}{tag}")
    else:
        lines.append(f"{num}Account:  {entry['account_email']}{tag}")
    lines.append(_field("From:", entry["from"], wrap=wrap))
    fwd = forwarded_to(entry, entry["account_email"])
    if fwd:
        lines.append(_field("To:", fwd, wrap=wrap))
    lines.append(_field("Subject:", entry["subject"], wrap=wrap))
    lines.append(_field("Date:", entry["date"], wrap=wrap))
    lines.append(_field("Summary:", summarize(entry["lines"], entry["subject"]),
                        wrap=wrap))
    pdfs = entry.get("pdfs") or []
    if pdfs:
        lines.append(_field("PDFs:", "; ".join(pdfs), wrap=wrap))
    if entry.get("spec"):
        lines.append(_field("Determine:",
                            entry["spec"].get("determine") or "(none)", width=11,
                            wrap=wrap))
    return "\n".join(lines)


def build_body(account_order, entries_by_account, errors, dry_run, width=None,
               now=None):
    """The emailed digest: a header, then the TOTAL section, then the
    per-account enumeration, closed by a bare divider. The enumeration is
    rendered FIRST so the TOTAL line that precedes it in the email can quote
    the exact count of entries that follow (n is the last sequence number
    used — the count can never drift from the numbering it introduces).
    TOTAL moved up from the footer on 2026-09-10 so the count, the
    SPECIFYING tally and any ERRORS line are read before the list.

    ``width`` is None for the email (the instruction's 50-char dividers, text
    wrapped at WRAP) or a line width — the text-file copy uses
    DIGEST_FILE_WIDTH — that sets both the dividers' length and the column
    the entry lines wrap at. ``now`` lets the two renderings of one pass
    carry the same Generated timestamp."""
    now = now or datetime.now()
    div, sub, wrap = ("=" * width, "-" * width, width) if width else (DIV, SUB, WRAP)
    listing = []
    n = 0
    for account, label in account_order:
        listing += ["", sub, f"Account: {label}", sub]
        if account in errors:
            listing += ["", f"ERROR: {errors[account]}"]
            continue
        entries = entries_by_account.get(account, [])
        if not entries:
            listing += ["", "No unread emails."]
        for entry in entries:
            n += 1
            listing += ["", format_entry(n, entry, wrap=wrap)]
    matched = [e for es in entries_by_account.values() for e in es if e.get("spec")]

    out = [div, "COMPREHENSIVE LIST OF UNREAD EMAILS", div,
           f"Generated {now:%Y-%m-%d %H:%M:%S} by UnreadSummary.py",
           "(deterministic, no LLM — summaries are each",
           "email's opening text)"]
    if dry_run:
        out.append("*** DRY RUN: no emails were modified ***")
    out += ["", div,
            f"TOTAL: {n} unread email(s) across {len(account_order)} account(s); "
            f"{len(matched)} SPECIFYING match(es)"]
    if errors:
        out.append(f"ERRORS: {len(errors)} account(s) unreadable — see below")
    out.append(div)
    out += listing
    out += ["", div]
    return "\n".join(out)


# ── Main ─────────────────────────────────────────────────────────────────────

class AccountsFileError(Exception):
    """An accounts.json that exists but cannot be read or parsed."""


def load_accounts(path):
    """The ``accounts`` object of ``path``/accounts.json — {} when there is no
    such file (that provider is simply not configured). A file that exists
    but cannot be read or parsed raises AccountsFileError: returning {} for it
    made every account of that provider vanish from the digest with no ERROR
    line and nothing in the log (a hand edit's trailing comma did it)."""
    f = path / "accounts.json"
    if not f.exists():
        return {}
    try:
        accounts = json.loads(f.read_text(encoding="utf-8")).get("accounts", {})
        if not isinstance(accounts, dict):
            raise ValueError('"accounts" is not an object')
        bad = [name for name, cfg in accounts.items() if not isinstance(cfg, dict)]
        if bad:
            raise ValueError(f"not an object: {', '.join(map(str, bad))}")
        return accounts
    except (OSError, ValueError, AttributeError) as e:
        raise AccountsFileError(f"{type(e).__name__}: {e}") from e


def _provider_accounts(provider, config_dir, account_order, errors):
    """load_accounts for one provider. An unreadable accounts.json becomes a
    digest block of its own, in the provider's place, carrying the ERROR line
    (and so an ACCOUNT ERROR log line) — its slot's account key is None,
    which no JSON key can be."""
    try:
        return load_accounts(config_dir)
    except AccountsFileError as e:
        slot = (provider, None)
        account_order.append((slot, f"{config_dir / 'accounts.json'} ({provider})"))
        errors[slot] = f"accounts.json unreadable, its accounts were skipped — {e}"
        return {}


def _console_print(text, stream=None):
    """print() that cannot die on a character the console's code page lacks —
    an emoji in a subject, printed to a cp1252 pipe under Windows (a
    --dry-run from Git Bash): it prints as '?' instead of raising
    UnicodeEncodeError, which ended the dry run before its log line."""
    stream = stream if stream is not None else sys.stdout
    if stream is None:        # pythonw: no console at all
        return
    try:
        stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
    print(text, file=stream)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="read-only pass: print the email body, change nothing")
    parser.add_argument("--no-view", action="store_true",
                        help="write the digest text file but don't open it in a viewer")
    args = parser.parse_args(argv)

    socket.setdefaulttimeout(60)  # imaplib has no per-call timeout; a hung
    # Bridge socket must not wedge an unattended launchd run forever.

    # Every per-account map is keyed by a SLOT — (provider, account key) —
    # because the three accounts.json files are separate namespaces: keyed by
    # the bare key, an account named in two of them had the later provider's
    # mail listed under both labels, the earlier one's lost, TOTAL doubled.
    account_order = []          # [(slot, display_label)]
    entries_by_account = {}
    errors = {}
    imap_conns = {}

    try:
        for account, cfg in _provider_accounts("Gmail", GOOGLE_CONFIG_DIR,
                                               account_order, errors).items():
            slot = ("Gmail", account)
            email_addr = cfg.get("email", account)
            account_order.append((slot, f"{email_addr} (Gmail)"))
            try:
                entries_by_account[slot] = gmail_collect(account, email_addr)
            except Exception as e:
                errors[slot] = f"{type(e).__name__}: {e}"

        for account, cfg in _provider_accounts("IMAP", PROTON_CONFIG_DIR,
                                               account_order, errors).items():
            slot = ("IMAP", account)
            host = cfg.get("imap_host", "")
            kind = "Proton Bridge" if host in ("127.0.0.1", "localhost") else "IMAP"
            account_order.append((slot, f"{cfg.get('email', account)} ({kind})"))
            try:
                conn = imap_connect(cfg)
                imap_conns[slot] = conn
                entries_by_account[slot] = imap_collect(account, cfg, conn)
            except Exception as e:
                errors[slot] = f"{type(e).__name__}: {e}"

        outlook_accounts = _provider_accounts("Outlook", OUTLOOK_CONFIG_DIR,
                                              account_order, errors)
        for account, cfg in outlook_accounts.items():
            slot = ("Outlook", account)
            email_addr = cfg.get("email", account)
            account_order.append((slot, f"{email_addr} (Outlook)"))
            try:
                entries_by_account[slot] = outlook_collect(account, email_addr)
            except Exception as e:
                errors[slot] = f"{type(e).__name__}: {e}"

        # Match against SpecifyingList.csv — in the emailed list a match's
        # entry gains the "SPECIFYING LIST EMAIL" marker, its Type / Index
        # line and a Determine line (plus its PDFs' names once saved).
        matched = []
        for entries in entries_by_account.values():
            for entry in entries:
                spec = match_specifying(entry)
                if spec:
                    entry["spec"] = spec
                    log(f"SPECIFYING match ({spec['name']}) in {entry['account']}")
                    matched.append(entry)

        # The action phase only ever sees SPECIFYING matches, each action is
        # individually flag-gated, outcomes are logged (not emailed), and all
        # of it is skipped on --dry-run. It runs in two halves: the READ-ONLY
        # one — saving PDFs — here, so their names are in the digest built
        # next; the mailbox changes only after the digest has been sent.
        acting = not args.dry_run and (SAVE_MATCH_PDFS or MARK_MATCHES_READ
                                       or TRASH_MATCHES)
        if acting and SAVE_MATCH_PDFS:
            for entry in matched:
                save = {"Gmail": gmail_save_pdfs, "IMAP": imap_save_pdfs,
                        "Outlook": outlook_save_pdfs}[entry["provider"]]
                try:
                    entry["pdfs"] = save(entry)
                except Exception as e:
                    log(f"PDF save failed for {entry['account']} {entry['subject']!r}: {e}")

        # One line per failed account so heartbeat.log-style triage works from
        # the log alone (e.g. "Proton Bridge not running" without opening the
        # emailed summary). The summary line below still carries the count.
        for (provider, account), reason in errors.items():
            log(f"ACCOUNT ERROR {account or provider + ' accounts.json'}: {reason}")

        now = datetime.now()
        body = build_body(account_order, entries_by_account, errors, args.dry_run, now=now)
        subject = f"{SUBJECT_PREFIX} - {now:%Y-%m-%d %H:%M:%S}"
        total = sum(len(v) for v in entries_by_account.values())

        # The local copy first — the same digest laid out at the file's wider
        # line width — so it exists whether or not the send below succeeds.
        log(show_digest(build_body(account_order, entries_by_account, errors,
                                   args.dry_run, width=DIGEST_FILE_WIDTH, now=now),
                        view=not args.no_view))

        if args.dry_run:
            _console_print(f"Subject: {subject}\n")
            _console_print(body)
            log(f"DRY RUN — {total} unread, {len(matched)} specifying match(es), "
                f"{len(errors)} account error(s)")
            return

        send_cfg = outlook_accounts.get(SEND_FROM_OUTLOOK_ACCOUNT, {})
        outlook_send(SEND_FROM_OUTLOOK_ACCOUNT,
                     send_cfg.get("email", SEND_FROM_OUTLOOK_ACCOUNT), subject, body)
        log(f"sent summary to {SEND_TO}: {total} unread across "
            f"{len(account_order)} accounts, {len(matched)} specifying match(es), "
            f"{len(errors)} account error(s)")

        # The mailbox half, now that the digest listing these matches is SENT.
        # Matches used to be marked read before the build and the send, so a
        # pass that died there — a bad header, the sender's expired token, a
        # Graph 5xx — had already cleared their unread flag, and no later
        # digest ever listed those bills. A mark that fails here leaves the
        # message unread: the next digest lists it once more.
        if acting:
            for entry in matched:
                mark = {"Gmail": gmail_mark, "IMAP": imap_mark,
                        "Outlook": outlook_mark}[entry["provider"]]
                try:
                    actions = mark(entry)
                    log(f"acted on match in {entry['account']}: {'; '.join(actions)}"
                        + (f"; pdfs: {', '.join(entry['pdfs'])}" if entry.get("pdfs") else ""))
                except Exception as e:
                    log(f"action failed for {entry['account']} {entry['subject']!r}: {e}")
    finally:
        # IMAP connections stay open until the marks above have run.
        for conn in imap_conns.values():
            try:
                conn.logout()
            except Exception:
                pass


if __name__ == "__main__":
    rotate_log_if_needed()
    try:
        main()
    except Exception as e:
        # Fatal (e.g. the summary send itself failed). Collected state is
        # lost, but no mailbox was changed: matches are marked read (or
        # trashed) only after a successful send, so the next scheduled pass
        # lists them again. (PDFs already saved stay; saving is idempotent.)
        log(f"ERROR {type(e).__name__}: {e}")
        sys.exit(1)
