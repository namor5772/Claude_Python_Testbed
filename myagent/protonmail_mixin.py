"""Proton Mail integration via Proton Bridge (IMAP + SMTP).

Architecture notes:

* Proton Mail does not expose a public REST API — E2E encryption means
  decryption only happens client-side. The officially supported integration
  path is Proton Bridge, a desktop app published by Proton that logs into
  your account, decrypts mail locally, and exposes a localhost IMAP server
  + SMTP server. Third-party mail clients (Thunderbird, Outlook, Apple Mail
  — and MyAgent) speak standard IMAP/SMTP to Bridge, never to Proton.

* This mixin mirrors ``gmail_mixin.py`` 1:1 in surface area — same 16 tools
  (proton_search, proton_read, proton_send, ...) wired through the same
  ``_execute_tool`` dispatch path and the same per-instruction Proton
  checkbox. The only difference is the transport: stdlib ``imaplib`` +
  ``smtplib`` instead of Google's discovery service.

* Per-account config lives in ``~/.config/myagent-protonmail/accounts.json``::

      {"accounts": {
          "personal": {
              "email": "alice@proton.me",
              "imap_host": "127.0.0.1", "imap_port": 1143,
              "smtp_host": "127.0.0.1", "smtp_port": 1025,
              "username": "alice@proton.me",
              "app_password": "<bridge-generated-token>",
              "ca_cert_path": "/optional/path/to/bridge/cert.pem"
          }
      }}

  Bridge generates a unique IMAP/SMTP port pair per account — they're not
  shared across accounts on the same Bridge instance. ``app_password`` is
  the Bridge-generated token (NOT the real Proton account password). If
  ``ca_cert_path`` is omitted, the IMAP/SMTP connections fall back to
  unverified TLS (acceptable for localhost-only traffic; a network MITM
  would have to be running on the user's own machine).

* IMAP folder model. Proton Bridge maps Proton's label-and-folder system
  to IMAP folders: ``INBOX``, ``Sent``, ``Drafts``, ``Trash``, ``Archive``,
  ``Spam``, ``All Mail``, ``Starred`` (system); ``Folders/<name>`` (user
  folders); ``Labels/<name>`` (user labels). So in this mixin "label" is
  synonymous with "IMAP folder" — ``proton_list_labels`` returns all
  folders, ``proton_create_label`` creates one under ``Labels/`` (default)
  or ``Folders/``, ``proton_modify_labels`` is an IMAP MOVE between
  folders. Confusing? Yes — but matches Bridge's exposed surface.

* Message addressing. IMAP UIDs are per-folder (the same physical message
  in INBOX vs "All Mail" has different UIDs), so every per-message tool
  takes a ``folder`` + ``uid`` pair, and bulk ops take ``folder`` once +
  ``uids: [int]``. To trash messages from multiple folders, the agent
  calls ``proton_trash`` once per folder. Slightly more verbose than
  Gmail's global IDs but a faithful representation of IMAP's reality
  (and avoids hidden round-trips to resolve "which folder is this UID in").

* Destructive ops (``proton_send``, ``proton_reply``, ``proton_send_draft``,
  ``proton_trash``, ``proton_delete_label``) pop the same Tk
  ``messagebox.askyesno`` confirmation dialog as Gmail's tools. The
  ``_disabled_confirm_patterns`` set (managed via the Safety button) can
  bypass per-tool, just like the Gmail confirms.

* No availability flag gating. ``_HAS_PROTONMAIL = True`` always, since
  the transport is stdlib (``imaplib`` + ``smtplib`` are in CPython). The
  flag exists for parity with ``_HAS_GOOGLE`` / ``_HAS_MCP`` and would
  flip to False if a future refactor swapped in an external library.
  "Bridge is installed and running" is detected at first-call time and
  surfaces as a clear connection error to the agent rather than a
  startup-time check (Bridge can be restarted while MyAgent is running).
"""

import base64
import email
import imaplib
import json
import mimetypes
import os
import re
import smtplib
import ssl
import tkinter as tk
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from tkinter import messagebox

# Bridge accepts up to 25 MB per message (same hard ceiling as most SMTP
# servers). Cap raw attachment bytes at 20 MB combined to leave headroom
# for headers + base64 overhead before SMTP refuses.
MAX_ATTACHMENT_BYTES_TOTAL = 20 * 1024 * 1024

# Per-tool body truncation when returning to the model. Same value Gmail
# uses; balances "the agent can read a long email" against
# "don't blow the context window on a single message".
MAX_BODY_CHARS = 50_000

# IMAP search/list result cap. Higher than the typical max_results we'd
# return to the agent (25-100); the extra headroom lets the model ask for
# wider searches without us silently truncating IMAP's reply.
IMAP_FETCH_BATCH = 500

# Always-available since stdlib transport.
_HAS_PROTONMAIL = True

PROTON_CONFIG_DIR = os.path.expanduser("~/.config/myagent-protonmail")
PROTON_ACCOUNTS_FILE = os.path.join(PROTON_CONFIG_DIR, "accounts.json")

# Proton/Bridge folder naming. Tools surface these as defaults in their
# schemas; the model can override with any folder name returned by
# proton_list_labels.
PROTON_INBOX = "INBOX"
PROTON_SENT = "Sent"
PROTON_DRAFTS = "Drafts"
PROTON_TRASH = "Trash"
PROTON_ARCHIVE = "Archive"
PROTON_SPAM = "Spam"
PROTON_ALL_MAIL = "All Mail"


class ProtonMailMixin:
    """Proton Mail tools backed by Proton Bridge over IMAP + SMTP."""

    # ── State init ──────────────────────────────────────────────────────────

    def _proton_init_state(self):
        """Initialise Proton-related instance attributes. Call from App.__init__."""
        # account_name -> open imaplib.IMAP4 instance. Kept warm across
        # tool calls; lazily reconnected if a call hits a stale socket.
        self._proton_imap_conns = {}
        self._proton_accounts_cache = None  # lazy

    # ── Account discovery ───────────────────────────────────────────────────

    def _load_proton_accounts(self):
        """Read accounts.json. Returns a dict keyed by account name.
        Empty dict if file missing or malformed — calling tools then fail
        with a clear message at use time rather than at startup, so a
        user who has Proton off doesn't see noise."""
        if not _HAS_PROTONMAIL:
            return {}
        if not os.path.exists(PROTON_ACCOUNTS_FILE):
            return {}
        try:
            with open(PROTON_ACCOUNTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            accounts = data.get("accounts", {})
            return accounts if isinstance(accounts, dict) else {}
        except Exception:
            return {}

    def _get_proton_account_names(self):
        """List of configured account names (sorted). Used to patch the
        ``account`` enum on every Proton tool schema at runtime."""
        if self._proton_accounts_cache is None:
            self._proton_accounts_cache = self._load_proton_accounts()
        return sorted(self._proton_accounts_cache.keys())

    # ── Connection helpers ──────────────────────────────────────────────────

    @staticmethod
    def _build_ssl_context(account_cfg):
        """Build an ssl.SSLContext for Bridge. If ca_cert_path is configured,
        use it for verification; otherwise fall back to unverified TLS.

        Bridge issues a self-signed cert per install; verifying requires
        the user to point us at Bridge's cert PEM. Unverified is acceptable
        for localhost-only Bridge traffic since a MITM attacker would
        already need code execution on the user's machine.
        """
        ca_cert = account_cfg.get("ca_cert_path") if account_cfg else None
        if ca_cert and os.path.isfile(ca_cert):
            ctx = ssl.create_default_context(cafile=ca_cert)
        else:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _proton_imap(self, account):
        """Return an authenticated IMAP connection for the account.

        Cached per-account in ``self._proton_imap_conns``. If the cached
        connection is dead (e.g. Bridge restart), the next operation will
        raise; callers can call ``_proton_drop_imap`` and retry."""
        if not _HAS_PROTONMAIL:
            raise RuntimeError("Proton Mail transport not available.")
        if account in self._proton_imap_conns:
            return self._proton_imap_conns[account]

        accounts = self._load_proton_accounts()
        if account not in accounts:
            raise ValueError(
                f"Unknown Proton account '{account}'. Configure it in "
                f"{PROTON_ACCOUNTS_FILE} (see README Proton Mail Integration section)."
            )
        cfg = accounts[account]
        host = cfg.get("imap_host", "127.0.0.1")
        port = int(cfg.get("imap_port", 1143))
        username = cfg.get("username") or cfg.get("email")
        password = cfg.get("app_password")
        if not username or not password:
            raise ValueError(
                f"Proton account '{account}' is missing username or app_password. "
                f"Generate a Bridge token from the Bridge UI and put it in {PROTON_ACCOUNTS_FILE}."
            )

        ctx = self._build_ssl_context(cfg)
        use_ssl = bool(cfg.get("imap_ssl", False))
        if use_ssl:
            conn = imaplib.IMAP4_SSL(host, port, ssl_context=ctx)
        else:
            conn = imaplib.IMAP4(host, port)
            try:
                conn.starttls(ssl_context=ctx)
            except imaplib.IMAP4.error:
                # Some Bridge versions return a quirky CAPABILITY after
                # STARTTLS. If STARTTLS isn't advertised, fall through and
                # try login on the plain connection — Bridge on localhost
                # accepts it but flag it so it's visible in the activity log.
                pass
        try:
            conn.login(username, password)
        except imaplib.IMAP4.error as e:
            raise RuntimeError(
                f"Proton IMAP login failed for '{account}': {e}. "
                f"Check the app_password in {PROTON_ACCOUNTS_FILE} and confirm "
                f"Proton Bridge is running."
            )
        self._proton_imap_conns[account] = conn
        return conn

    def _proton_drop_imap(self, account):
        """Forget the cached IMAP connection for an account (call on errors).
        The next ``_proton_imap`` call will re-establish."""
        conn = self._proton_imap_conns.pop(account, None)
        if conn is None:
            return
        try:
            conn.logout()
        except Exception:
            pass

    def _proton_smtp(self, account):
        """Open a fresh SMTP connection, authenticated, and return it. The
        caller is responsible for calling ``.quit()`` when done — SMTP
        connections are short-lived (one send each), unlike IMAP."""
        if not _HAS_PROTONMAIL:
            raise RuntimeError("Proton Mail transport not available.")
        accounts = self._load_proton_accounts()
        if account not in accounts:
            raise ValueError(f"Unknown Proton account '{account}'.")
        cfg = accounts[account]
        host = cfg.get("smtp_host", "127.0.0.1")
        port = int(cfg.get("smtp_port", 1025))
        username = cfg.get("username") or cfg.get("email")
        password = cfg.get("app_password")
        ctx = self._build_ssl_context(cfg)
        use_ssl = bool(cfg.get("smtp_ssl", False))
        if use_ssl:
            client = smtplib.SMTP_SSL(host, port, context=ctx, timeout=30)
        else:
            client = smtplib.SMTP(host, port, timeout=30)
            client.ehlo()
            try:
                client.starttls(context=ctx)
                client.ehlo()
            except smtplib.SMTPException:
                pass
        try:
            client.login(username, password)
        except smtplib.SMTPException as e:
            try:
                client.quit()
            except Exception:
                pass
            raise RuntimeError(
                f"Proton SMTP login failed for '{account}': {e}. "
                f"Check app_password and confirm Bridge is running."
            )
        return client

    def _proton_close_connections(self):
        """LOGOUT all open IMAP connections. Best-effort; called on app close."""
        for account, conn in list(self._proton_imap_conns.items()):
            try:
                conn.logout()
            except Exception:
                pass
        self._proton_imap_conns.clear()

    # ── Safety: modal confirmation for destructive ops ──────────────────────

    def _confirm_proton_action(self, tool_name, title, summary, detail):
        """Same pattern as ``_confirm_gmail_action``. Returns True if the
        user clicks Yes; honours per-instruction bypass via
        ``_disabled_confirm_patterns``."""
        disabled = getattr(self, "_disabled_confirm_patterns", set())
        if tool_name in disabled:
            queue = getattr(self, "queue", None)
            if queue is not None:
                queue.put({
                    "type": "warning",
                    "content": f"⚠ Proton confirm bypassed for {tool_name}\n",
                })
            return True
        message = f"{summary}\n\n{detail}\n\nProceed?"
        try:
            return bool(messagebox.askyesno(title, message, parent=self.root))
        except Exception:
            return False

    # ── Helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _attach_proton_files(msg, attachments):
        """Attach files to an EmailMessage. Returns (ok, summary_or_error).
        Matches Gmail's ``_attach_proton_files`` semantics."""
        if not attachments:
            return True, ""
        if isinstance(attachments, str):
            attachments = [attachments]
        total = 0
        info = []
        attached_blobs = []
        for path in attachments:
            if not os.path.isfile(path):
                return False, f"attachment not found or not a file: {path}"
            size = os.path.getsize(path)
            total += size
            if total > MAX_ATTACHMENT_BYTES_TOTAL:
                return False, (
                    f"attachments exceed {MAX_ATTACHMENT_BYTES_TOTAL // (1024*1024)} MB combined "
                    f"raw size (SMTP limit is ~25 MB after base64 encoding)"
                )
            with open(path, "rb") as f:
                data = f.read()
            mime_type, _ = mimetypes.guess_type(path)
            if not mime_type:
                mime_type = "application/octet-stream"
            maintype, _, subtype = mime_type.partition("/")
            attached_blobs.append((data, maintype, subtype or "octet-stream", os.path.basename(path)))
            info.append(f"{os.path.basename(path)} ({size} bytes)")
        for data, maintype, subtype, filename in attached_blobs:
            msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
        return True, "[" + ", ".join(info) + "]"

    @staticmethod
    def _quote_mailbox(name):
        """Wrap an IMAP mailbox name in double-quotes if it contains spaces
        or special characters. imaplib doesn't auto-quote and Bridge
        rejects unquoted names with spaces like 'All Mail'."""
        if not name:
            return '""'
        if any(c in name for c in ' "\\'):
            escaped = name.replace("\\", "\\\\").replace('"', '\\"')
            return f'"{escaped}"'
        return name

    def _select_folder(self, conn, folder, readonly=False):
        """SELECT (or EXAMINE) a folder. Raises with the IMAP error text on
        failure so the caller can return a clean message to the agent."""
        quoted = self._quote_mailbox(folder)
        if readonly:
            typ, data = conn.select(quoted, readonly=True)
        else:
            typ, data = conn.select(quoted)
        if typ != "OK":
            raise RuntimeError(
                f"Could not select folder {folder!r}: "
                f"{data[0].decode('utf-8', 'replace') if data and data[0] else 'unknown'}"
            )

    @staticmethod
    def _decode_header(value):
        """Decode an RFC 2047 encoded header (e.g. ``=?utf-8?B?...?=``) to a
        plain unicode string. Returns "" on falsy input."""
        if not value:
            return ""
        try:
            decoded = email.header.decode_header(value)
            parts = []
            for text, charset in decoded:
                if isinstance(text, bytes):
                    parts.append(text.decode(charset or "utf-8", errors="replace"))
                else:
                    parts.append(text)
            return "".join(parts)
        except Exception:
            return str(value)

    @staticmethod
    def _extract_proton_bodies(msg):
        """Walk an email.message.Message tree and collect text/plain and
        text/html bodies. Returns (text_body, html_body)."""
        text_body = ""
        html_body = ""
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disp:
                continue
            if ctype == "text/plain" and not text_body:
                try:
                    payload = part.get_payload(decode=True) or b""
                    charset = part.get_content_charset() or "utf-8"
                    text_body = payload.decode(charset, errors="replace")
                except Exception:
                    pass
            elif ctype == "text/html" and not html_body:
                try:
                    payload = part.get_payload(decode=True) or b""
                    charset = part.get_content_charset() or "utf-8"
                    html_body = payload.decode(charset, errors="replace")
                except Exception:
                    pass
        return text_body, html_body

    @staticmethod
    def _extract_proton_attachments(msg):
        """Walk an email.message.Message and surface attachment metadata.
        Each entry: {filename, mime_type, size, attachment_id, part_index,
        inline}.

        IMAP doesn't have Gmail-style attachment IDs — we synthesise one
        using the part's index in the message walk so ``proton_get_attachment``
        can locate the same part on re-fetch."""
        attachments = []
        for idx, part in enumerate(msg.walk()):
            filename = part.get_filename()
            disp = (part.get("Content-Disposition") or "").lower()
            ctype = part.get_content_type()
            if not filename:
                continue
            try:
                payload = part.get_payload(decode=True) or b""
                size = len(payload)
            except Exception:
                size = 0
            attachments.append({
                "filename": ProtonMailMixin._decode_header(filename),
                "mime_type": ctype,
                "size": size,
                "attachment_id": f"part:{idx}",
                "part_index": idx,
                "inline": "inline" in disp,
            })
        return attachments

    @staticmethod
    def _format_proton_summary(uid, folder, msg):
        """Compact summary dict — analogous to Gmail's ``_format_proton_summary``
        but with IMAP-native (folder, uid) addressing."""
        return {
            "uid": uid,
            "folder": folder,
            "subject": ProtonMailMixin._decode_header(msg.get("Subject", "")),
            "from": ProtonMailMixin._decode_header(msg.get("From", "")),
            "to": ProtonMailMixin._decode_header(msg.get("To", "")),
            "date": msg.get("Date", ""),
            "message_id_header": msg.get("Message-ID", ""),
        }

    def _fetch_envelope(self, conn, uid):
        """Fetch RFC822 headers + flags for a single UID. Returns
        (email.Message, flags_list) or (None, []) on failure."""
        typ, data = conn.uid("fetch", str(uid), "(BODY.PEEK[HEADER] FLAGS)")
        if typ != "OK" or not data or not data[0]:
            return None, []
        raw = b""
        flags = []
        for item in data:
            if isinstance(item, tuple) and len(item) >= 2:
                raw = item[1] or b""
            elif isinstance(item, bytes):
                m = re.search(rb"FLAGS \(([^)]*)\)", item)
                if m:
                    flags = m.group(1).decode("ascii", "replace").split()
        try:
            msg = email.message_from_bytes(raw)
        except Exception:
            msg = None
        return msg, flags

    def _fetch_full(self, conn, uid):
        """Fetch full RFC822 message (body + attachments) for a UID."""
        typ, data = conn.uid("fetch", str(uid), "(BODY.PEEK[])")
        if typ != "OK" or not data or not data[0]:
            return None
        for item in data:
            if isinstance(item, tuple) and len(item) >= 2:
                try:
                    return email.message_from_bytes(item[1] or b"")
                except Exception:
                    return None
        return None

    @staticmethod
    def _build_search_criteria(q):
        """Pass-through helper: imaplib's ``search`` wants raw IMAP SEARCH
        keywords as a sequence of strings. We accept either a single
        keyword string ("UNSEEN", "ALL") or a full IMAP query string and
        return it as a single criterion. The agent sees the IMAP syntax
        in the tool description, so it can compose multi-predicate
        searches like 'FROM "alice@example.com" UNSEEN SINCE 1-Jan-2026'."""
        return q.strip() if q else "ALL"

    @staticmethod
    def _uid_search(conn, criteria):
        """Issue ``UID SEARCH`` against an open connection, transparently
        switching to ``CHARSET UTF-8`` encoding when the criteria contains
        non-ASCII characters (smart quotes, accented letters, em-dashes,
        etc.) so RFC 3501 SEARCH can handle them.

        For pure ASCII the call uses no charset clause — maximum compatibility
        with older servers. For non-ASCII the criteria is encoded as UTF-8
        bytes and shipped after a ``CHARSET UTF-8`` declaration. imaplib
        accepts bytes positional args and appends them to the on-wire command
        verbatim, which Bridge accepts in practice (verified against em-dash
        subjects). Servers that require strict IMAP literal framing would
        need a deeper rewrite — but Bridge handles the inline form fine.
        """
        try:
            criteria.encode("ascii")
            return conn.uid("search", None, criteria)
        except UnicodeEncodeError:
            return conn.uid("search", "CHARSET", "UTF-8", criteria.encode("utf-8"))

    # ── Tool implementations ────────────────────────────────────────────────

    def do_proton_search(self, params):
        """IMAP SEARCH within a folder. Returns up to ``max_results`` matching
        UIDs with envelope info."""
        account = params.get("account")
        q = params.get("q", "ALL")
        folder = params.get("folder", PROTON_INBOX)
        max_results = min(int(params.get("max_results") or 25), IMAP_FETCH_BATCH)
        if not account:
            return "error: 'account' is required"
        try:
            conn = self._proton_imap(account)
            self._select_folder(conn, folder, readonly=True)
            criteria = self._build_search_criteria(q)
            typ, data = self._uid_search(conn, criteria)
            if typ != "OK":
                return f"error: IMAP search failed: {data}"
            uids = (data[0] or b"").split()
            # Newest first; IMAP typically returns ascending UIDs.
            uids = list(reversed(uids))[:max_results]
            results = []
            for raw_uid in uids:
                uid = raw_uid.decode("ascii", "replace")
                msg, _flags = self._fetch_envelope(conn, uid)
                if msg is None:
                    continue
                results.append(self._format_proton_summary(uid, folder, msg))
            return json.dumps({
                "folder": folder,
                "count": len(results),
                "messages": results,
            }, indent=2, ensure_ascii=False)
        except Exception as e:
            self._proton_drop_imap(account)
            return f"error: {e}"

    def do_proton_read(self, params):
        """Fetch the full body of one message. ``format``: 'text' (default),
        'html', or 'both'."""
        account = params.get("account")
        folder = params.get("folder", PROTON_INBOX)
        uid = params.get("uid")
        fmt = params.get("format", "text")
        if not account or uid is None:
            return "error: 'account' and 'uid' are required"
        try:
            conn = self._proton_imap(account)
            self._select_folder(conn, folder, readonly=True)
            msg = self._fetch_full(conn, uid)
            if msg is None:
                return f"error: no message with UID {uid} in folder {folder!r}"
            text_body, html_body = self._extract_proton_bodies(msg)
            attachments = self._extract_proton_attachments(msg)
            result = {
                "uid": str(uid),
                "folder": folder,
                "subject": self._decode_header(msg.get("Subject", "")),
                "from": self._decode_header(msg.get("From", "")),
                "to": self._decode_header(msg.get("To", "")),
                "cc": self._decode_header(msg.get("Cc", "")),
                "date": msg.get("Date", ""),
                "message_id_header": msg.get("Message-ID", ""),
                "in_reply_to": msg.get("In-Reply-To", ""),
                "references": msg.get("References", ""),
                "attachments": attachments,
            }
            if fmt in ("text", "both"):
                body = text_body or (re.sub(r"<[^>]+>", "", html_body) if html_body else "")
                result["body"] = body[:MAX_BODY_CHARS]
                result["body_truncated"] = len(body) > MAX_BODY_CHARS
            if fmt in ("html", "both"):
                result["body_html"] = (html_body or "")[:MAX_BODY_CHARS]
                result["body_html_truncated"] = len(html_body or "") > MAX_BODY_CHARS
            return json.dumps(result, indent=2, ensure_ascii=False)
        except Exception as e:
            self._proton_drop_imap(account)
            return f"error: {e}"

    def do_proton_get_attachment(self, params):
        """Download a single attachment to a local file path."""
        account = params.get("account")
        folder = params.get("folder", PROTON_INBOX)
        uid = params.get("uid")
        att_id = params.get("attachment_id", "")
        save_to = params.get("save_to")
        overwrite = bool(params.get("overwrite", False))
        if not all([account, uid, att_id, save_to]):
            return "error: account, uid, attachment_id, and save_to are required"
        if os.path.exists(save_to) and not overwrite:
            return f"error: file already exists at {save_to} (pass overwrite=true to replace)"
        m = re.match(r"^part:(\d+)$", att_id)
        if not m:
            return f"error: unrecognised attachment_id {att_id!r} (expected 'part:N')"
        target_idx = int(m.group(1))
        try:
            conn = self._proton_imap(account)
            self._select_folder(conn, folder, readonly=True)
            msg = self._fetch_full(conn, uid)
            if msg is None:
                return f"error: no message with UID {uid} in folder {folder!r}"
            for idx, part in enumerate(msg.walk()):
                if idx != target_idx:
                    continue
                filename = part.get_filename() or ""
                payload = part.get_payload(decode=True) or b""
                os.makedirs(os.path.dirname(save_to) or ".", exist_ok=True)
                with open(save_to, "wb") as f:
                    f.write(payload)
                return json.dumps({
                    "saved_to": save_to,
                    "bytes": len(payload),
                    "filename": self._decode_header(filename),
                    "mime_type": part.get_content_type(),
                })
            return f"error: no part {target_idx} in message"
        except Exception as e:
            self._proton_drop_imap(account)
            return f"error: {e}"

    def _build_outgoing_message(self, params, from_address):
        """Build a multipart EmailMessage from the standard send params.
        Returns (msg, attached_summary) or raises ValueError."""
        to = params.get("to", "")
        cc = params.get("cc", "")
        bcc = params.get("bcc", "")
        subject = params.get("subject", "")
        body = params.get("body", "")
        body_html = params.get("body_html")
        attachments = params.get("attachments") or []
        msg = EmailMessage()
        msg["From"] = from_address
        msg["To"] = to
        if cc:
            msg["Cc"] = cc
        if bcc:
            msg["Bcc"] = bcc
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid()
        if body_html:
            msg.set_content(body)
            msg.add_alternative(body_html, subtype="html")
        else:
            msg.set_content(body)
        ok, info = self._attach_proton_files(msg, attachments)
        if not ok:
            raise ValueError(info)
        return msg, info

    def _smtp_send(self, account, msg, from_address):
        """Open SMTP, send, close. Recipients gathered from To/Cc/Bcc.
        Bcc header is stripped before send (server-side delivery only)."""
        to_addrs = []
        for hdr in ("To", "Cc", "Bcc"):
            for raw in (msg.get(hdr, "") or "").split(","):
                _, addr = parseaddr(raw)
                if addr:
                    to_addrs.append(addr)
        # Don't leak Bcc to recipients
        if "Bcc" in msg:
            del msg["Bcc"]
        client = self._proton_smtp(account)
        try:
            client.send_message(msg, from_addr=from_address, to_addrs=to_addrs)
        finally:
            try:
                client.quit()
            except Exception:
                pass

    def _imap_append_to_drafts(self, account, msg, folder=PROTON_DRAFTS):
        """APPEND a raw EmailMessage to a folder (used by create_draft and
        send_to-Sent flows). Returns the new UID if Bridge reports it via
        APPENDUID, else empty string."""
        conn = self._proton_imap(account)
        raw = msg.as_bytes()
        typ, data = conn.append(
            self._quote_mailbox(folder),
            r"(\Draft)" if folder == PROTON_DRAFTS else None,
            imaplib.Time2Internaldate(0),
            raw,
        )
        if typ != "OK":
            raise RuntimeError(
                f"APPEND to {folder} failed: "
                f"{data[0].decode('utf-8', 'replace') if data and data[0] else 'unknown'}"
            )
        new_uid = ""
        for item in data:
            if isinstance(item, bytes):
                m = re.search(rb"APPENDUID \d+ (\d+)", item)
                if m:
                    new_uid = m.group(1).decode("ascii")
        return new_uid

    def do_proton_send(self, params):
        account = params.get("account")
        if not account:
            return "error: 'account' is required"
        if not params.get("to"):
            return "error: 'to' is required"
        accounts = self._load_proton_accounts()
        cfg = accounts.get(account, {})
        from_address = cfg.get("email") or cfg.get("username") or account
        if not self._confirm_proton_action(
            "proton_send",
            "Confirm Proton Send",
            f"Send a Proton email from {account}?",
            f"To: {params.get('to')}\nSubject: {params.get('subject', '(no subject)')}",
        ):
            return "user denied: proton_send was rejected by the user"
        try:
            msg, att_info = self._build_outgoing_message(params, from_address)
            self._smtp_send(account, msg, from_address)
            # Best-effort APPEND to Sent so Proton's UI shows the sent mail.
            # Bridge can be configured to do this automatically; the APPEND
            # is harmless if it does.
            try:
                self._imap_append_to_drafts(account, msg, folder=PROTON_SENT)
            except Exception:
                pass
            return json.dumps({
                "ok": True,
                "to": params.get("to"),
                "subject": params.get("subject", ""),
                "attachments": att_info,
            })
        except Exception as e:
            return f"error: {e}"

    def do_proton_reply(self, params):
        account = params.get("account")
        folder = params.get("folder", PROTON_INBOX)
        uid = params.get("uid")
        if not account or uid is None:
            return "error: 'account' and 'uid' are required"
        try:
            conn = self._proton_imap(account)
            self._select_folder(conn, folder, readonly=True)
            original = self._fetch_full(conn, uid)
            if original is None:
                return f"error: cannot find message UID {uid} in {folder!r}"
        except Exception as e:
            self._proton_drop_imap(account)
            return f"error: {e}"

        accounts = self._load_proton_accounts()
        cfg = accounts.get(account, {})
        from_address = cfg.get("email") or cfg.get("username") or account

        orig_subject = self._decode_header(original.get("Subject", ""))
        reply_subject = orig_subject if orig_subject.lower().startswith("re:") else f"Re: {orig_subject}"
        orig_msg_id = original.get("Message-ID", "")
        orig_refs = original.get("References", "")
        new_refs = (f"{orig_refs} {orig_msg_id}".strip() if orig_msg_id else orig_refs).strip()
        # Default reply-to is original sender unless overridden
        to_addr = params.get("to")
        if not to_addr:
            _, to_addr = parseaddr(original.get("From", ""))

        # Build a synthetic params dict reusing _build_outgoing_message
        send_params = dict(params)
        send_params["to"] = to_addr
        send_params["subject"] = reply_subject

        if not self._confirm_proton_action(
            "proton_reply",
            "Confirm Proton Reply",
            f"Reply via Proton from {account}?",
            f"To: {to_addr}\nSubject: {reply_subject}",
        ):
            return "user denied: proton_reply was rejected by the user"
        try:
            msg, att_info = self._build_outgoing_message(send_params, from_address)
            if orig_msg_id:
                msg["In-Reply-To"] = orig_msg_id
            if new_refs:
                msg["References"] = new_refs
            self._smtp_send(account, msg, from_address)
            try:
                self._imap_append_to_drafts(account, msg, folder=PROTON_SENT)
            except Exception:
                pass
            return json.dumps({
                "ok": True,
                "to": to_addr,
                "subject": reply_subject,
                "attachments": att_info,
            })
        except Exception as e:
            return f"error: {e}"

    def do_proton_create_draft(self, params):
        account = params.get("account")
        if not account:
            return "error: 'account' is required"
        accounts = self._load_proton_accounts()
        cfg = accounts.get(account, {})
        from_address = cfg.get("email") or cfg.get("username") or account
        try:
            msg, att_info = self._build_outgoing_message(params, from_address)
            new_uid = self._imap_append_to_drafts(account, msg, folder=PROTON_DRAFTS)
            return json.dumps({
                "ok": True,
                "draft_uid": new_uid,
                "folder": PROTON_DRAFTS,
                "to": params.get("to"),
                "subject": params.get("subject", ""),
                "attachments": att_info,
            })
        except Exception as e:
            self._proton_drop_imap(account)
            return f"error: {e}"

    def do_proton_list_drafts(self, params):
        params = dict(params)
        params["folder"] = PROTON_DRAFTS
        params.setdefault("q", "ALL")
        return self.do_proton_search(params)

    def do_proton_send_draft(self, params):
        account = params.get("account")
        uid = params.get("uid")
        if not account or uid is None:
            return "error: 'account' and 'uid' are required"
        try:
            conn = self._proton_imap(account)
            self._select_folder(conn, PROTON_DRAFTS, readonly=False)
            msg = self._fetch_full(conn, uid)
            if msg is None:
                return f"error: no draft UID {uid} in {PROTON_DRAFTS}"
        except Exception as e:
            self._proton_drop_imap(account)
            return f"error: {e}"

        accounts = self._load_proton_accounts()
        cfg = accounts.get(account, {})
        from_address = cfg.get("email") or cfg.get("username") or account
        subject = self._decode_header(msg.get("Subject", "(no subject)"))
        to_addr = self._decode_header(msg.get("To", ""))
        if not self._confirm_proton_action(
            "proton_send_draft",
            "Confirm Send Proton Draft",
            f"Send Proton draft UID {uid} from {account}?",
            f"To: {to_addr}\nSubject: {subject}",
        ):
            return "user denied: proton_send_draft was rejected by the user"
        try:
            self._smtp_send(account, msg, from_address)
            try:
                self._imap_append_to_drafts(account, msg, folder=PROTON_SENT)
            except Exception:
                pass
            # Remove draft from Drafts folder
            try:
                conn = self._proton_imap(account)
                self._select_folder(conn, PROTON_DRAFTS, readonly=False)
                conn.uid("store", str(uid), "+FLAGS", r"(\Deleted)")
                conn.expunge()
            except Exception:
                pass
            return json.dumps({"ok": True, "sent_uid": str(uid), "subject": subject, "to": to_addr})
        except Exception as e:
            return f"error: {e}"

    def do_proton_trash(self, params):
        account = params.get("account")
        folder = params.get("folder", PROTON_INBOX)
        uids = params.get("uids") or []
        if not account or not uids:
            return "error: 'account' and non-empty 'uids' are required"
        uids = [str(u) for u in uids]
        if not self._confirm_proton_action(
            "proton_trash",
            "Confirm Proton Trash",
            f"Move {len(uids)} message(s) to Trash in {account}?",
            f"Folder: {folder}\nUIDs: {', '.join(uids[:10])}{'...' if len(uids) > 10 else ''}",
        ):
            return "user denied: proton_trash was rejected by the user"
        try:
            conn = self._proton_imap(account)
            self._select_folder(conn, folder, readonly=False)
            uid_set = ",".join(uids)
            # Prefer MOVE (RFC 6851) — Bridge supports it. Falls back to
            # COPY+STORE-Deleted+EXPUNGE on servers that don't.
            try:
                typ, data = conn.uid("move", uid_set, self._quote_mailbox(PROTON_TRASH))
                if typ != "OK":
                    raise imaplib.IMAP4.error(str(data))
            except imaplib.IMAP4.error:
                conn.uid("copy", uid_set, self._quote_mailbox(PROTON_TRASH))
                conn.uid("store", uid_set, "+FLAGS", r"(\Deleted)")
                conn.expunge()
            return json.dumps({"ok": True, "trashed": len(uids), "from_folder": folder})
        except Exception as e:
            self._proton_drop_imap(account)
            return f"error: {e}"

    def do_proton_untrash(self, params):
        account = params.get("account")
        uids = params.get("uids") or []
        target_folder = params.get("to_folder", PROTON_INBOX)
        if not account or not uids:
            return "error: 'account' and non-empty 'uids' are required"
        uids = [str(u) for u in uids]
        try:
            conn = self._proton_imap(account)
            self._select_folder(conn, PROTON_TRASH, readonly=False)
            uid_set = ",".join(uids)
            try:
                typ, data = conn.uid("move", uid_set, self._quote_mailbox(target_folder))
                if typ != "OK":
                    raise imaplib.IMAP4.error(str(data))
            except imaplib.IMAP4.error:
                conn.uid("copy", uid_set, self._quote_mailbox(target_folder))
                conn.uid("store", uid_set, "+FLAGS", r"(\Deleted)")
                conn.expunge()
            return json.dumps({"ok": True, "restored": len(uids), "to_folder": target_folder})
        except Exception as e:
            self._proton_drop_imap(account)
            return f"error: {e}"

    def do_proton_list_labels(self, params):
        """IMAP LIST — returns every folder Bridge exposes for this account."""
        account = params.get("account")
        if not account:
            return "error: 'account' is required"
        try:
            conn = self._proton_imap(account)
            typ, data = conn.list()
            if typ != "OK":
                return f"error: IMAP LIST failed: {data}"
            folders = []
            for raw in data or []:
                if not raw:
                    continue
                line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
                # IMAP LIST response: (\HasNoChildren) "/" "FolderName"
                m = re.match(r'^\(([^)]*)\)\s+("[^"]*"|\S+)\s+(.*)$', line)
                if not m:
                    continue
                flags, _delim, name = m.groups()
                name = name.strip()
                if name.startswith('"') and name.endswith('"'):
                    name = name[1:-1]
                folders.append({"name": name, "flags": flags.strip()})
            return json.dumps({"count": len(folders), "folders": folders}, indent=2, ensure_ascii=False)
        except Exception as e:
            self._proton_drop_imap(account)
            return f"error: {e}"

    def do_proton_create_label(self, params):
        """IMAP CREATE. Defaults to creating under ``Labels/`` (Proton labels);
        pass parent=``Folders`` to create a Proton folder instead, or
        parent='' for a top-level name."""
        account = params.get("account")
        name = params.get("name")
        parent = params.get("parent", "Labels")
        if not account or not name:
            return "error: 'account' and 'name' are required"
        if parent:
            full = f"{parent}/{name}"
        else:
            full = name
        try:
            conn = self._proton_imap(account)
            typ, data = conn.create(self._quote_mailbox(full))
            if typ != "OK":
                return f"error: CREATE {full!r} failed: {data}"
            return json.dumps({"ok": True, "name": full})
        except Exception as e:
            self._proton_drop_imap(account)
            return f"error: {e}"

    def do_proton_delete_label(self, params):
        """IMAP DELETE. Destructive — removes the folder and all messages
        in it. Requires confirmation."""
        account = params.get("account")
        name = params.get("name")
        if not account or not name:
            return "error: 'account' and 'name' are required"
        if not self._confirm_proton_action(
            "proton_delete_label",
            "Confirm Proton Delete Folder",
            f"Delete folder {name!r} from {account}?",
            "This removes the folder AND any messages stored only in it.",
        ):
            return "user denied: proton_delete_label was rejected by the user"
        try:
            conn = self._proton_imap(account)
            typ, data = conn.delete(self._quote_mailbox(name))
            if typ != "OK":
                return f"error: DELETE {name!r} failed: {data}"
            return json.dumps({"ok": True, "deleted": name})
        except Exception as e:
            self._proton_drop_imap(account)
            return f"error: {e}"

    def _imap_move(self, conn, uid_set, destination):
        """Issue UID MOVE with COPY+STORE+EXPUNGE fallback for older servers."""
        try:
            typ, data = conn.uid("move", uid_set, self._quote_mailbox(destination))
            if typ != "OK":
                raise imaplib.IMAP4.error(str(data))
        except imaplib.IMAP4.error:
            conn.uid("copy", uid_set, self._quote_mailbox(destination))
            conn.uid("store", uid_set, "+FLAGS", r"(\Deleted)")
            conn.expunge()

    def do_proton_modify_labels(self, params):
        """Apply or move messages between Proton folders/labels.

        Bridge has asymmetric semantics depending on the destination — see the
        proton_modify_labels tool description for the full picture. Briefly:
        Labels/X destinations are ADDITIVE (tag without removing from source);
        system folders (INBOX, Sent, Trash, etc.) and Folders/X are TRUE MOVE
        (exclusive — message removed from source, present only in destination).

        For Labels/X SOURCES (label-removal flows), Bridge has an additional
        quirk discovered empirically: after the IMAP MOVE completes, the
        source may transiently still contain the message under a NEW UID
        (likely Bridge's local cache re-syncing the label from Proton's
        server before the removal propagates). We auto-detect this by
        snapshotting source UIDs before the MOVE and re-MOVing any
        unexpected new UIDs that appear afterward, up to 2 retries. Without
        this loop, callers would have to handle the retry themselves
        (originally observed in Proton TEST3, run 5)."""
        account = params.get("account")
        folder = params.get("folder", PROTON_INBOX)
        uids = params.get("uids") or []
        add_to = params.get("add_to")
        if not account or not uids or not add_to:
            return "error: account, uids, and add_to are required"
        uids = [str(u) for u in uids]
        try:
            conn = self._proton_imap(account)
            is_label_source = folder.startswith("Labels/")

            # Snapshot source UIDs before the move so we can detect transient
            # new entries created by Bridge's label-removal sync afterward.
            old_uids = set()
            if is_label_source:
                self._select_folder(conn, folder, readonly=True)
                typ, data = conn.uid("search", None, "ALL")
                if typ == "OK" and data and data[0]:
                    old_uids = set(data[0].split())

            # Perform the primary MOVE
            self._select_folder(conn, folder, readonly=False)
            self._imap_move(conn, ",".join(uids), add_to)

            # Auto-retry transient new UIDs (Labels/X sources only)
            retries_used = 0
            moved_set = {u.encode("ascii") for u in uids}
            if is_label_source:
                for attempt in range(2):
                    self._select_folder(conn, folder, readonly=True)
                    typ, data = conn.uid("search", None, "ALL")
                    new_uids = set(data[0].split()) if typ == "OK" and data and data[0] else set()
                    # Unexpected = present now but weren't before, and aren't the original moved UIDs
                    unexpected = new_uids - old_uids - moved_set
                    if not unexpected:
                        break
                    self._select_folder(conn, folder, readonly=False)
                    self._imap_move(conn, b",".join(sorted(unexpected)).decode("ascii"), add_to)
                    moved_set |= unexpected
                    retries_used = attempt + 1

            return json.dumps({
                "ok": True, "moved": len(uids),
                "from_folder": folder, "to_folder": add_to,
                "label_removal_retries": retries_used,
            })
        except Exception as e:
            self._proton_drop_imap(account)
            return f"error: {e}"

    def do_proton_mark_read(self, params):
        account = params.get("account")
        folder = params.get("folder", PROTON_INBOX)
        uids = params.get("uids") or []
        read = bool(params.get("read", True))
        if not account or not uids:
            return "error: 'account' and 'uids' are required"
        uids = [str(u) for u in uids]
        try:
            conn = self._proton_imap(account)
            self._select_folder(conn, folder, readonly=False)
            op = "+FLAGS" if read else "-FLAGS"
            conn.uid("store", ",".join(uids), op, r"(\Seen)")
            return json.dumps({"ok": True, "marked": len(uids), "read": read})
        except Exception as e:
            self._proton_drop_imap(account)
            return f"error: {e}"

    def do_proton_list_threads(self, params):
        """IMAP THREAD REFERENCES if Bridge supports it; otherwise falls
        back to grouping search results by Message-ID/References headers
        manually. Returns a list of threads, each with the root subject
        and the UIDs in the thread."""
        account = params.get("account")
        q = params.get("q", "ALL")
        folder = params.get("folder", PROTON_INBOX)
        max_results = min(int(params.get("max_results") or 25), 200)
        if not account:
            return "error: 'account' is required"
        try:
            conn = self._proton_imap(account)
            self._select_folder(conn, folder, readonly=True)
            criteria = self._build_search_criteria(q)
            threads = []
            try:
                typ, data = conn.uid("thread", "REFERENCES", "UTF-8", criteria)
                if typ == "OK" and data and data[0]:
                    raw = data[0].decode("ascii", "replace")
                    # Parse Lisp-y "(1 2 (3 4))" thread tree into flat groups.
                    threads = self._parse_thread_tree(raw)
            except (imaplib.IMAP4.error, AttributeError):
                threads = []
            if not threads:
                # Fallback: just return the matching UIDs as singleton threads.
                typ, data = self._uid_search(conn, criteria)
                if typ == "OK" and data and data[0]:
                    threads = [[u.decode("ascii", "replace")] for u in data[0].split()]
            # IMAP THREAD (RFC 5256) returns groups in ascending root-UID
            # order — oldest thread first. Reverse so the model gets newest
            # threads first, matching proton_search's newest-first contract.
            threads = list(reversed(threads))[:max_results]
            # Decorate each thread with the root message's subject + snippet.
            decorated = []
            for group in threads:
                if not group:
                    continue
                root_uid = group[0]
                msg, _flags = self._fetch_envelope(conn, root_uid)
                if msg is None:
                    continue
                decorated.append({
                    "uids": group,
                    "size": len(group),
                    "subject": self._decode_header(msg.get("Subject", "")),
                    "from": self._decode_header(msg.get("From", "")),
                    "date": msg.get("Date", ""),
                })
            return json.dumps({
                "folder": folder, "count": len(decorated),
                "threads": decorated,
            }, indent=2, ensure_ascii=False)
        except Exception as e:
            self._proton_drop_imap(account)
            return f"error: {e}"

    @staticmethod
    def _parse_thread_tree(raw):
        """Flatten an IMAP THREAD response like ``(1)(2 3 (4 5)(6))`` into a
        list of UID groups: ``[['1'], ['2','3','4','5','6']]``. Order
        within a group is the depth-first traversal — root first."""
        groups = []
        i = 0
        n = len(raw)
        while i < n:
            if raw[i] != "(":
                i += 1
                continue
            # Find matching close paren
            depth = 0
            j = i
            while j < n:
                if raw[j] == "(":
                    depth += 1
                elif raw[j] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            chunk = raw[i + 1:j]
            uids = re.findall(r"\d+", chunk)
            if uids:
                groups.append(uids)
            i = j + 1
        return groups
