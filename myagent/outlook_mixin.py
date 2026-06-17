"""Outlook / Microsoft 365 mail integration via the Microsoft Graph API
(native MyAgent tools, NOT MCP — same rationale as the Gmail and Proton
mixins: a tight, multi-account tool surface that flows through the existing
``_get_tools()`` / ``_execute_tool()`` pipeline).

Architecture notes:

* **Graph, not IMAP/SMTP.** Microsoft disabled Basic Auth (username +
  app-password) for personal ``outlook.com`` IMAP/POP/SMTP in late 2024, so
  the Proton-style "IMAP + app password" path is dead for these accounts —
  even IMAP now requires OAuth2/XOAUTH2. The modern path is the Microsoft
  Graph REST API authenticated with MSAL, which maps almost 1:1 onto the
  Gmail mixin (OAuth dance, per-account token cache, REST calls).

* **OAuth via MSAL.** All tools share one ``PublicClientApplication`` per
  account. The Azure app *client_id* (analogous to Gmail's
  ``oauth_client.json``) lives at ``~/.config/myagent-msmail/msal_app.json``
  ({"client_id": "...", "authority": "..."}) or the ``OUTLOOK_CLIENT_ID`` /
  ``MS_CLIENT_ID`` environment variable. Per-account token caches live at
  ``~/.config/myagent-msmail/{account}_token.json`` (chmod 600). Accounts are
  listed in ``~/.config/myagent-msmail/accounts.json`` so the tool-schema
  ``account`` enum is patched at runtime in ``_get_tools`` rather than
  hardcoded. First call per account opens a browser for consent; afterwards
  the refresh token in the cache is used silently.

* **Scopes are ``Mail.ReadWrite`` + ``Mail.Send``** — covers read, send,
  draft, move (incl. trash via the Deleted Items folder), categories, and
  mark-read. It does NOT grant permanent delete of mailbox items beyond the
  Deleted Items folder, mirroring Gmail's deliberate "soft-delete only"
  boundary (Deleted Items is recoverable from Outlook's UI).

* **Gmail "labels" → Outlook "categories".** Graph has no Gmail-style labels.
  The closest analogue is *categories* (colored tags managed via
  ``/me/outlook/masterCategories``). ``outlook_modify_labels`` operates on
  category **display names** (not IDs) because that's how Graph stores them on
  a message (``message.categories`` is an array of names). ``outlook_trash``
  moves to the Deleted Items folder rather than toggling a label.

* **Destructive operations** (``outlook_send``, ``outlook_reply``,
  ``outlook_send_draft``, ``outlook_trash``, ``outlook_delete_label``) pop the
  same modal Tk confirmation as Gmail/Proton, honouring the per-instruction
  bypass list in ``self._disabled_confirm_patterns``.

* When ``msal`` is not installed (``_HAS_OUTLOOK = False``) every method here
  is a graceful no-op and the Outlook checkbox in the editor is disabled —
  same opt-in degradation as MCP / Google / Ollama.

* **Helper-name hygiene.** Every non-shared helper here is prefixed
  ``_outlook_`` so it can never shadow (or be shadowed by) the identically-
  purposed statics on GmailMixin/ProtonMailMixin via the App's MRO — the
  flat-namespace mixin footgun documented in CLAUDE_MYAGENT.md.
"""

import base64
import json
import mimetypes
import os
from myagent.mail_common import confirm_action

from myagent.helpers import extract_text_from_html

_HAS_OUTLOOK = True
try:
    import msal
    import requests
except Exception:
    _HAS_OUTLOOK = False


OUTLOOK_CONFIG_DIR = os.path.expanduser("~/.config/myagent-msmail")
OUTLOOK_ACCOUNTS_FILE = os.path.join(OUTLOOK_CONFIG_DIR, "accounts.json")
OUTLOOK_APP_FILE = os.path.join(OUTLOOK_CONFIG_DIR, "msal_app.json")

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# Mail.ReadWrite covers read/draft/move/categories; Mail.Send covers sending.
# MSAL automatically reserves openid/profile/offline_access — do NOT list them
# here or MSAL warns and may error. Full resource URIs are used so MSAL doesn't
# have to guess the resource.
OUTLOOK_SCOPES = [
    "https://graph.microsoft.com/Mail.ReadWrite",
    "https://graph.microsoft.com/Mail.Send",
]

# Graph's single-request JSON body limit is ~4 MB; base64 inflates raw bytes by
# ~33%, so cap combined raw attachment bytes at 3 MB to stay safely under it.
# Larger attachments need a Graph upload session (createUploadSession), which
# this mixin does not yet implement — the error message says so.
MAX_OUTLOOK_ATTACH_TOTAL = 3 * 1024 * 1024


class OutlookMixin:
    """Outlook/Microsoft 365 mail tools backed by the Microsoft Graph API."""

    # ── State init ──────────────────────────────────────────────────────────

    def _outlook_init_state(self):
        """Initialize Outlook-related instance attributes. Call from App.__init__."""
        # account_name -> (PublicClientApplication, SerializableTokenCache, cache_path)
        self._outlook_apps = {}
        self._outlook_accounts_cache = None  # lazy

    # ── Account discovery ───────────────────────────────────────────────────

    def _load_outlook_accounts(self):
        """Read accounts.json. Returns a dict keyed by account name with
        optional metadata (e.g. ``email``). Empty dict if file missing or
        malformed — tools then fail with a clear message at use time rather
        than at startup, so a user with Outlook off sees no noise."""
        if not _HAS_OUTLOOK:
            return {}
        if not os.path.exists(OUTLOOK_ACCOUNTS_FILE):
            return {}
        try:
            with open(OUTLOOK_ACCOUNTS_FILE, encoding="utf-8") as f:
                data = json.load(f)
            accounts = data.get("accounts", {})
            return accounts if isinstance(accounts, dict) else {}
        except Exception:
            return {}

    def _get_outlook_account_names(self):
        """List of configured account names (sorted). Used to populate the
        ``account`` enum on every Outlook tool schema at runtime."""
        if self._outlook_accounts_cache is None:
            self._outlook_accounts_cache = self._load_outlook_accounts()
        return sorted(self._outlook_accounts_cache.keys())

    # ── OAuth (MSAL) ─────────────────────────────────────────────────────────

    def _outlook_client_config(self):
        """Resolve the Azure app client_id and authority.

        Priority: msal_app.json (client_id, optional authority) > the
        OUTLOOK_CLIENT_ID / MS_CLIENT_ID environment variables. Authority
        defaults to ``/common`` which accepts both personal Microsoft accounts
        (outlook.com / hotmail / live) and work/school accounts; set it to
        ``/consumers`` in msal_app.json to lock to personal accounts only."""
        client_id = None
        authority = "https://login.microsoftonline.com/common"
        if os.path.exists(OUTLOOK_APP_FILE):
            try:
                with open(OUTLOOK_APP_FILE, encoding="utf-8") as f:
                    data = json.load(f)
                client_id = data.get("client_id")
                authority = data.get("authority", authority)
            except Exception:
                pass
        client_id = (client_id or os.environ.get("OUTLOOK_CLIENT_ID")
                     or os.environ.get("MS_CLIENT_ID"))
        if not client_id:
            raise RuntimeError(
                f"No Azure app client_id found. Create an app registration "
                f"(Azure Portal → App registrations → New; redirect URI "
                f"'http://localhost' as a Mobile/desktop platform; add the "
                f"Microsoft Graph delegated permissions Mail.ReadWrite and "
                f"Mail.Send) and put {{\"client_id\": \"...\"}} in "
                f"{OUTLOOK_APP_FILE}, or set OUTLOOK_CLIENT_ID. See the README "
                f"Outlook Integration section."
            )
        return client_id, authority

    def _outlook_app(self, account):
        """Return (app, cache, cache_path) for an account, building and caching
        the MSAL PublicClientApplication on first use. The token cache is
        seeded from the on-disk file so silent refresh works across runs."""
        if account in self._outlook_apps:
            return self._outlook_apps[account]
        client_id, authority = self._outlook_client_config()
        os.makedirs(OUTLOOK_CONFIG_DIR, exist_ok=True)
        cache_path = os.path.join(OUTLOOK_CONFIG_DIR, f"{account}_token.json")
        cache = msal.SerializableTokenCache()
        if os.path.exists(cache_path):
            try:
                with open(cache_path, encoding="utf-8") as f:
                    cache.deserialize(f.read())
            except Exception:
                pass
        app = msal.PublicClientApplication(
            client_id, authority=authority, token_cache=cache
        )
        self._outlook_apps[account] = (app, cache, cache_path)
        return self._outlook_apps[account]

    def _outlook_token(self, account, force=False):
        """Return a valid Graph access token for the account.

        Tries silent acquisition from the cached refresh token first; on a
        miss (or force=True) runs the interactive browser flow, which prompts
        the user to pick the right Microsoft account. Persists the cache to
        disk (chmod 600) whenever it changes."""
        if not _HAS_OUTLOOK:
            raise RuntimeError(
                "msal not installed. Run: pip install msal requests"
            )
        accounts = self._load_outlook_accounts()
        if account not in accounts:
            raise ValueError(
                f"Unknown Outlook account '{account}'. Configure it in "
                f"{OUTLOOK_ACCOUNTS_FILE} (see README Outlook Integration section)."
            )
        email = accounts[account].get("email", account)
        app, cache, cache_path = self._outlook_app(account)

        result = None
        if not force:
            cached = app.get_accounts(username=email) or app.get_accounts()
            if cached:
                result = app.acquire_token_silent(OUTLOOK_SCOPES, account=cached[0])
        if not result:
            # Interactive: opens the system browser, runs a transient localhost
            # redirect server. prompt='select_account' so the user can pick the
            # right MS account even when already signed in to another.
            result = app.acquire_token_interactive(
                OUTLOOK_SCOPES, login_hint=email, prompt="select_account"
            )
        if not result or "access_token" not in result:
            err = (result or {}).get("error", "unknown")
            desc = (result or {}).get("error_description", "")[:200]
            raise RuntimeError(f"Outlook auth failed: {err}: {desc}")

        if cache.has_state_changed:
            try:
                with open(cache_path, "w", encoding="utf-8") as f:
                    f.write(cache.serialize())
                os.chmod(cache_path, 0o600)
            except OSError:
                pass
        return result["access_token"]

    def _outlook_graph(self, account, method, path, params=None,
                       json_body=None, raw=False):
        """Single entry point for Graph REST calls.

        Prefixes ``path`` with the Graph base URL (unless it's already
        absolute), attaches the bearer token, and returns parsed JSON ({} for
        204/empty), or raw bytes when ``raw=True``. A 401 triggers one forced
        token re-acquire + retry. Graph's structured error body is surfaced as
        a RuntimeError so each tool's try/except can return a clean message."""
        token = self._outlook_token(account)
        url = path if path.startswith("http") else GRAPH_BASE + path
        headers = {"Authorization": f"Bearer {token}"}
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        resp = requests.request(method, url, headers=headers, params=params,
                                json=json_body, timeout=60)
        if resp.status_code == 401:
            token = self._outlook_token(account, force=True)
            headers["Authorization"] = f"Bearer {token}"
            resp = requests.request(method, url, headers=headers, params=params,
                                    json=json_body, timeout=60)
        if resp.status_code >= 400:
            try:
                err = resp.json().get("error", {})
                raise RuntimeError(
                    f"Graph {resp.status_code} {err.get('code', '')}: "
                    f"{err.get('message', '')}"
                )
            except ValueError as e:
                raise RuntimeError(f"Graph {resp.status_code}: {resp.text[:300]}") from e
        if raw:
            return resp.content
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()

    # ── Safety: modal confirmation for destructive ops ──────────────────────

    def _confirm_outlook_action(self, tool_name, title, summary, detail):
        """Modal dialog confirming a destructive Outlook action. Returns True
        if the user clicks Yes. Honours the per-instruction bypass list in
        ``self._disabled_confirm_patterns`` (managed via the PS/Shell Safety
        dialog): if bypassed, returns True immediately and posts a
        ``⚠ Outlook confirm bypassed`` warning to the activity output. Same
        pattern as the Gmail/Proton/shell confirmations; survives --headless
        because Tk dialogs float standalone when the root is withdrawn."""
        return confirm_action(self, "Outlook", tool_name, title, summary, detail)

    # ── Helpers (all prefixed _outlook_ to avoid MRO shadowing) ──────────────

    @staticmethod
    def _outlook_addr(recipient):
        """Render a Graph recipient object as 'Name <addr>' (or just addr)."""
        ea = (recipient or {}).get("emailAddress", {}) or {}
        name, addr = ea.get("name", ""), ea.get("address", "")
        if name and name != addr:
            return f"{name} <{addr}>"
        return addr

    @staticmethod
    def _outlook_recip_list(recipients):
        return ", ".join(
            OutlookMixin._outlook_addr(r) for r in (recipients or [])
        )

    @staticmethod
    def _outlook_parse_recipients(value):
        """Turn a comma/semicolon-separated address string (or list) into the
        Graph ``[{"emailAddress": {"address": ...}}]`` shape. Empty/falsey
        input yields an empty list."""
        if not value:
            return []
        if isinstance(value, str):
            parts = [p.strip() for p in value.replace(";", ",").split(",")]
        else:
            parts = [str(p).strip() for p in value]
        return [{"emailAddress": {"address": p}} for p in parts if p]

    @staticmethod
    def _outlook_file_attachments(attachments):
        """Build Graph fileAttachment dicts from local file paths.

        Returns (ok, attachment_dicts, summary_or_error). On success the dicts
        are ready to drop into ``message["attachments"]`` or POST to
        ``/messages/{id}/attachments``. On failure (missing file / over the
        combined cap) returns (False, [], "error text")."""
        if not attachments:
            return True, [], ""
        if isinstance(attachments, str):
            attachments = [attachments]
        total = 0
        dicts = []
        info = []
        for path in attachments:
            if not os.path.isfile(path):
                return False, [], f"attachment not found or not a file: {path}"
            size = os.path.getsize(path)
            total += size
            if total > MAX_OUTLOOK_ATTACH_TOTAL:
                return False, [], (
                    f"attachments exceed {MAX_OUTLOOK_ATTACH_TOTAL // (1024 * 1024)} MB "
                    f"combined raw size (Graph's single-request limit is ~4 MB after "
                    f"base64 encoding; larger attachments need an upload session, "
                    f"not yet supported by this tool)"
                )
            with open(path, "rb") as f:
                data = f.read()
            mime_type, _ = mimetypes.guess_type(path)
            dicts.append({
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": os.path.basename(path),
                "contentType": mime_type or "application/octet-stream",
                "contentBytes": base64.b64encode(data).decode("ascii"),
            })
            info.append(f"{os.path.basename(path)} ({size} bytes)")
        return True, dicts, "[" + ", ".join(info) + "]"

    @staticmethod
    def _outlook_body_field(body, body_html):
        """Build the Graph ``body`` object, preferring HTML when provided.
        Graph has a single body (contentType html|text), not multipart, so
        when HTML is supplied it wins and the plain text is dropped (Graph
        renders a text fallback itself for non-HTML clients)."""
        if body_html:
            return {"contentType": "html", "content": body_html}
        return {"contentType": "text", "content": body or ""}

    @staticmethod
    def _outlook_summary(m):
        """Compact dict representation used in search/list results."""
        return {
            "id": m.get("id"),
            "conversationId": m.get("conversationId"),
            "subject": m.get("subject", ""),
            "from": OutlookMixin._outlook_addr(m.get("from") or m.get("sender")),
            "to": OutlookMixin._outlook_recip_list(m.get("toRecipients")),
            "date": m.get("receivedDateTime", ""),
            "snippet": (m.get("bodyPreview", "") or "")[:200],
            "hasAttachments": m.get("hasAttachments", False),
            "isRead": m.get("isRead"),
        }

    # Fields requested for search/list result summaries.
    _OUTLOOK_LIST_SELECT = (
        "id,conversationId,subject,from,sender,toRecipients,"
        "receivedDateTime,bodyPreview,hasAttachments,isRead"
    )

    # ── Tool implementations ────────────────────────────────────────────────

    def do_outlook_search(self, params):
        """Search messages. Uses Graph $search (KQL) when a query is given,
        else returns the most recent messages (newest first)."""
        try:
            account = params["account"]
            q = (params.get("q") or "").strip()
            max_results = min(int(params.get("max_results", 25)), 250)
            req_params = {"$top": max_results, "$select": self._OUTLOOK_LIST_SELECT}
            if q:
                # $search cannot combine with $orderby; results come back by
                # relevance. The value must be wrapped in double quotes.
                req_params["$search"] = f'"{q}"'
            else:
                req_params["$orderby"] = "receivedDateTime desc"
            resp = self._outlook_graph(account, "GET", "/me/messages",
                                       params=req_params)
            results = [self._outlook_summary(m) for m in resp.get("value", [])]
            return json.dumps({
                "account": account, "query": q,
                "count": len(results), "messages": results,
            }, indent=2, ensure_ascii=False)
        except Exception as e:
            return f"outlook_search failed: {type(e).__name__}: {e}"

    def do_outlook_read(self, params):
        """Fetch the full content of a single message by ID.

        The ``format`` param ('text' default / 'html' / 'both') controls the
        body representation, mirroring gmail_read. Bodies are truncated at
        50,000 chars with body_truncated / body_html_truncated flags. An
        attachments[] array is always included (metadata only)."""
        try:
            account = params["account"]
            message_id = params["message_id"]
            fmt = params.get("format", "text")
            if fmt not in ("text", "html", "both"):
                return f"outlook_read failed: format must be 'text', 'html', or 'both' (got {fmt!r})"
            select = ("id,conversationId,subject,from,sender,toRecipients,"
                      "ccRecipients,receivedDateTime,body,bodyPreview,"
                      "hasAttachments,isRead,categories")
            msg = self._outlook_graph(
                account, "GET", f"/me/messages/{message_id}",
                params={"$select": select},
            )
            body_obj = msg.get("body", {}) or {}
            content = body_obj.get("content", "") or ""
            is_html = (body_obj.get("contentType", "").lower() == "html")
            text_body = extract_text_from_html(content) if is_html else content
            html_body = content if is_html else ""

            attachments = []
            if msg.get("hasAttachments"):
                att = self._outlook_graph(
                    account, "GET", f"/me/messages/{message_id}/attachments",
                    params={"$select": "id,name,contentType,size,isInline"},
                )
                attachments = [{
                    "filename": a.get("name", ""),
                    "mime_type": a.get("contentType", "application/octet-stream"),
                    "size": a.get("size", 0),
                    "attachment_id": a.get("id", ""),
                    "inline": a.get("isInline", False),
                } for a in att.get("value", [])]

            result = {
                "account": account,
                "id": msg.get("id"),
                "conversationId": msg.get("conversationId"),
                "from": self._outlook_addr(msg.get("from") or msg.get("sender")),
                "to": self._outlook_recip_list(msg.get("toRecipients")),
                "cc": self._outlook_recip_list(msg.get("ccRecipients")),
                "subject": msg.get("subject", ""),
                "date": msg.get("receivedDateTime", ""),
                "isRead": msg.get("isRead"),
                "categories": msg.get("categories", []),
                "snippet": (msg.get("bodyPreview", "") or "")[:200],
                "attachments": attachments,
            }
            if fmt in ("text", "both"):
                result["body"] = text_body[:50000]
                result["body_truncated"] = len(text_body) > 50000
            if fmt in ("html", "both"):
                result["body_html"] = html_body[:50000]
                result["body_html_truncated"] = len(html_body) > 50000
            return json.dumps(result, indent=2, ensure_ascii=False)
        except Exception as e:
            return f"outlook_read failed: {type(e).__name__}: {e}"

    def do_outlook_get_attachment(self, params):
        """Download a single file attachment to a local path. Get the
        ``attachment_id`` from outlook_read's attachments[] array. Refuses to
        overwrite an existing file unless overwrite=true. Only file
        attachments are supported (item/reference attachments error out)."""
        try:
            account = params["account"]
            message_id = params["message_id"]
            attachment_id = params.get("attachment_id", "")
            save_to = params["save_to"]
            overwrite = bool(params.get("overwrite", False))

            if not attachment_id:
                return "outlook_get_attachment failed: empty attachment_id"
            if os.path.exists(save_to) and not overwrite:
                return (f"outlook_get_attachment failed: {save_to} already exists "
                        f"and overwrite=false. Pass overwrite=true to replace, "
                        f"or choose a different save_to path.")

            att = self._outlook_graph(
                account, "GET",
                f"/me/messages/{message_id}/attachments/{attachment_id}",
            )
            odata_type = att.get("@odata.type", "")
            content_bytes = att.get("contentBytes")
            if content_bytes is None:
                return (f"outlook_get_attachment failed: attachment is not a file "
                        f"attachment (type {odata_type or 'unknown'}); item and "
                        f"reference attachments are not supported.")
            decoded = base64.b64decode(content_bytes)
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
            }, indent=2)
        except Exception as e:
            return f"outlook_get_attachment failed: {type(e).__name__}: {e}"

    def do_outlook_send(self, params):
        """Send a new email. Requires user confirmation. Optionally attaches
        files (combined raw size up to ~3 MB; Graph's single-request ceiling
        is ~4 MB after base64)."""
        try:
            account = params["account"]
            to = params["to"]
            subject = params.get("subject", "")
            body = params.get("body", "")
            body_html = params.get("body_html", "")
            cc = params.get("cc", "")
            bcc = params.get("bcc", "")
            attachments = params.get("attachments") or []

            ok, attach_dicts, attach_summary = self._outlook_file_attachments(attachments)
            if not ok:
                return f"outlook_send failed: {attach_summary}"

            message = {
                "subject": subject,
                "body": self._outlook_body_field(body, body_html),
                "toRecipients": self._outlook_parse_recipients(to),
            }
            if cc:
                message["ccRecipients"] = self._outlook_parse_recipients(cc)
            if bcc:
                message["bccRecipients"] = self._outlook_parse_recipients(bcc)
            if attach_dicts:
                message["attachments"] = attach_dicts

            if not self._confirm_outlook_action(
                "outlook_send",
                "Confirm Outlook send",
                f"Send email from account '{account}' to: {to}",
                f"Subject: {subject}\nCc: {cc or '(none)'}\nBcc: {bcc or '(none)'}\n"
                f"Attachments: {attach_summary or '(none)'}\n"
                f"HTML body: {'yes' if body_html else 'no'}\n\n"
                f"{(body_html or body)[:500]}{'...' if len(body_html or body) > 500 else ''}",
            ):
                return "user denied: outlook_send not sent"

            self._outlook_graph(
                account, "POST", "/me/sendMail",
                json_body={"message": message, "saveToSentItems": True},
            )
            return json.dumps({
                "account": account, "to": to, "subject": subject,
                "attachments": attach_summary or None,
                "html": bool(body_html), "status": "sent",
            }, indent=2)
        except Exception as e:
            return f"outlook_send failed: {type(e).__name__}: {e}"

    def do_outlook_reply(self, params):
        """Reply to a message with proper Outlook threading. Requires
        confirmation. Uses Graph createReply (which sets conversationId and
        reply headers), replaces the draft body with the supplied content,
        optionally overrides recipients and adds attachments, then sends. On
        denial the server-side draft is deleted so nothing is left behind."""
        try:
            account = params["account"]
            message_id = params["message_id"]
            body = params.get("body", "")
            body_html = params.get("body_html", "")
            attachments = params.get("attachments") or []
            cc = params.get("cc", "")
            bcc = params.get("bcc", "")
            override_to = params.get("to", "")

            ok, attach_dicts, attach_summary = self._outlook_file_attachments(attachments)
            if not ok:
                return f"outlook_reply failed: {attach_summary}"

            # createReply returns a draft pre-addressed to the original sender,
            # threaded into the same conversation.
            draft = self._outlook_graph(
                account, "POST", f"/me/messages/{message_id}/createReply",
            )
            draft_id = draft.get("id")
            if not draft_id:
                return "outlook_reply failed: createReply returned no draft id"
            subject = draft.get("subject", "")
            reply_to = override_to or self._outlook_recip_list(draft.get("toRecipients"))

            # Replace the draft body with our content (parity with gmail_reply,
            # which sends only the new body — no quoted original).
            patch = {"body": self._outlook_body_field(body, body_html)}
            if override_to:
                patch["toRecipients"] = self._outlook_parse_recipients(override_to)
            if cc:
                patch["ccRecipients"] = self._outlook_parse_recipients(cc)
            if bcc:
                patch["bccRecipients"] = self._outlook_parse_recipients(bcc)
            self._outlook_graph(account, "PATCH", f"/me/messages/{draft_id}",
                                json_body=patch)
            for a in attach_dicts:
                self._outlook_graph(
                    account, "POST", f"/me/messages/{draft_id}/attachments",
                    json_body=a,
                )

            if not self._confirm_outlook_action(
                "outlook_reply",
                "Confirm Outlook reply",
                f"Reply from account '{account}' to: {reply_to}",
                f"Subject: {subject}\nCc: {cc or '(none)'}\nBcc: {bcc or '(none)'}\n"
                f"Attachments: {attach_summary or '(none)'}\n"
                f"HTML body: {'yes' if body_html else 'no'}\n\n"
                f"{(body_html or body)[:500]}{'...' if len(body_html or body) > 500 else ''}",
            ):
                # Clean up the orphaned draft so denial leaves no residue.
                try:
                    self._outlook_graph(account, "DELETE", f"/me/messages/{draft_id}")
                except Exception:
                    pass
                return "user denied: outlook_reply not sent"

            self._outlook_graph(account, "POST", f"/me/messages/{draft_id}/send")
            return json.dumps({
                "account": account, "replied_to_message_id": message_id,
                "to": reply_to, "subject": subject,
                "attachments": attach_summary or None,
                "html": bool(body_html), "status": "sent",
            }, indent=2)
        except Exception as e:
            return f"outlook_reply failed: {type(e).__name__}: {e}"

    def do_outlook_create_draft(self, params):
        """Create a draft (no confirmation — drafts aren't destructive).
        Optionally attaches files."""
        try:
            account = params["account"]
            attachments = params.get("attachments") or []
            body = params.get("body", "")
            body_html = params.get("body_html", "")

            ok, attach_dicts, attach_summary = self._outlook_file_attachments(attachments)
            if not ok:
                return f"outlook_create_draft failed: {attach_summary}"

            message = {
                "subject": params.get("subject", ""),
                "body": self._outlook_body_field(body, body_html),
                "toRecipients": self._outlook_parse_recipients(params.get("to", "")),
            }
            if params.get("cc"):
                message["ccRecipients"] = self._outlook_parse_recipients(params["cc"])
            if params.get("bcc"):
                message["bccRecipients"] = self._outlook_parse_recipients(params["bcc"])
            if attach_dicts:
                message["attachments"] = attach_dicts

            # POST to /me/messages creates the message as a draft in Drafts.
            draft = self._outlook_graph(account, "POST", "/me/messages",
                                        json_body=message)
            return json.dumps({
                "account": account, "draft_id": draft.get("id"),
                "subject": draft.get("subject", ""),
                "attachments": attach_summary or None,
                "html": bool(body_html),
            }, indent=2)
        except Exception as e:
            return f"outlook_create_draft failed: {type(e).__name__}: {e}"

    def do_outlook_list_drafts(self, params):
        try:
            account = params["account"]
            max_results = min(int(params.get("max_results", 25)), 100)
            resp = self._outlook_graph(
                account, "GET", "/me/mailFolders/drafts/messages",
                params={
                    "$top": max_results,
                    "$select": "id,toRecipients,subject,bodyPreview",
                    "$orderby": "lastModifiedDateTime desc",
                },
            )
            drafts = [{
                "draft_id": m.get("id"),
                "to": self._outlook_recip_list(m.get("toRecipients")),
                "subject": m.get("subject", ""),
                "snippet": (m.get("bodyPreview", "") or "")[:200],
            } for m in resp.get("value", [])]
            return json.dumps({
                "account": account, "count": len(drafts), "drafts": drafts,
            }, indent=2, ensure_ascii=False)
        except Exception as e:
            return f"outlook_list_drafts failed: {type(e).__name__}: {e}"

    def do_outlook_send_draft(self, params):
        """Send an existing draft by message ID. Requires user confirmation.
        (In Graph a draft IS a message, so pass its message id — what
        outlook_create_draft / outlook_list_drafts return as draft_id.)"""
        try:
            account = params["account"]
            draft_id = params.get("draft_id") or params["message_id"]
            # Fetch the draft for the confirmation summary.
            draft = self._outlook_graph(
                account, "GET", f"/me/messages/{draft_id}",
                params={"$select": "toRecipients,subject"},
            )
            to = self._outlook_recip_list(draft.get("toRecipients"))
            subject = draft.get("subject", "")
            if not self._confirm_outlook_action(
                "outlook_send_draft",
                "Confirm Outlook send draft",
                f"Send draft {draft_id} from account '{account}' to: {to}",
                f"Subject: {subject}",
            ):
                return "user denied: outlook_send_draft not sent"
            self._outlook_graph(account, "POST", f"/me/messages/{draft_id}/send")
            return json.dumps({
                "account": account, "draft_id": draft_id,
                "to": to, "subject": subject, "status": "sent",
            }, indent=2)
        except Exception as e:
            return f"outlook_send_draft failed: {type(e).__name__}: {e}"

    def do_outlook_trash(self, params):
        """Move one or more messages to Deleted Items (soft delete; recoverable
        from Outlook's UI). Requires confirmation. Each move yields a NEW
        message id in the destination folder, returned in moved_ids."""
        try:
            account = params["account"]
            message_ids = params["message_ids"]
            if isinstance(message_ids, str):
                message_ids = [message_ids]
            if not message_ids:
                return "outlook_trash: no message_ids provided"
            if not self._confirm_outlook_action(
                "outlook_trash",
                "Confirm Outlook trash",
                f"Move {len(message_ids)} message(s) to Deleted Items on account '{account}'",
                f"Message IDs: {', '.join(str(m) for m in message_ids[:10])}"
                + (f" (+{len(message_ids) - 10} more)" if len(message_ids) > 10 else ""),
            ):
                return "user denied: outlook_trash skipped"
            moved = []
            for mid in message_ids:
                res = self._outlook_graph(
                    account, "POST", f"/me/messages/{mid}/move",
                    json_body={"destinationId": "deleteditems"},
                )
                moved.append(res.get("id", mid))
            return json.dumps({
                "account": account, "trashed_count": len(moved),
                "moved_ids": moved,
            }, indent=2)
        except Exception as e:
            return f"outlook_trash failed: {type(e).__name__}: {e}"

    def do_outlook_untrash(self, params):
        """Restore one or more messages from Deleted Items back to the Inbox.
        Pass the message IDs as they exist in Deleted Items (e.g. the moved_ids
        returned by outlook_trash). Each restore yields a new id in the Inbox."""
        try:
            account = params["account"]
            message_ids = params["message_ids"]
            if isinstance(message_ids, str):
                message_ids = [message_ids]
            restored = []
            for mid in message_ids:
                res = self._outlook_graph(
                    account, "POST", f"/me/messages/{mid}/move",
                    json_body={"destinationId": "inbox"},
                )
                restored.append(res.get("id", mid))
            return json.dumps({
                "account": account, "untrashed_count": len(restored),
                "moved_ids": restored,
            }, indent=2)
        except Exception as e:
            return f"outlook_untrash failed: {type(e).__name__}: {e}"

    def do_outlook_list_labels(self, params):
        """List Outlook categories (the closest analogue to Gmail labels).
        Returns id, name (displayName), and color preset for each."""
        try:
            account = params["account"]
            resp = self._outlook_graph(account, "GET", "/me/outlook/masterCategories")
            labels = [
                {"id": c.get("id"), "name": c.get("displayName"),
                 "color": c.get("color", "")}
                for c in resp.get("value", [])
            ]
            return json.dumps({
                "account": account, "count": len(labels), "labels": labels,
            }, indent=2, ensure_ascii=False)
        except Exception as e:
            return f"outlook_list_labels failed: {type(e).__name__}: {e}"

    def do_outlook_create_label(self, params):
        """Create a new Outlook category. Non-destructive — no confirmation.
        ``color`` is a preset string preset0..preset24 (or 'none'); defaults
        to preset0. Errors if a category with this name already exists."""
        try:
            account = params["account"]
            name = params["name"]
            color = params.get("color", "preset0")
            cat = self._outlook_graph(
                account, "POST", "/me/outlook/masterCategories",
                json_body={"displayName": name, "color": color},
            )
            return json.dumps({
                "account": account, "label_id": cat.get("id"),
                "name": cat.get("displayName"), "color": cat.get("color", ""),
            }, indent=2, ensure_ascii=False)
        except Exception as e:
            return f"outlook_create_label failed: {type(e).__name__}: {e}"

    def do_outlook_delete_label(self, params):
        """Delete an Outlook category by ID. DESTRUCTIVE — requires
        confirmation. Note: deleting a master category does not strip it from
        messages that already carry the name; it just removes it from the
        master list (so it no longer shows as a colored tag)."""
        try:
            account = params["account"]
            label_id = params["label_id"]
            # Look up the name for the confirmation dialog.
            name = "(unknown)"
            try:
                resp = self._outlook_graph(account, "GET", "/me/outlook/masterCategories")
                for c in resp.get("value", []):
                    if c.get("id") == label_id:
                        name = c.get("displayName", "(unknown)")
                        break
            except Exception:
                pass
            if not self._confirm_outlook_action(
                "outlook_delete_label",
                "Confirm Outlook delete category",
                f"Delete category '{name}' (id={label_id}) from account '{account}'",
                "This removes the category from the master list. Messages that "
                "already carry the category NAME keep it as plain text until "
                "you clear it via outlook_modify_labels.",
            ):
                return "user denied: outlook_delete_label skipped"
            self._outlook_graph(account, "DELETE", f"/me/outlook/masterCategories/{label_id}")
            return json.dumps({
                "account": account, "deleted_label_id": label_id,
                "deleted_label_name": name,
            }, indent=2)
        except Exception as e:
            return f"outlook_delete_label failed: {type(e).__name__}: {e}"

    def do_outlook_modify_labels(self, params):
        """Add and/or remove categories on one or more messages. IMPORTANT:
        Outlook stores categories on a message by display NAME (not id), so
        add_labels / remove_labels are category names (from outlook_list_labels'
        ``name`` field). Reads each message's current categories, applies the
        diff, and PATCHes the result."""
        try:
            account = params["account"]
            message_ids = params["message_ids"]
            if isinstance(message_ids, str):
                message_ids = [message_ids]
            add_labels = params.get("add_labels", []) or []
            remove_labels = params.get("remove_labels", []) or []
            if isinstance(add_labels, str):
                add_labels = [add_labels]
            if isinstance(remove_labels, str):
                remove_labels = [remove_labels]
            remove_set = set(remove_labels)
            modified = []
            for mid in message_ids:
                cur = self._outlook_graph(
                    account, "GET", f"/me/messages/{mid}",
                    params={"$select": "categories"},
                )
                categories = [c for c in (cur.get("categories", []) or [])
                              if c not in remove_set]
                for a in add_labels:
                    if a not in categories:
                        categories.append(a)
                self._outlook_graph(
                    account, "PATCH", f"/me/messages/{mid}",
                    json_body={"categories": categories},
                )
                modified.append(mid)
            return json.dumps({
                "account": account, "modified_count": len(modified),
                "modified_ids": modified, "add": add_labels, "remove": remove_labels,
            }, indent=2)
        except Exception as e:
            return f"outlook_modify_labels failed: {type(e).__name__}: {e}"

    def do_outlook_mark_read(self, params):
        """Mark one or more messages read (read=true) or unread (read=false)."""
        try:
            account = params["account"]
            message_ids = params["message_ids"]
            if isinstance(message_ids, str):
                message_ids = [message_ids]
            read = bool(params.get("read", True))
            updated = []
            for mid in message_ids:
                self._outlook_graph(
                    account, "PATCH", f"/me/messages/{mid}",
                    json_body={"isRead": read},
                )
                updated.append(mid)
            return json.dumps({
                "account": account,
                "marked_read" if read else "marked_unread": len(updated),
                "message_ids": updated,
            }, indent=2)
        except Exception as e:
            return f"outlook_mark_read failed: {type(e).__name__}: {e}"

    def do_outlook_list_threads(self, params):
        """List conversations (Outlook's thread equivalent) matching a query.
        Messages are grouped by conversationId; the newest message in each
        conversation on the fetched page represents it. Returns conversation_id,
        subject, date, from, snippet."""
        try:
            account = params["account"]
            q = (params.get("q") or "").strip()
            max_results = min(int(params.get("max_results", 25)), 250)
            # Over-fetch a little so grouping still yields ~max_results threads.
            req_params = {
                "$top": min(max_results * 3, 250),
                "$select": self._OUTLOOK_LIST_SELECT,
            }
            if q:
                req_params["$search"] = f'"{q}"'
            else:
                req_params["$orderby"] = "receivedDateTime desc"
            resp = self._outlook_graph(account, "GET", "/me/messages",
                                       params=req_params)
            seen = {}
            order = []
            for m in resp.get("value", []):
                cid = m.get("conversationId")
                if cid and cid not in seen:
                    seen[cid] = {
                        "conversation_id": cid,
                        "subject": m.get("subject", ""),
                        "date": m.get("receivedDateTime", ""),
                        "from": self._outlook_addr(m.get("from") or m.get("sender")),
                        "snippet": (m.get("bodyPreview", "") or "")[:200],
                    }
                    order.append(cid)
                if len(order) >= max_results:
                    break
            threads = [seen[c] for c in order]
            return json.dumps({
                "account": account, "query": q,
                "count": len(threads), "threads": threads,
            }, indent=2, ensure_ascii=False)
        except Exception as e:
            return f"outlook_list_threads failed: {type(e).__name__}: {e}"
