"""The API Cost Log viewers' day rows (2026-09-22).

Under the SUMMARY's `today` row both viewers list the seven days before it,
most recent first: every calendar day (a day without a run reads $0.0000), the
weekday in the slot `today` has, and the amounts of those rows and of
`this month` in ONE money column — "$" included, right-aligned to end at
column 34, where a single-digit `today` always ended — so decimal points line
up when a day passes $10.

This runs the REAL viewer for the platform the suite runs on — CostLog_Win.ps1
under Windows PowerShell, view_costlog.command under bash on macOS (so the Mac
pass exercises the real BSD `date -j -v…` and awk) — and compares its day
block, byte for byte, with one computed here. Both platforms are held to the
same expectation, which is what pins "the two viewers render identically".

Isolation: the viewer is copied into a temp `<repo>/desktop_launchers/`. Both
scripts find the repo from their own location and fold in a repo-root
APICostLog.txt when one exists, so the copy cannot see the real one; and
MYAGENT_DATA_DIR (the override myagent/datapaths.py honours too) points the
share at the synthetic logs. Neither script is modified to be testable: the
pager and the closing prompt are shadowed from outside — PowerShell resolves a
function before a cmdlet (Out-Host -Paging reads the CONSOLE, so redirecting
stdin does not free it), and on macOS a `less` that is `cat` leads PATH.
Skips on any other platform.
"""

import datetime
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
LAUNCHERS = REPO / "desktop_launchers"
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")   # not %a: locale-free
TAIL = ";mode=Adaptive;12;Some_Instruction;3;1000;200;0;5000"  # fields 5-12 of a current line


def expected_block(today, by_days_back, month_sum):
    """The nine lines: today, the seven days before it, this month."""
    lines = []
    for back in range(8):
        day = today - datetime.timedelta(days=back)
        label = "today" if back == 0 else WEEKDAYS[day.weekday()]
        amount = "$%.4f" % by_days_back.get(back, 0.0)
        lines.append(f"  {label:<5} ({day.isoformat()}):{amount:>13}")
    lines.append(f"  this month ({today.strftime('%Y-%m')}):{'$%.4f' % month_sum:>11}")
    return lines


class _ViewerCase(unittest.TestCase):

    def setUp(self):
        if sys.platform == "win32":
            self.script_name = "CostLog_Win.ps1"
            if shutil.which("powershell.exe") is None:
                self.skipTest("Windows PowerShell not found")
        elif sys.platform == "darwin":
            self.script_name = "view_costlog.command"
        else:
            self.skipTest("the Cost Log viewers are Windows / macOS scripts")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        self.share = root / "share"
        self.share.mkdir()
        launchers = root / "repo" / "desktop_launchers"
        launchers.mkdir(parents=True)
        self.script = launchers / self.script_name
        shutil.copyfile(LAUNCHERS / self.script_name, self.script)
        self.shims = root / "shims"
        self.shims.mkdir()
        if sys.platform == "darwin":
            less = self.shims / "less"
            less.write_text("#!/bin/sh\nexec cat\n", encoding="utf-8")
            less.chmod(less.stat().st_mode | stat.S_IXUSR)

    def write_log(self, machine, rows, today, newline="\n", tail=TAIL, preamble=""):
        """rows: (days back, 'HH:MM:SS', 'cost') — dated relative to today."""
        body = preamble + "".join(
            f"{(today - datetime.timedelta(days=back)).isoformat()} {clock};"
            f"Anthropic;claude-sonnet-5;{cost}{tail}\n"
            for back, clock, cost in rows)
        path = self.share / f"APICostLog_{machine}.txt"
        path.write_bytes(body.replace("\n", newline).encode("utf-8"))

    def run_viewer(self):
        env = dict(os.environ, MYAGENT_DATA_DIR=str(self.share))
        if sys.platform == "win32":
            quoted = str(self.script).replace("'", "''")
            command = ["powershell.exe", "-NoProfile", "-NonInteractive",
                       "-ExecutionPolicy", "Bypass", "-Command",
                       "function Out-Host { param([switch]$Paging) process { $_ } }; "
                       "function Read-Host { param($Prompt) }; "
                       f"& '{quoted}'"]
            extra = {"creationflags": subprocess.CREATE_NO_WINDOW}
        else:
            env["PATH"] = str(self.shims) + os.pathsep + env.get("PATH", "")
            command = ["bash", str(self.script)]
            extra = {}
        done = subprocess.run(command, env=env, capture_output=True, timeout=120,
                              stdin=subprocess.DEVNULL, **extra)
        # The block under test is ASCII; the rest may hold box rules / arrows in
        # whatever code page a console-less PowerShell was left with.
        text = done.stdout.decode("utf-8", errors="replace")
        self.assertEqual(done.returncode, 0, done.stderr.decode("utf-8", errors="replace"))
        return text.replace("\r\n", "\n").split("\n")

    def day_block(self, build):
        """build(today) writes the logs and returns the expected block. Run
        again when the date changed underneath (a suite crossing midnight)."""
        for _attempt in range(2):
            today = datetime.date.today()
            expected = build(today)
            lines = self.run_viewer()
            if datetime.date.today() == today:
                break
        starts = [i for i, line in enumerate(lines) if line.startswith("  today (")]
        self.assertEqual(len(starts), 1, "\n".join(lines[:30]))
        return expected, lines[starts[0]:starts[0] + 9], lines


class DayRowTests(_ViewerCase):

    def test_today_then_the_last_seven_days_then_this_month_in_one_money_column(self):
        rows_a = [
            (0, "07:00:01", "0.0500"), (0, "09:30:00", "0.0088"),   # today       0.0588
            (1, "10:00:00", "3.2045"),                              # + machine B 4.2045
            #  2 days back: no run at all                            ->           0.0000
            (3, "11:00:00", "14.5937"),                             # past $10
            (4, "23:59:59", "1.0000"), (4, "00:00:00", "0.4184"),   # both edges of a day
            (5, "12:00:00", "123.4567"),                            # three digits
            (6, "12:00:00", "0.0001"),
            (7, "12:00:00", "2.2957"),                              # the last day listed
            (8, "12:00:00", "99.0000"),                             # one day too old
            (70, "12:00:00", "5.0000"),                             # an earlier month
        ]
        rows_b = [(1, "18:00:00", "1.0000")]
        sums = {0: 0.0588, 1: 4.2045, 3: 14.5937, 4: 1.4184, 5: 123.4567, 6: 0.0001, 7: 2.2957}

        def build(today):
            # A: the rotation marker (no semicolons), a blank line and a line
            # whose "timestamp" is one character — none may break the day math.
            self.write_log("MACHINE-A", rows_a, today,
                           preamble="--- log rotated ---\n\nx;y;z;7.0000\n")
            # B: a second machine, CRLF, the 4-field shape of the oldest lines.
            self.write_log("MACHINE-B", rows_b, today, newline="\r\n", tail="")
            month = today.strftime("%Y-%m")
            month_sum = sum(float(cost) for back, _clock, cost in rows_a + rows_b
                            if (today - datetime.timedelta(days=back)).strftime("%Y-%m") == month)
            return expected_block(today, sums, month_sum)

        expected, block, lines = self.day_block(build)
        self.assertEqual(block, expected)
        self.assertEqual({len(line) for line in block}, {34})       # one column edge
        self.assertNotIn("$99.0000", "\n".join(block))              # day 8 is not a row
        # The rest of the SUMMARY still follows the block.
        self.assertTrue(any(line.startswith("  By machine") for line in lines))
        self.assertTrue(any(line.startswith("THIS MONTH (") for line in lines))

    def test_a_week_without_a_run_is_eight_zero_rows(self):
        def build(today):
            self.write_log("MACHINE-A", [(200, "12:00:00", "5.0000")], today)
            return expected_block(today, {}, 0.0)

        expected, block, _lines = self.day_block(build)
        self.assertEqual(block, expected)
        self.assertEqual([line[-7:] for line in block], ["$0.0000"] * 9)


if __name__ == "__main__":
    unittest.main()
