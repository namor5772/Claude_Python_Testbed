"""The Agent Request reply that closes MyAgent (2026-10-01, CONVO_EXIT_WORD).

An Agent Request answered "exit" — Convo mode's own prompt, or the model's
user_prompt tool — ends the run as "quit" / an empty reply does and then asks
for the main window's close (`_on_close`, what [X] runs) once the loop has
ended and the cost-log line is written; "quit" and "stop" leave the window
open. Pinned here over the REAL stream_worker (the provider call stubbed at
its dispatch seam, the dialog replaced by scripted replies), the real
_execute_tool branch, and a scan that both loop-end tails make the one close
decision."""

import inspect
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import myagent.streaming_mixin as sm
from myagent.constants import CONVO_END_WORDS, CONVO_EXIT_WORD
from myagent.helpers import _ToolBlock
from tests.test_costlog_run_fields import _Host, _Var

STOCK_PROMPT = ("Reply, or type empty / 'quit' / 'stop' to end, "
                "or 'exit' to end and close MyAgent.")


class _Root:
    """Records what the loop schedules on the Tk thread."""

    def __init__(self):
        self.scheduled = []

    def after(self, delay, func):
        self.scheduled.append((delay, func))


class _ConvoHost(_Host):
    """_Host in Convo mode: every call ends the turn with text, so the loop
    asks the user after each one; the dialog is a scripted list of replies."""

    def __init__(self, replies, headless=False):
        super().__init__(second_call="end", instruction="JustChat")
        self.conversational_enabled = _Var(True)
        self._headless = headless
        self.root = _Root()
        self.replies = list(replies)
        self.asked = []
        self.notes = []

    def _stream_anthropic_call(self, messages, max_retries, label_emitted):
        self._calls_made += 1
        usage = {"input_tokens": 100, "output_tokens": 10}
        return "end_turn", [], f"answer {self._calls_made}", False, True, usage

    def do_user_prompt(self, message, convo=False):
        self.asked.append((message, convo))
        return self.replies.pop(0)

    def _take_prompt_images(self):
        return []

    def _tool_info(self, msg):
        self.notes.append(msg)

    def _on_close(self):
        raise AssertionError("scheduled, never called by the loop itself")


class _CostLogCase(unittest.TestCase):
    """Every run writes a cost-log line: keep it out of the real log."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.object(sm, "APICOST_LOG_FILE",
                                    str(Path(tmp.name) / "APICostLog_test.txt"))
        patcher.start()
        self.addCleanup(patcher.stop)


class ConvoExitTests(_CostLogCase):

    def run_convo(self, *replies, headless=False):
        host = _ConvoHost(replies, headless=headless)
        host.stream_worker([{"role": "user", "content": "hi"}])
        return host

    def test_exit_ends_the_conversation_and_schedules_the_close(self):
        host = self.run_convo("exit")
        self.assertEqual(host._calls_made, 1)                 # no call answers it
        self.assertTrue(host._close_after_run)
        self.assertEqual(host.root.scheduled, [(500, host._on_close)])
        self.assertIn("Conversation ended — 'exit' closes MyAgent.\n", host.notes)
        self.assertNotIn("Conversation ended.\n", host.notes)

    def test_quit_stop_and_an_empty_reply_end_it_and_leave_the_window_open(self):
        for reply in ("quit", "stop", "", "  STOP "):
            with self.subTest(reply=reply):
                host = self.run_convo(reply)
                self.assertEqual(host._calls_made, 1)
                self.assertFalse(host._close_after_run)
                self.assertEqual(host.root.scheduled, [])
                self.assertIn("Conversation ended.\n", host.notes)

    def test_exit_is_read_case_folded_and_stripped_like_the_other_end_words(self):
        host = self.run_convo("  EXIT \n")
        self.assertEqual(host.root.scheduled, [(500, host._on_close)])

    def test_the_word_inside_a_sentence_is_a_reply(self):
        host = self.run_convo("exit the editor first", "quit")
        self.assertEqual(host._calls_made, 2)                 # the sentence was answered
        self.assertEqual(host.root.scheduled, [])

    def test_the_flag_is_reset_at_every_run(self):
        host = self.run_convo("exit")
        host.replies, host.root, host._calls_made = ["quit"], _Root(), 0
        host.stream_worker([{"role": "user", "content": "hi again"}])
        self.assertFalse(host._close_after_run)
        self.assertEqual(host.root.scheduled, [])

    def test_a_headless_run_still_closes_on_quit(self):
        # The subagent / headless auto-close is unchanged — the same decision.
        host = self.run_convo("quit", headless=True)
        self.assertEqual(host.root.scheduled, [(500, host._on_close)])

    def test_the_stock_prompt_names_what_exit_does(self):
        host = self.run_convo("quit")
        self.assertEqual(host.asked, [(STOCK_PROMPT, True)])


class _ToolHost(sm.StreamingMixin):
    """The user_prompt tool's branch of _execute_tool, with a scripted dialog."""

    def __init__(self, reply):
        self.reply = reply
        self.stop_requested = False
        self.notes = []

    def _tool_info(self, msg):
        self.notes.append(msg)

    def do_user_prompt(self, message, convo=False):
        return self.reply

    def _take_prompt_images(self):
        return []


class UserPromptToolExitTests(unittest.TestCase):

    def call(self, reply):
        host = _ToolHost(reply)
        return host, host._execute_tool(_ToolBlock("user_prompt", "toolu_1", {"message": "Next?"}))

    def test_exit_stops_the_run_and_asks_for_the_close(self):
        host, result = self.call("exit")
        self.assertTrue(host.stop_requested)
        self.assertTrue(host._close_after_run)
        self.assertEqual(result, "[User typed 'exit' — stopping the agent and closing MyAgent]")

    def test_exit_is_read_case_folded_and_stripped(self):
        host, _result = self.call(" Exit ")
        self.assertTrue(host.stop_requested and host._close_after_run)

    def test_quit_and_stop_still_go_to_the_model_as_text(self):
        for reply in ("quit", "stop", "exit now please"):
            with self.subTest(reply=reply):
                host, result = self.call(reply)
                self.assertFalse(host.stop_requested)
                self.assertFalse(getattr(host, "_close_after_run", False))
                self.assertEqual(result, reply)

    def test_an_empty_reply_stops_without_closing(self):
        host, result = self.call("   ")
        self.assertTrue(host.stop_requested)
        self.assertFalse(getattr(host, "_close_after_run", False))
        self.assertEqual(result, "[User submitted empty response — stopping agent]")


class WiringTests(unittest.TestCase):

    def test_the_exit_word_is_one_of_the_end_words(self):
        self.assertEqual(CONVO_EXIT_WORD, "exit")
        self.assertIn(CONVO_EXIT_WORD, CONVO_END_WORDS)

    def test_both_loop_end_tails_make_the_one_close_decision(self):
        loop = inspect.getsource(sm.StreamingMixin.stream_worker)
        calls = loop.count("_close_if_due()") - loop.count("def _close_if_due()")
        self.assertEqual(calls, 2)                                # success + exception tail
        # The close itself lives in the helper alone, and reads the flag.
        self.assertEqual(loop.count("self.root.after(500, self._on_close)"), 1)
        helper = loop[loop.index("def _close_if_due():"):loop.index("try:")]
        for name in ("self._headless", "self._result_file", "_close_after_run",
                     "self.root.after(500, self._on_close)"):
            self.assertIn(name, helper)
        self.assertIn("self._close_after_run = False", loop)      # reset per run
        tool = inspect.getsource(sm.StreamingMixin._execute_tool)
        self.assertIn("CONVO_EXIT_WORD", tool)
        self.assertIn("self._close_after_run = True", tool)


if __name__ == "__main__":
    unittest.main()
