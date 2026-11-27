"""Regression tests for the 2026-09-28 review fixes in the zero-token jobs,
UnreadSummary.py and Heartbeat.py.

No mailbox, network, store or viewer is touched: every module global that
names a real place (LOG_FILE, DIGEST_FILE, the three ~/.config dirs,
DOWNLOAD_DIR, _SPECS, INSTRUCTIONS_FILE) is repointed at a temp dir or a
synthetic value, and every provider call is a fake. The one real process is
Heartbeat's Windows watchdog test, which spawns a sleeping Python whose
command line looks like a headless run and kills it by PID."""

import base64
import email
import imaplib
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import Heartbeat
import UnreadSummary as US
from tests.test_unread_delivery import FakeGmail


def _patch(test, target, name, value):
    p = mock.patch.object(target, name, value)
    p.start()
    test.addCleanup(p.stop)


class _TempLog(unittest.TestCase):
    """Repoints both jobs' log files at a temp dir."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.log_file = self.root / "review_jobs.log"
        _patch(self, US, "LOG_FILE", self.log_file)
        _patch(self, Heartbeat, "LOG_FILE", self.log_file)

    def log_text(self):
        return self.log_file.read_text(encoding="utf-8") if self.log_file.exists() else ""


# ── UnreadSummary: headers ────────────────────────────────────────────────

RAW_8BIT = (b"From: \xc3\x89milie <emilie@example.com>\r\n"
            b"To: me@example.com\r\n"
            b"Subject: Hello\r\n"
            b"Date: \xd0\x9f\xd0\xbd, 28 Sep 2026 07:00:00 +1000\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n\r\nBody text here.\r\n")


class _CollectConn:
    """Just enough of an IMAP connection for imap_collect."""

    def __init__(self, raw):
        self.raw = raw

    def list(self):
        return "OK", [b'(\\HasNoChildren) "." INBOX']

    def select(self, mailbox, readonly=False):
        return "OK", [b"1"]

    def uid(self, command, *args):
        if command == "search":
            return "OK", [b"7"]
        return "OK", [(b"7 (UID 7 BODY[] {1})", self.raw)]


class HeaderDecodingTests(_TempLog):
    def test_8bit_date_no_longer_kills_the_digest(self):
        # compat32 hands a raw 8-bit header back as an email.header.Header;
        # the Date was the one header stored undecoded, and textwrap on it
        # raised — no digest, exit 1, every pass until the message was gone.
        self.assertIsInstance(email.message_from_bytes(RAW_8BIT).get("Date"),
                              email.header.Header)
        entries = US.imap_collect("acct", {"email": "me@example.com"},
                                  _CollectConn(RAW_8BIT))
        self.assertEqual(len(entries), 1)
        self.assertIsInstance(entries[0]["date"], str)
        self.assertEqual(entries[0]["date"], "Пн, 28 Sep 2026 07:00:00 +1000")
        for width in (None, US.DIGEST_FILE_WIDTH):
            body = US.build_body([("acct", "me@example.com (IMAP)")],
                                 {"acct": entries}, {}, False, width=width)
            self.assertIn("Пн, 28 Sep 2026", body)

    def test_raw_utf8_headers_read_as_utf8_not_replacement_chars(self):
        msg = email.message_from_bytes(RAW_8BIT)
        self.assertEqual(US.decode_header(msg.get("From")),
                         "Émilie <emilie@example.com>")

    def test_undecodable_8bit_falls_back_to_latin1(self):
        self.assertEqual(US._decode_header_bytes(b"Caf\xe9", "unknown-8bit"), "Café")
        self.assertEqual(US._decode_header_bytes(b"Caf\xe9", "no-such-codec"), "Café")
        self.assertEqual(US._decode_header_bytes("Café".encode("iso-8859-1"), "iso-8859-1"),
                         "Café")

    def test_encoded_words_still_decode(self):
        self.assertEqual(US.decode_header("=?utf-8?B?w4ltaWxpZQ==?= <e@example.com>"),
                         "Émilie <e@example.com>")


# ── UnreadSummary: Gmail body charset ────────────────────────────────────

class GmailCharsetTests(unittest.TestCase):
    @staticmethod
    def _payload(data, content_type):
        return {"mimeType": "text/plain",
                "headers": [{"name": "Content-Type", "value": content_type}],
                "body": {"data": base64.urlsafe_b64encode(data).decode()}}

    def test_declared_charset_is_honoured(self):
        latin = "Café résumé naïve".encode("iso-8859-1")
        self.assertEqual(US._gmail_body_text(
            self._payload(latin, 'text/plain; charset="ISO-8859-1"')), "Café résumé naïve")

    def test_unknown_charset_falls_back_to_utf8(self):
        self.assertEqual(US._gmail_body_text(
            self._payload("Café".encode(), "text/plain; charset=x-nonsense")), "Café")

    def test_no_charset_means_utf8(self):
        self.assertEqual(US._gmail_part_charset({"headers": []}), "utf-8")


# ── UnreadSummary: IMAP move / mark / STARTTLS ───────────────────────────

BAD = imaplib.IMAP4.error("UID command error: BAD [b'Unknown command']")


class _ActConn:
    def __init__(self, **replies):
        self.replies = replies
        self.calls = []

    def select(self, mailbox, readonly=False):
        self.calls.append(("select", mailbox, readonly))
        return self.replies.get("select", ("OK", [b"1"]))

    def uid(self, command, *args):
        self.calls.append((command,) + args)
        reply = self.replies.get(command, ("OK", [None]))
        if isinstance(reply, Exception):
            raise reply
        return reply

    def expunge(self):
        self.calls.append(("plain-expunge",))
        return "OK", [None]

    def commands(self):
        return [c[0] for c in self.calls]


class ImapMoveTests(unittest.TestCase):
    def test_move_ok(self):
        conn = _ActConn()
        self.assertEqual(US.imap_move(conn, "7", "Trash"), "moved to Trash")
        self.assertEqual(conn.commands(), ["move"])

    def test_refused_move_never_falls_back(self):
        conn = _ActConn(move=("NO", [b"[TRYCREATE] no such mailbox"]))
        self.assertIn("FAILED", US.imap_move(conn, "7", "Trash"))
        self.assertEqual(conn.commands(), ["move"])

    def test_no_move_server_copies_then_expunges_only_that_uid(self):
        conn = _ActConn(move=BAD)
        self.assertEqual(US.imap_move(conn, "7", "Deleted Items"), "moved to Deleted Items")
        self.assertEqual(conn.commands(), ["move", "copy", "store", "expunge"])
        self.assertEqual(conn.calls[1], ("copy", "7", '"Deleted Items"'))
        self.assertEqual(conn.calls[2], ("store", "7", "+FLAGS", r"(\Deleted)"))
        self.assertEqual(conn.calls[3], ("expunge", "7"))      # UID EXPUNGE 7

    def test_failed_copy_deletes_nothing(self):
        conn = _ActConn(move=BAD, copy=("NO", [b"over quota"]))
        self.assertIn("FAILED (COPY refused)", US.imap_move(conn, "7", "Trash"))
        self.assertEqual(conn.commands(), ["move", "copy"])

    def test_no_uidplus_never_runs_a_folder_wide_expunge(self):
        conn = _ActConn(move=BAD, expunge=BAD)
        result = US.imap_move(conn, "7", "Trash")
        self.assertIn("copied to Trash", result)
        self.assertIn("not expunged", result)
        self.assertNotIn("plain-expunge", conn.commands())

    def test_dead_connection_raises(self):
        conn = _ActConn(move=imaplib.IMAP4.abort("socket error"))
        with self.assertRaises(imaplib.IMAP4.abort):
            US.imap_move(conn, "7", "Trash")


class ImapMarkTests(unittest.TestCase):
    ENTRY = {"id": "7", "folder": "INBOX.Junk", "_trash": "INBOX.Trash"}

    def test_failed_select_touches_no_uid(self):
        conn = _ActConn(select=("NO", [b"no such folder"]))
        with mock.patch.object(US, "MARK_MATCHES_READ", True):
            actions = US.imap_mark(dict(self.ENTRY, _conn=conn))
        self.assertIn("FAILED", actions[0])
        self.assertEqual(conn.commands(), ["select"])

    def test_mark_read_is_a_read_write_select_then_seen(self):
        conn = _ActConn()
        with mock.patch.object(US, "MARK_MATCHES_READ", True), \
                mock.patch.object(US, "TRASH_MATCHES", False):
            self.assertEqual(US.imap_mark(dict(self.ENTRY, _conn=conn)), ["marked read"])
        self.assertEqual(conn.calls[0], ("select", "INBOX.Junk", False))
        self.assertEqual(conn.calls[1], ("store", "7", "+FLAGS", r"(\Seen)"))


class _TlsIMAP4:
    error = imaplib.IMAP4.error
    abort = imaplib.IMAP4.abort
    last = None

    def __init__(self, host, port):
        self.host, self.calls = host, []
        _TlsIMAP4.last = self

    def starttls(self, ssl_context=None):
        self.calls.append("starttls")
        raise imaplib.IMAP4.error("STARTTLS: quirky CAPABILITY")

    def login(self, user, password):
        self.calls.append("login")

    def shutdown(self):
        self.calls.append("shutdown")


class StartTlsTests(unittest.TestCase):
    def setUp(self):
        _patch(self, imaplib, "IMAP4", _TlsIMAP4)

    def test_bridge_on_loopback_still_logs_in(self):
        US.imap_connect({"imap_host": "127.0.0.1", "imap_port": 1143,
                         "username": "u", "app_password": "p"})
        self.assertEqual(_TlsIMAP4.last.calls, ["starttls", "login"])

    def test_remote_host_refuses_a_plaintext_login(self):
        with self.assertRaises(RuntimeError) as cm:
            US.imap_connect({"imap_host": "mail.example.com", "imap_port": 143,
                             "username": "u", "app_password": "p"})
        self.assertIn("refusing to send the password unencrypted", str(cm.exception))
        self.assertNotIn("login", _TlsIMAP4.last.calls)


# ── UnreadSummary: Outlook ───────────────────────────────────────────────

def _graph_msg(i):
    return {"id": f"m{i}", "subject": f"s{i}",
            "from": {"emailAddress": {"name": "n", "address": "a@b.example"}},
            "toRecipients": [], "receivedDateTime": "2026-09-28T00:00:00Z",
            "body": {"contentType": "text", "content": "x"}, "hasAttachments": False}


class OutlookPagingTests(unittest.TestCase):
    def test_every_page_is_collected(self):
        link = US.GRAPH_BASE + "/me/mailFolders/inbox/messages?$skip=100"
        calls = []

        def fake_graph(account, account_email, method, path, params=None, json_body=None):
            calls.append(path)
            if path == link:
                return {"value": [_graph_msg(100)]}
            if "inbox" in path:
                return {"value": [_graph_msg(i) for i in range(100)], "@odata.nextLink": link}
            return {"value": []}

        with mock.patch.object(US, "graph", fake_graph):
            entries = US.outlook_collect("outlook", "me@outlook.example")
        self.assertEqual(len(entries), 101)
        self.assertEqual(calls, ["/me/mailFolders/inbox/messages", link,
                                 "/me/mailFolders/junkemail/messages"])

    def test_a_repeating_link_ends_the_walk(self):
        link = US.GRAPH_BASE + "/me/next"

        def fake_graph(account, account_email, method, path, params=None, json_body=None):
            return {"value": [_graph_msg(1)], "@odata.nextLink": link}

        with mock.patch.object(US, "graph", fake_graph):
            items = list(US._graph_pages("a", "b", "/me/x", {}))
        self.assertEqual(len(items), 2)

    def test_graph_refuses_a_link_off_the_graph_host(self):
        with mock.patch.object(US, "outlook_token",
                               side_effect=AssertionError("token fetched")):
            with self.assertRaises(RuntimeError):
                US.graph("a", "b", "GET", "https://evil.example/v1.0/me/messages")
            with self.assertRaises(RuntimeError):
                US.graph("a", "b", "GET", "https://graph.microsoft.com.evil.example/x")

    def test_graph_follows_a_graph_link_verbatim(self):
        link = US.GRAPH_BASE + "/me/messages?$skip=5"
        seen = {}

        class _Resp:
            status_code, content = 200, b"{}"

            @staticmethod
            def json():
                return {"value": []}

        def fake_request(method, url, **kwargs):
            seen.update(url=url, params=kwargs.get("params"))
            return _Resp()

        with mock.patch.object(US, "outlook_token", return_value="tok"), \
                mock.patch.object(US.requests, "request", fake_request):
            US.graph("a", "b", "GET", link)
        self.assertEqual(seen, {"url": link, "params": None})


class MsalTimeoutTests(unittest.TestCase):
    def test_public_client_gets_a_timeout(self):
        made = {}

        class _App:
            def __init__(self, client_id, **kwargs):
                made.update(kwargs)

            def get_accounts(self, username=None):
                return []

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        _patch(self, US, "OUTLOOK_CONFIG_DIR", Path(tmp.name))
        _patch(self, US, "_outlook_client_config",
               lambda: ("cid", "https://login.microsoftonline.com/common"))
        _patch(self, US.msal, "PublicClientApplication", _App)
        with mock.patch.dict(US._OUTLOOK_APPS, {}, clear=True):
            with self.assertRaises(RuntimeError):     # no cached account: silent fails
                US.outlook_token("zz_review_acct", "x@example.com")
        self.assertEqual(made.get("timeout"), US.OUTLOOK_HTTP_TIMEOUT)
        self.assertTrue(US.OUTLOOK_HTTP_TIMEOUT)


# ── UnreadSummary: accounts.json + console ───────────────────────────────

class LoadAccountsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def _write(self, text):
        (self.dir / "accounts.json").write_text(text, encoding="utf-8")

    def test_missing_file_is_an_unconfigured_provider(self):
        self.assertEqual(US.load_accounts(self.dir), {})

    def test_malformed_file_raises_instead_of_vanishing(self):
        self._write('{"accounts": {"a": {"email": "x@y"},}}')     # trailing comma
        with self.assertRaises(US.AccountsFileError):
            US.load_accounts(self.dir)

    def test_non_object_entries_raise(self):
        self._write('{"accounts": {"a": "x@y"}}')
        with self.assertRaises(US.AccountsFileError):
            US.load_accounts(self.dir)
        self._write('{"accounts": ["a"]}')
        with self.assertRaises(US.AccountsFileError):
            US.load_accounts(self.dir)

    def test_good_file_loads(self):
        self._write('{"accounts": {"a": {"email": "x@y"}}}')
        self.assertEqual(US.load_accounts(self.dir), {"a": {"email": "x@y"}})


class ConsolePrintTests(unittest.TestCase):
    def test_emoji_on_a_cp1252_stream_prints_as_question_mark(self):
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="cp1252")
        US._console_print("\U0001f389 Sale", stream)
        stream.flush()
        self.assertEqual(raw.getvalue().replace(b"\r\n", b"\n"), b"? Sale\n")

    def test_stream_without_reconfigure_still_prints(self):
        out = io.StringIO()
        US._console_print("\U0001f389 Sale", out)
        self.assertEqual(out.getvalue(), "\U0001f389 Sale\n")


# ── UnreadSummary: main() ordering, slots, errors ────────────────────────

SPEC = {"n": 1, "name": "Biller / Your bill", "from_has": "biller",
        "subject_pre": "your bill", "determine": "Amount due", "type": "bill",
        "index": "7"}


def _entry(provider, account, subject, frm="Biller <bills@biller.example>"):
    return {"provider": provider, "account": account,
            "account_email": f"{account}@{provider.lower()}.example", "folder_tag": "",
            "id": f"{provider}-{account}-1", "folder": "INBOX", "from": frm, "to": "",
            "subject": subject, "date": "Mon, 28 Sep 2026 07:00:00 +1000",
            "lines": ["Opening words."]}


class MainTests(_TempLog):
    def setUp(self):
        super().setUp()
        self.addCleanup(socket.setdefaulttimeout, socket.getdefaulttimeout())
        self.dirs = {}
        for provider, attr in (("Gmail", "GOOGLE_CONFIG_DIR"), ("IMAP", "PROTON_CONFIG_DIR"),
                               ("Outlook", "OUTLOOK_CONFIG_DIR")):
            d = self.root / f"cfg_{provider}"
            d.mkdir()
            self.dirs[provider] = d
            _patch(self, US, attr, d)
        _patch(self, US, "DIGEST_FILE", self.root / "review_digest.txt")
        # The notes of texts not to quote again: never this machine's own.
        self.notes_file = self.root / "review_unquoted.json"
        _patch(self, US, "UNQUOTED_FILE", self.notes_file)
        _patch(self, US, "DOWNLOAD_DIR", self.root / "review_attachments")
        _patch(self, US, "_SPECS", [SPEC])
        _patch(self, US, "MARK_MATCHES_READ", True)
        _patch(self, US, "SAVE_MATCH_PDFS", True)
        _patch(self, US, "TRASH_MATCHES", False)
        self.events = []
        self.sent = []
        self.shown = []             # what went to the digest file, per pass
        self.collected = {"Gmail": {}, "IMAP": {}, "Outlook": {}}
        _patch(self, US, "gmail_collect",
               lambda account, addr: self.collected["Gmail"][account])
        _patch(self, US, "outlook_collect",
               lambda account, addr: self.collected["Outlook"][account])
        _patch(self, US, "imap_collect",
               lambda account, cfg, conn: self.collected["IMAP"][account])
        _patch(self, US, "imap_connect", lambda cfg: self._conn())
        for provider, save, mark in (("Gmail", "gmail_save_pdfs", "gmail_mark"),
                                     ("IMAP", "imap_save_pdfs", "imap_mark"),
                                     ("Outlook", "outlook_save_pdfs", "outlook_mark")):
            _patch(self, US, save, self._recorder(f"save:{provider}", ["bill.pdf"]))
            _patch(self, US, mark, self._recorder(f"mark:{provider}", ["marked read"]))
        _patch(self, US, "show_digest", self._show)
        _patch(self, US, "outlook_send", self._send)
        _patch(self, US, "smtp_send", self._smtp_send)
        _patch(self, US, "SEND_FALLBACK_IMAP_ACCOUNT", "fallback")
        self.send_error = None
        self.smtp_error = None

    def _recorder(self, label, result):
        def record(entry):
            self.events.append(label)
            return result
        return record

    def _conn(self):
        test = self

        class _Conn:
            def logout(self):
                test.events.append("logout")
        return _Conn()

    def _show(self, body, view=True):
        self.events.append("digest")
        self.shown.append(body)
        return "digest written (test)"

    def _send(self, account, account_email, subject, body):
        self.events.append("send")
        if self.send_error:
            raise self.send_error
        self.sent.append(body)

    def _smtp_send(self, account, cfg, subject, body):
        self.events.append(f"smtp:{account}")
        if self.smtp_error:
            raise self.smtp_error
        self.sent.append(body)

    def accounts(self, provider, names):
        (self.dirs[provider] / "accounts.json").write_text(
            json.dumps({"accounts": {n: {"email": f"{n}@{provider.lower()}.example"}
                                     for n in names}}), encoding="utf-8")

    def test_marks_run_only_after_the_send_and_pdfs_reach_the_digest(self):
        self.accounts("Gmail", ["main"])
        self.collected["Gmail"]["main"] = [_entry("Gmail", "main", "Your bill is ready")]
        US.main(["--no-view"])
        self.assertEqual(self.events, ["save:Gmail", "digest", "send", "mark:Gmail"])
        self.assertIn("PDFs:", self.sent[0])
        self.assertIn("bill.pdf", self.sent[0])
        self.assertIn("acted on match in main: marked read; pdfs: bill.pdf", self.log_text())

    def test_a_failed_send_changes_no_mailbox(self):
        self.accounts("Gmail", ["main"])
        self.collected["Gmail"]["main"] = [_entry("Gmail", "main", "Your bill is ready")]
        self.send_error = RuntimeError("Graph 503 ServiceUnavailable")
        with self.assertRaises(RuntimeError):
            US.main(["--no-view"])
        self.assertEqual(self.events, ["save:Gmail", "digest", "send"])
        self.assertNotIn("acted on match", self.log_text())

    def test_a_failed_outlook_send_falls_back_to_the_imap_account(self):
        self.accounts("Gmail", ["main"])
        self.accounts("IMAP", ["fallback"])
        self.collected["Gmail"]["main"] = [_entry("Gmail", "main", "Your bill is ready")]
        self.collected["IMAP"]["fallback"] = []
        self.send_error = RuntimeError("Graph 504 UnknownError")
        US.main(["--no-view"])
        self.assertEqual(self.events,
                         ["save:Gmail", "digest", "send", "smtp:fallback", "mark:Gmail", "logout"])
        self.assertEqual(len(self.sent), 1)
        log = self.log_text()
        self.assertIn("falling back to fallback", log)
        self.assertIn("from fallback@imap.example", log)

    def test_both_senders_failing_changes_no_mailbox(self):
        self.accounts("Gmail", ["main"])
        self.accounts("IMAP", ["fallback"])
        self.collected["Gmail"]["main"] = [_entry("Gmail", "main", "Your bill is ready")]
        self.collected["IMAP"]["fallback"] = []
        self.send_error = RuntimeError("Graph 504 UnknownError")
        self.smtp_error = OSError("connection refused")
        with self.assertRaises(RuntimeError) as cm:
            US.main(["--no-view"])
        self.assertIn("Graph 504", str(cm.exception))
        self.assertIn("connection refused", str(cm.exception))
        self.assertNotIn("mark:Gmail", self.events)
        self.assertNotIn("acted on match", self.log_text())

    def test_no_fallback_account_reraises_the_outlook_error(self):
        self.accounts("Gmail", ["main"])
        self.collected["Gmail"]["main"] = []
        _patch(self, US, "SEND_FALLBACK_IMAP_ACCOUNT", None)
        self.send_error = RuntimeError("Graph 503 ServiceUnavailable")
        with self.assertRaisesRegex(RuntimeError, "^Graph 503"):
            US.main(["--no-view"])
        self.assertNotIn("smtp:fallback", self.events)

    def test_imap_connections_stay_open_until_the_marks_ran(self):
        self.accounts("IMAP", ["webcentral"])
        self.collected["IMAP"]["webcentral"] = [_entry("IMAP", "webcentral", "Your bill")]
        US.main(["--no-view"])
        self.assertEqual(self.events, ["save:IMAP", "digest", "send", "mark:IMAP", "logout"])

    def test_imap_connections_close_when_the_send_fails(self):
        self.accounts("IMAP", ["webcentral"])
        self.collected["IMAP"]["webcentral"] = [_entry("IMAP", "webcentral", "Your bill")]
        self.send_error = RuntimeError("boom")
        with self.assertRaises(RuntimeError):
            US.main(["--no-view"])
        self.assertEqual(self.events[-1], "logout")
        self.assertNotIn("mark:IMAP", self.events)

    def test_dry_run_saves_marks_and_sends_nothing(self):
        self.accounts("Gmail", ["main"])
        self.collected["Gmail"]["main"] = [_entry("Gmail", "main", "Your bill \U0001f389")]
        out = io.StringIO()
        with mock.patch.object(sys, "stdout", out):
            US.main(["--dry-run", "--no-view"])
        self.assertEqual(self.events, ["digest"])
        self.assertIn("Subject: Summary of Unread Emails", out.getvalue())
        self.assertIn("DRY RUN — 1 unread, 1 specifying match(es)", self.log_text())

    def test_the_same_key_in_two_providers_stays_two_accounts(self):
        self.accounts("Gmail", ["main"])
        self.accounts("Outlook", ["main"])
        self.collected["Gmail"]["main"] = [_entry("Gmail", "main", "From Gmail", frm="a@x")]
        self.collected["Outlook"]["main"] = [_entry("Outlook", "main", "From Outlook", frm="b@x")]
        US.main(["--no-view"])
        body = self.sent[0]
        self.assertIn("TOTAL: 2 unread email(s) across 2 account(s)", body)
        self.assertEqual(body.count("From Gmail"), 1)
        self.assertEqual(body.count("From Outlook"), 1)
        self.assertLess(body.index("main@gmail.example (Gmail)"), body.index("From Gmail"))
        self.assertLess(body.index("main@outlook.example (Outlook)"), body.index("From Outlook"))

    def test_a_malformed_accounts_file_is_an_error_block_and_a_log_line(self):
        self.accounts("Gmail", ["main"])
        self.collected["Gmail"]["main"] = [_entry("Gmail", "main", "Hello", frm="a@x")]
        (self.dirs["IMAP"] / "accounts.json").write_text(
            '{"accounts": {"a": {},}}', encoding="utf-8")
        US.main(["--no-view"])
        body = self.sent[0]
        self.assertIn("ERRORS: 1 account(s) unreadable", body)
        self.assertIn(f"Account: {self.dirs['IMAP'] / 'accounts.json'} (IMAP)", body)
        self.assertIn("ERROR: accounts.json unreadable, its accounts were skipped", body)
        self.assertIn("ACCOUNT ERROR IMAP accounts.json: accounts.json unreadable",
                      self.log_text())
        self.assertIn("Hello", body)                        # Gmail still listed

    # The delivery check (2026-11-27): the functions themselves are pinned in
    # tests/test_unread_delivery.py; these pin where main() calls them.

    def _delivery(self, outcome="arrived after 4s", error=None):
        """Route main()'s two delivery calls through recorders."""
        self.watched = []
        self.ensured = []

        def watch(gmail_accounts):
            self.events.append("watch")
            self.watched.append(dict(gmail_accounts))
            return {"service": "the service", "known": set()}

        def ensure(watch, sender, subject, body, second=None):
            self.events.append("ensure")
            self.ensured.append((watch, sender, subject, body))
            self.seconds.append(second)
            if error:
                raise error
            return outcome
        self.seconds = []
        _patch(self, US, "delivery_watch", watch)
        _patch(self, US, "ensure_delivered", ensure)

    def test_the_watch_begins_before_the_send_and_the_marks_wait_for_delivery(self):
        self._delivery()
        self.accounts("Gmail", ["main"])
        self.collected["Gmail"]["main"] = [_entry("Gmail", "main", "Your bill is ready")]
        US.main(["--no-view"])
        self.assertEqual(self.events,
                         ["save:Gmail", "digest", "watch", "send", "ensure", "mark:Gmail"])
        self.assertEqual(self.watched, [{"main": {"email": "main@gmail.example"}}])
        watch, sender, subject, body = self.ensured[0]
        self.assertEqual(watch, {"service": "the service", "known": set()})
        self.assertEqual(sender, US.SEND_FROM_OUTLOOK_ACCOUNT)   # no Outlook account file
        self.assertRegex(subject, r"^Summary of Unread Emails - \d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")
        self.assertEqual(body, self.sent[0])                     # what was emailed, whole
        # One line, the delivery outcome its tail.
        self.assertRegex(self.log_text(),
                         r"sent summary to \S+ from outlook: 1 unread across 1 accounts, "
                         r"1 specifying match\(es\), 0 account error\(s\); arrived after 4s\n")

    def test_an_undelivered_digest_changes_no_mailbox(self):
        self._delivery(error=RuntimeError("NOT arrived after 30s, and placing it failed"))
        self.accounts("Gmail", ["main"])
        self.accounts("IMAP", ["webcentral"])
        self.collected["Gmail"]["main"] = [_entry("Gmail", "main", "Your bill is ready")]
        self.collected["IMAP"]["webcentral"] = [_entry("IMAP", "webcentral", "Your bill")]
        with self.assertRaisesRegex(RuntimeError, "NOT arrived after 30s"):
            US.main(["--no-view"])
        self.assertEqual(self.events, ["save:Gmail", "save:IMAP", "digest", "watch", "send",
                                       "ensure", "logout"])
        log = self.log_text()
        self.assertNotIn("sent summary", log)
        self.assertNotIn("acted on match", log)

    def test_the_sender_that_worked_is_the_one_checked(self):
        self._delivery()
        self.accounts("Gmail", ["main"])
        self.accounts("IMAP", ["fallback"])
        self.collected["Gmail"]["main"] = []
        self.collected["IMAP"]["fallback"] = []
        self.send_error = RuntimeError("Graph 504 UnknownError")
        US.main(["--no-view"])
        self.assertEqual(self.events, ["digest", "watch", "send", "smtp:fallback", "ensure",
                                       "logout"])
        self.assertEqual(self.ensured[0][1], "fallback@imap.example")
        self.assertIn("from fallback@imap.example: 0 unread across 2 accounts, "
                      "0 specifying match(es), 0 account error(s); arrived after 4s",
                      self.log_text())

    def test_a_failed_send_is_never_checked_for_arrival(self):
        self._delivery()
        self.accounts("Gmail", ["main"])
        self.collected["Gmail"]["main"] = []
        _patch(self, US, "SEND_FALLBACK_IMAP_ACCOUNT", None)
        self.send_error = RuntimeError("Graph 503 ServiceUnavailable")
        with self.assertRaises(RuntimeError):
            US.main(["--no-view"])
        self.assertEqual(self.events, ["digest", "watch", "send"])

    def test_a_dry_run_neither_watches_nor_checks(self):
        self._delivery()
        self.accounts("Gmail", ["main"])
        self.collected["Gmail"]["main"] = []
        with mock.patch.object(sys, "stdout", io.StringIO()):
            US.main(["--dry-run", "--no-view"])
        self.assertEqual(self.events, ["digest"])

    def test_a_recipient_outside_the_scanned_accounts_is_sent_unchecked(self):
        # The real functions, no recorders: SEND_TO is not one of the accounts.
        opened = []
        _patch(self, US, "gmail_service", opened.append)
        self.accounts("Gmail", ["main"])
        self.collected["Gmail"]["main"] = [_entry("Gmail", "main", "Your bill is ready")]
        US.main(["--no-view"])
        self.assertEqual(opened, [])
        self.assertEqual(self.events, ["save:Gmail", "digest", "send", "mark:Gmail"])
        self.assertIn(f"0 account error(s); arrival not checked ({US.SEND_TO} is not a "
                      f"Gmail account this script reads)", self.log_text())

    def _real_delivery(self, delivers):
        """The REAL delivery functions over an in-memory recipient mailbox:
        SEND_TO is the scanned Gmail account, and the faked Outlook send
        either puts the digest in that mailbox or, like 2026-11-27's six,
        is accepted and goes nowhere."""
        gmail = FakeGmail({"id": "old1", "subject": "Your parcel", "labels": ["INBOX"]})
        _patch(self, US, "SEND_TO", "main@gmail.example")
        _patch(self, US, "gmail_service", lambda account: gmail)
        _patch(self, US, "DELIVERY_WAIT_SECONDS", 0)      # one look, no sleeping

        def send(account, account_email, subject, body):
            self._send(account, account_email, subject, body)
            if delivers:
                gmail.add(subject, delivers)
        _patch(self, US, "outlook_send", send)
        self.accounts("Gmail", ["main"])
        self.collected["Gmail"]["main"] = [_entry("Gmail", "main", "Your bill is ready")]
        return gmail

    def test_a_delivered_digest_is_seen_and_nothing_is_placed(self):
        gmail = self._real_delivery(delivers=("INBOX", "UNREAD"))
        US.main(["--no-view"])
        self.assertEqual(gmail.count("insert"), 0)
        self.assertEqual(self.events, ["save:Gmail", "digest", "send", "mark:Gmail"])
        self.assertIn("0 account error(s); arrived after 0s\n", self.log_text())

    def test_an_accepted_send_that_goes_nowhere_ends_with_the_digest_in_the_inbox(self):
        gmail = self._real_delivery(delivers=None)
        US.main(["--no-view"])
        placed = gmail.placed()
        self.assertEqual(len(placed), 1)
        self.assertEqual(gmail.mailbox[0]["labels"], ["INBOX", "UNREAD"])
        self.assertEqual(placed[0]["To"], "main@gmail.example")
        self.assertEqual(placed[0]["From"], US.SEND_FROM_OUTLOOK_ACCOUNT)
        text = placed[0].get_content()
        self.assertTrue(text.startswith("NOTE: placed in this mailbox directly"))
        self.assertIn(self.sent[0], text)                    # the emailed digest, whole
        self.assertIn("SPECIFYING LIST EMAIL", text)
        # Delivered after all, so the match is marked read as on any pass.
        self.assertEqual(self.events, ["save:Gmail", "digest", "send", "mark:Gmail"])
        log = self.log_text()
        self.assertIn("NOT arrived after 0s — placing it in the inbox directly", log)
        self.assertIn("0 account error(s); NOT arrived after 0s — placed in the inbox "
                      "directly\n", log)
        self.assertLess(log.index("placing it in the inbox"), log.index("sent summary to"))

    def test_a_digest_that_cannot_be_placed_either_marks_nothing(self):
        gmail = self._real_delivery(delivers=None)
        gmail.fail["insert"] = OSError("network down")
        with self.assertRaisesRegex(RuntimeError, "placing it in the inbox directly failed"):
            US.main(["--no-view"])
        self.assertNotIn("mark:Gmail", self.events)
        self.assertNotIn("sent summary", self.log_text())

    def test_a_digest_filed_under_spam_is_placed_in_the_inbox_too(self):
        gmail = self._real_delivery(delivers=("SPAM", "UNREAD"))
        US.main(["--no-view"])
        self.assertEqual(len(gmail.placed()), 1)
        self.assertIn("arrived in Spam after 0s — placed in the inbox directly\n",
                      self.log_text())
        self.assertIn("mark:Gmail", self.events)

    # The second copy (2026-11-27, the same evening): the functions are pinned
    # in tests/test_unread_second_copy.py; these pin where main() uses them.

    PITCH = "We can place your website on the first page. May I send you a quote?"

    def _spam_entry(self, text=None, tag="SPAM"):
        entry = _entry("IMAP", "webcentral", "Re: May I send you a quote?",
                       frm="Pitcher <p@pitch.example>")
        entry["folder_tag"] = tag
        entry["lines"] = [text or self.PITCH]
        return entry

    def test_a_pass_quoting_no_spam_text_offers_no_second_copy(self):
        self._delivery()
        self.accounts("Gmail", ["main"])
        self.collected["Gmail"]["main"] = [_entry("Gmail", "main", "Your bill is ready")]
        US.main(["--no-view"])
        self.assertEqual(self.seconds, [None])

    def test_a_pass_quoting_spam_text_has_the_second_copy_ready_and_unsent(self):
        self._delivery()
        self.accounts("Gmail", ["main"])
        self.accounts("IMAP", ["webcentral"])
        self.collected["Gmail"]["main"] = [_entry("Gmail", "main", "Your bill is ready")]
        self.collected["IMAP"]["webcentral"] = [self._spam_entry()]
        US.main(["--no-view"])
        how, send_again = self.seconds[0]
        self.assertEqual(how, "without the text of its 1 Spam/Junk-folder entry")
        self.assertEqual(len(self.sent), 1)                  # only the first copy went out
        self.assertIn(self.PITCH, " ".join(self.sent[0].split()))
        self.assertFalse(self.notes_file.exists())
        # Sending it goes through the same sender, under the same subject.
        self.assertEqual(send_again(), US.SEND_FROM_OUTLOOK_ACCOUNT)
        self.assertEqual(len(self.sent), 2)
        second = self.sent[1]
        self.assertTrue(second.startswith("NOTE: a second copy, sent without the text"))
        self.assertNotIn("We can place", second)
        self.assertIn("Opening words.", second)              # the inbox entry's text stays
        self.assertIn(f"   Summary: {US.NOT_QUOTED_HERE}", second.splitlines())
        # The same digest otherwise: its Generated line, TOTAL and numbering.
        for line in self.sent[0].splitlines():
            if line.startswith(("Generated ", "TOTAL: ", "1. ", "2. ", "   From:", "   Subject:")):
                self.assertIn(line, second.splitlines())

    def test_the_second_copy_is_the_same_digest_stamp_and_all(self):
        # A clock that moves at every look: the second copy, built later,
        # must still carry the first copy's Generated line — and it is a real
        # send, never a dry run's.
        class Ticking(datetime):
            ticks = 0

            @classmethod
            def now(cls, tz=None):
                cls.ticks += 1
                return datetime(2026, 11, 27, 21, 0, 0) + timedelta(seconds=cls.ticks)
        _patch(self, US, "datetime", Ticking)
        self._delivery()
        self.accounts("IMAP", ["webcentral"])
        self.collected["IMAP"]["webcentral"] = [self._spam_entry()]
        US.main(["--no-view"])
        self.seconds[0][1]()                               # the second copy, ticks later
        first, second = self.sent
        stamp = next(line for line in first.splitlines() if line.startswith("Generated "))
        self.assertIn(stamp, second.splitlines())
        self.assertNotIn("DRY RUN", second)
        self.assertNotIn("DRY RUN", first)

    def _sent_lines(self):
        """The log's "sent summary to" lines, one per completed pass."""
        return [line for line in self.log_text().splitlines() if "sent summary to" in line]

    def _filtered_route(self, drops=None):
        """The REAL delivery functions over an in-memory recipient mailbox,
        behind a route that — like 2026-11-27's — accepts every send and
        discards the ones quoting the campaign's text. Two entries are
        unread: a bill in the Gmail inbox and the pitch in an IMAP Spam
        folder."""
        drops = drops or (lambda body: "We can place" in body)
        gmail = FakeGmail({"id": "old1", "subject": "Your parcel", "labels": ["INBOX"]})
        _patch(self, US, "SEND_TO", "main@gmail.example")
        _patch(self, US, "gmail_service", lambda account: gmail)
        _patch(self, US, "DELIVERY_WAIT_SECONDS", 0)      # one look per copy, no sleeping

        def send(account, account_email, subject, body):
            self._send(account, account_email, subject, body)
            if not drops(body):
                gmail.add(subject, ("INBOX", "UNREAD"))
        _patch(self, US, "outlook_send", send)
        self.accounts("Gmail", ["main"])
        self.accounts("IMAP", ["webcentral"])
        self.collected["Gmail"]["main"] = [_entry("Gmail", "main", "Your bill is ready")]
        self.collected["IMAP"]["webcentral"] = [self._spam_entry()]
        return gmail

    def test_a_digest_dropped_for_the_text_it_quotes_arrives_as_the_second_copy(self):
        gmail = self._filtered_route()
        US.main(["--no-view"])
        first, second = self.sent
        self.assertIn(self.PITCH, " ".join(first.split()))
        self.assertNotIn("We can place", second)
        self.assertIn("Pitcher <p@pitch.example>", second)       # who and what stay
        self.assertIn("Re: May I send you a quote?", second)
        self.assertEqual(gmail.count("insert"), 0)               # nothing was placed
        self.assertEqual(gmail.mailbox[0]["labels"], ["INBOX", "UNREAD"])
        # Delivered, so the bill is marked read as on any pass.
        self.assertEqual(self.events, ["save:Gmail", "digest", "send", "send", "mark:Gmail",
                                       "logout"])
        # The digest file had everything, the pitch included.
        self.assertIn(self.PITCH, " ".join(self.shown[0].split()))
        log = self.log_text()
        self.assertIn("NOT arrived after 0s — sending it again without the text of its 1 "
                      "Spam/Junk-folder entry\n", log)
        self.assertIn("1 text(s) noted in review_unquoted.json", log)
        self.assertIn("0 account error(s); NOT arrived after 0s — sent again without the "
                      "text of its 1 Spam/Junk-folder entry: arrived after 0s\n", log)
        self.assertLess(log.index("sending it again"), log.index("sent summary to"))
        self.assertEqual(set(US.load_unquoted()), {US.text_key(self.PITCH)})

    def test_the_next_pass_does_not_quote_the_noted_text_and_arrives_at_once(self):
        gmail = self._filtered_route()
        US.main(["--no-view"])
        del self.sent[:], self.events[:], self.shown[:]
        US.main(["--no-view"])                # the same unread mail, a second pass
        self.assertEqual(len(self.sent), 1)   # one send: it arrives
        self.assertNotIn("We can place", self.sent[0])
        self.assertFalse(self.sent[0].startswith("NOTE"))
        self.assertIn(f"   Summary: {US.NOT_QUOTED_NOTED}", self.sent[0].splitlines())
        self.assertIn("Re: May I send you a quote?", self.sent[0])
        self.assertIn(self.PITCH, " ".join(self.shown[0].split()))   # the file: everything
        self.assertEqual(gmail.count("insert"), 0)
        self.assertEqual(self.events, ["save:Gmail", "digest", "send", "mark:Gmail", "logout"])
        log = self.log_text()
        self.assertIn("1 entry not quoted in the emailed digest: a digest quoting that "
                      "text did not arrive\n", log)
        first_pass, second_pass = self._sent_lines()
        self.assertTrue(first_pass.endswith("Spam/Junk-folder entry: arrived after 0s"))
        self.assertTrue(second_pass.endswith("0 account error(s); arrived after 0s"))
        self.assertEqual(log.count("sending it again"), 1)       # only the first pass
        self.assertEqual(log.count("text(s) noted"), 1)

    def test_a_follow_up_with_the_same_words_is_not_quoted_wherever_it_lands(self):
        gmail = self._filtered_route()
        US.main(["--no-view"])
        del self.sent[:], self.events[:]
        # The first message is gone; the campaign's next one reaches an INBOX,
        # from another sender, the same words in other casing and spacing.
        follow_up = _entry("IMAP", "webcentral", "Re: Re: May I send you a quote?",
                           frm="Other Pitcher <q@pitch2.example>")
        follow_up["lines"] = ["WE CAN PLACE your website on the first page.",
                              "  May I   send you a quote?"]
        self.collected["IMAP"]["webcentral"] = [follow_up]
        US.main(["--no-view"])
        self.assertEqual(len(self.sent), 1)
        self.assertNotIn("PLACE your website", self.sent[0])
        self.assertIn(US.NOT_QUOTED_NOTED, self.sent[0])
        self.assertIn("Other Pitcher <q@pitch2.example>", self.sent[0])
        self.assertEqual(gmail.count("insert"), 0)
        self.assertTrue(self._sent_lines()[-1].endswith(
            "0 account error(s); arrived after 0s"))

    def test_neither_copy_arriving_ends_with_the_whole_digest_placed(self):
        gmail = self._filtered_route(drops=lambda body: True)
        US.main(["--no-view"])
        self.assertEqual(len(self.sent), 2)
        placed = gmail.placed()
        self.assertEqual(len(placed), 1)
        text = placed[0].get_content()
        self.assertTrue(text.startswith("NOTE: placed in this mailbox directly — neither"))
        self.assertIn(self.sent[0], text)                    # the first copy, whole
        self.assertIn(self.PITCH, " ".join(text.split()))
        self.assertIn("mark:Gmail", self.events)             # in the mailbox after all
        self.assertIn("NOT arrived after 0s either — placed in the inbox directly\n",
                      self.log_text())

    def test_a_dry_run_shows_the_first_copy_and_notes_nothing(self):
        self._filtered_route()
        US.note_unquoted([US.text_key(self.PITCH)])
        before = self.notes_file.read_bytes()
        out = io.StringIO()
        with mock.patch.object(sys, "stdout", out):
            US.main(["--dry-run", "--no-view"])
        self.assertEqual(self.sent, [])
        self.assertNotIn("We can place", out.getvalue())
        self.assertIn(US.NOT_QUOTED_NOTED, out.getvalue())
        self.assertIn(self.PITCH, " ".join(self.shown[0].split()))   # the file: everything
        self.assertEqual(self.notes_file.read_bytes(), before)

    def test_a_dry_run_over_spam_text_writes_no_notes_file(self):
        self._filtered_route()
        with mock.patch.object(sys, "stdout", io.StringIO()):
            US.main(["--dry-run", "--no-view"])
        self.assertFalse(self.notes_file.exists())
        self.assertEqual(self.sent, [])


# ── Heartbeat ─────────────────────────────────────────────────────────────

class WatchdogPatternTests(unittest.TestCase):
    @staticmethod
    def _cmdline(name):
        return subprocess.list2cmdline([r"C:\x\python.exe", "MyAgent.py", "-l", name,
                                        "--headless"])

    def test_matches_names_as_popen_quotes_them(self):
        import re
        for name in ("Balance Westpac Mastercard account", "NoSpace",
                     'Odd "quoted" name', "Café & co #1"):
            with self.subTest(name=name):
                self.assertRegex(self._cmdline(name), Heartbeat.watchdog_pattern(name))
                self.assertTrue(re.search(Heartbeat.watchdog_pattern(name), self._cmdline(name)))

    def test_hand_quoted_name_matches(self):
        self.assertRegex(r'C:\x\python.exe MyAgent.py -l "NoSpace" --headless',
                         Heartbeat.watchdog_pattern("NoSpace"))

    def test_a_shorter_name_is_not_a_match(self):
        self.assertNotRegex(self._cmdline("Balance Westpac Mastercard account"),
                            Heartbeat.watchdog_pattern("Balance"))
        self.assertNotRegex(self._cmdline("Balance Westpac"),
                            Heartbeat.watchdog_pattern("Balance Westpac Mastercard account"))

    def test_the_windows_query_is_bounded(self):
        # A wedged WMI stalled one query for minutes; unbounded, it would hang
        # the unattended pass. (CREATE_NO_WINDOW is created off Windows so the
        # Windows branch runs on every platform.)
        seen = {}

        def fake_run(argv, **kwargs):
            seen.update(kwargs)
            return subprocess.CompletedProcess(argv, 0, stdout="4242 12\n", stderr="")

        with mock.patch.object(Heartbeat.platform, "system", return_value="Windows"), \
                mock.patch.object(subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True), \
                mock.patch.object(subprocess, "run", fake_run):
            self.assertEqual(Heartbeat.running_instances("ZZ Probe"), [(4242, 12)])
        self.assertEqual(seen.get("timeout"), Heartbeat.WATCHDOG_QUERY_TIMEOUT)
        self.assertTrue(Heartbeat.WATCHDOG_QUERY_TIMEOUT)


@unittest.skipUnless(sys.platform == "win32", "the Windows CIM query")
class RunningInstancesLiveTests(unittest.TestCase):
    """The real query (PowerShell, CIM, the .NET regex) against a real process
    whose command line reads like a headless run of an instruction whose name
    holds spaces — the case the watchdog never saw."""

    def test_finds_a_run_whose_name_has_spaces(self):
        name = f"ZZ Watchdog Probe {os.getpid()}"
        exe = getattr(sys, "_base_executable", sys.executable)
        proc = subprocess.Popen(
            [exe, "-c", "import time; time.sleep(60)", "MyAgent.py", "-l", name, "--headless"],
            creationflags=subprocess.CREATE_NO_WINDOW)

        def kill():
            proc.kill()
            proc.wait(10)
        self.addCleanup(kill)
        found = []
        deadline = time.time() + 20
        while time.time() < deadline:
            found = [pid for pid, _age in Heartbeat.running_instances(name)]
            if proc.pid in found:
                break
            time.sleep(0.3)
        self.assertIn(proc.pid, found)
        self.assertNotIn(proc.pid, [pid for pid, _ in Heartbeat.running_instances("ZZ Watchdog")])


class RewriteMarkerTests(_TempLog):
    def setUp(self):
        super().setUp()
        self.store = self.root / "hb_review_store.json"
        self.store.write_text(json.dumps(
            {"ZZ": {"text": "HEADER\n*****\nold core\n*****\nFOOTER"}}), encoding="utf-8")
        _patch(self, Heartbeat, "INSTRUCTIONS_FILE", self.store)

    def text(self):
        return json.loads(self.store.read_text(encoding="utf-8"))["ZZ"]["text"]

    def test_a_marker_line_inside_the_core_does_not_corrupt_the_next_rewrite(self):
        Heartbeat.rewrite_instruction("ZZ", "task A\n*****\ntask A line 2")
        self.assertEqual(self.text(), "HEADER\n*****\ntask A\n*****\ntask A line 2\n*****\nFOOTER")
        Heartbeat.rewrite_instruction("ZZ", "task B")
        self.assertEqual(self.text(), "HEADER\n*****\ntask B\n*****\nFOOTER")

    def test_a_rewrite_says_replaced(self):
        self.assertEqual(Heartbeat.rewrite_instruction("ZZ", "task"), "replaced")

    def test_fewer_than_two_markers_means_prepend_and_leaves_the_store_alone(self):
        # 2026-10-28 (the user's request): such an instruction runs with the
        # prompt in front for that run — poison until then.
        for text in ("HEADER\n*****\nbody", "no markers at all"):
            with self.subTest(text=text):
                self.store.write_text(json.dumps({"ZZ": {"text": text}}), encoding="utf-8")
                before = self.store.read_bytes()
                self.assertEqual(Heartbeat.rewrite_instruction("ZZ", "x"), "prepend")
                self.assertEqual(self.store.read_bytes(), before)

    def test_an_empty_prompt_changes_nothing_with_or_without_markers(self):
        for text in ("HEADER\n*****\nold core\n*****\nFOOTER", "no markers at all"):
            for core in ("", "   \n"):
                with self.subTest(text=text, core=core):
                    self.store.write_text(json.dumps({"ZZ": {"text": text}}), encoding="utf-8")
                    before = self.store.read_bytes()
                    self.assertEqual(Heartbeat.rewrite_instruction("ZZ", core), "unchanged")
                    self.assertEqual(self.store.read_bytes(), before)

    def test_an_unknown_name_is_poison_even_with_an_empty_prompt(self):
        for core in ("x", ""):
            with self.subTest(core=core):
                with self.assertRaises(LookupError):
                    Heartbeat.rewrite_instruction("Nope", core)


class _Exec:
    def __init__(self, result=None, error=None):
        self.result, self.error = result, error

    def execute(self):
        if self.error:
            raise self.error
        return self.result


class DispatchOrderTests(_TempLog):
    """dispatch_trigger's safety order (the fix of 2026-09-28, then on the
    email transport): the trigger is CONSUMED before the launch, so a tick
    that dies between the two can never start the same run twice."""

    def setUp(self):
        super().setUp()
        self.events = []
        _patch(self, Heartbeat, "rewrite_instruction", lambda name, core: None)
        _patch(self, Heartbeat, "running_instances", lambda name: [])
        self.launch_error = None
        self.consume_error = None

        def launch(name, result_file=None, relay_dir=None):
            self.events.append(f"launch:{name}")
            if self.launch_error:
                raise self.launch_error
            return 4242
        _patch(self, Heartbeat, "launch_instruction", launch)

    def consume(self):
        self.events.append("consume")
        if self.consume_error:
            raise self.consume_error

    def dispatch(self):
        return Heartbeat.dispatch_trigger("msg t1", "ZZ Probe", "Do the thing", self.consume)

    def test_trigger_is_consumed_before_the_launch(self):
        self.assertEqual(self.dispatch(), ("launched", 4242))
        self.assertEqual(self.events, ["consume", "launch:ZZ Probe"])
        self.assertIn("launched 'ZZ Probe' headless (PID 4242) from msg t1", self.log_text())

    def test_a_failed_consume_launches_nothing(self):
        self.consume_error = RuntimeError("Telegram 503")
        with self.assertRaises(RuntimeError):
            self.dispatch()
        self.assertEqual(self.events, ["consume"])

    def test_a_failed_launch_raises_after_the_consume(self):
        self.launch_error = OSError("python missing")
        with self.assertRaises(OSError):
            self.dispatch()
        self.assertEqual(self.events, ["consume", "launch:ZZ Probe"])

    def test_an_unanswered_watchdog_query_consumes_and_launches_nothing(self):
        def stalled(name):
            raise subprocess.TimeoutExpired("powershell", Heartbeat.WATCHDOG_QUERY_TIMEOUT)
        _patch(self, Heartbeat, "running_instances", stalled)
        with self.assertRaises(subprocess.TimeoutExpired):
            self.dispatch()
        self.assertEqual(self.events, [])       # still pending: the next tick retries


if __name__ == "__main__":
    unittest.main()
