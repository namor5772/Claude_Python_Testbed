"""UnreadSummary's second copy, and the texts it does not quote again
(2026-11-27, the evening of the delivery check).

Why it exists: the delivery check put a digest that had not arrived straight
into the recipient's inbox, and the emailed copy went on being discarded on
every pass. What discarded it was pinned that evening to the TEXT the digest
quotes: one search-ranking pitch in a Spam folder, its whole wording inside
the 45-word Summary line, sat in every dropped digest and in no delivered
one, and the same digest sent with that text left out arrived in 6 s. So a
digest that is not seen arriving is emailed again without the text of its
Spam / Junk folder entries before anything is placed, and the texts it had
quoted are noted — as SHA-256 keys in a file beside the log — so that a later
digest does not quote them and arrives on its first send.

Four things are pinned here, on tests/test_unread_delivery.py's in-memory
mailbox and fake clock (no mailbox, network or clock is touched, and the
notes file is a temp file — _Base repoints it, so no test can reach this
machine's own): the notes file; the three renderings of one digest
(``withhold``); second_copy, which prepares the copy; and the ladder inside
ensure_delivered — first copy, second copy, placement. main()'s side of it is
in tests/test_review_jobs.py's MainTests."""

import inspect
import json
import unittest
from datetime import date, datetime
from pathlib import Path

import UnreadSummary as US
from tests.test_unread_delivery import BODY, RECIPIENT, SENDER, _Base, _patch

# Read at import, before any test repoints them.
REAL_NOTES, REAL_LOG, REAL_WAIT = US.UNQUOTED_FILE, US.LOG_FILE, US.DELIVERY_WAIT_SECONDS

HOW = "without the text of its 2 Spam/Junk-folder entries"
PITCH = ("Hi, I noticed that your website is not ranking as well as it could be. "
         "We can place your website on the first page. May I send you a quote?")
NOTHING, NOTED, SPAM = US.WITHHOLD_NOTHING, US.WITHHOLD_NOTED, US.WITHHOLD_SPAM


def _entry(tag="", text="Opening words of the body.", subject="Hello",
           frm="Sender <sender@example.com>", spec=None):
    entry = {"provider": "IMAP", "account": "box", "account_email": "box@imap.example",
             "folder_tag": tag, "id": "7", "folder": "INBOX", "from": frm, "to": "",
             "subject": subject, "date": "Fri, 27 Nov 2026 09:00:00 +1100",
             "lines": [text] if text else []}
    if spec:
        entry["spec"] = spec
    return entry


def _noted(entry):
    return dict(entry, unquoted=True)


def _flat(text):
    return " ".join(text.split())


class RealPlacesTests(unittest.TestCase):
    def test_the_notes_sit_beside_the_log_under_their_own_name(self):
        self.assertEqual(REAL_NOTES.parent, REAL_LOG.parent)
        self.assertEqual(REAL_NOTES.name, "unread_summary_unquoted.json")

    def test_the_notes_file_is_ignored_by_git(self):
        # On Windows the log's folder is the repo root.
        ignored = (Path(US.BASE_DIR) / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("unread_summary_unquoted.json*", ignored)

    def test_the_wait_clears_the_slow_deliveries_and_the_callers_timeout(self):
        # Four of the 99 digests of the four weeks before took 24-27 s: the
        # wait leaves them a wide margin. And two whole waits — a first and a
        # second copy that never come — must still end well inside the 300 s
        # the Unread_Summary instruction gives the script.
        self.assertEqual(REAL_WAIT, 60)
        self.assertGreaterEqual(REAL_WAIT, 2 * 27)
        self.assertLessEqual(2 * REAL_WAIT + 60, 300)


class NoteLayoutTests(unittest.TestCase):
    def test_a_note_is_headed_and_wrapped_to_the_dividers(self):
        note = US._note("placed in this mailbox directly — " + "word " * 30)
        self.assertTrue(note.startswith("NOTE: placed in this mailbox directly"))
        self.assertGreater(len(note.splitlines()), 2)
        self.assertLessEqual(max(len(line) for line in note.splitlines()), len(US.DIV))

    def test_a_note_never_breaks_at_a_hyphen(self):
        # An address, or "Spam/Junk-folder", stays in one piece — whichever
        # column the hyphen happens to fall on.
        for pad in range(20, 50):
            note = US._note("x" * pad + " lee-ann@a-b.example Spam/Junk-folder end")
            for line in note.splitlines():
                self.assertFalse(line.endswith("-"), (pad, note))
            self.assertIn("lee-ann@a-b.example", note)
            self.assertIn("Spam/Junk-folder", note)


class NotesFileTests(_Base):
    def test_a_key_is_a_sha256_that_ignores_case_and_spacing(self):
        key = US.text_key(PITCH)
        self.assertRegex(key, r"^[0-9a-f]{64}$")
        self.assertEqual(US.text_key("  " + PITCH.upper().replace(" ", "\n  ") + " "), key)
        self.assertNotEqual(US.text_key(PITCH + " Thanks"), key)

    def test_no_file_is_nothing_noted_and_reading_creates_none(self):
        self.assertEqual(US.load_unquoted(), {})
        self.assertFalse(self.notes_file.exists())

    def test_noting_writes_the_keys_under_the_date_and_never_the_text(self):
        US.note_unquoted([US.text_key(PITCH)], today=date(2026, 11, 27))
        raw = self.notes_file.read_text(encoding="utf-8")
        self.assertEqual(json.loads(raw), {"texts": {US.text_key(PITCH): "2026-11-27"}})
        self.assertNotIn("website", raw)
        self.assertEqual(US.load_unquoted(today=date(2026, 11, 27)),
                         {US.text_key(PITCH): "2026-11-27"})
        # Written whole and swapped in: no half-written file is left behind.
        self.assertEqual([p.name for p in self.notes_file.parent.iterdir()
                          if p.name.endswith(".tmp")], [])
        self.assertIn("1 text(s) noted in delivery_unquoted.json: later digests will "
                      "not quote them", self.log_text())

    def test_noting_keeps_what_was_there_and_redates_a_key_noted_again(self):
        US.note_unquoted(["a" * 64, "b" * 64], today=date(2026, 11, 1))
        US.note_unquoted(["b" * 64, "c" * 64], today=date(2026, 11, 27))
        self.assertEqual(US.load_unquoted(today=date(2026, 11, 27)),
                         {"a" * 64: "2026-11-01", "b" * 64: "2026-11-27",
                          "c" * 64: "2026-11-27"})

    def test_a_generator_of_keys_is_noted_whole(self):
        US.note_unquoted((letter * 64 for letter in "ab"), today=date(2026, 11, 27))
        self.assertEqual(set(US.load_unquoted(today=date(2026, 11, 27))),
                         {"a" * 64, "b" * 64})
        self.assertIn("2 text(s) noted", self.log_text())

    def test_a_note_lapses_after_the_keep_days(self):
        _patch(self, "UNQUOTED_KEEP_DAYS", 90)
        US.note_unquoted(["a" * 64], today=date(2026, 8, 29))
        self.assertIn("a" * 64, US.load_unquoted(today=date(2026, 11, 27)))   # day 90
        self.assertEqual(US.load_unquoted(today=date(2026, 11, 28)), {})      # day 91

    def test_a_lapsed_note_leaves_the_file_at_the_next_noting(self):
        US.note_unquoted(["a" * 64], today=date(2026, 1, 1))
        US.note_unquoted(["b" * 64], today=date(2026, 11, 27))
        self.assertEqual(json.loads(self.notes_file.read_text(encoding="utf-8")),
                         {"texts": {"b" * 64: "2026-11-27"}})

    def test_an_unreadable_file_is_nothing_noted_and_is_written_over(self):
        for junk in ("{not json", "[]", '{"texts": 5}', '{"texts": {"k": "soon"}}', ""):
            self.notes_file.write_text(junk, encoding="utf-8")
            self.assertEqual(US.load_unquoted(), {}, junk)
        US.note_unquoted(["a" * 64], today=date(2026, 11, 27))
        self.assertEqual(US.load_unquoted(today=date(2026, 11, 27)),
                         {"a" * 64: "2026-11-27"})

    def test_a_file_that_cannot_be_written_is_a_log_line_not_a_raise(self):
        blocker = self.notes_file.parent / "a_file_where_a_folder_should_be"
        blocker.write_text("x", encoding="utf-8")
        _patch(self, "UNQUOTED_FILE", blocker / "notes.json")
        US.note_unquoted(["a" * 64])                       # must not raise
        self.assertIn("texts NOT noted in notes.json (", self.log_text())
        self.assertNotIn("text(s) noted in", self.log_text())

    def test_the_folder_is_made_when_it_is_not_there(self):
        _patch(self, "UNQUOTED_FILE", self.notes_file.parent / "new" / "deeper" / "n.json")
        US.note_unquoted(["a" * 64], today=date(2026, 11, 27))
        self.assertEqual(set(US.load_unquoted(today=date(2026, 11, 27))), {"a" * 64})


class SummaryTextTests(unittest.TestCase):
    def test_the_file_rendering_quotes_everything(self):
        for entry in (_entry(), _entry("SPAM", PITCH), _entry("JUNK", PITCH),
                      _noted(_entry("SPAM", PITCH)), _noted(_entry("", PITCH))):
            self.assertEqual(US.summary_text(entry),
                             US.summarize(entry["lines"], entry["subject"]))
            self.assertEqual(US.summary_text(entry, NOTHING), US.summary_text(entry))

    def test_the_first_copy_leaves_out_only_the_noted_texts(self):
        self.assertEqual(US.summary_text(_entry("SPAM", PITCH), NOTED), PITCH)
        self.assertEqual(US.summary_text(_entry("", PITCH), NOTED), PITCH)
        self.assertEqual(US.summary_text(_noted(_entry("SPAM", PITCH)), NOTED),
                         US.NOT_QUOTED_NOTED)
        # ... wherever the message sits: a follow-up that reached an inbox.
        self.assertEqual(US.summary_text(_noted(_entry("", PITCH)), NOTED),
                         US.NOT_QUOTED_NOTED)

    def test_the_second_copy_leaves_out_spam_and_junk_text_as_well(self):
        for tag in ("SPAM", "JUNK"):
            self.assertEqual(US.summary_text(_entry(tag, PITCH), SPAM), US.NOT_QUOTED_HERE)
        self.assertEqual(US.summary_text(_entry("", PITCH), SPAM), PITCH)   # an inbox text stays
        self.assertEqual(US.summary_text(_noted(_entry("SPAM", PITCH)), SPAM),
                         US.NOT_QUOTED_NOTED)
        self.assertEqual(US.summary_text(_noted(_entry("", PITCH)), SPAM),
                         US.NOT_QUOTED_NOTED)

    def test_a_message_without_text_says_so_in_every_rendering(self):
        for withhold in (NOTHING, NOTED, SPAM):
            for entry in (_entry("SPAM", ""), _noted(_entry("SPAM", "")), _entry("", "")):
                self.assertEqual(US.summary_text(entry, withhold), US.NO_BODY_TEXT)
        self.assertEqual(US.summarize([], "Hello"), US.NO_BODY_TEXT)
        self.assertEqual(US.NO_BODY_TEXT, "(no readable body text)")

    def test_each_level_leaves_out_more(self):
        self.assertLess(NOTHING, NOTED)
        self.assertLess(NOTED, SPAM)
        self.assertEqual(inspect.signature(US.build_body).parameters["withhold"].default,
                         NOTHING)
        self.assertEqual(inspect.signature(US.format_entry).parameters["withhold"].default,
                         NOTHING)

    def test_both_markers_fit_on_the_emails_summary_line(self):
        for marker in (US.NOT_QUOTED_NOTED, US.NOT_QUOTED_HERE):
            line = US._field("Summary:", marker)
            self.assertNotIn("\n", line)
            self.assertLessEqual(len(line), US.WRAP)
        # Each says its own reason: the reader of one copy is not sent to a
        # NOTE that only the other carries.
        self.assertEqual(US.NOT_QUOTED_NOTED,
                         "(not quoted — a digest quoting it did not arrive)")
        self.assertEqual(US.NOT_QUOTED_HERE,
                         "(not quoted in this copy — see the NOTE above)")

    def test_quotes_spam_text_is_what_the_second_copy_would_leave_out(self):
        self.assertTrue(US.quotes_spam_text(_entry("SPAM", PITCH)))
        self.assertTrue(US.quotes_spam_text(_entry("JUNK", PITCH)))
        self.assertFalse(US.quotes_spam_text(_entry("", PITCH)))             # not Spam / Junk
        self.assertFalse(US.quotes_spam_text(_entry("SPAM", "")))            # nothing to quote
        self.assertFalse(US.quotes_spam_text(_noted(_entry("SPAM", PITCH))))  # not quoted anyway

    def test_mark_unquoted_flags_a_noted_text_in_any_folder(self):
        inbox, spam = _entry("", PITCH.upper()), _entry("SPAM", PITCH)
        other, empty = _entry("SPAM", "Other words."), _entry("SPAM", "")
        noted = {US.text_key(PITCH): "2026-11-27", US.text_key(US.NO_BODY_TEXT): "2026-11-27"}
        self.assertEqual(US.mark_unquoted({"a": [inbox, other], "b": [spam, empty]}, noted), 2)
        self.assertIs(inbox.get("unquoted"), True)
        self.assertIs(spam.get("unquoted"), True)
        self.assertNotIn("unquoted", other)
        self.assertNotIn("unquoted", empty)      # "no body text" is never a noted text

    def test_nothing_noted_flags_nothing(self):
        entries = {"a": [_entry("SPAM", PITCH), _entry()]}
        self.assertEqual(US.mark_unquoted(entries, {}), 0)
        self.assertEqual([e for e in entries["a"] if "unquoted" in e], [])
        self.assertEqual(US.mark_unquoted({}, {US.text_key(PITCH): "2026-11-27"}), 0)

    def test_the_key_is_of_the_summary_as_it_is_quoted(self):
        # A long text is noted by the 45 words (and the ellipsis) the digest
        # quotes, so a message with the same opening is the same text.
        long_a = " ".join(f"word{i}" for i in range(60))
        long_b = long_a + " and a different ending"
        entry = _entry("", long_b)
        noted = {US.text_key(US.summarize([long_a], "Hello")): "2026-11-27"}
        self.assertEqual(US.mark_unquoted({"a": [entry]}, noted), 1)


def _without_summaries(body):
    """The body's lines, the Summary lines and their continuations taken out."""
    out, skipping = [], False
    for line in body.splitlines():
        if line.startswith("   Summary:"):
            skipping = True
            continue
        if skipping and line.startswith(" " * 12):
            continue
        skipping = False
        out.append(line)
    return out


class BodyRenderingTests(unittest.TestCase):
    def setUp(self):
        self.order = [("one", "one@example.com (Gmail)"), ("two", "two@example.com (IMAP)")]
        self.spam = _entry("SPAM", PITCH, subject="Re: May I send you a quote?",
                           frm="Pitcher <p@pitch.example>")
        self.bill = _entry("JUNK", "Your bill of 40 dollars is due.", subject="Your bill",
                           frm="Biller <b@biller.example>",
                           spec={"type": "bill", "index": "7", "determine": "Amount due"})
        self.bill["pdfs"] = ["bill.pdf"]
        self.entries = {"one": [_entry("", "Inbox words stay.")],
                        "two": [self.spam, self.bill]}
        self.now = datetime(2026, 11, 27, 21, 0, 0)

    def body(self, withhold, **kwargs):
        return US.build_body(self.order, self.entries, {}, False, now=self.now,
                             withhold=withhold, **kwargs)

    def test_the_default_rendering_is_the_whole_digest(self):
        whole = US.build_body(self.order, self.entries, {}, False, now=self.now)
        self.assertEqual(whole, self.body(NOTHING))
        self.assertIn(PITCH, _flat(whole))
        self.assertIn("40 dollars", whole)

    def test_with_nothing_noted_the_first_copy_is_the_whole_digest(self):
        self.assertEqual(self.body(NOTED), self.body(NOTHING))

    def test_the_second_copy_differs_in_the_spam_and_junk_summary_lines_only(self):
        whole, second = self.body(NOTHING), self.body(SPAM)
        self.assertNotIn("We can place your website", _flat(second))
        self.assertNotIn("40 dollars", second)
        self.assertIn("Inbox words stay.", second)                # an inbox entry's text
        self.assertEqual(second.count(US.NOT_QUOTED_HERE), 2)
        self.assertEqual(_without_summaries(whole), _without_summaries(second))
        self.assertNotEqual(whole, second)

    def test_who_what_and_when_stay_in_the_second_copy(self):
        lines = self.body(SPAM).splitlines()
        for line in ("2. Account:  box@imap.example [SPAM]",
                     "   From:    Pitcher <p@pitch.example>",
                     "   Subject: Re: May I send you a quote?",
                     "   Date:    Fri, 27 Nov 2026 09:00:00 +1100",
                     f"   Summary: {US.NOT_QUOTED_HERE}"):
            self.assertIn(line, lines)

    def test_a_specifying_match_in_junk_keeps_its_own_lines(self):
        lines = self.body(SPAM).splitlines()
        for line in ("3. SPECIFYING LIST EMAIL", "   Type=bill, Index=7",
                     "   Account:  box@imap.example [JUNK]", "   PDFs:    bill.pdf",
                     "   Determine: Amount due"):
            self.assertIn(line, lines)

    def test_the_numbering_and_the_total_never_differ(self):
        for withhold in (NOTHING, NOTED, SPAM):
            body = self.body(withhold)
            self.assertIn("TOTAL: 3 unread email(s) across 2 account(s); "
                          "1 SPECIFYING match(es)", body)
            self.assertIn("\n1. Account:  box@imap.example\n", body)
            self.assertIn("\n2. Account:  box@imap.example [SPAM]\n", body)
            self.assertIn("\n3. SPECIFYING LIST EMAIL\n", body)
            self.assertNotIn("\n4. ", body)
            self.assertIn("Generated 2026-11-27 21:00:00 by UnreadSummary.py", body)

    def test_a_noted_text_is_out_of_both_emailed_copies_and_in_the_file(self):
        self.spam["unquoted"] = True
        first, second = self.body(NOTED), self.body(SPAM)
        self.assertNotIn("We can place your website", _flat(first))
        self.assertEqual(first.count(US.NOT_QUOTED_NOTED), 1)
        self.assertIn("40 dollars", first)          # the other Junk text: still quoted
        self.assertEqual(second.count(US.NOT_QUOTED_NOTED), 1)
        self.assertEqual(second.count(US.NOT_QUOTED_HERE), 1)
        on_file = self.body(NOTHING, width=US.DIGEST_FILE_WIDTH)
        self.assertIn(PITCH, _flat(on_file))
        self.assertNotIn("not quoted", on_file)

    def test_the_wide_layout_takes_the_level_too(self):
        wide = self.body(SPAM, width=80)
        self.assertEqual(wide.count(US.NOT_QUOTED_HERE), 2)
        self.assertLessEqual(max(len(line) for line in wide.splitlines()), 80)

    def test_format_entry_alone_takes_the_level(self):
        self.assertIn(US.NOT_QUOTED_HERE, US.format_entry(2, self.spam, withhold=SPAM))
        self.assertIn("ranking", US.format_entry(2, self.spam, withhold=NOTED))
        self.assertIn("ranking", US.format_entry(2, self.spam))


class SecondCopyTests(_Base):
    def setUp(self):
        super().setUp()
        self.built = 0
        self.sent = []
        _patch(self, "DIGEST_FILE", Path("some folder") / "unread_summary.txt")

    def build(self):
        self.built += 1
        return "THE DIGEST WITHOUT ITS SPAM TEXT"

    def send(self, body):
        self.sent.append(body)
        return "from@outlook.example"

    def second(self, *entries):
        return US.second_copy({"acct": list(entries)}, self.build, self.send)

    def test_no_spam_text_quoted_means_no_second_copy(self):
        self.assertIsNone(self.second())
        self.assertIsNone(self.second(_entry("", PITCH)))
        self.assertIsNone(self.second(_entry("SPAM", "")))
        self.assertIsNone(self.second(_noted(_entry("SPAM", PITCH))))
        self.assertIsNone(US.second_copy({}, self.build, self.send))
        self.assertEqual((self.built, self.sent), (0, []))

    def test_how_counts_the_spam_and_junk_entries_whose_text_is_quoted(self):
        how, _ = self.second(_entry("SPAM", PITCH))
        self.assertEqual(how, "without the text of its 1 Spam/Junk-folder entry")
        how, _ = self.second(_entry("SPAM", PITCH), _entry("JUNK", "Other."),
                             _entry("", "Inbox."), _entry("SPAM", ""),
                             _noted(_entry("JUNK", "Noted before.")))
        self.assertEqual(how, HOW)

    def test_entries_are_counted_across_every_account(self):
        how, _ = US.second_copy({"a": [_entry("SPAM", PITCH)], "b": [_entry("JUNK", "Other.")],
                                 "c": []}, self.build, self.send)
        self.assertEqual(how, HOW)

    def test_preparing_the_copy_builds_sends_and_notes_nothing(self):
        self.second(_entry("SPAM", PITCH))
        self.assertEqual((self.built, self.sent), (0, []))
        self.assertFalse(self.notes_file.exists())
        self.assertEqual(self.log_text(), "")

    def test_sending_it_emails_the_rendering_under_a_note(self):
        _, send_again = self.second(_entry("SPAM", PITCH), _entry("JUNK", "Other."))
        self.assertEqual(send_again(), "from@outlook.example")    # the address it went from
        self.assertEqual(self.built, 1)
        note, _, rest = self.sent[0].partition("\n\n")
        self.assertEqual(rest, "THE DIGEST WITHOUT ITS SPAM TEXT")
        self.assertEqual(
            _flat(note),
            "NOTE: a second copy, sent without the text of its 2 Spam/Junk-folder entries "
            "— the first, which quoted that text, had not arrived after 30 s. The whole "
            "digest is in unread_summary.txt on the machine that ran this pass.")
        self.assertLessEqual(max(len(line) for line in note.splitlines()), len(US.DIV))
        self.assertEqual(len(self.sent), 1)

    def test_sending_it_notes_the_spam_and_junk_texts_and_only_those(self):
        _, send_again = self.second(_entry("SPAM", PITCH), _entry("", "Inbox words."),
                                    _entry("JUNK", "Other."), _entry("SPAM", ""))
        send_again()
        self.assertEqual(set(US.load_unquoted()), {US.text_key(PITCH), US.text_key("Other.")})
        self.assertIn("2 text(s) noted in delivery_unquoted.json", self.log_text())

    def test_the_texts_are_noted_before_the_send_is_tried(self):
        # That a digest quoting them was not seen arriving is known by then,
        # whatever becomes of the second copy.
        def failing(body):
            self.assertEqual(set(US.load_unquoted()), {US.text_key(PITCH)})
            raise RuntimeError("Graph 503 ServiceUnavailable")
        _, send_again = US.second_copy({"a": [_entry("SPAM", PITCH)]}, self.build, failing)
        with self.assertRaisesRegex(RuntimeError, "Graph 503"):
            send_again()
        self.assertEqual(set(US.load_unquoted()), {US.text_key(PITCH)})

    def test_a_noted_text_is_then_flagged_and_offers_no_second_copy(self):
        _, send_again = self.second(_entry("SPAM", PITCH))
        send_again()
        # The campaign's follow-up: the same words, and in an inbox this time.
        later = _entry("", "  " + PITCH.replace(". ", ".\n"))
        later["lines"] = later["lines"][0].split("\n")
        self.assertEqual(US.mark_unquoted({"a": [later]}, US.load_unquoted()), 1)
        self.assertEqual(US.summary_text(later, NOTED), US.NOT_QUOTED_NOTED)
        again = _entry("SPAM", PITCH)
        US.mark_unquoted({"a": [again]}, US.load_unquoted())
        self.assertIsNone(self.second(again))


class LadderTests(_Base):
    """ensure_delivered with a second copy to offer: first copy, second copy,
    placement — each step taken only when the one before it failed."""

    def second(self, after=None, labels=("INBOX", "UNREAD"), error=None, then=None):
        self.again = []                 # the fake clock at each second send

        def send_again():
            self.again.append(self.clock.now)
            if error:
                raise error
            if then:
                then()
            if after is not None:
                self.arrives(after, labels=labels)
            return SENDER
        return HOW, send_again

    def placed_note(self):
        return _flat(self.gmail.placed()[0].get_content().partition("\n\n")[0])

    def test_the_second_copy_is_off_by_default(self):
        self.assertIsNone(inspect.signature(US.ensure_delivered).parameters["second"].default)

    def test_a_first_copy_that_arrives_never_sends_the_second(self):
        self.arrives(after=4)
        self.assertEqual(self.ensure(second=self.second(after=1)), "arrived after 4s")
        self.assertEqual(self.again, [])
        self.assertEqual(self.gmail.count("insert"), 0)

    def test_a_first_copy_arriving_on_the_last_look_never_sends_the_second(self):
        self.arrives(after=30)
        self.assertEqual(self.ensure(second=self.second(after=1)), "arrived after 30s")
        self.assertEqual(self.again, [])

    def test_a_first_copy_under_spam_is_placed_without_a_second(self):
        # It did arrive: the text was not what kept it out of the mailbox.
        self.arrives(after=4, labels=("SPAM", "UNREAD"))
        self.assertEqual(self.ensure(second=self.second(after=1)),
                         "arrived in Spam after 4s — placed in the inbox directly")
        self.assertEqual(self.again, [])
        self.assertEqual(self.gmail.count("insert"), 1)

    def test_the_second_copy_goes_out_only_after_the_whole_wait(self):
        outcome = self.ensure(second=self.second(after=4))
        self.assertEqual(outcome, f"NOT arrived after 30s — sent again {HOW}: arrived after 4s")
        self.assertEqual(self.again, [1030.0])                 # once, at the end of the wait
        self.assertEqual(sum(self.clock.sleeps), 34)
        self.assertEqual(self.gmail.count("insert"), 0)        # nothing placed
        self.assertIn(f"digest from {SENDER} to {RECIPIENT}: NOT arrived after 30s — "
                      f"sending it again {HOW}", self.log_text())
        self.assertNotIn("placing it in the inbox", self.log_text())

    def test_the_second_wait_is_counted_from_the_second_send(self):
        outcome = self.ensure(second=self.second(after=30))
        self.assertEqual(outcome, f"NOT arrived after 30s — sent again {HOW}: arrived after 30s")
        self.assertEqual(sum(self.clock.sleeps), 60)
        self.assertEqual(self.gmail.count("insert"), 0)

    def test_a_first_copy_turning_up_while_the_second_is_awaited_counts(self):
        # Either copy in the mailbox is the digest delivered.
        self.arrives(after=36)
        outcome = self.ensure(second=self.second())
        self.assertEqual(outcome, f"NOT arrived after 30s — sent again {HOW}: arrived after 6s")
        self.assertEqual(self.gmail.count("insert"), 0)

    def test_a_second_copy_under_spam_is_placed(self):
        outcome = self.ensure(second=self.second(after=2, labels=("SPAM", "UNREAD")))
        self.assertEqual(outcome, f"NOT arrived after 30s — sent again {HOW}: arrived in Spam "
                                  f"after 2s — placed in the inbox directly")
        self.assertEqual(self.placed_note(),
                         f"NOTE: placed in this mailbox directly — the copy emailed from "
                         f"{SENDER} had not arrived after 30 s, and a second, sent {HOW}, "
                         f"was filed under Spam.")
        # What is placed is the first copy, whole: it passes no filter.
        placed = self.gmail.placed()[0].get_content()
        self.assertEqual(placed.partition("\n\n")[2].rstrip("\n"), BODY)

    def test_neither_copy_arriving_places_the_first_copy_whole(self):
        outcome = self.ensure(second=self.second())
        self.assertEqual(outcome, f"NOT arrived after 30s — sent again {HOW}: NOT arrived "
                                  f"after 30s either — placed in the inbox directly")
        self.assertEqual(sum(self.clock.sleeps), 60)           # two waits, no more
        self.assertEqual(len(self.again), 1)                   # the second copy: sent once
        self.assertEqual(self.gmail.count("insert"), 1)
        self.assertEqual(self.placed_note(),
                         f"NOTE: placed in this mailbox directly — neither the copy emailed "
                         f"from {SENDER} nor a second, sent {HOW}, had arrived after 30 s.")
        text = self.gmail.placed()[0].get_content()
        note, _, rest = text.partition("\n\n")
        self.assertLessEqual(max(len(line) for line in note.splitlines()), len(US.DIV))
        self.assertEqual(rest.rstrip("\n"), BODY)
        log = self.log_text()
        self.assertLess(log.index("sending it again"), log.index("placing it in the inbox"))
        self.assertIn(f"NOT arrived after 30s — sent again {HOW}: NOT arrived after 30s "
                      f"either — placing it in the inbox directly", log)

    def test_a_second_copy_that_cannot_be_sent_leaves_the_placement(self):
        outcome = self.ensure(second=self.second(
            error=RuntimeError("Graph 503 ServiceUnavailable")))
        self.assertEqual(outcome, "NOT arrived after 30s — placed in the inbox directly")
        self.assertEqual(sum(self.clock.sleeps), 30)           # nothing more to wait for
        self.assertEqual(self.placed_note(),
                         f"NOTE: placed in this mailbox directly — the copy emailed from "
                         f"{SENDER} had not arrived after 30 s.")
        self.assertIn("second copy NOT sent (RuntimeError: Graph 503 ServiceUnavailable)",
                      self.log_text())

    def test_an_api_error_while_the_second_is_awaited_is_unchecked(self):
        def break_the_api():
            self.gmail.fail["list"] = OSError("connection reset")
        outcome = self.ensure(second=self.second(then=break_the_api))
        self.assertEqual(outcome, f"NOT arrived after 30s — sent again {HOW}: arrival not "
                                  f"checked (OSError: connection reset)")
        self.assertEqual(self.gmail.count("insert"), 0)

    def test_an_api_error_on_the_first_watch_sends_no_second(self):
        watch = self.watch()
        self.gmail.fail["list"] = OSError("connection reset")
        self.assertEqual(self.ensure(watch, second=self.second(after=1)),
                         "arrival not checked (OSError: connection reset)")
        self.assertEqual(self.again, [])

    def test_an_unchecked_watch_sends_no_second(self):
        outcome = self.ensure({"unchecked": "the delivery check is turned off"},
                              second=self.second(after=1))
        self.assertEqual(outcome, "arrival not checked (the delivery check is turned off)")
        self.assertEqual(self.again, [])

    def test_a_failed_placement_after_two_copies_names_all_of_it(self):
        self.gmail.fail["insert"] = PermissionError("insufficient scope")
        with self.assertRaises(RuntimeError) as cm:
            self.ensure(second=self.second())
        self.assertEqual(
            str(cm.exception),
            f"digest from {SENDER} to {RECIPIENT}: NOT arrived after 30s — sent again {HOW}: "
            f"NOT arrived after 30s either, and placing it in the inbox directly failed "
            f"(PermissionError: insufficient scope)")
        self.assertIsInstance(cm.exception.__cause__, PermissionError)

    def test_a_zero_wait_looks_once_for_each_copy(self):
        _patch(self, "DELIVERY_WAIT_SECONDS", 0)
        outcome = self.ensure(second=self.second(after=0))
        self.assertEqual(outcome, f"NOT arrived after 0s — sent again {HOW}: arrived after 0s")
        self.assertEqual(self.clock.sleeps, [])
        self.assertEqual(self.gmail.count("list"), 2)


if __name__ == "__main__":
    unittest.main()
