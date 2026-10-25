"""Characterization tests for myagent.helpers.rotate_log_if_needed — the
one-slot size-cap rotation shared by heartbeat.log (Heartbeat.py wraps it,
see test_heartbeat.py), unread_summary.log (UnreadSummary.py) and the
geometry log (state_mixin) — and, since 2026-10-25, the rule that the API
cost log is NOT one of its callers (CostLogNeverRotatesTests)."""

import inspect
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import myagent.constants as constants
import myagent.streaming_mixin as sm
from myagent.helpers import rotate_log_if_needed
from tests.test_selfbot_anthropic_parity import SelfBot, _app, _needs_selfbot


class RotateLogHelperTests(unittest.TestCase):
    CAP = 500

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log = Path(tmp.name) / "APICostLog.txt"
        self.old = self.log.with_name("APICostLog.txt.old")

    def test_missing_log_returns_false(self):
        self.assertFalse(rotate_log_if_needed(self.log, self.CAP))
        self.assertFalse(self.log.exists())
        self.assertFalse(self.old.exists())

    def test_log_at_cap_returns_false_untouched(self):
        content = "x" * self.CAP
        self.log.write_text(content, encoding="utf-8")
        self.assertFalse(rotate_log_if_needed(self.log, self.CAP))
        self.assertEqual(self.log.read_text(encoding="utf-8"), content)
        self.assertFalse(self.old.exists())

    def test_over_cap_archives_and_seeds_marker(self):
        content = "x" * (self.CAP + 1)
        self.log.write_text(content, encoding="utf-8")
        self.assertTrue(rotate_log_if_needed(self.log, self.CAP))
        self.assertEqual(self.old.read_text(encoding="utf-8"), content)
        fresh = self.log.read_text(encoding="utf-8")
        self.assertIn("log restarted", fresh)
        self.assertIn("APICostLog.txt.old", fresh)
        self.assertEqual(len(fresh.splitlines()), 1)

    def test_rotation_replaces_previous_archive(self):
        self.old.write_text("ancient history", encoding="utf-8")
        content = "y" * (self.CAP + 1)
        self.log.write_text(content, encoding="utf-8")
        self.assertTrue(rotate_log_if_needed(self.log, self.CAP))
        self.assertEqual(self.old.read_text(encoding="utf-8"), content)

    def test_accepts_str_path(self):
        content = "z" * (self.CAP + 1)
        self.log.write_text(content, encoding="utf-8")
        self.assertTrue(rotate_log_if_needed(str(self.log), self.CAP))
        self.assertTrue(self.old.exists())


class _MyAgentHost(sm.StreamingMixin):
    """Bare host exposing only what _log_api_cost touches."""

    def __init__(self):
        self.provider = "Anthropic"
        self.model = "claude-haiku-5-5"
        self.infos = []

    def _get_model_param_summary(self):
        return "mode=Medium"

    def _tool_info(self, msg):
        self.infos.append(msg)


class CostLogNeverRotatesTests(unittest.TestCase):
    """The API cost log is NOT one of the helper's callers (2026-10-25). It is
    the ledger both Cost Log viewers sum over its whole life — grand total,
    span, every lifetime rollup — and the one-slot 100 KB rotation its writers
    ran before each append moved the laptop's 963 lines, June to that
    morning's four runs, into a `.old` file the viewers exclude by name: the
    viewer showed one run and a $0.02 lifetime total, and a second rotation
    would have overwritten that archive for good. Pinned on the REAL
    `_log_api_cost` of MyAgent and of SelfBot (a byte-identical copy): a log
    already past the old cap keeps every byte and gains its line, no `.old`
    appears and no marker is written; and neither writer names the helper."""

    OLD_CAP = 100_000

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log = Path(tmp.name) / "APICostLog_test.txt"
        self.old = self.log.with_name(self.log.name + ".old")
        self.history = "2026-06-09 08:11:17;Anthropic;claude-sonnet-4-6;0.3392\n" * 2000
        self.assertGreater(len(self.history.encode("utf-8")), self.OLD_CAP)
        self.log.write_bytes(self.history.encode("utf-8"))

    def check(self):
        text = self.log.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(self.history))        # every byte kept
        added = text[len(self.history):].splitlines()
        self.assertEqual(len(added), 1, added)
        self.assertIn(";Anthropic;claude-haiku-5-5;0.0219;", added[0])
        self.assertFalse(self.old.exists())
        self.assertNotIn("log restarted", text)

    def test_myagent_appends_to_a_log_past_the_old_cap(self):
        host = _MyAgentHost()
        with mock.patch.object(sm, "APICOST_LOG_FILE", str(self.log)):
            self.assertTrue(host._log_api_cost(0.0219, had_usage=True))
        self.check()

    @_needs_selfbot
    def test_selfbot_appends_to_a_log_past_the_old_cap(self):
        app = _app()
        with mock.patch.object(SelfBot, "APICOST_LOG_FILE", str(self.log)):
            self.assertTrue(app._log_api_cost(0.0219, had_usage=True, model="claude-haiku-5-5"))
        self.check()

    def test_neither_writer_names_the_helper_and_the_cap_is_gone(self):
        self.assertNotIn("rotate_log_if_needed", inspect.getsource(sm))
        self.assertFalse(hasattr(constants, "APICOST_LOG_MAX_BYTES"))
        if SelfBot is not None:
            self.assertNotIn("rotate_log_if_needed", inspect.getsource(SelfBot))
            self.assertNotIn("APICOST_LOG_MAX_BYTES", inspect.getsource(SelfBot))


if __name__ == "__main__":
    unittest.main()
