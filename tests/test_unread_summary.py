"""Characterization tests for UnreadSummary.py: the log-rotation wiring and
the layout of the emailed digest body.

Rotation: the mechanics are the shared myagent.helpers.rotate_log_if_needed
(pinned by test_log_rotation.py); these pin the module-global wrapper —
LOG_FILE and LOG_MAX_BYTES resolved at call time — same shape as
test_heartbeat.py.

Body: build_body is pure (dicts in, text out), so the digest layout is pinned
without touching any mailbox — in particular that the TOTAL section sits
between the header and the first account block (moved up from the footer
2026-09-10) and that the enumeration closes on a bare divider.

Digest file + viewer (2026-09-24): viewer_command's per-platform choice with
every lookup injected, write_digest's file, and show_digest's never-raise
contract with the launcher faked — no viewer is ever opened by the tests."""

import os
import subprocess
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

    def test_width_relays_out_the_file_copy(self):
        # The text-file copy (2026-09-24) is rendered at DIGEST_FILE_WIDTH:
        # the dividers are that long and no entry line wraps past it, where
        # the email keeps the instruction's 50-char dividers and WRAP column.
        long = " ".join(["word"] * 30)   # 149 chars: wraps in both layouts
        entries = {"one": [self._entry("one", long)]}
        entries["one"][0]["lines"] = [long]
        email = UnreadSummary.build_body(self.order, entries, {}, False)
        wide = UnreadSummary.build_body(self.order, entries, {}, False, width=80)
        self.assertEqual(UnreadSummary.DIGEST_FILE_WIDTH, 80)
        self.assertEqual(wide.splitlines()[0], "=" * 80)
        self.assertIn("\n" + "-" * 80 + "\nAccount: one@example.com (Gmail)\n"
                      + "-" * 80 + "\n", wide)
        self.assertNotIn(UnreadSummary.DIV, wide.splitlines())
        self.assertNotIn(UnreadSummary.SUB, wide.splitlines())
        self.assertLessEqual(max(len(line) for line in wide.splitlines()), 80)
        # ... and wider than the email's, which stays exactly as it was.
        self.assertGreater(max(len(line) for line in wide.splitlines()),
                           max(len(line) for line in email.splitlines()))
        self.assertEqual(email.splitlines()[0], UnreadSummary.DIV)
        # (only the indented entry lines wrap — the email's TOTAL header line
        # has always run past WRAP unwrapped, so the check is on those)
        self.assertLessEqual(max(len(line) for line in email.splitlines()
                                 if line.startswith("   ")), UnreadSummary.WRAP)
        # Same content in both: the numbering, the TOTAL line, the subject
        # words — only the layout differs.
        self.assertEqual([line for line in email.splitlines() if line.startswith("TOTAL")],
                         [line for line in wide.splitlines() if line.startswith("TOTAL")])
        def words(body):   # every token that is not a divider
            return [w for w in body.split() if set(w) - set("=-")]
        self.assertEqual(words(email), words(wide))

    def test_now_pins_the_generated_timestamp(self):
        from datetime import datetime
        stamp = datetime(2026, 9, 24, 7, 0, 0)
        body = UnreadSummary.build_body(self.order, self.entries, {}, False, now=stamp)
        self.assertIn("Generated 2026-09-24 07:00:00 by UnreadSummary.py", body)


class ViewerCommandTests(unittest.TestCase):
    """viewer_command is pure once the platform and lookups are injected."""

    PATH = os.path.join("some", "dir", "unread_summary.txt")

    def test_windows_prefers_notepadpp_on_path(self):
        argv = UnreadSummary.viewer_command(
            self.PATH, system="Windows",
            which=lambda name: r"C:\Tools\notepad++.exe" if name == "notepad++" else None,
            exists=lambda p: False, env={})
        self.assertEqual(argv, [r"C:\Tools\notepad++.exe", self.PATH])

    def test_windows_finds_notepadpp_in_program_files(self):
        pf = r"C:\Program Files"
        expected = os.path.join(pf, "Notepad++", "notepad++.exe")
        argv = UnreadSummary.viewer_command(
            self.PATH, system="Windows", which=lambda name: None,
            exists=lambda p: p == expected,
            env={"ProgramFiles": pf, "ProgramFiles(x86)": r"C:\Program Files (x86)"})
        self.assertEqual(argv, [expected, self.PATH])

    def test_windows_finds_per_user_notepadpp(self):
        local = r"C:\Users\me\AppData\Local"
        expected = os.path.join(local, "Programs", "Notepad++", "notepad++.exe")
        argv = UnreadSummary.viewer_command(
            self.PATH, system="Windows", which=lambda name: None,
            exists=lambda p: p == expected,
            env={"ProgramFiles": r"C:\Program Files", "LOCALAPPDATA": local})
        self.assertEqual(argv, [expected, self.PATH])

    def test_windows_falls_back_to_notepad(self):
        argv = UnreadSummary.viewer_command(
            self.PATH, system="Windows", which=lambda name: None,
            exists=lambda p: False, env={"ProgramFiles": r"C:\Program Files"})
        self.assertEqual(argv, ["notepad.exe", self.PATH])

    def test_macos_opens_the_default_text_editor(self):
        argv = UnreadSummary.viewer_command(self.PATH, system="Darwin",
                                            which=lambda name: None,
                                            exists=lambda p: True, env={})
        self.assertEqual(argv, ["open", "-t", self.PATH])

    def test_other_platforms_use_xdg_open(self):
        argv = UnreadSummary.viewer_command(self.PATH, system="Linux",
                                            which=lambda name: None,
                                            exists=lambda p: True, env={})
        self.assertEqual(argv, ["xdg-open", self.PATH])

    def test_defaults_answer_for_this_machine(self):
        # No injection: the real platform's answer names a real tool and
        # ends in the path — the shape the launcher gets on a live run.
        argv = UnreadSummary.viewer_command(self.PATH)
        self.assertEqual(argv[-1], self.PATH)
        self.assertTrue(argv[0])


class ShowDigestTests(unittest.TestCase):
    """write_digest replaces the file beside the log; show_digest launches the
    viewer through an injectable launcher and never raises."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.digest = Path(tmp.name) / "logs" / "unread_summary.txt"
        orig = UnreadSummary.DIGEST_FILE
        UnreadSummary.DIGEST_FILE = self.digest
        self.addCleanup(setattr, UnreadSummary, "DIGEST_FILE", orig)
        self.launched = []

    def _launch(self, argv, **kwargs):
        self.launched.append((argv, kwargs))

    def test_write_digest_creates_parent_and_replaces_previous(self):
        UnreadSummary.write_digest("first pass")
        path = UnreadSummary.write_digest("second pass")
        self.assertEqual(path, self.digest)
        self.assertEqual(self.digest.read_text(encoding="utf-8"), "second pass\n")

    def test_show_digest_writes_then_opens_the_file(self):
        outcome = UnreadSummary.show_digest("the digest", launch=self._launch)
        self.assertEqual(self.digest.read_text(encoding="utf-8"), "the digest\n")
        self.assertEqual(len(self.launched), 1)
        argv, kwargs = self.launched[0]
        self.assertEqual(argv, UnreadSummary.viewer_command(str(self.digest)))
        self.assertEqual(argv[-1], str(self.digest))
        # Fire-and-forget: no pipes to hold the viewer or the run open.
        self.assertEqual(kwargs, {"stdin": subprocess.DEVNULL,
                                  "stdout": subprocess.DEVNULL,
                                  "stderr": subprocess.DEVNULL})
        self.assertIn(str(self.digest), outcome)
        self.assertIn(f"opened with {argv[0]}", outcome)

    def test_no_view_writes_without_launching(self):
        outcome = UnreadSummary.show_digest("the digest", view=False,
                                            launch=self._launch)
        self.assertTrue(self.digest.exists())
        self.assertEqual(self.launched, [])
        self.assertEqual(outcome, f"digest written to {self.digest}")

    def test_viewer_failure_is_reported_not_raised(self):
        def boom(argv, **kwargs):
            raise FileNotFoundError("no such viewer")
        outcome = UnreadSummary.show_digest("the digest", launch=boom)
        self.assertTrue(self.digest.exists())
        self.assertIn("NOT opened", outcome)
        self.assertIn("FileNotFoundError: no such viewer", outcome)

    def test_unwritable_file_is_reported_not_raised(self):
        # A FILE where the parent directory should be: mkdir fails.
        self.digest.parent.parent.mkdir(parents=True, exist_ok=True)
        self.digest.parent.write_text("in the way", encoding="utf-8")
        outcome = UnreadSummary.show_digest("the digest", launch=self._launch)
        self.assertIn("NOT written", outcome)
        self.assertEqual(self.launched, [])


if __name__ == "__main__":
    unittest.main()
