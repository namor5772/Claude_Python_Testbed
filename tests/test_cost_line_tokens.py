"""The cost line's tokens: `in:` is the whole prompt (2026-11-17).

The user read the last run in the API cost log — Reconcile_Banking_Transactions
on gpt-6.1-terra, upgraded to gpt-6-astra at call 131, 153 calls, US$7.59 — and
every one of its 153 cost lines said `in:3`, against prompts of 17,671 to
174,053 tokens. Nothing was miscounted. The line printed the four DISJOINT
buckets the pricing tables rate, and labelled the uncached one `in:`. On the
OpenAI tiers that bill cache writes (GPT-5.6 on) the provider puts a cache
breakpoint at the end of the newest message, so every prompt token is a cache
read or a cache write except what follows the breakpoint: 3 tokens on an
ordinary call (68 until 2026-10-08, on all three machines — the provider's
change, no commit touched the request), more when a server-side web search
feeds the model mid-call, the whole prompt under the 1,024-token cache floor.
Probed live that day on gpt-6-luna: input_tokens 3,130 with cache_write_tokens
3,127; the repeat 3,130 with cached_tokens 3,127; an 86-token prompt 86 / 0 / 0.
Anthropic's lines read `in:2` or so for the same reason.

So the buckets and the money were right and the label was not: `in:` reads as
"tokens sent in". Now `in:` IS that — uncached + cache write + cache read — and
the split by billing rate follows it in brackets whenever a cache bucket is in
play. The queue message, the cost log's fields and the viewers keep the
disjoint buckets; only the sentence shown changes.

Pinned here: the token string on the real run's own figures; the split always
summing to `in:`; the short form of a call with no cache bucket, unchanged; the
three line forms (priced, unpriced, free) carrying it; the REAL check_queue
drawing it in the output pane, gated by Activity; SelfBot's byte-identical
copy, and SelfBot's real check_queue drawing it.

The pane test builds the real main window on a withdrawn Tk root and skips
without a display.
"""
import gc
import inspect
import queue
import re
import tkinter as tk
import unittest

from myagent.event_loop_mixin import EventLoopMixin
from tests._util import stub
from tests.test_agent_request_display import _Window
from tests.test_selfbot_anthropic_parity import SelfBot, _needs_selfbot
from tests.test_selfbot_opener_handoff import _Flag, _host

SPLIT = re.compile(r"^in:([\d,]+) \[(.+)\]  out:[\d,]+$")


def _msg(uncached, out, cw=0, cr=0, call_cost=0.01, total_cost=0.02, **extra):
    """A cost_update message as stream_worker queues it: the disjoint buckets."""
    return dict({"type": "cost_update", "call_cost": call_cost, "total_cost": total_cost,
                 "input_tokens": uncached, "output_tokens": out,
                 "cache_write_tokens": cw, "cache_read_tokens": cr}, **extra)


def _n(text):
    return int(text.replace(",", ""))


class TokenStringTests(unittest.TestCase):
    """EventLoopMixin._cost_tokens, pure."""

    tokens = staticmethod(EventLoopMixin._cost_tokens)

    def test_the_run_that_raised_it(self):
        # Call 1 (gpt-6.1-terra, a cold cache) and call 153 (gpt-6-astra) of
        # Reconcile_Banking_Transactions_2026-11-17_094024: both read `in:3`.
        self.assertEqual(self.tokens(_msg(3, 117, cw=17668)),
                         "in:17,671 [uncached:3  cache_write:17,668]  out:117")
        self.assertEqual(self.tokens(_msg(3, 517, cw=11931, cr=162119)),
                         "in:174,053 [uncached:3  cache_write:11,931  cache_read:162,119]  out:517")

    def test_a_cache_hit_with_nothing_written(self):
        # The live probe's repeat call: 3,127 of 3,130 read back.
        self.assertEqual(self.tokens(_msg(3, 5, cr=3127)),
                         "in:3,130 [uncached:3  cache_read:3,127]  out:5")

    def test_a_web_search_call_shows_its_uncached_tokens(self):
        # Act_on_unread_emails_2026-11-12_154336, cost line 17: the search
        # results the server fed the model mid-call are neither read nor written.
        self.assertEqual(self.tokens(_msg(4388, 334, cw=1664, cr=96467)),
                         "in:102,519 [uncached:4,388  cache_write:1,664  cache_read:96,467]  out:334")

    def test_a_call_with_no_cache_bucket_keeps_the_short_form(self):
        # Under the provider's cache floor (the probe's 86-token prompt), a
        # provider without caching, Ollama: nothing to split.
        self.assertEqual(self.tokens(_msg(86, 5)), "in:86  out:5")
        self.assertEqual(self.tokens(_msg(12345, 678)), "in:12,345  out:678")
        self.assertEqual(self.tokens(_msg(0, 0)), "in:0  out:0")

    def test_the_split_always_sums_to_in(self):
        for uncached in (0, 3, 68, 4388):
            for cw in (0, 40, 91000):
                for cr in (0, 7, 11817353):
                    if not (cw or cr):
                        continue
                    with self.subTest(uncached=uncached, cw=cw, cr=cr):
                        m = SPLIT.match(self.tokens(_msg(uncached, 9, cw=cw, cr=cr)))
                        self.assertTrue(m)
                        parts = dict(p.split(":") for p in m.group(2).split("  "))
                        self.assertEqual(_n(m.group(1)), uncached + cw + cr)
                        self.assertEqual(sum(_n(v) for v in parts.values()), _n(m.group(1)))
                        # uncached is always named, a zero cache bucket never
                        self.assertEqual(_n(parts["uncached"]), uncached)
                        self.assertEqual("cache_write" in parts, bool(cw))
                        self.assertEqual("cache_read" in parts, bool(cr))

    def test_the_message_is_not_changed(self):
        # The renderer adds the buckets up; the message keeps them disjoint
        # for whatever reads it next.
        msg = _msg(3, 517, cw=11931, cr=162119)
        before = dict(msg)
        self.tokens(msg)
        self.assertEqual(msg, before)


class LineTests(unittest.TestCase):
    """EventLoopMixin._cost_line: every form of the line carries the string."""

    def test_the_priced_line(self):
        self.assertEqual(
            EventLoopMixin._cost_line(_msg(3, 517, cw=11931, cr=162119,
                                           call_cost=0.3371, total_cost=7.5873)),
            "  $0.3371 this call  |  $7.59 total  "
            "(in:174,053 [uncached:3  cache_write:11,931  cache_read:162,119]  out:517)\n")

    def test_the_unpriced_and_the_free_lines(self):
        msg = _msg(3, 5, cr=3127, call_cost=None, total_cost=None)
        self.assertEqual(EventLoopMixin._cost_line(msg),
                         "  unpriced this call  |  no pricing row  "
                         "(in:3,130 [uncached:3  cache_read:3,127]  out:5)\n")
        self.assertEqual(EventLoopMixin._cost_line(dict(msg, free=True)),
                         "  local model — no charge  "
                         "(in:3,130 [uncached:3  cache_read:3,127]  out:5)\n")

    def test_nothing_prints_the_uncached_bucket_as_in_any_more(self):
        src = inspect.getsource(EventLoopMixin._cost_line)
        self.assertIn("token_str = EventLoopMixin._cost_tokens(msg)", src)
        self.assertNotIn('f"in:', src)


class PaneTests(unittest.TestCase):
    """The REAL check_queue draws the line in the real output pane."""

    LINE = ("  $0.3371 this call  |  $7.59 total  "
            "(in:174,053 [uncached:3  cache_write:11,931  cache_read:162,119]  out:517)\n")

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:  # headless box, no display
            self.skipTest(f"Tk unavailable: {exc}")
        self.root.withdraw()
        app = stub(_Window, root=self.root, provider="OpenAI", model="m",
                   available_models=["m"], temperature=1.0, queue=queue.Queue())
        for name in ("debug_enabled", "tool_calls_enabled", "show_activity",
                     "show_thinking", "save_thinking", "diag_enabled"):
            setattr(app, name, tk.BooleanVar(master=self.root, value=False))
        app._update_title = lambda: None
        app._get_display_name = lambda model_id: model_id
        app.open_instruction_editor = app._start_agent = app._stop_agent = lambda: None
        app._voice_setup_from_main = lambda: None
        app._upgrade_setup_from_main = lambda: None
        app._other_setup_from_main = lambda: None
        app.setup_ui()
        self.app = app
        self.pane = app.chat_display

    def tearDown(self):
        # As tests/test_agent_request_display.py's PaneTests: the real
        # check_queue re-arms its own poll (cancel it before the root goes),
        # and the stub's Tk variables are collected here, on the main thread.
        for after_id in self.root.tk.splitlist(self.root.tk.call("after", "info")):
            self.root.after_cancel(after_id)
        self.app = self.pane = None
        gc.collect()
        self.root.destroy()

    def feed(self, message):
        self.app.queue.put(message)
        self.app.check_queue()

    def test_the_line_reaches_the_pane_in_the_cost_tag(self):
        self.app.show_activity.set(True)
        self.feed(_msg(3, 517, cw=11931, cr=162119, call_cost=0.3371, total_cost=7.5873))
        self.assertEqual(self.pane.get("1.0", "end-1c"), self.LINE)
        self.assertEqual(set(self.pane.tag_names("1.2")), {"cost_info"})

    def test_it_is_still_gated_by_activity(self):
        self.feed(_msg(3, 517, cw=11931, cr=162119))
        self.assertEqual(self.pane.get("1.0", "end-1c"), "")


@_needs_selfbot
class SelfBotTests(unittest.TestCase):
    """SelfBot's cost line is the same sentence (its total reads "session")."""

    @staticmethod
    def _shown(msg):
        app = _host()
        app.show_activity = _Flag(True)
        app.queue.put(msg)
        app.check_queue()
        return app.chat_display.inserts

    def test_the_token_string_is_a_byte_identical_copy(self):
        self.assertEqual(inspect.getsource(SelfBot.App._cost_tokens),
                         inspect.getsource(EventLoopMixin._cost_tokens))

    def test_the_real_check_queue_draws_it(self):
        # An Anthropic call as SelfBot sees one: 4 uncached tokens of 65,480.
        shown = self._shown(_msg(4, 812, cw=3021, cr=62455, call_cost=0.0123, total_cost=0.0456))
        self.assertEqual(shown, [(
            "  $0.0123 this call  |  $0.05 session  "
            "(in:65,480 [uncached:4  cache_write:3,021  cache_read:62,455]  out:812)\n",
            "cost_info")])

    def test_an_unpriced_call_shows_the_same_tokens(self):
        shown = self._shown(_msg(4, 812, cr=62455, call_cost=None, total_cost=None))
        self.assertEqual(shown, [(
            "  unpriced this call  |  no pricing row  "
            "(in:62,459 [uncached:4  cache_read:62,455]  out:812)\n", "cost_info")])

    def test_check_queue_builds_no_token_string_of_its_own(self):
        src = inspect.getsource(SelfBot.App.check_queue)
        self.assertIn("token_str = self._cost_tokens(msg)", src)
        self.assertNotIn('f"in:', src)


if __name__ == "__main__":
    unittest.main()
