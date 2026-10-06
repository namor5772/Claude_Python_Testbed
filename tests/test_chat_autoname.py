"""Every MyAgent run names its chat (2026-10-07), and the cost log says which.

A -l / headless launch always filled "Save Chat as" with "<Name>_<timestamp>"
so its transcript was captured; a GUI run started with the box empty (the
default) saved nothing, and one started with the previous run's name still in
the box overwrote that run's files. Now _start_agent gives EVERY run a chat
name through chat_mixin._name_run_chat: the box's text when the user typed
one, else a fresh "<Instruction>_<YYYY-MM-DD_HHMMSS>" ("Agent_…" for an
ad-hoc run) put into the box — also when the box still holds the auto name
of the previous run, since each START is a new conversation. The -l path
leaves the box empty and relies on the same code: one place, one form.

And since every run now has one, the cost log records it: a 14th field, the
chat file stem (saved_chats/<stem>.json + .txt — no extension), sanitised
like the instruction; with it the 13th field (the per-model split) is ALWAYS
present, blank for a one-model run, so the two can never be confused. A
caller without a chat (SelfBot, a bare host) keeps the 12-/13-field shapes
exactly — tests/test_costlog_params.py and tests/test_costlog_split.py pin
that. Both viewers render it as the rightmost CHAT column
(tests/test_costlog_viewer_days.py's ChatColumnTests run the real viewer on
such lines).
"""

import inspect
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import myagent.chat_mixin as cm
import myagent.safety_mixin as sfm
import myagent.state_mixin as stm
import myagent.streaming_mixin as sm
from tests.test_costlog_run_fields import _Host


class _Entry:
    """A stand-in for the tk.Entry "Save Chat as" box: get / delete / insert."""

    def __init__(self, text=""):
        self.text = text

    def get(self):
        return self.text

    def delete(self, first, last=None):
        self.text = ""

    def insert(self, index, text):
        self.text = self.text[:index] + text + self.text[index:]


class _NameHost(cm.ChatMixin):
    def __init__(self, instruction="Balance Westpac", box=""):
        self.agent_instruction_name = instruction
        self.chat_name_entry = _Entry(box)
        self._auto_chat_name = None
        self._run_chat_name = ""


def _stamp(*stamps):
    return mock.patch.object(cm.time, "strftime", side_effect=list(stamps))


class AutoNameTests(unittest.TestCase):

    def test_the_form_is_the_instruction_name_then_a_timestamp(self):
        with _stamp("2026-10-07_091500"):
            self.assertEqual(_NameHost()._new_auto_chat_name(),
                             "Balance Westpac_2026-10-07_091500")

    def test_an_ad_hoc_run_is_named_agent(self):
        with _stamp("2026-10-07_091500"):
            self.assertEqual(_NameHost(instruction="")._new_auto_chat_name(),
                             "Agent_2026-10-07_091500")

    def test_the_timestamp_is_the_launch_paths_format(self):
        # Real strftime: "<Name>_YYYY-MM-DD_HHMMSS", what -l always wrote.
        self.assertRegex(_NameHost()._new_auto_chat_name(),
                         r"^Balance Westpac_\d{4}-\d{2}-\d{2}_\d{6}$")


class NameRunChatTests(unittest.TestCase):

    def test_an_empty_box_gets_the_auto_name(self):
        host = _NameHost(box="")
        with _stamp("2026-10-07_091500"):
            name = host._name_run_chat()
        self.assertEqual(name, "Balance Westpac_2026-10-07_091500")
        self.assertEqual(host.chat_name_entry.get(), name)   # shown in the box
        self.assertEqual(host._run_chat_name, name)          # the log's stem
        self.assertEqual(host._auto_chat_name, name)         # remembered as auto

    def test_a_typed_name_is_kept_and_its_file_stem_recorded(self):
        host = _NameHost(box="  my: chat  ")
        self.assertEqual(host._name_run_chat(), "my: chat")
        self.assertEqual(host.chat_name_entry.get(), "  my: chat  ")   # untouched
        self.assertEqual(host._run_chat_name, "my_ chat")              # as the file is named
        self.assertIsNone(host._auto_chat_name)

    def test_the_previous_runs_auto_name_is_replaced_by_a_fresh_one(self):
        # Each START is a new conversation: reusing the name the app gave the
        # last run would overwrite that run's transcript.
        host = _NameHost(box="")
        with _stamp("2026-10-07_091500", "2026-10-07_093000"):
            first = host._name_run_chat()
            second = host._name_run_chat()          # the box still holds `first`
        self.assertEqual(first, "Balance Westpac_2026-10-07_091500")
        self.assertEqual(second, "Balance Westpac_2026-10-07_093000")
        self.assertEqual(host.chat_name_entry.get(), second)
        self.assertEqual(host._run_chat_name, second)

    def test_a_name_the_user_edited_after_an_auto_name_is_kept(self):
        host = _NameHost(box="")
        with _stamp("2026-10-07_091500"):
            host._name_run_chat()
        host.chat_name_entry.text = "Keep me"
        self.assertEqual(host._name_run_chat(), "Keep me")
        self.assertEqual(host._run_chat_name, "Keep me")

    def test_the_stem_is_exactly_what_sanitize_filename_writes(self):
        raw = 'a<b>:"c/d\\e|f?g*h. '
        host = _NameHost(box=raw)
        host._name_run_chat()
        self.assertEqual(host._run_chat_name + ".json", cm.ChatMixin._sanitize_filename(raw))
        self.assertEqual(host._run_chat_name, 'a_b___c_d_e_f_g_h')

    def test_the_instruction_name_is_whitespace_collapsed(self):
        host = _NameHost(instruction="  Two   words\n", box="")
        with _stamp("2026-10-07_091500"):
            self.assertEqual(host._name_run_chat(), "Two words_2026-10-07_091500")


class WiringTests(unittest.TestCase):

    def test_start_agent_names_the_chat_before_the_run_is_reset_and_started(self):
        src = inspect.getsource(sfm.SafetyMixin._start_agent)
        self.assertIn("self._name_run_chat()", src)
        self.assertLess(src.index("self._name_run_chat()"), src.index("self.messages = []"))
        self.assertLess(src.index("self._name_run_chat()"), src.index("stream_worker"))

    def test_the_launch_path_leaves_the_naming_to_start_agent(self):
        src = inspect.getsource(stm.StateMixin._auto_launch)
        self.assertNotIn("strftime", src)
        self.assertNotIn("chat_name_entry.insert", src)
        self.assertIn("self.chat_name_entry.delete(0, tk.END)", src)
        self.assertLess(src.index("self.chat_name_entry.delete(0, tk.END)"),
                        src.index("self.root.after(200, self._start_agent)"))

    def test_stream_worker_snapshots_the_stem_and_hands_it_to_the_log(self):
        src = inspect.getsource(sm.StreamingMixin.stream_worker)
        self.assertIn('chat_name = getattr(self, "_run_chat_name", "")', src)
        self.assertIn("chat=chat_name", src)


class _LogHost(sm.StreamingMixin):
    def __init__(self, provider="OpenAI", model="gpt-6-luna"):
        self.provider, self.model = provider, model

    def _get_model_param_summary(self):
        return "reasoning=Max verbosity=low"

    def _tool_info(self, msg):
        pass


class _Isolated(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log = Path(tmp.name) / "APICostLog_test.txt"
        patcher = mock.patch.object(sm, "APICOST_LOG_FILE", str(self.log))
        patcher.start()
        self.addCleanup(patcher.stop)

    def fields(self):
        lines = self.log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.log.unlink()
        return lines[0].split(";")


class LogLineTests(_Isolated):

    def test_the_chat_is_the_14th_field_and_forces_a_blank_13th(self):
        _LogHost()._log_api_cost(0.0129, had_usage=True, duration_secs=117,
                                 instruction="Act_on_unread_emails", calls=9,
                                 tokens=(612, 8138, 48113, 276910),
                                 chat="Act_on_unread_emails_2026-10-07_071620")
        f = self.fields()
        self.assertEqual(len(f), 14)
        self.assertEqual(f[6:8], ["Act_on_unread_emails", "9"])
        self.assertEqual(f[8:12], ["612", "8138", "48113", "276910"])
        self.assertEqual(f[12], "")                               # nothing to split
        self.assertEqual(f[13], "Act_on_unread_emails_2026-10-07_071620")

    def test_with_a_split_both_sit_in_their_own_places(self):
        split = {"claude-sonnet-5": [0.0264, 2, 60, 10324, 0],
                 "claude-fable-5-1": [0.141, 2, 226, 10372, 0]}
        _LogHost("Anthropic", "claude-fable-5-1")._log_api_cost(
            0.1674, had_usage=True, tokens=(4, 286, 20696, 0), split=split,
            chat="ZZ_2026-10-07_091500")
        f = self.fields()
        self.assertEqual(len(f), 14)
        self.assertEqual(f[12], "claude-sonnet-5,0.026400,2,60,10324,0"
                                "|claude-fable-5-1,0.141000,2,226,10372,0")
        self.assertEqual(f[13], "ZZ_2026-10-07_091500")

    def test_the_chat_is_sanitised_like_the_instruction(self):
        _LogHost()._log_api_cost(0.5, had_usage=True, tokens=(1, 2, 3, 4),
                                 chat="  odd; name\nwith   breaks ")
        f = self.fields()
        self.assertEqual(len(f), 14)
        self.assertEqual(f[13], "odd, name with breaks")

    def test_no_chat_keeps_the_12_field_shape(self):
        for chat in (None, "", "   "):
            with self.subTest(chat=chat):
                _LogHost()._log_api_cost(0.5, had_usage=True, tokens=(1, 2, 3, 4), chat=chat)
                self.assertEqual(len(self.fields()), 12)

    def test_no_chat_with_a_split_keeps_the_13_field_shape(self):
        _LogHost("Anthropic", "claude-fable-5-1")._log_api_cost(
            0.5, had_usage=True, tokens=(1, 2, 3, 4),
            split={"claude-sonnet-5": [0.1, 1, 1, 1, 1], "claude-fable-5-1": [0.4, 0, 1, 2, 3]})
        self.assertEqual(len(self.fields()), 13)


class StreamWorkerChatTests(_Isolated):
    """The real loop (tests/test_costlog_run_fields._Host: a stubbed
    Anthropic call, tool round then answer) hands the stem to the log."""

    def test_the_run_logs_its_chat_stem_on_both_loop_end_paths(self):
        for second in ("end", "raise"):
            with self.subTest(path=second):
                host = _Host(second_call=second)
                host._run_chat_name = "Balance Westpac_2026-10-07_091500"
                host.stream_worker([{"role": "user", "content": "go"}])
                f = self.fields()
                self.assertEqual(len(f), 14)
                self.assertEqual(f[6], "Balance Westpac")
                self.assertEqual(f[12], "")
                self.assertEqual(f[13], "Balance Westpac_2026-10-07_091500")

    def test_the_stem_is_snapshotted_at_run_start(self):
        # Like the instruction name: the box can be edited mid-run, and the
        # line belongs to the chat the run STARTED as.
        host = _Host(second_call="end")
        host._run_chat_name = "Started_as"
        real_call = host._stream_anthropic_call

        def rename_then_call(*a, **k):
            host._run_chat_name = "Renamed_mid_run"
            return real_call(*a, **k)

        host._stream_anthropic_call = rename_then_call
        host.stream_worker([{"role": "user", "content": "go"}])
        self.assertEqual(self.fields()[13], "Started_as")

    def test_a_host_without_a_chat_keeps_12_fields(self):
        host = _Host(second_call="end")
        host.stream_worker([{"role": "user", "content": "go"}])
        self.assertEqual(len(self.fields()), 12)


class ChatFileTests(unittest.TestCase):
    """The stem the log records is where _auto_save_on_close writes."""

    def test_the_stem_plus_extension_is_the_saved_file(self):
        host = _NameHost(box="")
        with _stamp("2026-10-07_091500"):
            name = host._name_run_chat()
        self.assertEqual(Path(cm.ChatMixin._chat_file_path(name)).name,
                         host._run_chat_name + ".json")
        self.assertTrue(re.fullmatch(r"Balance Westpac_\d{4}-\d{2}-\d{2}_\d{6}", host._run_chat_name))


if __name__ == "__main__":
    unittest.main()
