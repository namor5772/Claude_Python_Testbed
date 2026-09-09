"""Characterization tests for UnreadSummary.py: the log-rotation wiring and
the layout of the emailed digest body.

Rotation: the mechanics are the shared myagent.helpers.rotate_log_if_needed
(pinned by test_log_rotation.py); these pin the module-global wrapper —
LOG_FILE and LOG_MAX_BYTES resolved at call time — same shape as
test_heartbeat.py.

Body: build_body is pure (dicts in, text out), so the digest layout is pinned
without touching any mailbox — in particular that the TOTAL section sits
between the header and the first account block (moved up from the footer
2026-09-10) and that the enumeration closes on a bare divider."""

import tempfile
import unittest
from pathlib import Path

import UnreadSummary


class RotateLogTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log = Path(tmp.name) / "unread_summary.log"
        self.old = self.log.with_name("unread_summary.log.old")
        orig = UnreadSummary.LOG_FILE
        UnreadSummary.LOG_FILE = self.log
        self.addCleanup(setattr, UnreadSummary, "LOG_FILE", orig)

    def test_under_cap_is_untouched(self):
        self.log.write_text("one line\n", encoding="utf-8")
        UnreadSummary.rotate_log_if_needed()
        self.assertEqual(self.log.read_text(encoding="utf-8"), "one line\n")
        self.assertFalse(self.old.exists())

    def test_over_cap_archives_and_seeds_marker(self):
        content = "x" * (UnreadSummary.LOG_MAX_BYTES + 1)
        self.log.write_text(content, encoding="utf-8")
        UnreadSummary.rotate_log_if_needed()
        self.assertEqual(self.old.read_text(encoding="utf-8"), content)
        fresh = self.log.read_text(encoding="utf-8")
        self.assertIn("log restarted", fresh)
        self.assertIn("unread_summary.log.old", fresh)


class BuildBodyTests(unittest.TestCase):
    """Since 2026-09-10 the divider-framed TOTAL section (count, SPECIFYING
    tally, any ERRORS line) sits between the header and the first account
    block instead of at the foot, so it is read before scrolling through the
    enumeration; the listing then closes on a bare divider."""

    @staticmethod
    def _entry(account, subject, spec=None):
        entry = {"provider": "Gmail", "account": account,
                 "account_email": f"{account}@example.com", "folder_tag": "",
                 "id": "1", "from": "Sender <sender@example.com>", "to": "",
                 "subject": subject, "date": "Thu, 10 Sep 2026 06:00:00 +1000",
                 "lines": ["Opening words of the body."]}
        if spec:
            entry["spec"] = spec
        return entry

    def setUp(self):
        self.order = [("one", "one@example.com (Gmail)"),
                      ("two", "two@example.com (IMAP)"),
                      ("three", "three@example.com (Outlook)")]
        self.entries = {
            "one": [self._entry("one", "Hello"),
                    self._entry("one", "Bill", spec={"type": "Bill", "index": "3",
                                                     "determine": "note"})],
            "three": [self._entry("three", "Another")],
        }
        self.errors = {"two": "ConnectionRefusedError: [Errno 61]"}

    @staticmethod
    def _total_index(lines):
        return next(i for i, line in enumerate(lines) if line.startswith("TOTAL: "))

    def test_total_section_precedes_first_account_block(self):
        body = UnreadSummary.build_body(self.order, self.entries, self.errors, False)
        lines = body.splitlines()
        total = self._total_index(lines)
        self.assertLess(total, lines.index("Account: one@example.com (Gmail)"))
        self.assertEqual(
            lines[total],
            "TOTAL: 3 unread email(s) across 3 account(s); 1 SPECIFYING match(es)")
        # Framed by the 50-char dividers, with the ERRORS line inside the
        # frame — and it points DOWN now, since the per-account ERROR lines
        # follow it.
        self.assertEqual(lines[total - 1], UnreadSummary.DIV)
        self.assertEqual(lines[total + 1],
                         "ERRORS: 1 account(s) unreadable — see below")
        self.assertEqual(lines[total + 2], UnreadSummary.DIV)
        self.assertNotIn("see above", body)

    def test_total_is_the_last_sequence_number(self):
        # The count is the last number the enumeration used, so the two
        # cannot drift apart; the errored account contributes nothing.
        body = UnreadSummary.build_body(self.order, self.entries, self.errors, False)
        self.assertIn("\n3. Account:  three@example.com", body)
        self.assertNotIn("\n4. ", body)
        self.assertIn("\nERROR: ConnectionRefusedError: [Errno 61]", body)

    def test_footer_is_a_bare_divider(self):
        body = UnreadSummary.build_body(self.order, self.entries, {}, True)
        lines = body.splitlines()
        self.assertEqual(lines[-1], UnreadSummary.DIV)
        self.assertEqual(lines[-2], "")   # blank line closing the last entry
        self.assertEqual(sum(1 for line in lines if line.startswith("TOTAL: ")), 1)
        self.assertNotIn("ERRORS:", body)
        # The dry-run banner stays in the header, above the TOTAL frame.
        self.assertLess(lines.index("*** DRY RUN: no emails were modified ***"),
                        self._total_index(lines))


if __name__ == "__main__":
    unittest.main()
