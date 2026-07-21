"""Characterization tests for the context-overflow compaction helpers in
myagent/helpers.py (ported from SelfBot's sliding-window recovery): turn-
boundary-only cuts that never orphan a tool_use/tool_result pair, the
two-round floor, proportional vs halving cut sizing, the token estimator's
flat image count, and the overflow-400 message parser."""

import unittest

from myagent.helpers import (estimate_content_tokens, parse_overflow_counts,
                             trim_history_for_context)


def _round(user_text, big=200):
    """One conversation round: real user turn, assistant tool_use, tool_result
    carrier (role=user but NOT a turn boundary), assistant text."""
    return [
        {"role": "user", "content": user_text * big},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1",
                                           "name": "run_command", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result",
                                      "tool_use_id": "t1", "content": "ok"}]},
        {"role": "assistant", "content": "reply " + user_text},
    ]


class EstimateCase(unittest.TestCase):
    def test_string_is_quarter_chars(self):
        self.assertEqual(estimate_content_tokens("x" * 400), 100)

    def test_image_counts_flat_1600(self):
        content = [{"type": "image", "source": {"data": "A" * 500_000}}]
        self.assertEqual(estimate_content_tokens(content), 1600)

    def test_mixed_blocks_sum(self):
        content = [{"type": "text", "text": "y" * 400},
                   {"type": "image", "source": {"data": "A" * 999}}]
        est = estimate_content_tokens(content)
        self.assertGreaterEqual(est, 1600 + 100)
        self.assertLess(est, 1600 + 200)  # text block json overhead only


class ParseCase(unittest.TestCase):
    def test_parses_real_overflow_message(self):
        t, m = parse_overflow_counts(
            "prompt is too long: 1597842 tokens > 1000000 maximum")
        self.assertEqual((t, m), (1597842, 1000000))

    def test_parses_comma_grouped_counts(self):
        t, m = parse_overflow_counts(
            "prompt is too long: 1,597,842 tokens > 1,000,000 maximum")
        self.assertEqual((t, m), (1597842, 1000000))

    def test_no_match_returns_nones(self):
        self.assertEqual(parse_overflow_counts("something else"), (None, None))
        self.assertEqual(parse_overflow_counts(""), (None, None))


class TrimCase(unittest.TestCase):
    def test_two_rounds_or_fewer_is_untrimmable(self):
        msgs = _round("a") + _round("b")
        before = list(msgs)
        self.assertEqual(trim_history_for_context(msgs, 999_999, 100), 0)
        self.assertEqual(msgs, before)

    def test_first_kept_message_is_a_real_user_turn(self):
        msgs = _round("a") + _round("b") + _round("c") + _round("d")
        removed = trim_history_for_context(msgs)  # unknown sizes → drop oldest half
        self.assertGreater(removed, 0)
        first = msgs[0]
        self.assertEqual(first["role"], "user")
        self.assertIsInstance(first["content"], str)  # not a tool_result carrier

    def test_never_orphans_tool_results(self):
        msgs = _round("a") + _round("b") + _round("c") + _round("d")
        trim_history_for_context(msgs, 100_000, 50_000)
        # No message may reference a tool_use id that was trimmed away: since
        # cuts land only on real user turns, every tool_result must still be
        # preceded (somewhere) by its assistant tool_use.
        seen_tool_use = set()
        for m in msgs:
            c = m.get("content")
            if not isinstance(c, list):
                continue
            for b in c:
                if b.get("type") == "tool_use":
                    seen_tool_use.add(b["id"])
                elif b.get("type") == "tool_result":
                    self.assertIn(b["tool_use_id"], seen_tool_use)

    def test_always_keeps_last_two_rounds(self):
        msgs = _round("a") + _round("b") + _round("c") + _round("d")
        tail = list(msgs[-8:])  # the last two rounds (4 messages each)
        # Massive overflow — asks for far more removal than exists.
        trim_history_for_context(msgs, 10_000_000, 1_000)
        self.assertGreaterEqual(len(msgs), 8)
        self.assertEqual(msgs[-8:], tail)

    def test_small_overflow_drops_only_oldest_round(self):
        msgs = _round("a") + _round("b") + _round("c") + _round("d")
        # Reported barely over: need_remove is tiny, so one round suffices.
        removed = trim_history_for_context(msgs, 101, 100)
        self.assertEqual(removed, 4)  # exactly one round of 4 messages
        self.assertEqual(len(msgs), 12)

    def test_returns_message_count_removed(self):
        msgs = _round("a") + _round("b") + _round("c") + _round("d")
        n = len(msgs)
        removed = trim_history_for_context(msgs)
        self.assertEqual(removed, n - len(msgs))


if __name__ == "__main__":
    unittest.main()
