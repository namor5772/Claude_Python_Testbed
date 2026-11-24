"""The cost log's clock: UTC, marked with a trailing Z (2026-11-24).

The user could not reconcile the API Cost Log's days with the providers' usage
pages. The providers book usage by the UTC day and month — Anthropic's usage
and cost reports bucket by UTC, OpenAI's usage dashboard shows its data in
UTC — while both apps stamped a line with the machine's own clock
(`datetime.now()`): in Sydney, UTC+10 in winter and +11 in summer, every run
before 10:00 / 11:00 local sat on the provider's PREVIOUS day, and the first
hours of a month on the previous month.

Now a stamp is UTC and says so — "2026-11-23 23:56:26Z" — made in one place
for both apps (`datapaths.costlog_stamp`), and the lines already in a log are
converted where they stand, by the machine that wrote them, the first time an
updated app starts there (`datapaths.migrate_costlog_to_utc`, run by
`resolve_costlog`). The mark is per LINE, which is what makes the pass
idempotent and lets three machines change over in any order: a copy of the app
from before the change keeps appending unmarked local stamps, and they are
converted at that machine's next updated launch. No machine rewrites another's
log (the rule that keeps the per-machine files free of OneDrive conflict
forks); the viewers convert a not-yet-migrated machine's lines for display —
`tests/test_costlog_viewer_days.py`'s UtcClockTests, on the real viewer.

Pinned here: the stamp's form and its conversions on Sydney's own rules (both
offsets, the day the clocks change, a month and a year boundary) and on the
machine's zone against the C library; the conversion of a file — only the
stamps change, CRLF endings and every other byte survive, the original is
kept beside it once, a second pass is a no-op, a later stray line is picked
up, and the log is left alone when it changed underneath or cannot be backed
up; `resolve_costlog` running it on both the shared and the repo-root path;
both writers stamping in UTC; and SelfBot's package-absent stand-in agreeing
with the original.
"""

import ast
import datetime
import inspect
import os
import pathlib
import re
import tempfile
import time
import unittest
from unittest import mock

import myagent.streaming_mixin as sm
from myagent import datapaths as dp
from tests.test_datapaths import DatapathsCase

ROOT = pathlib.Path(__file__).resolve().parents[1]
STAMP = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}Z$")
FMT = "%Y-%m-%d %H:%M:%S"


class _Sydney(datetime.tzinfo):
    """Australia/Sydney as its rules stand — UTC+10, and +11 from 02:00 on
    the first Sunday of October until 03:00 on the first Sunday of April —
    written out here so the tests need no time-zone database (Windows ships
    none for zoneinfo) and do not depend on the zone of the machine they run
    on."""

    @staticmethod
    def _first_sunday(year, month):
        first = datetime.datetime(year, month, 1)
        return first + datetime.timedelta(days=(6 - first.weekday()) % 7)

    def _summer(self, when):
        wall = when.replace(tzinfo=None)
        return (wall >= self._first_sunday(wall.year, 10).replace(hour=2)
                or wall < self._first_sunday(wall.year, 4).replace(hour=3))

    def utcoffset(self, when):
        return datetime.timedelta(hours=11 if self._summer(when) else 10)

    def dst(self, when):
        return datetime.timedelta(hours=1 if self._summer(when) else 0)

    def tzname(self, when):
        return "AEDT" if self._summer(when) else "AEST"


SYDNEY = _Sydney()


def by_the_c_library(local):
    """A local stamp as the UTC stamp, by mktime — not by the datetime calls
    costlog_stamp makes."""
    return time.strftime(FMT + "Z", time.gmtime(time.mktime(time.strptime(local, FMT))))


class StampTests(unittest.TestCase):
    def test_now_is_utc_and_marked(self):
        before = time.time()
        stamp = dp.costlog_stamp()
        after = time.time()
        self.assertRegex(stamp, STAMP)
        as_utc = datetime.datetime.strptime(stamp[:-1], FMT).replace(
            tzinfo=datetime.timezone.utc).timestamp()
        self.assertGreaterEqual(as_utc, int(before) - 1)
        self.assertLessEqual(as_utc, after + 1)

    def test_a_marked_stamp_is_returned_as_it_is(self):
        self.assertEqual(dp.costlog_stamp("2026-11-23 23:56:26Z"), "2026-11-23 23:56:26Z")
        self.assertEqual(dp.costlog_stamp("2026-11-23 23:56:26Z", SYDNEY), "2026-11-23 23:56:26Z")

    def test_an_unmarked_stamp_is_local_time_on_sydneys_rules(self):
        cases = [
            # the laptop's last line the day of the change: 10:56 on the 24th
            # in Sydney was still the 23rd for the providers
            ("2026-11-24 10:56:26", "2026-11-23 23:56:26Z"),
            ("2026-06-09 08:11:17", "2026-06-08 22:11:17Z"),   # winter: +10
            ("2026-07-01 12:00:00", "2026-07-01 02:00:00Z"),
            ("2026-10-04 01:59:59", "2026-10-03 15:59:59Z"),   # the last second of +10
            ("2026-10-04 03:00:00", "2026-10-03 16:00:00Z"),   # the first second of +11
            ("2026-11-01 05:00:00", "2026-10-31 18:00:00Z"),   # the 1st, local: last month's
            ("2027-01-01 08:00:00", "2026-12-31 21:00:00Z"),   # and last year's
        ]
        for local, utc in cases:
            with self.subTest(local=local):
                self.assertEqual(dp.costlog_stamp(local, SYDNEY), utc)

    def test_without_a_zone_the_machines_own_rules_apply(self):
        # Two seasons, so a zone with daylight saving is exercised on both.
        for local in ("2026-01-15 09:30:00", "2026-07-15 09:30:00", "2026-11-24 10:56:26"):
            with self.subTest(local=local):
                self.assertEqual(dp.costlog_stamp(local), by_the_c_library(local))

    def test_text_that_is_not_a_stamp_comes_back_unchanged(self):
        for text in ("x", "t1", "2026-13-45 99:99:99", "2026-11-24", "log restarted"):
            with self.subTest(text=text):
                self.assertEqual(dp.costlog_stamp(text, SYDNEY), text)
                self.assertEqual(dp.costlog_stamp(text), text)

    def test_a_date_the_platform_cannot_place_does_not_raise(self):
        # Windows' C library refuses local times before 1970.
        self.assertIn(dp.costlog_stamp("1601-01-01 00:00:00")[:4], ("1601", "1600"))


class _TempLog(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = pathlib.Path(tmp.name)
        self.log = self.dir / "APICostLog_TEST.txt"
        self.backup = self.dir / ("APICostLog_TEST.txt" + dp.COSTLOG_LOCAL_BACKUP_SUFFIX)

    def leftovers(self):
        """Files in the folder other than the log and its backup."""
        return sorted(p.name for p in self.dir.iterdir() if p not in (self.log, self.backup))


CHAT_LINE = (";OpenAI;gpt-6-luna;0.0165;reasoning=Medium, verbosity=low;119;"
             "Act_on_unread_emails;19;57;7639;47891;669842;;Act_on_unread_emails_2026-11-24_094148")


class MigrationTests(_TempLog):
    # Every shape a log holds: the oldest 4-field line with a Windows CRLF, a
    # current 14-field line whose CHAT name carries its own (local) time, a
    # line already in UTC, the old rotation marker, a blank line, and a line
    # whose first field is not a stamp.
    BEFORE = (b"2026-06-09 08:11:17;Anthropic;claude-sonnet-4-6;0.3392\r\n"
              b"2026-11-24 09:50:30" + CHAT_LINE.encode() + b"\n"
              b"2026-11-23 23:28:27Z;Anthropic;claude-haiku-5-5;0.0326;mode=Medium\n"
              b"2026-10-25 11:04:22 log restarted (previous log archived to x.old)\n"
              b"\n"
              b"x;y;z;7.0000\n")
    AFTER = (b"2026-06-08 22:11:17Z;Anthropic;claude-sonnet-4-6;0.3392\r\n"
             b"2026-11-23 22:50:30Z" + CHAT_LINE.encode() + b"\n"
             b"2026-11-23 23:28:27Z;Anthropic;claude-haiku-5-5;0.0326;mode=Medium\n"
             b"2026-10-25 11:04:22 log restarted (previous log archived to x.old)\n"
             b"\n"
             b"x;y;z;7.0000\n")

    def test_only_the_local_stamps_change_and_the_original_is_kept(self):
        self.log.write_bytes(self.BEFORE)
        self.assertEqual(dp.migrate_costlog_to_utc(str(self.log), SYDNEY), 2)
        self.assertEqual(self.log.read_bytes(), self.AFTER)
        self.assertEqual(self.backup.read_bytes(), self.BEFORE)
        self.assertEqual(self.leftovers(), [])                 # no temp file left behind

    def test_a_second_pass_changes_nothing(self):
        self.log.write_bytes(self.BEFORE)
        dp.migrate_costlog_to_utc(str(self.log), SYDNEY)
        stat = self.log.stat()
        self.assertEqual(dp.migrate_costlog_to_utc(str(self.log), SYDNEY), 0)
        self.assertEqual(self.log.read_bytes(), self.AFTER)
        self.assertEqual(self.log.stat().st_mtime_ns, stat.st_mtime_ns)   # not rewritten
        self.assertEqual(self.backup.read_bytes(), self.BEFORE)

    def test_a_line_an_old_copy_appends_later_is_picked_up_and_the_backup_stands(self):
        self.log.write_bytes(self.BEFORE)
        dp.migrate_costlog_to_utc(str(self.log), SYDNEY)
        with open(self.log, "ab") as f:                        # a pre-change app, still running
            f.write(b"2026-11-24 12:30:00;xAI;grok-4.5;0.0100\n")
        self.assertEqual(dp.migrate_costlog_to_utc(str(self.log), SYDNEY), 1)
        self.assertEqual(self.log.read_bytes(),
                         self.AFTER + b"2026-11-24 01:30:00Z;xAI;grok-4.5;0.0100\n")
        self.assertEqual(self.backup.read_bytes(), self.BEFORE)   # the FIRST state, still

    def test_a_log_already_in_utc_gets_no_backup(self):
        self.log.write_bytes(self.AFTER)
        self.assertEqual(dp.migrate_costlog_to_utc(str(self.log), SYDNEY), 0)
        self.assertEqual(self.log.read_bytes(), self.AFTER)
        self.assertFalse(self.backup.exists())

    def test_no_log_is_nothing_to_do(self):
        self.assertEqual(dp.migrate_costlog_to_utc(str(self.log), SYDNEY), 0)
        self.assertFalse(self.log.exists())
        self.assertFalse(self.backup.exists())

    def test_a_log_that_changes_underneath_is_left_for_the_next_launch(self):
        # A run ends in another process between the read and the replace: its
        # line must not be lost under the converted copy.
        self.log.write_bytes(self.BEFORE)
        late = b"2026-11-24 12:31:00;xAI;grok-4.5;0.0200\n"
        real_replace = dp._replace_with

        def backup_then_append(path, data):
            real_replace(path, data)
            if path == str(self.backup):
                with open(self.log, "ab") as f:
                    f.write(late)

        with mock.patch.object(dp, "_replace_with", backup_then_append):
            self.assertEqual(dp.migrate_costlog_to_utc(str(self.log), SYDNEY), 0)
        self.assertEqual(self.log.read_bytes(), self.BEFORE + late)
        # The next launch converts the lot.
        self.assertEqual(dp.migrate_costlog_to_utc(str(self.log), SYDNEY), 3)
        self.assertEqual(self.log.read_bytes(),
                         self.AFTER + b"2026-11-24 01:31:00Z;xAI;grok-4.5;0.0200\n")

    def test_without_a_backup_the_log_is_not_rewritten(self):
        self.log.write_bytes(self.BEFORE)
        with mock.patch.object(dp, "_replace_with", side_effect=OSError("disk full")):
            self.assertEqual(dp.migrate_costlog_to_utc(str(self.log), SYDNEY), 0)
        self.assertEqual(self.log.read_bytes(), self.BEFORE)
        self.assertFalse(self.backup.exists())

    def test_a_failed_replace_leaves_the_log_and_no_temp_file(self):
        self.log.write_bytes(self.BEFORE)
        with mock.patch.object(dp.os, "replace", side_effect=PermissionError("in use")):
            self.assertEqual(dp.migrate_costlog_to_utc(str(self.log), SYDNEY), 0)
        self.assertEqual(self.log.read_bytes(), self.BEFORE)
        self.assertEqual(self.leftovers(), [])

    def test_the_viewers_cannot_mistake_the_backup_for_a_machines_log(self):
        # Both viewers take APICostLog_<machine>.txt and nothing else: the
        # Windows regex, and the macOS glob APICostLog_*.txt.
        name = "APICostLog_LenovoAug23.txt" + dp.COSTLOG_LOCAL_BACKUP_SUFFIX
        self.assertIsNone(re.match(r"^APICostLog_.+\.txt$", name))
        self.assertFalse(name.endswith(".txt"))
        for script in ("CostLog_Win.ps1", "view_costlog.command"):
            src = (ROOT / "desktop_launchers" / script).read_text(encoding="utf-8")
            with self.subTest(script=script):
                self.assertTrue(r"'^APICostLog_.+\.txt$'" in src or '"$SHARE"/APICostLog_*.txt' in src)


class ResolveTests(DatapathsCase):
    """resolve_costlog converts this machine's log wherever it lives — and
    only this machine's."""

    LOCAL = b"2026-11-24 10:56:26;Anthropic;claude-opus-5-5;5.2521\n"

    def setUp(self):
        super().setUp()
        stamp = dp.costlog_stamp("2026-11-24 10:56:26")
        self.assertRegex(stamp, STAMP)
        self.utc = stamp.encode("ascii") + b";Anthropic;claude-opus-5-5;5.2521\n"

    def test_the_shared_log_is_converted_at_resolve(self):
        self.shared.mkdir(parents=True)
        mine = self.shared / f"APICostLog_{dp.machine_label()}.txt"
        other = self.shared / "APICostLog_SOME-OTHER-MACHINE.txt"
        mine.write_bytes(self.LOCAL)
        other.write_bytes(self.LOCAL)
        self.assertEqual(dp.resolve_costlog(), str(mine))
        self.assertEqual(mine.read_bytes(), self.utc)
        # Another machine's log is that machine's to convert.
        self.assertEqual(other.read_bytes(), self.LOCAL)
        self.assertEqual(sorted(p.name for p in self.shared.iterdir()),
                         sorted([mine.name, mine.name + dp.COSTLOG_LOCAL_BACKUP_SUFFIX, other.name]))

    def test_a_repo_root_log_folded_into_the_share_is_converted_with_it(self):
        (self.repo / dp.COSTLOG_BASENAME).write_bytes(self.LOCAL)
        path = pathlib.Path(dp.resolve_costlog())
        # The fold itself appends in text mode (CRLF on Windows, as it always
        # has); the conversion keeps whatever ending the line arrived with.
        self.assertEqual(path.read_bytes().replace(b"\r\n", b"\n"), self.utc)

    def test_the_repo_root_fallback_is_converted_too(self):
        os.environ.pop(dp.DATA_DIR_ENV)
        orig = dp.find_onedrive_root
        dp.find_onedrive_root = lambda: None
        self.addCleanup(setattr, dp, "find_onedrive_root", orig)
        local = self.repo / dp.COSTLOG_BASENAME
        local.write_bytes(self.LOCAL)
        self.assertEqual(dp.resolve_costlog(), str(local))
        self.assertEqual(local.read_bytes(), self.utc)


class _Host(sm.StreamingMixin):
    """Bare host exposing only what _log_api_cost touches."""

    provider, model = "xAI", "grok-4.5"

    def _get_model_param_summary(self):
        return "reasoning=Medium"

    def _tool_info(self, _message):
        pass


class WriterTests(_TempLog):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(sm, "APICOST_LOG_FILE", str(self.log))
        patcher.start()
        self.addCleanup(patcher.stop)

    def stamp(self):
        return self.log.read_text(encoding="utf-8").splitlines()[-1].split(";")[0]

    def test_a_line_is_stamped_now_in_utc(self):
        before = time.time()
        self.assertTrue(_Host()._log_api_cost(0.3742))
        self.assertRegex(self.stamp(), STAMP)
        as_utc = datetime.datetime.strptime(self.stamp()[:-1], FMT).replace(
            tzinfo=datetime.timezone.utc).timestamp()
        self.assertLess(abs(as_utc - before), 5)

    def test_a_stamp_handed_in_is_kept_when_marked_and_converted_when_not(self):
        host = _Host()
        host._log_api_cost(0.5, timestamp="2026-11-23 23:56:26Z")
        self.assertEqual(self.stamp(), "2026-11-23 23:56:26Z")
        host._log_api_cost(0.5, timestamp="2026-11-24 10:56:26")     # a pre-change record's
        self.assertEqual(self.stamp(), by_the_c_library("2026-11-24 10:56:26"))

    def test_the_writers_make_no_stamp_of_their_own(self):
        # One place makes the stamp: neither writer may go back to the
        # machine's clock (datetime.now / time.strftime) for it.
        for name in ("_log_api_cost", "_run_progress_write"):
            src = inspect.getsource(getattr(sm.StreamingMixin, name))
            with self.subTest(name=name):
                self.assertIn("costlog_stamp(", src)
                self.assertNotIn("datetime.now", src)
                self.assertNotIn("strftime", src)
        self.assertIs(sm.costlog_stamp, dp.costlog_stamp)


class SelfBotStubTests(unittest.TestCase):
    """SelfBot runs without the myagent package too; its stand-in for
    costlog_stamp must stamp exactly as the original does."""

    def stub(self):
        tree = ast.parse((ROOT / "SelfBot.py").read_text(encoding="utf-8"))
        found = [node for node in ast.walk(tree)
                 if isinstance(node, ast.FunctionDef) and node.name == "costlog_stamp"]
        self.assertEqual(len(found), 1)
        scope = {"datetime": datetime.datetime, "timezone": datetime.timezone}
        exec(compile(ast.Module(body=found, type_ignores=[]), "SelfBot.py", "exec"), scope)
        return scope["costlog_stamp"]

    def test_the_stub_agrees_with_the_original(self):
        stub = self.stub()
        self.assertRegex(stub(), STAMP)
        self.assertLess(abs(time.mktime(time.strptime(stub()[:-1], FMT))
                            - time.mktime(time.strptime(dp.costlog_stamp()[:-1], FMT))), 5)
        for text in ("2026-11-23 23:56:26Z", "2026-11-24 10:56:26", "2026-07-15 09:30:00",
                     "x", "2026-13-45 99:99:99", "1601-01-01 00:00:00"):
            with self.subTest(text=text):
                self.assertEqual(stub(text), dp.costlog_stamp(text))

    def test_selfbot_imports_the_original_when_the_package_is_there(self):
        src = (ROOT / "SelfBot.py").read_text(encoding="utf-8")
        block = src[src.index("from myagent.datapaths import ("):]
        self.assertIn("\n        costlog_stamp,\n", block[:block.index(")")])


if __name__ == "__main__":
    unittest.main()
