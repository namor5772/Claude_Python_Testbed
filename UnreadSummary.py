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
marker line at the top of the entry, followed by "Type={Type}" and
"Index={Index}" lines echoing those rule columns verbatim, and, at the
bottom, the names of their downloaded PDF attachments plus a "Determine:"
line echoing the rule's Determine column verbatim (a reference note —
nothing is extracted from the email body). Their PDFs are saved to ~/Downloads (idempotently) and
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
  * Even with all flags enabled, actions run only for emails matching a
    SPECIFYING entry, and Trash is recoverable from each provider's UI —
    nothing is permanently deleted (same boundary as MyAgent's mail mixins).

Reuses MyAgent's stored credentials and never starts an interactive flow:
  Gmail   ~/.config/myagent-google/{account}_token.json   (silent refresh)
  IMAP    ~/.config/myagent-protonmail/accounts.json      (Bridge/IMAP creds)
  Outlook ~/.config/myagent-msmail/{account}_token.json   (MSAL silent only)
If a token is missing/unrefreshable the account is reported as an ERROR line
in the sent summary (or the run fails if it's the sending account) — run
MyAgent once interactively to repair, as with Heartbeat.py.

Usage:
  python UnreadSummary.py            # real run: acts on matches, sends email
  python UnreadSummary.py --dry-run  # read-only: prints the email to stdout,
                                     # no downloads, no mark/trash, no send

Designed for launchd/Task Scheduler (e.g. daily at 07:00); exits 0 on a
normal pass (even with per-account errors — they're visible in the email),
1 on a fatal failure such as the summary send itself failing.
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
import socket
import ssl
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

SEND_FROM_OUTLOOK_ACCOUNT = "outlook"  # account key in msmail accounts.json
SEND_TO = "namor5772@gmail.com"
SUBJECT_PREFIX = "Summary of Unread Emails"

# What to do with a SPECIFYING match beyond flagging it in the
# COMPREHENSIVE LIST. Each action is independent; the defaults download
# the bill PDFs and mark the email read, but leave it in place. Setting
# TRASH_MATCHES True as well restores the full Email_AllUnreadSummary_Mac3
# behaviour (save PDFs, mark read, move to Trash).
SAVE_MATCH_PDFS = True     # save pdf attachments to ~/Downloads (idempotent)
MARK_MATCHES_READ = True   # mark the matched email as read
TRASH_MATCHES = False      # move the matched email to Trash/Bin

DOWNLOAD_DIR = Path.home() / "Downloads"
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


# ── SPECIFYING LIST (loaded from SpecifyingList.csv) ─────────────────────────
# The rules live in SpecifyingList.csv in the shared OneDrive folder
# MyImportant/DeathFinances — one copy synced to all three machines (Mac mini,
# Windows laptop, Windows desktop), so an edit on any of them propagates via
# OneDrive rather than git. A repo-root copy is the fallback when no OneDrive
# copy is found (see _locate_specifying_csv). One row per email
# type, semicolon-delimited with every field double-quoted (same convention
# as APICostLog.txt: the data itself is full of commas) — with columns:
#   Type       the user's category for this bill type (e.g. "setup", "info").
#              Echoed verbatim as a "Type={Type}" line directly under the
#              entry's "SPECIFYING LIST EMAIL" marker; never used for
#              matching.
#   Index      the user's reference index for this bill type. Echoed
#              verbatim as an "Index={Index}" line under the Type line;
#              never used for matching.
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
    s = re.sub(r"^((fwd?|fw|re):\s*)+", "", s, flags=re.I)
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

def decode_header(value):
    if not value:
        return ""
    try:
        parts = []
        for t, cs in email.header.decode_header(value):
            parts.append(t.decode(cs or "utf-8", "replace") if isinstance(t, bytes) else t)
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
    """Write attachment bytes to ~/Downloads. Idempotent: a matched email is
    left unread by default, so every later run sees it again — if a file
    with the same (cleaned) name and identical bytes is already there, reuse
    it instead of stacking up "name (1).pdf", "name (2).pdf" day after day.
    A same-named file with DIFFERENT content still gets a fresh suffix."""
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r'[\\/:*?"<>|]', "_", filename or "attachment.pdf").strip() or "attachment.pdf"
    target = DOWNLOAD_DIR / safe
    stem, suffix = target.stem, target.suffix
    i = 1
    while target.exists():
        try:
            if target.stat().st_size == len(data) and target.read_bytes() == data:
                return f"{target.name} (already in Downloads)"
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


def _gmail_body_text(payload):
    plain, html = None, None
    for part in _gmail_walk(payload):
        data = part.get("body", {}).get("data")
        if not data:
            continue
        text = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
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


def gmail_act(entry):
    """Action phase for a SPECIFYING match, honouring the action flags.
    Attachment download is itself read-only (messages.attachments.get)."""
    service = entry["_service"]
    pdfs, actions = [], []
    if SAVE_MATCH_PDFS:
        for part in _gmail_walk(entry["_payload"]):
            fname = part.get("filename", "")
            att_id = part.get("body", {}).get("attachmentId")
            if att_id and _is_pdf(fname, part.get("mimeType")):
                att = service.users().messages().attachments().get(
                    userId="me", messageId=entry["id"], id=att_id).execute()
                pdfs.append(save_pdf(fname, base64.urlsafe_b64decode(att["data"])))
    if MARK_MATCHES_READ:
        service.users().messages().modify(
            userId="me", id=entry["id"], body={"removeLabelIds": ["UNREAD"]}).execute()
        actions.append("marked read")
    if TRASH_MATCHES:
        service.users().messages().trash(userId="me", id=entry["id"]).execute()
        actions.append("moved to Trash")
    if not actions:
        actions.append("left unread in place")
    return pdfs, actions


# ── IMAP (Proton Bridge / WebCentral) ────────────────────────────────────────

def _imap_ssl_context(cfg):
    """Mirror of ProtonMailMixin._build_ssl_context: pinned cert > explicit
    opt-out > loopback CERT_NONE > system trust store."""
    ca = cfg.get("ca_cert_path")
    if ca and os.path.isfile(ca):
        return ssl.create_default_context(cafile=ca)
    if cfg.get("verify_tls") is False or \
            (cfg.get("imap_host") or "").strip().lower() in ("127.0.0.1", "localhost", "::1", "0.0.0.0"):
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
        except imaplib.IMAP4.error:
            pass  # Bridge quirk — login proceeds on the plain local socket
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
                "date": msg.get("Date", ""),
                "lines": body_lines(_imap_body_text(msg)),
                "_conn": conn, "_msg": msg, "_trash": trash_folder,
            })
    return entries


def imap_act(entry):
    conn, uid = entry["_conn"], entry["id"]
    pdfs, actions = [], []
    if SAVE_MATCH_PDFS:
        # The PDFs come out of the message already fetched with BODY.PEEK
        # during collection — saving them costs no IMAP traffic and cannot
        # touch the \Seen flag.
        for part in entry["_msg"].walk():
            fname = decode_header(part.get_filename() or "")
            if fname and _is_pdf(fname, part.get_content_type()):
                payload = part.get_payload(decode=True) or b""
                if payload:
                    pdfs.append(save_pdf(fname, payload))
    if MARK_MATCHES_READ or TRASH_MATCHES:
        conn.select(_quote_mailbox(entry["folder"]))  # read-write select
    if MARK_MATCHES_READ:
        typ, _ = conn.uid("store", uid, "+FLAGS", r"(\Seen)")
        actions.append("marked read" if typ == "OK" else "mark-read FAILED")
    if TRASH_MATCHES:
        trash = _quote_mailbox(entry["_trash"])
        if "MOVE" in conn.capabilities:
            typ, _ = conn.uid("move", uid, trash)
        else:
            typ, _ = conn.uid("copy", uid, trash)
            if typ == "OK":
                conn.uid("store", uid, "+FLAGS", r"(\Deleted)")
                conn.expunge()
        actions.append(f"moved to {entry['_trash']}" if typ == "OK"
                       else f"move to {entry['_trash']} FAILED")
    if not actions:
        actions.append("left unread in place")
    return pdfs, actions


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
        app = msal.PublicClientApplication(client_id, authority=authority, token_cache=cache)
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
    token = outlook_token(account, account_email)
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.request(method, GRAPH_BASE + path, headers=headers,
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


def outlook_collect(account, account_email):
    entries = []
    select = "id,subject,from,sender,toRecipients,receivedDateTime,body,hasAttachments"
    for folder, tag in (("inbox", ""), ("junkemail", "JUNK")):
        url = f"/me/mailFolders/{folder}/messages"
        params = {"$filter": "isRead eq false", "$top": 100, "$select": select}
        resp = graph(account, account_email, "GET", url, params=params)
        for m in resp.get("value", []):
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


def outlook_act(entry):
    account, account_email = entry["account"], entry["account_email"]
    pdfs, actions = [], []
    if SAVE_MATCH_PDFS and entry["_has_atts"]:
        resp = graph(account, account_email, "GET",
                     f"/me/messages/{entry['id']}/attachments")
        for a in resp.get("value", []):
            if a.get("contentBytes") and _is_pdf(a.get("name"), a.get("contentType")):
                pdfs.append(save_pdf(a.get("name"), base64.b64decode(a["contentBytes"])))
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
    return pdfs, actions


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

def _field(label, value, width=9):
    """One wrapped 'Label: value' entry line with a hanging indent. ``width``
    widens the label column for labels that outgrow the default
    ("Determine:" is 10 chars)."""
    prefix = f"   {label:<{width}}"
    return textwrap.fill(value or "", width=WRAP, initial_indent=prefix,
                         subsequent_indent=" " * len(prefix)) or prefix.rstrip()


def format_entry(n, entry):
    """A SPECIFYING match renders like any other email; the only additions
    are the bare "SPECIFYING LIST EMAIL" marker line at the top, followed by
    "Type={Type}" and "Index={Index}" lines echoing those rule columns
    verbatim (blank value -> bare "Type=" / "Index="), and, at the bottom,
    the names of any downloaded PDF attachments plus a Determine: line
    echoing the rule's Determine column verbatim from SpecifyingList.csv."""
    tag = f" [{entry['folder_tag']}]" if entry["folder_tag"] else ""
    num = f"{n}. "
    lines = []
    if entry.get("spec"):
        lines.append(f"{num}SPECIFYING LIST EMAIL")
        lines.append(f"   Type={entry['spec'].get('type') or ''}")
        lines.append(f"   Index={entry['spec'].get('index') or ''}")
        lines.append(f"   Account:  {entry['account_email']}{tag}")
    else:
        lines.append(f"{num}Account:  {entry['account_email']}{tag}")
    lines.append(_field("From:", entry["from"]))
    fwd = forwarded_to(entry, entry["account_email"])
    if fwd:
        lines.append(_field("To:", fwd))
    lines.append(_field("Subject:", entry["subject"]))
    lines.append(_field("Date:", entry["date"]))
    lines.append(_field("Summary:", summarize(entry["lines"], entry["subject"])))
    pdfs = entry.get("pdfs") or []
    if pdfs:
        lines.append(_field("PDFs:", "; ".join(pdfs)))
    if entry.get("spec"):
        lines.append(_field("Determine:",
                            entry["spec"].get("determine") or "(none)", width=11))
    return "\n".join(lines)


def build_body(account_order, entries_by_account, errors, dry_run):
    now = datetime.now()
    out = [DIV, "COMPREHENSIVE LIST OF UNREAD EMAILS", DIV,
           f"Generated {now:%Y-%m-%d %H:%M:%S} by UnreadSummary.py",
           "(deterministic, no LLM — summaries are each",
           "email's opening text)"]
    if dry_run:
        out.append("*** DRY RUN: no emails were modified ***")
    n = 0
    for account, label in account_order:
        out += ["", SUB, f"Account: {label}", SUB]
        if account in errors:
            out += ["", f"ERROR: {errors[account]}"]
            continue
        entries = entries_by_account.get(account, [])
        if not entries:
            out += ["", "No unread emails."]
        for entry in entries:
            n += 1
            out += ["", format_entry(n, entry)]
    matched = [e for es in entries_by_account.values() for e in es if e.get("spec")]
    out += ["", DIV,
            f"TOTAL: {n} unread email(s) across {len(account_order)} account(s); "
            f"{len(matched)} SPECIFYING match(es)"]
    if errors:
        out.append(f"ERRORS: {len(errors)} account(s) unreadable — see above")
    out.append(DIV)
    return "\n".join(out)


# ── Main ─────────────────────────────────────────────────────────────────────

def load_accounts(path):
    f = path / "accounts.json"
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8")).get("accounts", {})
    except Exception:
        return {}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="read-only pass: print the email body, change nothing")
    args = parser.parse_args()

    socket.setdefaulttimeout(60)  # imaplib has no per-call timeout; a hung
    # Bridge socket must not wedge an unattended launchd run forever.

    gmail_accounts = load_accounts(GOOGLE_CONFIG_DIR)
    imap_accounts = load_accounts(PROTON_CONFIG_DIR)
    outlook_accounts = load_accounts(OUTLOOK_CONFIG_DIR)

    account_order = []          # [(account_key, display_label)]
    entries_by_account = {}
    errors = {}
    imap_conns = {}

    for account, cfg in gmail_accounts.items():
        email_addr = cfg.get("email", account)
        account_order.append((account, f"{email_addr} (Gmail)"))
        try:
            entries_by_account[account] = gmail_collect(account, email_addr)
        except Exception as e:
            errors[account] = f"{type(e).__name__}: {e}"

    for account, cfg in imap_accounts.items():
        host = cfg.get("imap_host", "")
        kind = "Proton Bridge" if host in ("127.0.0.1", "localhost") else "IMAP"
        account_order.append((account, f"{cfg.get('email', account)} ({kind})"))
        try:
            conn = imap_connect(cfg)
            imap_conns[account] = conn
            entries_by_account[account] = imap_collect(account, cfg, conn)
        except Exception as e:
            errors[account] = f"{type(e).__name__}: {e}"

    for account, cfg in outlook_accounts.items():
        email_addr = cfg.get("email", account)
        account_order.append((account, f"{email_addr} (Outlook)"))
        try:
            entries_by_account[account] = outlook_collect(account, email_addr)
        except Exception as e:
            errors[account] = f"{type(e).__name__}: {e}"

    # Match against SpecifyingList.csv — the emailed list only carries the
    # "SPECIFYING LIST EMAIL" marker (and PDF names once downloaded).
    matched = []
    for entries in entries_by_account.values():
        for entry in entries:
            spec = match_specifying(entry)
            if spec:
                entry["spec"] = spec
                log(f"SPECIFYING match ({spec['name']}) in {entry['account']}")
                matched.append(entry)

    # Action phase — only ever sees SPECIFYING matches, and each action is
    # individually flag-gated (download PDFs / mark read; trash is off).
    # Outcomes are logged, not emailed. Skipped wholesale on --dry-run.
    if not args.dry_run and (SAVE_MATCH_PDFS or MARK_MATCHES_READ or TRASH_MATCHES):
        for entry in matched:
            try:
                act = {"Gmail": gmail_act, "IMAP": imap_act, "Outlook": outlook_act}[entry["provider"]]
                entry["pdfs"], actions = act(entry)
                log(f"acted on match in {entry['account']}: {'; '.join(actions)}"
                    + (f"; pdfs: {', '.join(entry['pdfs'])}" if entry["pdfs"] else ""))
            except Exception as e:
                log(f"action failed for {entry['account']} {entry['subject']!r}: {e}")

    for conn in imap_conns.values():
        try:
            conn.logout()
        except Exception:
            pass

    body = build_body(account_order, entries_by_account, errors, args.dry_run)
    subject = f"{SUBJECT_PREFIX} - {datetime.now():%Y-%m-%d %H:%M:%S}"
    total = sum(len(v) for v in entries_by_account.values())

    if args.dry_run:
        print(f"Subject: {subject}\n")
        print(body)
        log(f"DRY RUN — {total} unread, {len(matched)} specifying match(es), "
            f"{len(errors)} account error(s)")
        return

    send_cfg = outlook_accounts.get(SEND_FROM_OUTLOOK_ACCOUNT, {})
    outlook_send(SEND_FROM_OUTLOOK_ACCOUNT,
                 send_cfg.get("email", SEND_FROM_OUTLOOK_ACCOUNT), subject, body)
    log(f"sent summary to {SEND_TO}: {total} unread across "
        f"{len(account_order)} accounts, {len(matched)} specifying match(es), "
        f"{len(errors)} account error(s)")


if __name__ == "__main__":
    rotate_log_if_needed()
    try:
        main()
    except Exception as e:
        # Fatal (e.g. the summary send itself failed). Collected state is
        # lost but nothing is half-mutated: per-entry actions either ran and
        # were logged, or didn't run. The next scheduled pass retries.
        log(f"ERROR {type(e).__name__}: {e}")
        sys.exit(1)
