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
from pathlib import Path
from unittest import mock

import Heartbeat
import UnreadSummary as US


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
        _patch(self, US, "DOWNLOAD_DIR", self.root / "review_attachments")
        _patch(self, US, "_SPECS", [SPEC])
        _patch(self, US, "MARK_MATCHES_READ", True)
        _patch(self, US, "SAVE_MATCH_PDFS", True)
        _patch(self, US, "TRASH_MATCHES", False)
        self.events = []
        self.sent = []
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
        self.send_error = None

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
        return "digest written (test)"

    def _send(self, account, account_email, subject, body):
        self.events.append("send")
        if self.send_error:
            raise self.send_error
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

    def test_fewer_than_two_markers_is_still_poison(self):
        self.store.write_text(json.dumps({"ZZ": {"text": "HEADER\n*****\nbody"}}),
                              encoding="utf-8")
        with self.assertRaises(LookupError):
            Heartbeat.rewrite_instruction("ZZ", "x")


class _Exec:
    def __init__(self, result=None, error=None):
        self.result, self.error = result, error

    def execute(self):
        if self.error:
            raise self.error
        return self.result


class _FakeGmail:
    def __init__(self, events, fail_mark_read=False):
        self.events, self.fail_mark_read = events, fail_mark_read
        body = base64.urlsafe_b64encode(b"ZZ Probe\n\nDo the thing").decode()
        self.full = {"id": "t1", "payload": {
            "mimeType": "text/plain", "body": {"data": body},
            "headers": [{"name": "Subject", "value": Heartbeat.SUBJECT}]}}

    def users(self):
        return self

    def messages(self):
        return self

    def list(self, **kwargs):
        return _Exec({"messages": [{"id": "t1"}]})

    def get(self, **kwargs):
        return _Exec(self.full)

    def modify(self, userId, id, body):
        if "removeLabelIds" in body:
            self.events.append("mark-read")
            if self.fail_mark_read:
                return _Exec(error=RuntimeError("Gmail 503"))
        else:
            self.events.append(f"mark-unread:{body.get('addLabelIds')}")
        return _Exec({})


class HeartbeatOrderTests(_TempLog):
    def setUp(self):
        super().setUp()
        self.events = []
        _patch(self, Heartbeat, "rewrite_instruction", lambda name, core: None)
        _patch(self, Heartbeat, "running_instances", lambda name: [])
        self.launch_error = None

        def launch(name):
            self.events.append(f"launch:{name}")
            if self.launch_error:
                raise self.launch_error
            return 4242
        _patch(self, Heartbeat, "launch_instruction", launch)

    def run_main(self, **gmail):
        _patch(self, Heartbeat, "gmail_service", lambda: _FakeGmail(self.events, **gmail))
        Heartbeat.main()

    def test_trigger_is_marked_read_before_the_launch(self):
        self.run_main()
        self.assertEqual(self.events, ["mark-read", "launch:ZZ Probe"])
        self.assertIn("launched 'ZZ Probe' headless (PID 4242)", self.log_text())

    def test_a_failed_mark_launches_nothing(self):
        with self.assertRaises(RuntimeError):
            self.run_main(fail_mark_read=True)
        self.assertEqual(self.events, ["mark-read"])

    def test_a_failed_launch_puts_the_unread_label_back(self):
        self.launch_error = OSError("python missing")
        with self.assertRaises(OSError):
            self.run_main()
        self.assertEqual(self.events, ["mark-read", "launch:ZZ Probe",
                                       "mark-unread:['UNREAD']"])

    def test_an_unanswered_watchdog_query_marks_and_launches_nothing(self):
        def stalled(name):
            raise subprocess.TimeoutExpired("powershell", Heartbeat.WATCHDOG_QUERY_TIMEOUT)
        _patch(self, Heartbeat, "running_instances", stalled)
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_main()
        self.assertEqual(self.events, [])       # still unread: the next tick retries


if __name__ == "__main__":
    unittest.main()
