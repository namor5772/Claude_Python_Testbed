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


def tok(n):
    """The viewers' compact token count (Format-Tok / tok())."""
    if n >= 1000000:
        return f"{n / 1000000:.2f}M"
    if n >= 10000:
        return f"{n / 1000:.1f}k"
    return str(int(n))


class ModelSplitTests(_ViewerCase):
    """A run more than one model served (the 13th field, 2026-09-27: a Model
    upgrade from an Agent Request reply on, or a refusal fallback) is split
    between its models in BOTH By-model blocks — cost, lifetime and this
    month, and tokens + blended rate — while every other block counts it as
    the one run it was. The numbers avoid rounding ties, so the two
    platforms' formatting cannot disagree on them."""

    UPGRADED = ("claude-sonnet-5,0.250000,1000,300,0,10000"
                "|claude-fable-5-1,1.000000,2000,400,10000,10000")

    def write(self, today):
        day = today.isoformat()
        lines = [
            f"{day} 08:00:00;Anthropic;claude-sonnet-5;0.5000;mode=Low;30;Split_Test;4;"
            "1000;200;0;5000",
            f"{day} 09:00:00;Anthropic;claude-fable-5-1;1.2500;mode=Max, "
            f"upgraded-from=claude-sonnet-5@call2;60;Split_Test;5;3000;700;10000;20000;"
            f"{self.UPGRADED}",
            f"{day} 10:00:00;Anthropic;claude-fable-5-1;2.0000;mode=Max;20;Split_Test;3;"
            "1000;200;0;5000",
        ]
        (self.share / "APICostLog_MACHINE-A.txt").write_bytes(
            ("\n".join(lines) + "\n").encode("utf-8"))

    @staticmethod
    def block(lines, heading):
        """The lines under `heading`, up to the next blank line."""
        start = lines.index(heading) + 1
        end = start
        while end < len(lines) and lines[end].strip():
            end += 1
        return lines[start:end]

    def test_both_by_model_blocks_split_the_upgraded_run_and_nothing_else_does(self):
        for _attempt in range(2):          # a run crossing a month boundary retries
            today = datetime.date.today()
            self.write(today)
            lines = self.run_viewer()
            if datetime.date.today() == today:
                break
        cost_rows = ["    claude-fable-5-1                 $    3.0000  (2)",
                     "    claude-sonnet-5                  $    0.7500  (2)"]
        self.assertEqual(self.block(lines, "  By model (highest spend first):"), cost_rows)
        self.assertEqual(self.block(lines, "  By model (this month; highest spend first):"),
                         cost_rows)

        def token_row(model, runs, tin, tout, tcw, tcr, cost):
            everything = tin + tout + tcw + tcr
            rate = f"{cost / everything * 1000000:.4f}"
            share = f"{tcr / (tin + tcw + tcr) * 100:.1f}%"
            return (f"    {model:<32} {runs:>5} {tok(tin):>8} {tok(tout):>8} {tok(tcw):>8} "
                    f"{tok(tcr):>8} {cost:>10.4f} {rate:>9} {share:>7}")

        header = (f"    {'MODEL':<32} {'RUNS':>5} {'IN':>8} {'OUT':>8} {'CACHE-W':>8} "
                  f"{'CACHE-R':>8} {'COST(USD)':>10} {'$/MTok':>9} {'CACHE%':>7}")
        self.assertEqual(
            self.block(lines, "  By model (tokens and effective blended rate; "
                              "runs logged with token counts):"),
            [header,
             token_row("claude-fable-5-1", 2, 3000, 600, 10000, 15000, 3.0),
             token_row("claude-sonnet-5", 2, 2000, 500, 0, 15000, 0.75)])
        # Everything else still counts the upgraded run once, whole.
        self.assertEqual(self.block(lines, "  By machine:"),
                         ["    MACHINE-A                $    3.7500  (3 runs)"])
        self.assertEqual(self.block(lines, "  By provider:"),
                         ["    Anthropic    $    3.7500  (3 runs)"])
        self.assertIn("  3 runs", "\n".join(lines))
        full = [line for line in lines if " claude-fable-5-1 " in line and "upgraded-from" in line]
        self.assertEqual(len(full), 1)          # one FULL LOG row, under the model it ended on
        self.assertIn("1.2500", full[0])


class ChatColumnTests(_ViewerCase):
    """The CHAT column (2026-10-07): the 14th field — the stem of the chat
    files the run's transcript is saved under — rendered rightmost after
    INSTRUCTION, blank on a line without one. With it the 13th field (the
    split) is always present, blank for a one-model run, so both viewers must
    read the split at 13 and the chat at 14 on every line shape, and the
    By-model blocks must still split a 13- or 14-field line that carries a
    split. The ad-hoc line (empty INSTRUCTION, a chat) pins that the chat
    name does not slide left into the empty column."""

    SPLIT_A = "claude-sonnet-5,0.100000,50,25,0,0|claude-fable-5-1,0.200000,50,25,0,0"
    SPLIT_B = "claude-sonnet-5,0.250000,1000,300,0,10000|claude-fable-5-1,1.000000,2000,400,10000,10000"

    def write(self, today):
        day = today.isoformat()
        self.chat_luna = f"Act_on_unread_emails_{day}_080000"
        self.chat_split = f"Split_Test_{day}_090000"
        self.chat_adhoc = f"Agent_{day}_110000"
        lines = [
            # 13 fields: a split, no chat (a run from before the column)
            f"{day} 07:00:00;Anthropic;claude-fable-5-1;0.3000;mode=Max, "
            f"upgraded-from=claude-sonnet-5@call1;10;Old_Split;2;100;50;0;0;{self.SPLIT_A}",
            # 14 fields: a one-model run, blank split, a chat
            f"{day} 08:00:00;OpenAI;gpt-6-luna;0.0129;reasoning=Max, verbosity=low;117;"
            f"Act_on_unread_emails;9;612;8138;48113;276910;;{self.chat_luna}",
            # 14 fields: a split AND a chat
            f"{day} 09:00:00;Anthropic;claude-fable-5-1;1.2500;mode=Max, "
            f"upgraded-from=claude-sonnet-5@call2;60;Split_Test;5;3000;700;10000;20000;"
            f"{self.SPLIT_B};{self.chat_split}",
            # 12 fields: an older line
            f"{day} 10:00:00;Anthropic;claude-sonnet-5;0.5000;mode=Low;30;Old_Line;4;"
            "1000;200;0;5000",
            # 14 fields: an ad-hoc GUI run — empty INSTRUCTION, a chat
            f"{day} 11:00:00;OpenAI;gpt-6-luna;0.0100;reasoning=Max, verbosity=low;8;;3;"
            f"10;20;0;0;;{self.chat_adhoc}",
        ]
        (self.share / "APICostLog_MACHINE-A.txt").write_bytes(
            ("\n".join(lines) + "\n").encode("utf-8"))

    def test_the_chat_is_the_last_column_and_the_splits_still_split(self):
        for _attempt in range(2):          # a run crossing a month boundary retries
            today = datetime.date.today()
            self.write(today)
            lines = self.run_viewer()
            if datetime.date.today() == today:
                break
        header = next(line for line in lines if line.startswith("DATE/TIME"))
        self.assertEqual(header.split()[-2:], ["INSTRUCTION", "CHAT"])

        def row(marker):
            found = [line for line in lines if marker in line and line[:4].isdigit()]
            self.assertEqual(len(found), 1, marker)
            return found[0]

        luna = row(self.chat_luna)
        self.assertTrue(luna.rstrip().endswith(self.chat_luna))
        self.assertIn(" Act_on_unread_emails ", luna)
        split = row("@call2")
        self.assertTrue(split.rstrip().endswith(self.chat_split))
        self.assertIn("1.2500", split)
        adhoc = row(self.chat_adhoc)
        self.assertTrue(adhoc.rstrip().endswith(self.chat_adhoc))
        # The chat column starts where it does on every other row: an empty
        # INSTRUCTION is padded (Windows) or dashed (macOS), never collapsed.
        self.assertEqual(adhoc.index(self.chat_adhoc), luna.index(self.chat_luna))
        self.assertTrue(row(" Old_Line").rstrip().endswith("Old_Line"))    # no chat
        self.assertTrue(row("@call1").rstrip().endswith("Old_Split"))      # split, no chat

        def cost_row(model, cost, runs):
            return f"    {model:<32} ${cost:>10.4f}  ({runs})"

        # fable 0.2 + 1.0, sonnet 0.1 + 0.25 + 0.5, luna 0.0129 + 0.0100
        self.assertEqual(
            ModelSplitTests.block(lines, "  By model (highest spend first):"),
            [cost_row("claude-fable-5-1", 1.2, 2), cost_row("claude-sonnet-5", 0.85, 3),
             cost_row("gpt-6-luna", 0.0229, 2)])
        self.assertIn("  5 runs", "\n".join(lines))


if __name__ == "__main__":
    unittest.main()
