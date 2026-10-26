"""Characterization tests for myagent/relay.py — the file protocol through
which an Agent Request of a run the Telegram listener launched is posted to
the phone and answered from it (2026-10-26): the three files, the sequence
rule, atomic writes, and the missing / unreadable cases.
"""

import json
import tempfile
import unittest
from pathlib import Path

from myagent import relay


class RelayProtocolTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.d = Path(tmp.name) / "relay" / "10"       # created on the first write

    def test_request_round_trip(self):
        relay.write_request(self.d, 1, "Which month?")
        req = relay.read_request(self.d)
        self.assertEqual((req["seq"], req["text"]), (1, "Which month?"))
        self.assertIsInstance(req["written"], float)
        self.assertEqual(sorted(p.name for p in self.d.iterdir()), [relay.REQUEST])

    def test_no_request(self):
        self.assertIsNone(relay.read_request(self.d))
        self.d.mkdir(parents=True)
        (self.d / relay.REQUEST).write_text("{", encoding="utf-8")
        self.assertIsNone(relay.read_request(self.d))
        (self.d / relay.REQUEST).write_text("[1]", encoding="utf-8")
        self.assertIsNone(relay.read_request(self.d))

    def test_reply_matches_its_request_only(self):
        relay.write_reply(self.d, 2, "October")
        self.assertEqual(relay.read_reply(self.d, 2), "October")
        self.assertIsNone(relay.read_reply(self.d, 1))
        self.assertIsNone(relay.read_reply(self.d, 3))

    def test_no_reply(self):
        self.assertIsNone(relay.read_reply(self.d, 1))

    def test_clear_removes_request_and_reply(self):
        relay.write_request(self.d, 1, "q")
        relay.write_reply(self.d, 1, "a")
        relay.clear(self.d)
        self.assertEqual(list(self.d.iterdir()), [])
        relay.clear(self.d)                 # nothing to remove is fine

    def test_answered_marker_is_taken_once(self):
        self.assertIsNone(relay.take_answered(self.d))
        relay.mark_answered(self.d, 3)
        self.assertEqual(relay.take_answered(self.d), 3)
        self.assertIsNone(relay.take_answered(self.d))
        self.assertFalse((self.d / relay.ANSWERED).exists())

    def test_text_is_written_as_utf8_json_with_no_temp_left_behind(self):
        relay.write_request(self.d, 1, "café ☕\nline 2")
        raw = json.loads((self.d / relay.REQUEST).read_text(encoding="utf-8"))
        self.assertEqual(raw["text"], "café ☕\nline 2")
        self.assertEqual([p.suffix for p in self.d.iterdir()], [".json"])

    def test_a_non_string_text_and_seq_are_coerced(self):
        relay.write_request(self.d, "4", None)
        self.assertEqual(relay.read_request(self.d)["seq"], 4)
        relay.write_reply(self.d, 4, 12345)
        self.assertEqual(relay.read_reply(self.d, "4"), "12345")

    def test_stdlib_only(self):
        src = Path(relay.__file__).read_text(encoding="utf-8")
        for line in src.splitlines():
            if line.startswith(("import ", "from ")):
                self.assertIn(line.split()[1].split(".")[0],
                              {"json", "os", "tempfile", "time", "pathlib"}, line)


if __name__ == "__main__":
    unittest.main()
