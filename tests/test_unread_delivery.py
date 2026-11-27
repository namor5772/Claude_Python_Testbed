"""UnreadSummary's delivery check (2026-11-27): a send the provider accepted
is checked at the recipient's mailbox, and a digest that has not arrived — or
was filed under Spam — is placed in the inbox directly.

Why it exists: six digests in a row were accepted by Microsoft Graph, filed
in Sent Items and never reached Gmail — each listed the unread mail of the
Spam folders it had scanned and was dropped on the way for it — while the
log said "sent" every time.

No mailbox, network or clock is touched: the Gmail service is an in-memory
mailbox with the shape of the API calls the check makes (users().messages()
list / get / insert, each answering through execute()), the wait runs on a
fake clock whose sleep makes scheduled messages "arrive", and LOG_FILE is a
temp file. main()'s side of it — the watch begun before the send, the marks
held back until the digest is delivered — is in tests/test_review_jobs.py's
MainTests, which owns the main() harness. What happens between "not seen"
and "placed" since the same evening — the second emailed copy, and the texts
noted so that later digests do not quote them — is in
tests/test_unread_second_copy.py, on this module's mailbox and clock."""

import base64
import email
import email.policy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import UnreadSummary as US

RECIPIENT = "reader@gmail.example"
SENDER = "sender@outlook.example"
SUBJECT = "Summary of Unread Emails - 2026-11-27 19:47:53"
BODY = "=" * 50 + "\nCOMPREHENSIVE LIST OF UNREAD EMAILS\n1. Account:  a — \U0001f4e9 b"


def _patch(test, name, value):
    p = mock.patch.object(US, name, value)
    p.start()
    test.addCleanup(p.stop)


class _Call:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeGmail:
    """An in-memory mailbox, newest first, behind the three API calls."""

    def __init__(self, *messages):
        self.mailbox = [dict(m) for m in messages]
        self.calls = []            # ("list" | "get" | "insert", argument dict)
        self.fail = {}             # op -> exception raised by its execute()
        self.before_list = None    # called at each list, e.g. to deliver mail
        self._n = 0

    def add(self, subject, labels=("INBOX", "UNREAD")):
        self._n += 1
        message = {"id": f"new{self._n}", "subject": subject, "labels": list(labels)}
        self.mailbox.insert(0, message)
        return message

    def users(self):
        return self

    def messages(self):
        return self

    def _run(self, op, kwargs, answer):
        self.calls.append((op, kwargs))

        def execute():
            if op in self.fail:
                raise self.fail[op]
            return answer()
        return _Call(execute)

    def list(self, **kwargs):
        def answer():
            if self.before_list:
                self.before_list()
            rows = self.mailbox
            if not kwargs.get("includeSpamTrash"):
                rows = [m for m in rows if not {"SPAM", "TRASH"} & set(m["labels"])]
            rows = rows[:kwargs.get("maxResults", 100)]
            return {"messages": [{"id": m["id"]} for m in rows]} if rows else {}
        return self._run("list", kwargs, answer)

    def get(self, **kwargs):
        def answer():
            m = next(m for m in self.mailbox if m["id"] == kwargs["id"])
            return {"id": m["id"], "labelIds": list(m["labels"]),
                    "payload": {"headers": [{"name": "Subject", "value": m["subject"]}]}}
        return self._run("get", kwargs, answer)

    def insert(self, **kwargs):
        def answer():
            raw = base64.urlsafe_b64decode(kwargs["body"]["raw"])
            parsed = email.message_from_bytes(raw, policy=email.policy.default)
            m = self.add(str(parsed["Subject"]), kwargs["body"]["labelIds"])
            m["parsed"] = parsed
            return {"id": m["id"], "labelIds": m["labels"]}
        return self._run("insert", kwargs, answer)

    def count(self, op):
        return sum(1 for name, _ in self.calls if name == op)

    def placed(self):
        return [m["parsed"] for m in self.mailbox if "parsed" in m]


class _Clock:
    """time.monotonic and time.sleep on a clock that only sleep moves."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


OLD = [{"id": "old1", "subject": "Your parcel", "labels": ["INBOX"]},
       {"id": "old2", "subject": "Summary of Unread Emails - 2026-11-27 07:00:13",
        "labels": ["INBOX"]}]


class _Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log_file = Path(tmp.name) / "delivery.log"
        _patch(self, "LOG_FILE", self.log_file)
        # The notes of texts not to quote again: never this machine's own.
        self.notes_file = Path(tmp.name) / "delivery_unquoted.json"
        _patch(self, "UNQUOTED_FILE", self.notes_file)
        _patch(self, "SEND_TO", RECIPIENT)
        _patch(self, "DELIVERY_WAIT_SECONDS", 30)
        _patch(self, "DELIVERY_POLL_SECONDS", 2)
        _patch(self, "DELIVERY_SCAN", 10)
        self.gmail = FakeGmail(*OLD)
        self.clock = _Clock()

    def log_text(self):
        return self.log_file.read_text(encoding="utf-8") if self.log_file.exists() else ""

    def watch(self):
        return {"service": self.gmail, "known": {m["id"] for m in self.gmail.mailbox}}

    def arrives(self, after, subject=SUBJECT, labels=("INBOX", "UNREAD")):
        """Deliver a message once the fake clock has moved ``after`` seconds."""
        due = self.clock.now + after
        previous = self.gmail.before_list

        def deliver():
            if previous:
                previous()
            if self.clock.now >= due and not deliver.done:
                deliver.done = True
                self.gmail.add(subject, labels)
        deliver.done = False
        self.gmail.before_list = deliver

    def ensure(self, watch=None, second=None):
        return US.ensure_delivered(watch or self.watch(), SENDER, SUBJECT, BODY,
                                   second=second, sleep=self.clock.sleep, clock=self.clock)


class RecipientAccountTests(_Base):
    def test_the_account_is_found_by_its_email_whatever_the_case(self):
        accounts = {"other": {"email": "other@gmail.example"},
                    "reader": {"email": "Reader@Gmail.Example"}}
        self.assertEqual(US.recipient_gmail_account(accounts), "reader")

    def test_an_account_without_an_email_answers_by_its_key(self):
        # main() labels such an account by its key too: cfg.get("email", account).
        self.assertEqual(US.recipient_gmail_account({RECIPIENT: {}}), RECIPIENT)

    def test_no_such_account_is_none(self):
        self.assertIsNone(US.recipient_gmail_account({"a": {"email": "a@gmail.example"}}))
        self.assertIsNone(US.recipient_gmail_account({}))


class DeliveryWatchTests(_Base):
    def setUp(self):
        super().setUp()
        self.opened = []

        def service(account):
            self.opened.append(account)
            return self.gmail
        _patch(self, "gmail_service", service)

    def test_the_watch_knows_everything_already_in_the_mailbox(self):
        watch = US.delivery_watch({"reader": {"email": RECIPIENT}})
        self.assertIs(watch["service"], self.gmail)
        self.assertEqual(watch["known"], {"old1", "old2"})
        self.assertEqual(self.opened, ["reader"])
        # One list call, Spam and Trash included, no message fetched.
        self.assertEqual(self.gmail.calls, [("list", {
            "userId": "me", "maxResults": 10, "includeSpamTrash": True})])

    def test_spam_is_part_of_what_is_known(self):
        self.gmail.add("Old junk", labels=("SPAM", "UNREAD"))
        watch = US.delivery_watch({"reader": {"email": RECIPIENT}})
        self.assertEqual(watch["known"], {"new1", "old1", "old2"})

    def test_an_empty_mailbox_is_an_empty_watch(self):
        self.gmail.mailbox.clear()
        watch = US.delivery_watch({"reader": {"email": RECIPIENT}})
        self.assertEqual(watch["known"], set())

    def test_a_recipient_this_script_does_not_read_is_unchecked(self):
        watch = US.delivery_watch({"a": {"email": "a@gmail.example"}})
        self.assertEqual(watch, {"unchecked":
                                 f"{RECIPIENT} is not a Gmail account this script reads"})
        self.assertEqual(self.opened, [])

    def test_a_dead_token_is_unchecked_not_raised(self):
        def dead(account):
            raise RuntimeError("stored token invalid and not refreshable")
        _patch(self, "gmail_service", dead)
        watch = US.delivery_watch({"reader": {"email": RECIPIENT}})
        self.assertEqual(watch, {"unchecked":
                                 "RuntimeError: stored token invalid and not refreshable"})

    def test_a_failed_listing_is_unchecked_not_raised(self):
        self.gmail.fail["list"] = OSError("network down")
        watch = US.delivery_watch({"reader": {"email": RECIPIENT}})
        self.assertEqual(watch, {"unchecked": "OSError: network down"})

    def test_none_turns_the_check_off(self):
        _patch(self, "DELIVERY_WAIT_SECONDS", None)
        watch = US.delivery_watch({"reader": {"email": RECIPIENT}})
        self.assertEqual(watch, {"unchecked": "the delivery check is turned off"})
        self.assertEqual(self.opened, [])


class FindDeliveredTests(_Base):
    def test_nothing_new_is_none_and_fetches_nothing(self):
        self.assertIsNone(US.find_delivered(self.watch(), SUBJECT))
        self.assertEqual(self.gmail.count("get"), 0)

    def test_a_new_message_with_the_subject_answers_with_its_labels(self):
        watch = self.watch()
        self.gmail.add(SUBJECT)
        self.assertEqual(US.find_delivered(watch, SUBJECT), ["INBOX", "UNREAD"])

    def test_a_message_there_before_the_watch_is_never_the_digest(self):
        # Same subject, but it was in the mailbox when the watch began.
        self.gmail.add(SUBJECT)
        self.assertIsNone(US.find_delivered(self.watch(), SUBJECT))
        self.assertEqual(self.gmail.count("get"), 0)

    def test_only_the_exact_subject_counts(self):
        watch = self.watch()
        self.gmail.add(SUBJECT + " (2)")
        self.gmail.add("Re: " + SUBJECT)
        self.gmail.add("Summary of Unread Emails - 2026-11-27 19:47:54")
        self.assertIsNone(US.find_delivered(watch, SUBJECT))

    def test_a_folded_subject_header_still_matches(self):
        watch = self.watch()
        self.gmail.add("Summary of Unread Emails -\r\n 2026-11-27 19:47:53")
        self.assertEqual(US.find_delivered(watch, SUBJECT), ["INBOX", "UNREAD"])

    def test_each_new_message_is_fetched_once(self):
        watch = self.watch()
        self.gmail.add("Something else")
        self.assertIsNone(US.find_delivered(watch, SUBJECT))
        self.assertIsNone(US.find_delivered(watch, SUBJECT))
        self.assertEqual(self.gmail.count("get"), 1)
        self.assertIn("new1", watch["known"])

    def test_a_message_without_a_subject_header_is_not_a_match(self):
        watch = self.watch()
        self.gmail.add(SUBJECT)
        self.gmail.get = lambda **kwargs: _Call(
            lambda: {"id": kwargs["id"], "labelIds": ["INBOX"], "payload": {}})
        self.assertIsNone(US.find_delivered(watch, SUBJECT))


class EnsureDeliveredTests(_Base):
    def test_a_digest_that_arrives_is_reported_with_its_wait(self):
        self.arrives(after=4)
        self.assertEqual(self.ensure(), "arrived after 4s")
        self.assertEqual(self.clock.sleeps, [2, 2])       # looked at 0, 2 and 4 s
        self.assertEqual(self.gmail.count("insert"), 0)
        self.assertEqual(self.log_text(), "")

    def test_a_digest_already_there_at_the_first_look_costs_no_sleep(self):
        watch = self.watch()
        self.gmail.add(SUBJECT)
        self.assertEqual(self.ensure(watch), "arrived after 0s")
        self.assertEqual(self.clock.sleeps, [])

    def test_other_new_mail_is_not_taken_for_the_digest(self):
        self.arrives(after=2, subject="A newsletter that happened to arrive")
        self.arrives(after=6)
        self.assertEqual(self.ensure(), "arrived after 6s")
        self.assertEqual(self.gmail.count("insert"), 0)

    def test_a_digest_archived_on_arrival_counts_as_delivered(self):
        # A filter that skips the inbox is the user's own choice of place.
        self.arrives(after=2, labels=("UNREAD",))
        self.assertEqual(self.ensure(), "arrived after 2s")
        self.assertEqual(self.gmail.count("insert"), 0)

    def test_a_digest_that_never_arrives_is_placed_in_the_inbox(self):
        outcome = self.ensure()
        self.assertEqual(outcome, "NOT arrived after 30s — placed in the inbox directly")
        # The wait is the whole of DELIVERY_WAIT_SECONDS and no longer.
        self.assertEqual(sum(self.clock.sleeps), 30)
        self.assertEqual(set(self.clock.sleeps), {2})
        self.assertEqual(self.gmail.count("insert"), 1)
        op, kwargs = self.gmail.calls[-1]
        self.assertEqual(op, "insert")
        self.assertEqual(kwargs["userId"], "me")
        self.assertEqual(kwargs["body"]["labelIds"], ["INBOX", "UNREAD"])
        placed = self.gmail.placed()[0]
        self.assertEqual(placed["Subject"], SUBJECT)
        self.assertEqual(placed["From"], SENDER)
        self.assertEqual(placed["To"], RECIPIENT)
        text = placed.get_content()
        note, _, rest = text.partition("\n\n")
        self.assertEqual(
            " ".join(note.split()),
            f"NOTE: placed in this mailbox directly — the copy emailed from {SENDER} "
            f"had not arrived after 30 s.")
        self.assertLessEqual(max(len(line) for line in note.splitlines()), len(US.DIV))
        self.assertEqual(rest.rstrip("\n"), BODY)          # the digest itself, whole
        self.assertIn(f"digest from {SENDER} to {RECIPIENT}: NOT arrived after 30s — "
                      f"placing it in the inbox directly", self.log_text())

    def test_a_digest_filed_under_spam_is_placed_at_once(self):
        self.arrives(after=4, labels=("SPAM", "UNREAD"))
        outcome = self.ensure()
        self.assertEqual(outcome, "arrived in Spam after 4s — placed in the inbox directly")
        self.assertEqual(self.clock.sleeps, [2, 2])        # no waiting out the 30 s
        placed = self.gmail.placed()[0]
        note = placed.get_content().partition("\n\n")[0]
        self.assertEqual(" ".join(note.split()),
                         f"NOTE: placed in this mailbox directly — the copy emailed "
                         f"from {SENDER} was filed under Spam.")
        self.assertIn("arrived in Spam after 4s — placing it in the inbox directly",
                      self.log_text())

    def test_a_digest_arriving_on_the_last_look_is_not_placed(self):
        self.arrives(after=30)
        self.assertEqual(self.ensure(), "arrived after 30s")
        self.assertEqual(self.gmail.count("insert"), 0)

    def test_an_unchecked_watch_touches_nothing(self):
        outcome = self.ensure({"unchecked": "x@y is not a Gmail account this script reads"})
        self.assertEqual(outcome,
                         "arrival not checked (x@y is not a Gmail account this script reads)")
        self.assertEqual(self.gmail.calls, [])
        self.assertEqual(self.clock.sleeps, [])

    def test_an_api_error_mid_watch_is_unchecked_and_places_nothing(self):
        watch = self.watch()
        self.gmail.fail["list"] = OSError("connection reset")
        self.assertEqual(self.ensure(watch), "arrival not checked (OSError: connection reset)")
        self.assertEqual(self.gmail.count("insert"), 0)

    def test_a_failed_placement_raises_naming_both_facts(self):
        self.gmail.fail["insert"] = PermissionError("insufficient scope")
        with self.assertRaises(RuntimeError) as cm:
            self.ensure()
        message = str(cm.exception)
        self.assertIn(f"digest from {SENDER} to {RECIPIENT}: NOT arrived after 30s", message)
        self.assertIn("placing it in the inbox directly failed "
                      "(PermissionError: insufficient scope)", message)
        self.assertIsInstance(cm.exception.__cause__, PermissionError)
        self.assertIn("placing it in the inbox directly", self.log_text())

    def test_a_shorter_wait_is_honoured(self):
        _patch(self, "DELIVERY_WAIT_SECONDS", 6)
        self.assertEqual(self.ensure(), "NOT arrived after 6s — placed in the inbox directly")
        self.assertEqual(self.clock.sleeps, [2, 2, 2])

    def test_a_zero_wait_looks_once_and_places(self):
        _patch(self, "DELIVERY_WAIT_SECONDS", 0)
        self.assertEqual(self.ensure(), "NOT arrived after 0s — placed in the inbox directly")
        self.assertEqual(self.clock.sleeps, [])
        self.assertEqual(self.gmail.count("list"), 1)

    def test_the_defaults_are_the_real_clock_and_sleep(self):
        import inspect
        import time
        params = inspect.signature(US.ensure_delivered).parameters
        self.assertIs(params["sleep"].default, time.sleep)
        self.assertIs(params["clock"].default, time.monotonic)


class PlaceAndMessageTests(_Base):
    def test_the_message_round_trips_non_ascii_text(self):
        msg = US._digest_message(SENDER, SUBJECT, BODY)
        parsed = email.message_from_bytes(msg.as_bytes(), policy=email.policy.default)
        self.assertEqual(parsed["From"], SENDER)
        self.assertEqual(parsed["To"], RECIPIENT)
        self.assertEqual(parsed["Subject"], SUBJECT)
        self.assertTrue(parsed["Date"])
        self.assertTrue(parsed["Message-ID"])
        self.assertEqual(parsed.get_content_type(), "text/plain")
        self.assertEqual(parsed.get_content().rstrip("\n"), BODY)

    def test_two_messages_never_share_a_message_id(self):
        a = US._digest_message(SENDER, SUBJECT, BODY)
        b = US._digest_message(SENDER, SUBJECT, BODY)
        self.assertNotEqual(a["Message-ID"], b["Message-ID"])

    def test_place_inserts_unread_into_the_inbox_and_sends_nothing(self):
        US.gmail_place(self.gmail, SENDER, SUBJECT, BODY)
        self.assertEqual([op for op, _ in self.gmail.calls], ["insert"])
        _, kwargs = self.gmail.calls[0]
        self.assertEqual(set(kwargs), {"userId", "body"})
        self.assertEqual(set(kwargs["body"]), {"raw", "labelIds"})
        self.assertEqual(kwargs["body"]["labelIds"], ["INBOX", "UNREAD"])
        # urlsafe base64, as the API requires: no '+' or '/'.
        self.assertNotRegex(kwargs["body"]["raw"], r"[+/]")
        self.assertEqual(self.gmail.mailbox[0]["subject"], SUBJECT)
        self.assertEqual(self.gmail.placed()[0].get_content().rstrip("\n"), BODY)

    def test_the_smtp_fallback_sends_the_same_message_shape(self):
        # smtp_send builds its message through _digest_message too.
        sent = []

        class _Smtp:
            def __init__(self, host, port, timeout=None):
                pass

            def starttls(self, context=None):
                pass

            def login(self, user, password):
                pass

            def send_message(self, msg):
                sent.append(msg)

            def quit(self):
                pass
        _patch(self, "smtplib", mock.Mock(SMTP=_Smtp, SMTPException=Exception))
        US.smtp_send("fallback", {"email": "fallback@imap.example", "app_password": "x",
                                  "smtp_host": "127.0.0.1"}, SUBJECT, BODY)
        self.assertEqual(sent[0]["From"], "fallback@imap.example")
        self.assertEqual(sent[0]["To"], RECIPIENT)
        self.assertEqual(sent[0]["Subject"], SUBJECT)
        self.assertEqual(sent[0].get_content().rstrip("\n"), BODY)


if __name__ == "__main__":
    unittest.main()
