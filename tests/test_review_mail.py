"""Regression tests for the 2026-09-28 review fixes in the mail mixins.

No mail server is contacted: fake IMAP / SMTP connections and a fake Graph.

- Proton: a MOVE the server refuses raises instead of falling back to a COPY
  it ignored and an EXPUNGE that destroyed the messages ("ok": true); the
  fallback (servers without MOVE) checks its COPY and expunges only the moved
  UIDs; the SMTP envelope survives quoted display names with commas, and
  refused recipients are reported; raw 8-bit headers decode; STARTTLS failing
  against a non-local server refuses the plaintext login; a failed folder
  discovery is not cached; a move to Trash / Spam through modify_labels asks
  like proton_trash, and the stray-UID retry keeps a different message's label.
- Gmail / Outlook: a batch carries on past a bad id and reports both lists;
  gmail_modify_labels adding TRASH / SPAM asks like gmail_trash;
  overwrote_existing tells the truth; a body is decoded in its declared
  charset; a headless run refuses an interactive sign-in.
"""

import email
import imaplib
import json
import os
import queue
import shutil
import tempfile
import unittest
from unittest import mock

from myagent import gmail_mixin, outlook_mixin, protonmail_mixin
from myagent.gmail_mixin import GmailMixin
from myagent.mail_common import run_per_id
from myagent.outlook_mixin import OutlookMixin
from myagent.protonmail_mixin import ProtonMailMixin
from tests._util import stub


class _Conn:
    """A fake IMAP connection: `replies` maps a UID command to a reply
    (typ, data) or an exception to raise; every command is recorded."""

    def __init__(self, **replies):
        self.replies = replies
        self.calls = []

    def uid(self, command, *args):
        self.calls.append((command.lower(),) + args)
        reply = self.replies.get(command.lower(), ("OK", [b"done"]))
        if isinstance(reply, Exception):
            raise reply
        return reply

    def expunge(self):
        self.calls.append(("plain-expunge",))
        return "OK", [None]

    def select(self, *args, **kwargs):
        return "OK", [b"1"]

    def commands(self):
        return [c[0] for c in self.calls]


IMAP_ERROR, IMAP_ABORT = imaplib.IMAP4.error, imaplib.IMAP4.abort
BAD = IMAP_ERROR("MOVE command error: BAD [b'unknown command']")


class ImapMoveTests(unittest.TestCase):

    def setUp(self):
        self.host = stub(ProtonMailMixin)

    def test_a_refused_move_raises_and_deletes_nothing(self):
        conn = _Conn(move=("NO", [b"[TRYCREATE] Mailbox doesn't exist: Folders/Wrok"]))
        with self.assertRaises(imaplib.IMAP4.error) as ctx:
            self.host._imap_move(conn, "11,12", "Folders/Wrok")
        self.assertIn("TRYCREATE", str(ctx.exception))
        self.assertEqual(conn.commands(), ["move"])       # no copy, store or expunge

    def test_a_server_without_move_gets_a_checked_copy_and_a_uid_expunge(self):
        conn = _Conn(move=BAD)
        self.host._imap_move(conn, "11,12", "Archive")
        self.assertEqual(conn.commands(), ["move", "copy", "store", "expunge"])
        self.assertEqual(conn.calls[-1], ("expunge", "11,12"))   # UID EXPUNGE of these only

    def test_a_refused_copy_raises_before_anything_is_flagged(self):
        conn = _Conn(move=BAD, copy=("NO", [b"no such mailbox"]))
        with self.assertRaises(imaplib.IMAP4.error):
            self.host._imap_move(conn, "11", "Nowhere")
        self.assertEqual(conn.commands(), ["move", "copy"])

    def test_no_uidplus_falls_back_to_a_plain_expunge(self):
        conn = _Conn(move=BAD, expunge=BAD)
        self.host._imap_move(conn, "11", "Archive")
        self.assertEqual(conn.commands()[-1], "plain-expunge")


class _ProtonHost(ProtonMailMixin):
    def __init__(self, conn, confirm=True):
        self.conn = conn
        self.confirm = confirm
        self.asked = []
        self._proton_imap_conns = {}
        self._proton_folder_cache = {"acct": dict(self._FOLDER_ROLE_DEFAULTS)}
        self.queue = queue.Queue()

    def _proton_imap(self, account):
        return self.conn

    def _proton_drop_imap(self, account):
        pass

    def _load_proton_accounts(self):
        return {"acct": {}}

    def _confirm_proton_action(self, tool_name, title, summary, detail):
        self.asked.append(tool_name)
        return self.confirm


class ProtonToolTests(unittest.TestCase):

    def test_untrash_to_a_mistyped_folder_reports_an_error_not_ok(self):
        conn = _Conn(move=("NO", [b"[TRYCREATE] no such folder"]))
        out = _ProtonHost(conn).do_proton_untrash(
            {"account": "acct", "uids": [11, 12], "to_folder": "Folders/Wrok"})
        self.assertTrue(out.startswith("error:"), out)
        self.assertNotIn("expunge", conn.commands())

    def test_modify_labels_into_trash_asks_like_proton_trash(self):
        host = _ProtonHost(_Conn(), confirm=False)
        out = host.do_proton_modify_labels({"account": "acct", "uids": [5], "add_to": "Trash"})
        self.assertIn("user denied", out)
        self.assertEqual(host.asked, ["proton_trash"])

    def test_modify_labels_to_a_label_does_not_ask(self):
        host = _ProtonHost(_Conn())
        out = host.do_proton_modify_labels({"account": "acct", "uids": [5],
                                            "add_to": "Labels/Work"})
        self.assertEqual(host.asked, [])
        self.assertTrue(json.loads(out)["ok"])


class _SMTP:
    def __init__(self, refused=None):
        self.refused = refused or {}
        self.sent = None

    def send_message(self, msg, from_addr=None, to_addrs=None):
        self.sent = (from_addr, list(to_addrs))
        return self.refused

    def quit(self):
        pass


class SmtpEnvelopeTests(unittest.TestCase):

    def send(self, refused=None, **headers):
        client = _SMTP(refused)
        host = stub(ProtonMailMixin)
        host._proton_smtp = lambda account: client
        msg = email.message.EmailMessage()
        for name, value in headers.items():
            msg[name] = value
        msg.set_content("hi")
        return host._smtp_send("acct", msg, "me@example.com"), client, msg

    def test_a_display_name_with_a_comma_keeps_its_address(self):
        _refused, client, msg = self.send(
            To='"Smith, John" <john@example.com>, bad@example.com',
            Bcc="hidden@example.com")
        self.assertEqual(client.sent[1],
                         ["john@example.com", "bad@example.com", "hidden@example.com"])
        self.assertNotIn("Bcc", msg)                    # never leaked to recipients

    def test_refused_recipients_are_reported(self):
        refused, _client, _msg = self.send(
            refused={"bad@example.com": (550, b"No such user")},
            To="ok@example.com, bad@example.com")
        self.assertEqual(refused, {"bad@example.com": "550 No such user"})


class HeaderAndTlsTests(unittest.TestCase):

    def test_a_raw_8bit_header_decodes(self):
        msg = email.message_from_bytes("Subject: Café déjà vu\r\n\r\nx"
                                       .encode("utf-8"))
        self.assertEqual(ProtonMailMixin._decode_header(msg.get("Subject")),
                         "Café déjà vu")

    def connect(self, host_name):
        logins = []

        class _Imap:
            # The real exception classes: the code catches imaplib.IMAP4.error,
            # which is this class while the patch is in place.
            error = IMAP_ERROR
            abort = IMAP_ABORT

            def __init__(self, host, port):
                pass

            def starttls(self, ssl_context=None):
                raise IMAP_ERROR("STARTTLS extension not supported by server.")

            def login(self, user, password):
                logins.append(user)

            def shutdown(self):
                pass

        host = stub(ProtonMailMixin, _proton_imap_conns={})
        host._load_proton_accounts = lambda: {"acct": {
            "imap_host": host_name, "username": "u", "app_password": "p"}}
        with mock.patch.object(protonmail_mixin.imaplib, "IMAP4", _Imap):
            try:
                host._proton_imap("acct")
            except RuntimeError as e:
                return logins, str(e)
        return logins, ""

    def test_starttls_failing_against_a_remote_server_refuses_the_login(self):
        logins, error = self.connect("mail.example.com")
        self.assertEqual(logins, [])
        self.assertIn("refusing to send its password unencrypted", error)

    def test_bridge_on_this_machine_still_logs_in(self):
        logins, error = self.connect("127.0.0.1")
        self.assertEqual((logins, error), (["u"], ""))


class DiscoveryTests(unittest.TestCase):

    def test_a_failed_discovery_serves_defaults_without_caching_them(self):
        host = stub(ProtonMailMixin, _proton_folder_cache={}, _proton_imap_conns={})
        host._load_proton_accounts = lambda: {"acct": {}}
        host._proton_imap = mock.Mock(side_effect=OSError("connection reset"))
        self.assertEqual(host._proton_folder("acct", "trash"), "Trash")
        self.assertEqual(host._proton_folder_cache, {})         # retried next call
        conn = _Conn()
        conn.list = lambda: ("OK", [b'(\\HasNoChildren \\Trash) "." "INBOX.Trash"'])
        host._proton_imap = lambda account: conn
        self.assertEqual(host._proton_folder("acct", "trash"), "INBOX.Trash")
        self.assertIn("acct", host._proton_folder_cache)


class StrayUidTests(unittest.TestCase):

    def test_only_the_same_message_is_moved_again(self):
        # Labels/Work holds 1, 2 (to move) before; after the MOVE Bridge shows
        # 7 (message <a> back under a new UID) and 8 (a DIFFERENT message a
        # filter just labelled).
        class _LabelConn(_Conn):
            def __init__(self):
                super().__init__()
                self.searches = 0

            def uid(self, command, *args):
                self.calls.append((command.lower(),) + args)
                if command.lower() == "search":
                    self.searches += 1
                    return "OK", [b"1 2" if self.searches == 1 else b"7 8"]
                if command.lower() == "fetch":
                    ids = {b"1": b"<a@x>", b"2": b"<b@x>", b"7": b"<a@x>", b"8": b"<new@x>"}
                    return "OK", [(b"%s (UID %s BODY[HEADER.FIELDS (MESSAGE-ID)] {20}" % (u, u),
                                   b"Message-ID: " + ids[u] + b"\r\n\r\n")
                                  for u in args[0].encode().split(b",") if u in ids]
                return "OK", [b"done"]

        conn = _LabelConn()
        out = _ProtonHost(conn).do_proton_modify_labels(
            {"account": "acct", "folder": "Labels/Work", "uids": [1, 2], "add_to": "INBOX"})
        moves = [c for c in conn.calls if c[0] == "move"]
        self.assertEqual([m[1] for m in moves], ["1,2", "7"])    # 8 keeps its label
        self.assertEqual(json.loads(out)["label_removal_retries"], 1)


class BatchTests(unittest.TestCase):

    def test_run_per_id_carries_on(self):
        def act(i):
            if i == "bad":
                raise ValueError("gone")
            return i.upper()
        self.assertEqual(run_per_id(["a", "bad", "c"], act),
                         (["A", "C"], [{"id": "bad", "error": "ValueError: gone"}]))

    def test_outlook_trash_keeps_the_new_ids_of_what_moved(self):
        host = stub(OutlookMixin)
        host._confirm_outlook_action = lambda *a: True

        def graph(account, method, path, **kw):
            if "/m2/" in path:
                raise RuntimeError("Graph 404")
            return {"id": "new-" + path.split("/")[3]}

        host._outlook_graph = graph
        out = json.loads(host.do_outlook_trash({"account": "a",
                                                "message_ids": ["m1", "m2", "m3"]}))
        self.assertEqual(out["moved_ids"], ["new-m1", "new-m3"])
        self.assertEqual(out["failed"][0]["id"], "m2")


class _Execute:
    def __init__(self, fail=False):
        self.fail = fail

    def execute(self):
        if self.fail:
            raise RuntimeError("404 not found")
        return {}


class _GmailMessages:
    def __init__(self):
        self.calls = []

    def trash(self, userId, id):
        self.calls.append(("trash", id))
        return _Execute(fail=(id == "bad"))

    def modify(self, userId, id, body):
        self.calls.append(("modify", id))
        return _Execute(fail=(id == "bad"))


class GmailTests(unittest.TestCase):

    def host(self, confirm=True):
        messages = _GmailMessages()
        service = mock.Mock()
        service.users.return_value.messages.return_value = messages
        h = stub(GmailMixin)
        h.asked = []
        h._gmail_service = lambda account: service
        h._confirm_gmail_action = lambda tool, *a: (h.asked.append(tool), confirm)[1]
        return h, messages

    def test_a_batch_reports_what_worked_and_what_failed(self):
        h, _messages = self.host()
        out = json.loads(h.do_gmail_trash({"account": "a",
                                           "message_ids": ["m1", "bad", "m3"]}))
        self.assertEqual(out["trashed_ids"], ["m1", "m3"])
        self.assertEqual(out["failed"][0]["id"], "bad")

    def test_adding_trash_through_modify_labels_asks_like_gmail_trash(self):
        h, messages = self.host(confirm=False)
        out = h.do_gmail_modify_labels({"account": "a", "message_ids": ["m1"],
                                        "add_labels": ["TRASH"]})
        self.assertIn("user denied", out)
        self.assertEqual((h.asked, messages.calls), (["gmail_trash"], []))
        h, _ = self.host(confirm=False)
        json.loads(h.do_gmail_modify_labels({"account": "a", "message_ids": ["m1"],
                                             "add_labels": ["Label_7"]}))
        self.assertEqual(h.asked, [])                   # an ordinary label: no question

    def test_a_body_decodes_in_its_declared_charset(self):
        import base64
        part = {"mimeType": "text/plain",
                "headers": [{"name": "Content-Type", "value": 'text/plain; charset="iso-8859-1"'}],
                "body": {"data": base64.urlsafe_b64encode("Café".encode("latin-1")).decode()}}
        text, _html = GmailMixin._extract_bodies(part)
        self.assertEqual(text, "Café")

    def test_overwrote_existing_tells_the_truth(self):
        import base64
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        target = os.path.join(d, "a.pdf")
        service = mock.Mock()
        (service.users.return_value.messages.return_value.attachments.return_value
         .get.return_value.execute.return_value) = {"data": base64.urlsafe_b64encode(b"PDF").decode()}
        h = stub(GmailMixin)
        h._gmail_service = lambda account: service
        h._gmail_get_message = lambda *a, **k: {}
        out = h.do_gmail_get_attachment({"account": "a", "message_id": "m", "attachment_id": "x",
                                         "save_to": target, "overwrite": True})
        self.assertIs(json.loads(out)["overwrote_existing"], False)   # nothing was there


class HeadlessSigninTests(unittest.TestCase):

    def test_outlook_refuses_a_browser_signin_headless(self):
        app = mock.Mock()
        app.get_accounts.return_value = []
        host = stub(OutlookMixin, _headless=True)
        host._load_outlook_accounts = lambda: {"a": {"email": "me@x.com"}}
        host._outlook_app = lambda account: (app, mock.Mock(has_state_changed=False), "c")
        with mock.patch.object(outlook_mixin, "_HAS_OUTLOOK", True):
            with self.assertRaises(RuntimeError) as ctx:
                host._outlook_token("a")
        self.assertIn("browser sign-in", str(ctx.exception))
        app.acquire_token_interactive.assert_not_called()

    @unittest.skipUnless(gmail_mixin._HAS_GOOGLE, "google client libraries not installed")
    def test_gmail_refuses_a_browser_signin_headless(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        host = stub(GmailMixin, _headless=True, _gmail_services={})
        host._load_google_accounts = lambda: {"a": {}}
        with mock.patch.object(gmail_mixin, "GOOGLE_CONFIG_DIR", d):
            with self.assertRaises(RuntimeError) as ctx:
                host._gmail_service("a")
        self.assertIn("browser sign-in", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
