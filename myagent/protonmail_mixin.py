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

import email
import imaplib
import json
import mimetypes
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, getaddresses, make_msgid, parseaddr
from myagent.mail_common import confirm_action

from myagent.helpers import extract_text_from_html, normalize_save_path

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
        # Per-account folder-role resolution cache. Populated lazily on first
        # call to _proton_folder(). Keyed by account name → dict mapping
        # role (sent/drafts/trash/archive/spam/all_mail) → actual folder
        # path on that server. Different IMAP servers nest these differently
        # (Bridge: top-level "Sent"; dovecot: "INBOX.Sent").
        self._proton_folder_cache = {}

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
            with open(PROTON_ACCOUNTS_FILE, encoding="utf-8") as f:
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

    _LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0.0.0.0"})

    @staticmethod
    def _is_loopback_host(host):
        """True for this machine's own addresses (Proton Bridge's case)."""
        return (host or "").strip().lower() in ProtonMailMixin._LOOPBACK_HOSTS

    @staticmethod
    def _build_ssl_context(account_cfg):
        """Build an ssl.SSLContext for an IMAP/SMTP account.

        Priority order:

        1. ``ca_cert_path`` set + file exists → pin to that cert. Use case:
           Proton Bridge with its self-signed cert exported. Strongest
           verification — the cert chain is checked against exactly the cert
           the user exported from Bridge's UI.

        2. ``verify_tls: false`` explicitly set → CERT_NONE escape hatch.
           Use case: dev environments, self-signed servers without an
           exported cert, opt-in unverified TLS.

        3. Loopback host (``127.0.0.1`` / ``localhost`` / ``::1``) →
           CERT_NONE default. Use case: Proton Bridge without ca_cert_path —
           the historical Bridge default. Acceptable because MITM-ing
           localhost requires code execution on the user's machine.

        4. Public host (anything not loopback) → ``ssl.create_default_context()``
           with the system trust store + hostname verification. Use case:
           any real IMAP/SMTP server on the public internet (WebCentral,
           Fastmail, custom-domain hosting, etc.). Auto-validates against
           Let's Encrypt, DigiCert, and every CA in the OS trust store.

        This auto-detection means existing Bridge configs keep working
        unchanged (loopback → CERT_NONE) while public-server connections
        get proper TLS verification without per-account configuration.
        """
        if not account_cfg:
            account_cfg = {}

        # 1. Pinned cert beats everything else
        ca_cert = account_cfg.get("ca_cert_path")
        if ca_cert and os.path.isfile(ca_cert):
            return ssl.create_default_context(cafile=ca_cert)

        # 2. Explicit opt-out for unverified TLS
        if account_cfg.get("verify_tls") is False:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx

        # 3. Loopback detection (Bridge case) — preserve existing behaviour
        if (ProtonMailMixin._is_loopback_host(account_cfg.get("imap_host"))
                or ProtonMailMixin._is_loopback_host(account_cfg.get("smtp_host"))):
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx

        # 4. Public host → system trust store with hostname verification
        return ssl.create_default_context()

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
            except imaplib.IMAP4.error as e:
                # Some Bridge versions return a quirky CAPABILITY after
                # STARTTLS: Bridge on THIS machine gets the plain login, which
                # never leaves it. Any other host would receive the app
                # password in clear — an attacker who strips STARTTLS reads
                # it — so there the login is refused instead.
                if not self._is_loopback_host(host):
                    try:
                        conn.shutdown()
                    except Exception:
                        pass
                    raise RuntimeError(
                        f"STARTTLS failed for IMAP account '{account}' ({host}): {e} — "
                        f"refusing to send its password unencrypted. Set imap_ssl: true "
                        f"in {PROTON_ACCOUNTS_FILE} if the server speaks implicit TLS."
                    ) from e
        try:
            conn.login(username, password)
        except imaplib.IMAP4.error as e:
            raise RuntimeError(
                f"Proton IMAP login failed for '{account}': {e}. "
                f"Check the app_password in {PROTON_ACCOUNTS_FILE} and confirm "
                f"Proton Bridge is running."
            ) from e
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
            except smtplib.SMTPException as e:
                # Plain login only on this machine (Bridge) — see _proton_imap.
                if not self._is_loopback_host(host):
                    try:
                        client.close()
                    except Exception:
                        pass
                    raise RuntimeError(
                        f"STARTTLS failed for SMTP account '{account}' ({host}): {e} — "
                        f"refusing to send its password unencrypted. Set smtp_ssl: true "
                        f"in {PROTON_ACCOUNTS_FILE} if the server speaks implicit TLS."
                    ) from e
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
            ) from e
        return client

    def _proton_close_connections(self):
        """LOGOUT all open IMAP connections. Best-effort; called on app close."""
        for conn in list(self._proton_imap_conns.values()):
            try:
                conn.logout()
            except Exception:
                pass
        self._proton_imap_conns.clear()

    # ── Special-use folder discovery ────────────────────────────────────────

    # Default folder names per role — match Proton Bridge convention. Used
    # as fallbacks when neither accounts.json override nor IMAP SPECIAL-USE
    # discovery yields a value. Overridden per-role at runtime via:
    #   1. accounts.json[account]["folders"][role] — explicit override
    #   2. IMAP LIST response flags (\Sent, \Drafts, \Trash, \Junk, \Archive,
    #      \All) — RFC 6154 auto-discovery, cached per-account on first use
    #   3. These constants — fallback for servers that publish neither
    _FOLDER_ROLE_DEFAULTS = {
        "inbox": PROTON_INBOX,
        "sent": PROTON_SENT,
        "drafts": PROTON_DRAFTS,
        "trash": PROTON_TRASH,
        "archive": PROTON_ARCHIVE,
        "spam": PROTON_SPAM,
        "all_mail": PROTON_ALL_MAIL,
    }

    # IMAP SPECIAL-USE flag (RFC 6154) → role mapping. \Junk and \Spam both
    # map to "spam" because dovecot uses \Junk, some other servers use \Spam.
    _SPECIAL_USE_FLAG_TO_ROLE = {
        "\\Sent": "sent",
        "\\Drafts": "drafts",
        "\\Trash": "trash",
        "\\Archive": "archive",
        "\\Junk": "spam",
        "\\Spam": "spam",
        "\\All": "all_mail",
        "\\Inbox": "inbox",
    }

    def _proton_discover_folders(self, account):
        """Run IMAP LIST against the account and parse RFC 6154 SPECIAL-USE
        flags to discover where the server keeps each special-purpose folder
        (Sent, Drafts, Trash, etc.).

        Returns a dict mapping role names to actual folder paths, with the
        default constants for any role the server didn't tag — or None when
        the LIST itself failed (network, a stale connection), so that
        _proton_folder serves defaults for this call only and retries: a
        failure cached as "the server tags nothing" sent a dovecot account's
        trash to "Trash" instead of "INBOX.Trash" for the whole session.

        Discovered example for a dovecot server (WebCentral): {
            "sent": "INBOX.Sent", "drafts": "INBOX.Drafts",
            "trash": "INBOX.Trash", "spam": "INBOX.Junk", ...
        }

        Discovered example for Proton Bridge: {
            "sent": "Sent", "drafts": "Drafts", "trash": "Trash", ...
        }
        """
        discovered = dict(self._FOLDER_ROLE_DEFAULTS)
        try:
            conn = self._proton_imap(account)
            typ, data = conn.list()
            if typ != "OK":
                return None
            if not data:
                return discovered
            for raw in data:
                if not raw:
                    continue
                line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
                # IMAP LIST response: (\flag1 \flag2 ...) "/" "FolderName"
                m = re.match(r'^\(([^)]*)\)\s+("[^"]*"|\S+)\s+(.*)$', line)
                if not m:
                    continue
                flags_str, _delim, name = m.groups()
                name = name.strip()
                if name.startswith('"') and name.endswith('"'):
                    name = name[1:-1]
                for flag in flags_str.split():
                    role = self._SPECIAL_USE_FLAG_TO_ROLE.get(flag)
                    if role:
                        discovered[role] = name
        except Exception:
            # Non-fatal, but NOT an answer: drop the connection it failed on
            # (a stale socket would fail the next call too) and report None.
            self._proton_drop_imap(account)
            return None
        return discovered

    def _proton_folder(self, account, role):
        """Resolve the actual folder name for a special-use role on an account.

        Priority order:

        1. **Explicit override** in ``accounts.json`` —
           ``accounts[account]["folders"][role]``. Useful when auto-discovery
           gets it wrong or for servers that don't advertise SPECIAL-USE.

        2. **Auto-detected via IMAP SPECIAL-USE** — cached per-account on
           first call. RFC 6154 flag → role mapping handles \\Sent, \\Drafts,
           \\Trash, \\Archive, \\Junk/\\Spam, \\All.

        3. **Default constants** — match Proton Bridge convention
           (top-level "Sent", "Drafts", "Trash", etc.).

        Role names: 'inbox', 'sent', 'drafts', 'trash', 'archive', 'spam',
        'all_mail'. Unknown roles return PROTON_INBOX as a safe fallback.
        """
        role = (role or "").lower()

        # 1. Explicit override
        accounts = self._load_proton_accounts()
        override = accounts.get(account, {}).get("folders", {}).get(role)
        if override:
            return override

        # 2. Cached auto-discovery (one SUCCESSFUL IMAP LIST per account; a
        # failed one serves the defaults for this call and is retried)
        if account not in self._proton_folder_cache:
            found = self._proton_discover_folders(account)
            if found is None:
                return self._FOLDER_ROLE_DEFAULTS.get(role, PROTON_INBOX)
            self._proton_folder_cache[account] = found

        return self._proton_folder_cache[account].get(
            role, self._FOLDER_ROLE_DEFAULTS.get(role, PROTON_INBOX)
        )

    # ── Safety: modal confirmation for destructive ops ──────────────────────

    def _confirm_proton_action(self, tool_name, title, summary, detail):
        """Same pattern as ``_confirm_gmail_action``. Returns True if the
        user clicks Yes; honours per-instruction bypass via
        ``_disabled_confirm_patterns``."""
        return confirm_action(self, "Proton", tool_name, title, summary, detail)

    # ── Helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _attach_proton_files(msg, attachments):
        """Attach files to an EmailMessage. Returns (ok, summary_or_error).
        Matches Gmail's ``_attach_files`` semantics."""
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
                    parts.append(ProtonMailMixin._decode_header_bytes(text, charset))
                else:
                    parts.append(text)
            return "".join(parts)
        except Exception:
            return str(value)

    @staticmethod
    def _decode_header_bytes(raw, charset):
        """One decoded header chunk's bytes as text. A raw 8-bit header
        (RFC 6532 UTF-8 with no encoded-word) arrives labelled 'unknown-8bit'
        — a charset no codec has, so the decode raised and the header came
        back as str(Header), every non-ASCII letter a U+FFFD. It, and any
        other charset Python doesn't know, is read as UTF-8, else Latin-1."""
        if charset and charset.lower() not in ("unknown-8bit", "x-unknown"):
            try:
                return raw.decode(charset, errors="replace")
            except LookupError:
                pass
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("latin-1")

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
        """Compact summary dict — analogous to Gmail's ``_format_message_summary``
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
        """Fetch the RFC822 headers of a single UID (PEEK: nothing is marked
        read) -> email.Message, or None on failure. (It also fetched FLAGS,
        which both callers discarded — and which its parser, looking only
        outside the literal's tuple, would rarely have found.)"""
        typ, data = conn.uid("fetch", str(uid), "(BODY.PEEK[HEADER])")
        if typ != "OK" or not data or not data[0]:
            return None
        raw = b""
        for item in data:
            if isinstance(item, tuple) and len(item) >= 2:
                raw = item[1] or b""
        try:
            return email.message_from_bytes(raw)
        except Exception:
            return None

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
                msg = self._fetch_envelope(conn, uid)
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
            # Choose the canonical "text" body: prefer text/plain when present,
            # otherwise convert HTML structurally via HTMLTextExtractor (which
            # drops <script>/<style> CONTENT — not just tags — adds newlines
            # at block-level elements, and decodes HTML entities). The naive
            # re.sub regex used previously would leave CSS, JS, and entities
            # like &amp;/&nbsp; in the output, swamping marketing emails with
            # boilerplate the model had to ignore.
            if text_body:
                clean_text = text_body
            elif html_body:
                clean_text = extract_text_from_html(html_body)
            else:
                clean_text = ""
            result = {
                "account": account,
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
                # Snippet = first 200 chars of cleaned text body, whitespace
                # collapsed. Parallels Gmail API's server-side snippet so
                # callers have a quick preview without rendering full body.
                "snippet": re.sub(r"\s+", " ", clean_text).strip()[:200],
                "attachments": attachments,
            }
            if fmt in ("text", "both"):
                result["body"] = clean_text[:MAX_BODY_CHARS]
                result["body_truncated"] = len(clean_text) > MAX_BODY_CHARS
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
        save_to, path_note = normalize_save_path(save_to)
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
                    **({"note": path_note} if path_note else {}),
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
        Bcc header is stripped before send (server-side delivery only).
        Returns {address: "code reason"} for the recipients the server
        REFUSED — {} when all were accepted (smtplib raises only when EVERY
        recipient is refused, so a partial refusal went unreported)."""
        # getaddresses, not a split on ",": a quoted display name may hold a
        # comma — '"Smith, John" <john@x.com>' split into 'Smith' and 'John',
        # and john never became an envelope recipient although the To:
        # header showed him.
        to_addrs = [addr for _name, addr in getaddresses(
            [str(value) for hdr in ("To", "Cc", "Bcc") for value in msg.get_all(hdr, [])])
            if addr]
        # Don't leak Bcc to recipients
        if "Bcc" in msg:
            del msg["Bcc"]
        client = self._proton_smtp(account)
        try:
            refused = client.send_message(msg, from_addr=from_address, to_addrs=to_addrs)
        finally:
            try:
                client.quit()
            except Exception:
                pass
        return {addr: f"{code} {reason.decode('utf-8', 'replace') if isinstance(reason, bytes) else reason}"
                for addr, (code, reason) in (refused or {}).items()}

    def _imap_append_to_drafts(self, account, msg, folder=None):
        """APPEND a raw EmailMessage to a folder (used by create_draft and
        send-to-Sent flows). The ``folder`` parameter defaults to the
        account's resolved drafts folder (per ``_proton_folder``) but
        callers typically pass an explicit role-resolved folder name. The
        ``\\Draft`` IMAP flag is set automatically when the destination
        IS the account's drafts folder. Returns the new UID if the server
        reports APPENDUID, else empty string."""
        if folder is None:
            folder = self._proton_folder(account, "drafts")
        conn = self._proton_imap(account)
        raw = msg.as_bytes()
        is_drafts = (folder == self._proton_folder(account, "drafts"))
        typ, data = conn.append(
            self._quote_mailbox(folder),
            r"(\Draft)" if is_drafts else None,
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

    @staticmethod
    def _smtp_autosaves_to_sent(cfg):
        """True if this account's SMTP server already stores a server-side
        copy of every outgoing message in Sent — in which case the client
        must NOT also APPEND one, or Sent ends up with two copies.

        Proton (via Bridge) ALWAYS saves sent mail to Sent server-side, and
        it can't be turned off, so for Bridge accounts the explicit APPEND is
        a pure duplicate. Generic IMAP/SMTP servers (dovecot/cPanel, Fastmail,
        custom-domain hosting) do NOT auto-save on SMTP send, so there the
        client APPEND is exactly what populates Sent.

        Resolution order:
          1. Explicit per-account override ``smtp_saves_to_sent: true|false``.
          2. Auto-detect Proton Bridge by a loopback SMTP host (matches the
             loopback set used for TLS in _build_ssl_context).
          3. Default False — assume a generic server that needs the APPEND.
        """
        if not cfg:
            return False
        if "smtp_saves_to_sent" in cfg:
            return bool(cfg["smtp_saves_to_sent"])
        host = (cfg.get("smtp_host") or "").strip().lower()
        return host in {"127.0.0.1", "localhost", "::1", "0.0.0.0"}

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
            refused = self._smtp_send(account, msg, from_address)
            # APPEND a copy to Sent only when SMTP didn't already store one.
            # Proton Bridge always saves sent mail server-side, so APPENDing
            # there duplicates it; generic IMAP needs the APPEND to populate
            # Sent at all. See _smtp_autosaves_to_sent.
            if not self._smtp_autosaves_to_sent(cfg):
                try:
                    self._imap_append_to_drafts(account, msg, folder=self._proton_folder(account, "sent"))
                except Exception:
                    pass
            return json.dumps({
                "ok": True,
                **({"refused_recipients": refused} if refused else {}),
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
            refused = self._smtp_send(account, msg, from_address)
            # Skip the Sent APPEND when SMTP auto-saves (Proton Bridge) to
            # avoid a duplicate; see _smtp_autosaves_to_sent.
            if not self._smtp_autosaves_to_sent(cfg):
                try:
                    self._imap_append_to_drafts(account, msg, folder=self._proton_folder(account, "sent"))
                except Exception:
                    pass
            return json.dumps({
                "ok": True,
                **({"refused_recipients": refused} if refused else {}),
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
            drafts_folder = self._proton_folder(account, "drafts")
            new_uid = self._imap_append_to_drafts(account, msg, folder=drafts_folder)
            return json.dumps({
                "ok": True,
                "draft_uid": new_uid,
                "folder": drafts_folder,
                "to": params.get("to"),
                "subject": params.get("subject", ""),
                "attachments": att_info,
            })
        except Exception as e:
            self._proton_drop_imap(account)
            return f"error: {e}"

    def do_proton_list_drafts(self, params):
        account = params.get("account")
        params = dict(params)
        params["folder"] = self._proton_folder(account, "drafts") if account else PROTON_DRAFTS
        params.setdefault("q", "ALL")
        return self.do_proton_search(params)

    def do_proton_send_draft(self, params):
        account = params.get("account")
        uid = params.get("uid")
        if not account or uid is None:
            return "error: 'account' and 'uid' are required"
        drafts_folder = self._proton_folder(account, "drafts")
        try:
            conn = self._proton_imap(account)
            self._select_folder(conn, drafts_folder, readonly=False)
            msg = self._fetch_full(conn, uid)
            if msg is None:
                return f"error: no draft UID {uid} in {drafts_folder}"
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
            refused = self._smtp_send(account, msg, from_address)
            # Skip the Sent APPEND when SMTP auto-saves (Proton Bridge) to
            # avoid a duplicate; see _smtp_autosaves_to_sent.
            if not self._smtp_autosaves_to_sent(cfg):
                try:
                    self._imap_append_to_drafts(account, msg, folder=self._proton_folder(account, "sent"))
                except Exception:
                    pass
            # Remove draft from Drafts folder
            try:
                conn = self._proton_imap(account)
                self._select_folder(conn, drafts_folder, readonly=False)
                conn.uid("store", str(uid), "+FLAGS", r"(\Deleted)")
                self._imap_uid_expunge(conn, str(uid))   # this draft only
            except Exception:
                pass
            return json.dumps({"ok": True, "sent_uid": str(uid), "subject": subject, "to": to_addr,
                               **({"refused_recipients": refused} if refused else {})})
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
            trash_folder = self._proton_folder(account, "trash")
            # MOVE (RFC 6851) — Bridge supports it; _imap_move falls back to
            # a CHECKED COPY + UID EXPUNGE only on servers that lack it.
            self._imap_move(conn, uid_set, trash_folder)
            return json.dumps({"ok": True, "trashed": len(uids), "from_folder": folder, "to_folder": trash_folder})
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
            self._select_folder(conn, self._proton_folder(account, "trash"), readonly=False)
            uid_set = ",".join(uids)
            self._imap_move(conn, uid_set, target_folder)
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
        full = f"{parent}/{name}" if parent else name
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

    @staticmethod
    def _imap_reply_text(data):
        """An IMAP response's data list as readable text (for error messages)."""
        return " ".join(d.decode("utf-8", "replace") if isinstance(d, bytes) else str(d)
                        for d in (data or []) if d is not None) or "(no detail)"

    def _imap_move(self, conn, uid_set, destination):
        """UID MOVE (RFC 6851) of `uid_set` from the selected folder.

        Only a server that does not KNOW MOVE — it answers BAD, which imaplib
        raises — gets the COPY + STORE \\Deleted + UID EXPUNGE fallback, and
        only once the COPY succeeded. A MOVE the server REFUSES (NO: e.g. a
        destination that does not exist, [TRYCREATE]) raises. The fallback
        used to run for a refusal too, ignore its equally refused COPY and
        expunge the messages: a mistyped folder permanently deleted them
        while the tool reported ok."""
        dest = self._quote_mailbox(destination)
        try:
            typ, data = conn.uid("move", uid_set, dest)
        except imaplib.IMAP4.abort:
            raise                              # the connection died: nothing to fall back on
        except imaplib.IMAP4.error:
            typ, data = None, None             # BAD: this server has no MOVE
        if typ == "OK":
            return
        if typ is not None:
            raise imaplib.IMAP4.error(
                f"MOVE to {destination!r} was refused: {self._imap_reply_text(data)}")
        typ, data = conn.uid("copy", uid_set, dest)
        if typ != "OK":
            raise imaplib.IMAP4.error(
                f"COPY to {destination!r} was refused: {self._imap_reply_text(data)}")
        conn.uid("store", uid_set, "+FLAGS", r"(\Deleted)")
        self._imap_uid_expunge(conn, uid_set)

    def _imap_uid_expunge(self, conn, uid_set):
        """Expunge exactly `uid_set` — UID EXPUNGE (RFC 4315 UIDPLUS). A plain
        EXPUNGE also destroys every message another client (Thunderbird's
        mark-as-deleted mode, say) has flagged \\Deleted in the folder. Only a
        server that rejects UID EXPUNGE outright (BAD: no UIDPLUS) gets the
        plain form; a refusal (NO) leaves the messages flagged \\Deleted for
        a later expunge rather than widening it."""
        try:
            conn.uid("expunge", uid_set)
        except imaplib.IMAP4.abort:
            raise
        except imaplib.IMAP4.error:
            conn.expunge()

    @staticmethod
    def _proton_message_ids(conn, uid_set):
        """{uid (bytes): Message-ID, lower-cased} for `uid_set` in the selected
        folder; a message without one is left out. BODY.PEEK marks nothing
        read."""
        found = {}
        if not uid_set:
            return found
        typ, data = conn.uid("fetch", uid_set, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
        if typ != "OK":
            return found
        for item in data or []:
            if not isinstance(item, tuple) or len(item) < 2:
                continue
            uid = re.search(rb"UID (\d+)", item[0] or b"")
            mid = email.message_from_bytes(item[1] or b"").get("Message-ID")
            if uid and mid:
                found[uid.group(1)] = str(mid).strip().lower()
        return found

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
        # A move to the account's Trash or Spam hides the mail just as
        # proton_trash does, so it asks just as proton_trash does — under
        # that tool's bypass key, so an instruction that bypasses trash
        # confirmation bypasses this too. (It was an unconfirmed side door
        # the tool description even advertised.)
        try:
            bins = {self._proton_folder(account, "trash").lower(),
                    self._proton_folder(account, "spam").lower()}
        except Exception:
            bins = {PROTON_TRASH.lower(), PROTON_SPAM.lower()}
        if str(add_to).lower() in bins and not self._confirm_proton_action(
                "proton_trash",
                "Confirm Proton Trash",
                f"Move {len(uids)} message(s) to {add_to} in {account}?",
                f"Folder: {folder}\nUIDs: {', '.join(uids[:10])}{'...' if len(uids) > 10 else ''}",
        ):
            return "user denied: proton_modify_labels to trash/spam was rejected by the user"
        try:
            conn = self._proton_imap(account)
            is_label_source = folder.startswith("Labels/")

            # Snapshot source UIDs before the move so we can detect transient
            # new entries created by Bridge's label-removal sync afterward —
            # and the moved messages' Message-IDs, which tell such a stray
            # (the same message back under a new UID) from a message
            # genuinely labelled in the meantime.
            old_uids = set()
            moved_ids = set()
            if is_label_source:
                self._select_folder(conn, folder, readonly=True)
                typ, data = conn.uid("search", None, "ALL")
                if typ == "OK" and data and data[0]:
                    old_uids = set(data[0].split())
                moved_ids = set(self._proton_message_ids(conn, ",".join(uids)).values())

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
                    if unexpected and moved_ids:
                        # Only the SAME messages back under new UIDs: another
                        # message labelled in this window (a filter, another
                        # client) keeps its label.
                        found = self._proton_message_ids(
                            conn, b",".join(sorted(unexpected)).decode("ascii"))
                        unexpected = {u for u in unexpected if found.get(u) in moved_ids}
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
                msg = self._fetch_envelope(conn, root_uid)
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
