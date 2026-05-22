"""Gmail integration via the official google-api-python-client (Option A in
the design discussion — native MyAgent tools, NOT MCP).

Architecture notes:

* All tools share one OAuth flow per account. Tokens live under
  ``~/.config/myagent-google/{account}_token.json``; the OAuth client JSON
  downloaded from Google Cloud Console lives at the shared path
  ``~/.config/myagent-google/oauth_client.json``. Accounts are listed in
  ``~/.config/myagent-google/accounts.json`` so the tool-schema ``account``
  enum can be patched at runtime in ``_get_tools`` rather than hardcoded
  in source.

* Scopes default to ``gmail.modify`` — covers read, send, draft, label,
  trash. **Does NOT cover permanent delete**: that requires the broader
  ``mail.google.com`` scope and is intentionally excluded so the agent
  can only soft-delete (Trash is recoverable from Gmail's UI). If a future
  need arises, add a separate ``do_gmail_delete_forever`` tool and request
  the broader scope at OAuth time.

* Destructive operations (``gmail_send``, ``gmail_send_draft``, ``gmail_trash``)
  pop a modal Tk confirmation dialog showing what's about to happen. Click
  Yes to proceed, No to cancel — the tool returns a "user denied" string
  so the agent can decide what to do next. Headless runs see the same
  dialog as a free-floating window (same pattern as PS Safety / user_prompt).

* When ``google`` is not installed (``_HAS_GOOGLE = False``), every method
  here is a graceful no-op and the Google checkbox in the editor is disabled.
  Same opt-in degradation pattern as MCP and Ollama.
"""

import base64
import json
import mimetypes
import os
import re
import tkinter as tk
from email.message import EmailMessage
from tkinter import messagebox

# Gmail API ceiling is 25 MB per message TOTAL (body + headers + attachments,
# all after base64 encoding which adds ~33% overhead). Capping raw attachment
# bytes at 20 MB combined leaves a safety margin for the base64 + headers and
# fails locally with a clear message instead of a 413 from Google.
MAX_ATTACHMENT_BYTES_TOTAL = 20 * 1024 * 1024

_HAS_GOOGLE = True
try:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
except Exception:
    _HAS_GOOGLE = False


GOOGLE_CONFIG_DIR = os.path.expanduser("~/.config/myagent-google")
GOOGLE_ACCOUNTS_FILE = os.path.join(GOOGLE_CONFIG_DIR, "accounts.json")
GOOGLE_OAUTH_CLIENT_FILE = os.path.join(GOOGLE_CONFIG_DIR, "oauth_client.json")

# `gmail.modify` covers read, send, draft, label, trash — but NOT
# permanent delete. That's deliberate; trash is recoverable from the
# Gmail UI, permanent delete is not.
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


class GmailMixin:
    """Gmail tool methods backed by the official Google API Python client."""

    # ── State init ──────────────────────────────────────────────────────────

    def _google_init_state(self):
        """Initialize Gmail-related instance attributes. Call from App.__init__."""
        self._gmail_services = {}   # account_name -> googleapiclient service
        self._google_accounts_cache = None  # lazy

    # ── Account discovery ───────────────────────────────────────────────────

    def _load_google_accounts(self):
        """Read accounts.json. Returns a dict keyed by account name with
        optional metadata (e.g. ``email``). Empty dict if file missing or
        malformed — calling tools then fail with a clear message at use time
        rather than at startup, so a user who has Google off doesn't see noise."""
        if not _HAS_GOOGLE:
            return {}
        if not os.path.exists(GOOGLE_ACCOUNTS_FILE):
            return {}
        try:
            with open(GOOGLE_ACCOUNTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            accounts = data.get("accounts", {})
            return accounts if isinstance(accounts, dict) else {}
        except Exception:
            return {}

    def _get_google_account_names(self):
        """List of configured account names (sorted). Used to populate the
        ``account`` enum on every Gmail tool schema at runtime."""
        if self._google_accounts_cache is None:
            self._google_accounts_cache = self._load_google_accounts()
        return sorted(self._google_accounts_cache.keys())

    # ── OAuth + service ─────────────────────────────────────────────────────

    def _gmail_service(self, account):
        """Return an authenticated Gmail service for the given account.

        On first call per account, runs the InstalledAppFlow OAuth dance
        (opens a browser; user picks the right Google account; consent
        granted). Token is then saved to ``{account}_token.json`` and reused.
        Subsequent calls hit the cache; expired tokens auto-refresh.
        """
        if not _HAS_GOOGLE:
            raise RuntimeError(
                "Google API client not installed. Run: "
                "pip install google-api-python-client google-auth-oauthlib google-auth-httplib2"
            )

        if account in self._gmail_services:
            return self._gmail_services[account]

        accounts = self._load_google_accounts()
        if account not in accounts:
            raise ValueError(
                f"Unknown Google account '{account}'. Configure it in "
                f"{GOOGLE_ACCOUNTS_FILE} (see README Google Integration section)."
            )

        os.makedirs(GOOGLE_CONFIG_DIR, exist_ok=True)
        token_path = os.path.join(GOOGLE_CONFIG_DIR, f"{account}_token.json")

        creds = None
        if os.path.exists(token_path):
            try:
                creds = Credentials.from_authorized_user_file(token_path, GMAIL_SCOPES)
            except Exception:
                creds = None

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not os.path.exists(GOOGLE_OAUTH_CLIENT_FILE):
                    raise RuntimeError(
                        f"OAuth client credentials not found at "
                        f"{GOOGLE_OAUTH_CLIENT_FILE}. Download a Desktop-app "
                        f"OAuth client JSON from Google Cloud Console and save "
                        f"it there. See README Google Integration section."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(
                    GOOGLE_OAUTH_CLIENT_FILE, GMAIL_SCOPES
                )
                # prompt='select_account' so the user can pick the right
                # account explicitly even when already signed in to another
                # Google account in the browser — crucial for multi-account.
                creds = flow.run_local_server(port=0, prompt="select_account")
            with open(token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
            # google-auth writes tokens with the default umask (typically 644).
            # Refresh tokens grant full Gmail access — lock to owner-only.
            try:
                os.chmod(token_path, 0o600)
            except OSError:
                pass

        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        self._gmail_services[account] = service
        return service

    # ── Safety: modal confirmation for destructive ops ──────────────────────

    def _confirm_gmail_action(self, tool_name, title, summary, detail):
        """Modal dialog confirming a destructive Gmail action. Returns True
        if the user clicks Yes, False otherwise.

        Honours the per-instruction bypass list: if ``tool_name`` appears in
        ``self._disabled_confirm_patterns`` (managed via the PS/Shell Safety
        dialog), the dialog is skipped, True is returned immediately, and a
        ``⚠ Gmail confirm bypassed`` warning is posted to the activity output
        — same pattern as the shell-command bypass uses, so users always
        have a visible audit trail of skipped confirmations.

        Uses messagebox.askyesno with a fixed-format prompt. Survives
        ``--headless`` mode because Tk dialogs work even when the main root
        is withdrawn (the dialog floats as a standalone window)."""
        disabled = getattr(self, "_disabled_confirm_patterns", set())
        if tool_name in disabled:
            queue = getattr(self, "queue", None)
            if queue is not None:
                queue.put({
                    "type": "warning",
                    "content": f"⚠ Gmail confirm bypassed for {tool_name}\n",
                })
            return True
        message = f"{summary}\n\n{detail}\n\nProceed?"
        try:
            return bool(messagebox.askyesno(title, message, parent=self.root))
        except Exception:
            return False

    # ── Helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _attach_files(msg, attachments):
        """Add one or more files as attachments to an EmailMessage.

        Returns (ok, summary_or_error). On success, returns
        (True, "[a.pdf (12345 bytes), b.png (67890 bytes)]"). On failure
        (missing file, oversize, total cap exceeded) returns (False, "error
        text") and leaves msg unmodified. Caller should bail out and surface
        the error to the agent."""
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
                    f"raw size (Gmail limit is ~25 MB total per message after base64 encoding)"
                )
            with open(path, "rb") as f:
                data = f.read()
            mime_type, _ = mimetypes.guess_type(path)
            if not mime_type:
                mime_type = "application/octet-stream"
            maintype, _, subtype = mime_type.partition("/")
            attached_blobs.append((data, maintype, subtype or "octet-stream", os.path.basename(path)))
            info.append(f"{os.path.basename(path)} ({size} bytes)")
        # All checks passed — actually mutate msg
        for data, maintype, subtype, filename in attached_blobs:
            msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
        return True, "[" + ", ".join(info) + "]"

    @staticmethod
    def _header(msg, name, default=""):
        for h in msg.get("payload", {}).get("headers", []):
            if h.get("name", "").lower() == name.lower():
                return h.get("value", default)
        return default

    @staticmethod
    def _extract_bodies(payload):
        """Walk a Gmail message payload tree once and collect BOTH the
        text/plain body and the raw text/html body, if present.

        Returns ``(text_body, html_body)`` — either may be the empty string.
        First-seen wins per type (the typical multipart/alternative structure
        has text/plain and text/html as siblings, so order doesn't matter)."""
        text_body = ""
        html_body = ""

        def walk(part):
            nonlocal text_body, html_body
            if not part:
                return
            mime = part.get("mimeType", "")
            data = part.get("body", {}).get("data")
            if data:
                try:
                    decoded = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                except Exception:
                    decoded = ""
                if mime == "text/plain" and not text_body:
                    text_body = decoded
                elif mime == "text/html" and not html_body:
                    html_body = decoded
            for sub in part.get("parts", []) or []:
                walk(sub)

        walk(payload)
        return text_body, html_body

    @staticmethod
    def _extract_body(payload):
        """Backwards-compatible "give me the best text body you can find"
        helper. Prefers text/plain; falls back to a tag-stripped text/html.
        Used by code paths that just want a plain string. New code should
        prefer ``_extract_bodies`` so the caller can decide how to handle
        the html-only case."""
        text, html = GmailMixin._extract_bodies(payload)
        if text:
            return text
        if html:
            return re.sub(r"<[^>]+>", "", html)
        return ""

    @staticmethod
    def _extract_attachments(payload):
        """Walk a Gmail message payload tree and surface attachment metadata.

        Returns a list of dicts:
            {filename, mime_type, size, attachment_id, part_id, inline}

        A "part with a non-empty filename" is treated as an attachment. Two
        delivery modes for the bytes themselves:
        - ``attachment_id`` is non-empty → fetch separately via
          ``users.messages.attachments.get`` (the normal path, used for
          anything more than a few KB).
        - ``attachment_id`` is empty → the bytes are inline in ``body.data``
          on this same part (rare; only used for very small attachments).
          ``inline=True`` flags this case so callers know not to do a
          separate fetch.
        """
        attachments = []

        def walk(part):
            if not part:
                return
            filename = part.get("filename", "")
            body = part.get("body", {}) or {}
            if filename:
                attachments.append({
                    "filename": filename,
                    "mime_type": part.get("mimeType", "application/octet-stream"),
                    "size": body.get("size", 0),
                    "attachment_id": body.get("attachmentId", ""),
                    "part_id": part.get("partId", ""),
                    "inline": not body.get("attachmentId"),
                })
            for sub in part.get("parts", []) or []:
                walk(sub)

        walk(payload)
        return attachments

    @staticmethod
    def _format_message_summary(msg):
        """Compact dict representation used in search/list results."""
        return {
            "id": msg.get("id"),
            "threadId": msg.get("threadId"),
            "snippet": msg.get("snippet", "")[:200],
            "from": GmailMixin._header(msg, "From"),
            "to": GmailMixin._header(msg, "To"),
            "subject": GmailMixin._header(msg, "Subject"),
            "date": GmailMixin._header(msg, "Date"),
        }

    # ── Tool implementations ────────────────────────────────────────────────

    def do_gmail_search(self, params):
        """Search messages using Gmail's standard query syntax."""
        try:
            account = params["account"]
            q = params.get("q", "")
            max_results = min(int(params.get("max_results", 25)), 500)
            service = self._gmail_service(account)
            resp = service.users().messages().list(
                userId="me", q=q, maxResults=max_results
            ).execute()
            ids = [m["id"] for m in resp.get("messages", [])]
            results = []
            for mid in ids:
                msg = service.users().messages().get(
                    userId="me", id=mid, format="metadata",
                    metadataHeaders=["From", "To", "Subject", "Date"],
                ).execute()
                results.append(self._format_message_summary(msg))
            return json.dumps({
                "account": account, "query": q,
                "count": len(results), "messages": results,
            }, indent=2, ensure_ascii=False)
        except Exception as e:
            return f"gmail_search failed: {type(e).__name__}: {e}"

    def do_gmail_read(self, params):
        """Fetch the full content of a single message by ID.

        The ``format`` param selects what body representation to return:
        - ``"text"`` (default, backward compatible): plain text only. If the
          message is HTML-only, returns the tag-stripped HTML as a string.
        - ``"html"``: raw HTML only. If the message is text-only, returns "".
        - ``"both"``: returns both ``body`` (text or stripped fallback) AND
          ``body_html`` (raw HTML, may be empty if the message is text-only).

        Each body is truncated at 50,000 characters with a ``body_truncated``
        or ``body_html_truncated`` flag in the response."""
        try:
            account = params["account"]
            message_id = params["message_id"]
            fmt = params.get("format", "text")
            if fmt not in ("text", "html", "both"):
                return f"gmail_read failed: format must be 'text', 'html', or 'both' (got {fmt!r})"
            service = self._gmail_service(account)
            msg = service.users().messages().get(
                userId="me", id=message_id, format="full"
            ).execute()
            text_body, html_body = self._extract_bodies(msg.get("payload", {}))
            attachments = self._extract_attachments(msg.get("payload", {}))
            result = {
                "account": account,
                "id": msg.get("id"),
                "threadId": msg.get("threadId"),
                "labelIds": msg.get("labelIds", []),
                "from": self._header(msg, "From"),
                "to": self._header(msg, "To"),
                "cc": self._header(msg, "Cc"),
                "subject": self._header(msg, "Subject"),
                "date": self._header(msg, "Date"),
                "snippet": msg.get("snippet", ""),
                # Attachment metadata always included — small payload, lets the
                # model decide whether to call gmail_get_attachment without
                # needing a second round trip to discover what's attached.
                "attachments": attachments,
            }
            if fmt in ("text", "both"):
                # If only HTML is present, strip tags as the text fallback
                body = text_body or (re.sub(r"<[^>]+>", "", html_body) if html_body else "")
                result["body"] = body[:50000]
                result["body_truncated"] = len(body) > 50000
            if fmt in ("html", "both"):
                result["body_html"] = html_body[:50000]
                result["body_html_truncated"] = len(html_body) > 50000
            return json.dumps(result, indent=2, ensure_ascii=False)
        except Exception as e:
            return f"gmail_read failed: {type(e).__name__}: {e}"

    def do_gmail_get_attachment(self, params):
        """Download a single attachment to a local file path.

        Get the ``attachment_id`` from the ``attachments`` array returned by
        ``gmail_read`` on the message. Non-destructive — creates a local file,
        does not modify Gmail state. Refuses to overwrite an existing file
        unless ``overwrite=True`` is passed.

        Returns the absolute path of the saved file and the byte count. On
        a path-collision refusal, returns an error string; the agent can
        choose to retry with overwrite=true or pick a different ``save_to``.
        """
        try:
            account = params["account"]
            message_id = params["message_id"]
            attachment_id = params.get("attachment_id", "")
            save_to = params["save_to"]
            overwrite = bool(params.get("overwrite", False))

            if not attachment_id:
                return ("gmail_get_attachment failed: empty attachment_id. For "
                        "inline attachments (inline=true in gmail_read's "
                        "attachments[] list), the bytes are already in the "
                        "message body — there's no separate fetch to do.")

            if os.path.exists(save_to) and not overwrite:
                return (f"gmail_get_attachment failed: {save_to} already exists "
                        f"and overwrite=false. Pass overwrite=true to replace, "
                        f"or choose a different save_to path.")

            service = self._gmail_service(account)
            resp = service.users().messages().attachments().get(
                userId="me", messageId=message_id, id=attachment_id,
            ).execute()
            data = resp.get("data", "")
            if not data:
                return "gmail_get_attachment failed: API returned no data payload"

            decoded = base64.urlsafe_b64decode(data)
            parent_dir = os.path.dirname(os.path.abspath(save_to))
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)
            with open(save_to, "wb") as f:
                f.write(decoded)

            return json.dumps({
                "account": account,
                "message_id": message_id,
                "attachment_id": attachment_id,
                "saved_to": os.path.abspath(save_to),
                "bytes_written": len(decoded),
                "overwrote_existing": overwrite and os.path.exists(save_to),
            }, indent=2)
        except Exception as e:
            return f"gmail_get_attachment failed: {type(e).__name__}: {e}"

    def do_gmail_send(self, params):
        """Send a new email. Requires user confirmation. Optionally attaches files."""
        try:
            account = params["account"]
            to = params["to"]
            subject = params.get("subject", "")
            body = params.get("body", "")
            body_html = params.get("body_html", "")
            cc = params.get("cc", "")
            bcc = params.get("bcc", "")
            attachments = params.get("attachments") or []
            msg = EmailMessage()
            msg.set_content(body)
            # If HTML is provided, layer it as the multipart/alternative
            # preferred view. Email clients that render HTML show the HTML
            # version; clients that don't fall back to the plain text body
            # set above. Always provide BOTH — sending HTML-only emails
            # without a plain-text fallback degrades client compatibility
            # and trips some spam filters.
            if body_html:
                msg.add_alternative(body_html, subtype="html")
            msg["To"] = to
            msg["Subject"] = subject
            if cc:
                msg["Cc"] = cc
            if bcc:
                msg["Bcc"] = bcc
            ok, attach_summary = self._attach_files(msg, attachments)
            if not ok:
                return f"gmail_send failed: {attach_summary}"
            if not self._confirm_gmail_action(
                "gmail_send",
                "Confirm Gmail send",
                f"Send email from account '{account}' to: {to}",
                f"Subject: {subject}\nCc: {cc or '(none)'}\nBcc: {bcc or '(none)'}\n"
                f"Attachments: {attach_summary or '(none)'}\n"
                f"HTML body: {'yes (' + str(len(body_html)) + ' chars)' if body_html else 'no'}\n\n"
                f"{body[:500]}{'...' if len(body) > 500 else ''}",
            ):
                return "user denied: gmail_send not sent"
            service = self._gmail_service(account)
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            sent = service.users().messages().send(
                userId="me", body={"raw": raw}
            ).execute()
            return json.dumps({
                "account": account, "sent_message_id": sent.get("id"),
                "threadId": sent.get("threadId"), "to": to, "subject": subject,
                "attachments": attach_summary or None,
                "html_alternative": bool(body_html),
            }, indent=2)
        except Exception as e:
            return f"gmail_send failed: {type(e).__name__}: {e}"

    def do_gmail_reply(self, params):
        """Reply to a message with proper Gmail threading. Requires confirmation.

        Builds an EmailMessage with In-Reply-To and References headers pointing
        at the original Message-ID, prepends 'Re: ' to the subject if not
        already present, and passes the original's threadId to messages.send
        so Gmail nests the reply inside the existing conversation. Reply-to
        target defaults to the original sender; override via the 'to' param
        if you need to reply to a different address (e.g., reply to a group's
        list address rather than the original poster)."""
        try:
            account = params["account"]
            message_id = params["message_id"]
            body = params.get("body", "")
            body_html = params.get("body_html", "")
            attachments = params.get("attachments") or []
            cc = params.get("cc", "")
            bcc = params.get("bcc", "")
            override_to = params.get("to", "")

            service = self._gmail_service(account)
            original = service.users().messages().get(
                userId="me", id=message_id, format="metadata",
                metadataHeaders=["Message-ID", "References", "Subject", "From", "To"],
            ).execute()
            orig_msgid = self._header(original, "Message-ID")
            orig_refs = self._header(original, "References")
            orig_subject = self._header(original, "Subject")
            orig_from = self._header(original, "From")
            thread_id = original.get("threadId")
            if not orig_msgid:
                return ("gmail_reply failed: original message has no Message-ID "
                        "header (cannot construct In-Reply-To)")
            # Reply target: explicit override > original sender. Strip display
            # name from "Name <addr>" form if the model passed the full From line.
            reply_to = override_to or orig_from
            # References header chains the conversation: existing refs + the
            # message we're replying to. Gmail uses this to thread on the
            # recipient side; threadId only handles the sender side.
            new_refs = (f"{orig_refs} {orig_msgid}".strip() if orig_refs else orig_msgid)
            # Prepend "Re: " only if not already present (case-insensitive)
            subject = orig_subject if re.match(r"^re:\s", orig_subject, re.I) else f"Re: {orig_subject}"

            msg = EmailMessage()
            msg.set_content(body)
            if body_html:
                msg.add_alternative(body_html, subtype="html")
            msg["To"] = reply_to
            msg["Subject"] = subject
            msg["In-Reply-To"] = orig_msgid
            msg["References"] = new_refs
            if cc:
                msg["Cc"] = cc
            if bcc:
                msg["Bcc"] = bcc
            ok, attach_summary = self._attach_files(msg, attachments)
            if not ok:
                return f"gmail_reply failed: {attach_summary}"
            if not self._confirm_gmail_action(
                "gmail_reply",
                "Confirm Gmail reply",
                f"Reply from account '{account}' in thread {thread_id} to: {reply_to}",
                f"Subject: {subject}\nCc: {cc or '(none)'}\nBcc: {bcc or '(none)'}\n"
                f"Attachments: {attach_summary or '(none)'}\n"
                f"HTML body: {'yes (' + str(len(body_html)) + ' chars)' if body_html else 'no'}\n\n"
                f"{body[:500]}{'...' if len(body) > 500 else ''}",
            ):
                return "user denied: gmail_reply not sent"
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            sent = service.users().messages().send(
                userId="me",
                body={"raw": raw, "threadId": thread_id},
            ).execute()
            return json.dumps({
                "account": account, "sent_message_id": sent.get("id"),
                "threadId": sent.get("threadId"), "in_reply_to": orig_msgid,
                "to": reply_to, "subject": subject,
                "attachments": attach_summary or None,
                "html_alternative": bool(body_html),
            }, indent=2)
        except Exception as e:
            return f"gmail_reply failed: {type(e).__name__}: {e}"

    def do_gmail_create_draft(self, params):
        """Create a draft (no confirmation — drafts aren't destructive).
        Optionally attaches files."""
        try:
            account = params["account"]
            attachments = params.get("attachments") or []
            body_html = params.get("body_html", "")
            msg = EmailMessage()
            msg.set_content(params.get("body", ""))
            if body_html:
                msg.add_alternative(body_html, subtype="html")
            msg["To"] = params.get("to", "")
            msg["Subject"] = params.get("subject", "")
            if params.get("cc"):
                msg["Cc"] = params["cc"]
            if params.get("bcc"):
                msg["Bcc"] = params["bcc"]
            ok, attach_summary = self._attach_files(msg, attachments)
            if not ok:
                return f"gmail_create_draft failed: {attach_summary}"
            service = self._gmail_service(account)
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            draft = service.users().drafts().create(
                userId="me", body={"message": {"raw": raw}}
            ).execute()
            return json.dumps({
                "account": account, "draft_id": draft.get("id"),
                "message_id": draft.get("message", {}).get("id"),
                "attachments": attach_summary or None,
                "html_alternative": bool(body_html),
            }, indent=2)
        except Exception as e:
            return f"gmail_create_draft failed: {type(e).__name__}: {e}"

    def do_gmail_list_drafts(self, params):
        try:
            account = params["account"]
            max_results = min(int(params.get("max_results", 25)), 100)
            service = self._gmail_service(account)
            resp = service.users().drafts().list(
                userId="me", maxResults=max_results
            ).execute()
            drafts = []
            for d in resp.get("drafts", []):
                full = service.users().drafts().get(
                    userId="me", id=d["id"], format="metadata"
                ).execute()
                m = full.get("message", {})
                drafts.append({
                    "draft_id": d["id"],
                    "message_id": m.get("id"),
                    "to": self._header(m, "To"),
                    "subject": self._header(m, "Subject"),
                    "snippet": m.get("snippet", "")[:200],
                })
            return json.dumps({
                "account": account, "count": len(drafts), "drafts": drafts,
            }, indent=2, ensure_ascii=False)
        except Exception as e:
            return f"gmail_list_drafts failed: {type(e).__name__}: {e}"

    def do_gmail_send_draft(self, params):
        """Send an existing draft. Requires user confirmation."""
        try:
            account = params["account"]
            draft_id = params["draft_id"]
            service = self._gmail_service(account)
            draft = service.users().drafts().get(
                userId="me", id=draft_id, format="metadata"
            ).execute()
            m = draft.get("message", {})
            to = self._header(m, "To")
            subject = self._header(m, "Subject")
            if not self._confirm_gmail_action(
                "gmail_send_draft",
                "Confirm Gmail send draft",
                f"Send draft {draft_id} from account '{account}' to: {to}",
                f"Subject: {subject}",
            ):
                return "user denied: gmail_send_draft not sent"
            sent = service.users().drafts().send(
                userId="me", body={"id": draft_id}
            ).execute()
            return json.dumps({
                "account": account, "sent_message_id": sent.get("id"),
                "draft_id": draft_id,
            }, indent=2)
        except Exception as e:
            return f"gmail_send_draft failed: {type(e).__name__}: {e}"

    def do_gmail_trash(self, params):
        """Move one or more messages to Trash. Requires user confirmation."""
        try:
            account = params["account"]
            message_ids = params["message_ids"]
            if isinstance(message_ids, str):
                message_ids = [message_ids]
            if not message_ids:
                return "gmail_trash: no message_ids provided"
            if not self._confirm_gmail_action(
                "gmail_trash",
                "Confirm Gmail trash",
                f"Move {len(message_ids)} message(s) to Trash on account '{account}'",
                f"Message IDs: {', '.join(str(m) for m in message_ids[:10])}"
                + (f" (+{len(message_ids) - 10} more)" if len(message_ids) > 10 else ""),
            ):
                return "user denied: gmail_trash skipped"
            service = self._gmail_service(account)
            trashed = []
            for mid in message_ids:
                service.users().messages().trash(userId="me", id=mid).execute()
                trashed.append(mid)
            return json.dumps({
                "account": account, "trashed_count": len(trashed),
                "trashed_ids": trashed,
            }, indent=2)
        except Exception as e:
            return f"gmail_trash failed: {type(e).__name__}: {e}"

    def do_gmail_untrash(self, params):
        try:
            account = params["account"]
            message_ids = params["message_ids"]
            if isinstance(message_ids, str):
                message_ids = [message_ids]
            service = self._gmail_service(account)
            restored = []
            for mid in message_ids:
                service.users().messages().untrash(userId="me", id=mid).execute()
                restored.append(mid)
            return json.dumps({
                "account": account, "untrashed_count": len(restored),
                "untrashed_ids": restored,
            }, indent=2)
        except Exception as e:
            return f"gmail_untrash failed: {type(e).__name__}: {e}"

    def do_gmail_list_labels(self, params):
        try:
            account = params["account"]
            service = self._gmail_service(account)
            resp = service.users().labels().list(userId="me").execute()
            labels = [
                {"id": lab["id"], "name": lab["name"], "type": lab.get("type", "")}
                for lab in resp.get("labels", [])
            ]
            return json.dumps({
                "account": account, "count": len(labels), "labels": labels,
            }, indent=2, ensure_ascii=False)
        except Exception as e:
            return f"gmail_list_labels failed: {type(e).__name__}: {e}"

    def do_gmail_create_label(self, params):
        """Create a new user label. Non-destructive — no confirmation needed.
        Returns the new label's id and name. If a label with this name already
        exists, Gmail returns a 409 which is surfaced as an error string."""
        try:
            account = params["account"]
            name = params["name"]
            visibility_label = params.get("label_list_visibility", "labelShow")
            visibility_message = params.get("message_list_visibility", "show")
            service = self._gmail_service(account)
            label = service.users().labels().create(
                userId="me",
                body={
                    "name": name,
                    "labelListVisibility": visibility_label,
                    "messageListVisibility": visibility_message,
                },
            ).execute()
            return json.dumps({
                "account": account, "label_id": label.get("id"),
                "name": label.get("name"), "type": label.get("type", "user"),
            }, indent=2, ensure_ascii=False)
        except Exception as e:
            return f"gmail_create_label failed: {type(e).__name__}: {e}"

    def do_gmail_delete_label(self, params):
        """Delete a user label. DESTRUCTIVE — removes the label from EVERY
        message that has it (the messages themselves are not deleted, but the
        labelling is gone and not recoverable). System labels (INBOX, SENT,
        TRASH, etc.) cannot be deleted; Gmail returns 400. Requires
        confirmation via the standard dialog."""
        try:
            account = params["account"]
            label_id = params["label_id"]
            service = self._gmail_service(account)
            # Look up the name for the confirmation dialog detail
            try:
                lab = service.users().labels().get(userId="me", id=label_id).execute()
                label_name = lab.get("name", "(unknown)")
                label_type = lab.get("type", "user")
            except Exception:
                label_name, label_type = "(could not fetch)", "(unknown)"
            if not self._confirm_gmail_action(
                "gmail_delete_label",
                "Confirm Gmail delete label",
                f"Delete label '{label_name}' (id={label_id}, type={label_type}) "
                f"from account '{account}'",
                "This removes the label from EVERY message that has it. "
                "The messages themselves are NOT deleted, but the labelling "
                "is gone with no automatic recovery (you'd have to recreate "
                "the label and re-apply manually).",
            ):
                return "user denied: gmail_delete_label skipped"
            service.users().labels().delete(userId="me", id=label_id).execute()
            return json.dumps({
                "account": account, "deleted_label_id": label_id,
                "deleted_label_name": label_name,
            }, indent=2)
        except Exception as e:
            return f"gmail_delete_label failed: {type(e).__name__}: {e}"

    def do_gmail_modify_labels(self, params):
        """Add and/or remove labels on one or more messages."""
        try:
            account = params["account"]
            message_ids = params["message_ids"]
            if isinstance(message_ids, str):
                message_ids = [message_ids]
            add_labels = params.get("add_labels", []) or []
            remove_labels = params.get("remove_labels", []) or []
            service = self._gmail_service(account)
            body = {}
            if add_labels:
                body["addLabelIds"] = add_labels
            if remove_labels:
                body["removeLabelIds"] = remove_labels
            modified = []
            for mid in message_ids:
                service.users().messages().modify(
                    userId="me", id=mid, body=body
                ).execute()
                modified.append(mid)
            return json.dumps({
                "account": account, "modified_count": len(modified),
                "modified_ids": modified, "add": add_labels, "remove": remove_labels,
            }, indent=2)
        except Exception as e:
            return f"gmail_modify_labels failed: {type(e).__name__}: {e}"

    def do_gmail_mark_read(self, params):
        """Mark messages read or unread (toggles the UNREAD label)."""
        try:
            account = params["account"]
            message_ids = params["message_ids"]
            if isinstance(message_ids, str):
                message_ids = [message_ids]
            read = bool(params.get("read", True))
            service = self._gmail_service(account)
            body = {"removeLabelIds": ["UNREAD"]} if read else {"addLabelIds": ["UNREAD"]}
            updated = []
            for mid in message_ids:
                service.users().messages().modify(
                    userId="me", id=mid, body=body
                ).execute()
                updated.append(mid)
            return json.dumps({
                "account": account, "marked_read" if read else "marked_unread": len(updated),
                "message_ids": updated,
            }, indent=2)
        except Exception as e:
            return f"gmail_mark_read failed: {type(e).__name__}: {e}"

    def do_gmail_list_threads(self, params):
        """List threads matching a query."""
        try:
            account = params["account"]
            q = params.get("q", "")
            max_results = min(int(params.get("max_results", 25)), 500)
            service = self._gmail_service(account)
            resp = service.users().threads().list(
                userId="me", q=q, maxResults=max_results
            ).execute()
            threads = []
            for t in resp.get("threads", []):
                threads.append({
                    "thread_id": t["id"],
                    "snippet": t.get("snippet", "")[:200],
                    "history_id": t.get("historyId"),
                })
            return json.dumps({
                "account": account, "query": q,
                "count": len(threads), "threads": threads,
            }, indent=2, ensure_ascii=False)
        except Exception as e:
            return f"gmail_list_threads failed: {type(e).__name__}: {e}"
