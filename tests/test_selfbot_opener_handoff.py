"""SelfBot's opener handoff between the duo's two windows (2026-10-14).

The duo passes every reply through `selfbot_auto_msg.json`; once per process a
second file, `selfbot_inject.txt`, carries the HUMAN's opening line to the
partner window — whose own conversation starts with the reply, so the line the
human typed would never appear there. The handoff assumed the human always
typed in instance 1: instance 1 captured its first `send_message` text and
only instance 2 polled. Opened in instance 2's window instead (all four duo
chats of 2026-10-14), instance 1's first message was instance 2's OWN reply
arriving through the auto-message path, so instance 1 handed that reply back
and instance 2 printed it a second time — `Nigel: Hi Shaun! …` under
`Nigel:\\nHi Shaun! …`, the user's report. Display only: the API history was
clean, but the .txt transcript is a dump of the display.

What this pins: an opener is captured only when typed in this window, in
either instance, never from the partner's reply; the writer names its pid,
writes once and nothing without an opener; the reader leaves its own file
alone, shows a partner's opener only into a conversation not yet started,
takes a file once, looks again after a half-written one and stops after this
window's first reply; the `complete` handler and the Auto toggle write the
opener with the FIRST reply only, and only when that reply goes to a partner;
both instances poll and both clean up; and two-window replays of the reported
chat (opened in instance 2) and of the original flow (opened in instance 1).
"""
import inspect
import json
import os
import queue
import re
import tempfile
import unittest
from contextlib import ExitStack
from unittest import mock

from tests.test_selfbot_anthropic_parity import SelfBot, _needs_selfbot


class _Flag:
    def __init__(self, value=False):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _FakeText:
    """The chat display or the message box: a string buffer answering the Text
    calls the handoff paths make."""

    def __init__(self):
        self.text = ""
        self.inserts = []           # (text, tag) in order

    def get(self, *index):
        if index == ("end-2c", "end-1c"):   # _ensure_newline's last character
            return self.text[-1:]
        return self.text

    def index(self, _index):
        return "2.0" if self.text else "1.0"

    def insert(self, _index, text, tag=None):
        self.text += text
        self.inserts.append((text, tag))

    def delete(self, *_index):
        self.text = ""

    def config(self, **_kw):
        pass

    def see(self, _index):
        pass

    def focus_set(self):
        pass


class _FakeRoot:
    def __init__(self):
        self.afters = []            # (ms, callback)

    def after(self, ms, fn=None, *_args):
        self.afters.append((ms, fn))
        return f"after#{len(self.afters)}"

    def update_idletasks(self):
        pass

    def bell(self):
        pass


class _FakeButton:
    def config(self, **_kw):
        pass


def _host(instance2=False, pid=1, label="Shaun", auto=True):
    """A bare App with what the handoff, send_message, the auto-message poll
    and check_queue's `complete` tail touch — no Tk, no API."""
    app = SelfBot.App.__new__(SelfBot.App)
    app._is_second_instance = instance2
    app._my_pid = pid
    app._response_count = 0
    app._first_message_text = ""
    app._held_for_partner = False
    app._stashed_draft = ""
    app._pass_pending = False
    app._current_response_text = ""
    app._current_thinking_text = ""
    app._pending_injection = False
    app._send_delay = 0
    app._delay_seconds = 5
    app.streaming = False
    app.messages = []
    app.pending_images = []
    app.chat_display = _FakeText()
    app.input_field = _FakeText()
    app.root = _FakeRoot()
    app.queue = queue.Queue()
    app._auto_chat = _Flag(auto)
    app._auto_chat_btn = _FakeButton()
    app.show_thinking = _Flag(False)
    app._get_user_label = lambda: label
    return app


def _scheduled(app, name):
    return [ms for ms, fn in app.root.afters if getattr(fn, "__name__", "") == name]


def _run(app, name):
    """Run (and drop) the callbacks the app scheduled under that name."""
    due = [fn for _ms, fn in app.root.afters if getattr(fn, "__name__", "") == name]
    app.root.afters = [(ms, fn) for ms, fn in app.root.afters
                       if getattr(fn, "__name__", "") != name]
    for fn in due:
        fn()


def _finish_reply(app, text, friend):
    """What the streaming worker and check_queue do for a reply: the text under
    the friend's label, the assistant turn, then the REAL `complete` tail."""
    app.chat_display.insert("end", f"{friend}:\n{text}", "assistant")
    app.messages.append({"role": "assistant", "content": text})
    app._current_response_text = text
    app.queue.put({"type": "complete"})
    app.check_queue()


class _SharedFiles(unittest.TestCase):
    """Each test gets its own handoff, auto-message and busy-mark files (never
    the repo's), and send_message starts no streaming thread."""

    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        d = stack.enter_context(tempfile.TemporaryDirectory())
        self.dir = d
        self.inject = os.path.join(d, "selfbot_inject.txt")
        self.auto_msg = os.path.join(d, "selfbot_auto_msg.json")
        stack.enter_context(mock.patch.object(SelfBot, "INJECT_FILE", self.inject))
        stack.enter_context(mock.patch.object(SelfBot, "AUTO_MSG_FILE", self.auto_msg))
        stack.enter_context(mock.patch.object(
            SelfBot, "DUO_BUSY_PREFIX", os.path.join(d, "selfbot_busy_"), create=True))
        stack.enter_context(mock.patch.object(SelfBot.threading, "Thread"))

    def _busy(self, instance):
        return os.path.join(self.dir, f"selfbot_busy_{instance}.flag")

    def _write(self, data):
        with open(self.inject, "w", encoding="utf-8") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))

    def _read(self):
        with open(self.inject, encoding="utf-8") as f:
            return json.load(f)


# ── 1. which message is an opener ───────────────────────────────────────

@_needs_selfbot
class CaptureTests(_SharedFiles):
    def _send(self, app, text, **kw):
        app.input_field.text = text
        app.send_message(**kw)

    def test_a_first_message_typed_in_instance_1_is_the_opener(self):
        app = _host(instance2=False)
        self._send(app, "Hi there")
        self.assertEqual(app._first_message_text, "Hi there")

    def test_a_first_message_typed_in_instance_2_is_the_opener_too(self):
        app = _host(instance2=True)
        self._send(app, "Hi, who are you?")
        self.assertEqual(app._first_message_text, "Hi, who are you?")

    def test_the_partners_reply_is_never_an_opener_in_either_instance(self):
        for instance2 in (False, True):
            app = _host(instance2=instance2)
            self._send(app, "Hi Shaun! I'm Nigel.", from_peer=True)
            self.assertEqual(app._first_message_text, "", instance2)
            self.assertEqual(app.messages[-1]["content"], "Hi Shaun! I'm Nigel.")

    def test_a_message_after_the_first_reply_is_no_opener(self):
        app = _host()
        app._response_count = 1
        self._send(app, "And another thing")
        self.assertEqual(app._first_message_text, "")


# ── 2. the writer ───────────────────────────────────────────────────────

@_needs_selfbot
class WriterTests(_SharedFiles):
    def test_the_file_names_the_writer_and_carries_label_and_text(self):
        app = _host(instance2=True, pid=4242, label="Shaun")
        app._first_message_text = "Hi, who are you?"
        app._write_inject_file()
        self.assertEqual(self._read(),
                         {"from_pid": 4242, "label": "Shaun", "text": "Hi, who are you?"})

    def test_it_is_written_once(self):
        app = _host()
        app._first_message_text = "Hello"
        app._write_inject_file()
        os.remove(self.inject)
        app._write_inject_file()
        self.assertFalse(os.path.exists(self.inject))
        self.assertEqual(app._first_message_text, "")

    def test_no_opener_writes_nothing(self):
        app = _host()
        app._write_inject_file()
        self.assertFalse(os.path.exists(self.inject))


# ── 3. the reader ───────────────────────────────────────────────────────

@_needs_selfbot
class ReaderTests(_SharedFiles):
    def test_a_partners_opener_is_shown_into_an_empty_conversation(self):
        app = _host(pid=1)
        self._write({"from_pid": 2, "label": "Shaun", "text": "Hi, who are you?"})
        app._poll_inject_file()
        self.assertEqual(app.chat_display.inserts, [
            ("Shaun: ", "assistant_label"), ("Hi, who are you?\n\n", "assistant")])
        self.assertFalse(os.path.exists(self.inject))
        self.assertEqual(_scheduled(app, "_poll_inject_file"), [])   # taken once

    def test_its_own_file_is_left_for_the_partner(self):
        app = _host(pid=1)
        self._write({"from_pid": 1, "label": "Shaun", "text": "Hi"})
        app._poll_inject_file()
        self.assertEqual(app.chat_display.inserts, [])
        self.assertTrue(os.path.exists(self.inject))
        self.assertEqual(_scheduled(app, "_poll_inject_file"), [500])

    def test_a_started_conversation_takes_the_file_and_shows_nothing(self):
        app = _host(pid=1)
        app.messages = [{"role": "user", "content": "Typed here first"}]
        self._write({"from_pid": 2, "label": "Nigel", "text": "Hi Shaun!"})
        app._poll_inject_file()
        self.assertEqual(app.chat_display.inserts, [])
        self.assertFalse(os.path.exists(self.inject))
        self.assertEqual(_scheduled(app, "_poll_inject_file"), [])

    def test_a_half_written_file_is_read_again_next_poll(self):
        app = _host(pid=1)
        self._write('{"from_pid": 2, "label": "Sha')
        app._poll_inject_file()
        self.assertTrue(os.path.exists(self.inject))
        self.assertEqual(_scheduled(app, "_poll_inject_file"), [500])

    def test_no_file_keeps_polling(self):
        app = _host()
        app._poll_inject_file()
        self.assertEqual(_scheduled(app, "_poll_inject_file"), [500])

    def test_polling_ends_after_this_windows_first_reply(self):
        app = _host(pid=1)
        app._response_count = 1
        self._write({"from_pid": 2, "label": "Nigel", "text": "Hi Shaun!"})
        app._poll_inject_file()
        self.assertEqual(app.chat_display.inserts, [])
        self.assertTrue(os.path.exists(self.inject))           # not even read
        self.assertEqual(app.root.afters, [])

    def test_a_closing_window_stops_polling(self):
        app = _host()
        app._closing = True
        app._poll_inject_file()
        self.assertEqual(app.root.afters, [])


# ── 4. when the opener goes ─────────────────────────────────────────────

@_needs_selfbot
class CompleteTests(_SharedFiles):
    def test_the_first_reply_takes_the_opener_ahead_of_it_in_either_instance(self):
        for instance2 in (False, True):
            app = _host(instance2=instance2, pid=7)
            app._first_message_text = "Hi there"
            app.streaming = True
            _finish_reply(app, "Hello!", "Nigel")
            self.assertEqual(self._read()["text"], "Hi there", instance2)
            self.assertEqual(_scheduled(app, "_inject_response_to_other"), [1000])
            os.remove(self.inject)

    def test_a_reply_held_back_while_auto_is_off_writes_no_opener(self):
        app = _host(auto=False)
        app._first_message_text = "Hello"
        _finish_reply(app, "Hi!", "Nigel")
        self.assertFalse(os.path.exists(self.inject))
        self.assertTrue(app._pending_injection)
        self.assertEqual(app._first_message_text, "Hello")   # kept for the release

    def test_a_later_reply_writes_no_opener(self):
        app = _host()
        app._response_count = 1
        app._first_message_text = "stray"
        _finish_reply(app, "Second answer", "Nigel")
        self.assertFalse(os.path.exists(self.inject))

    def test_releasing_a_held_first_reply_sends_its_opener_first(self):
        app = _host(auto=False)
        app._first_message_text = "Hello"
        _finish_reply(app, "Hi!", "Nigel")
        app._toggle_auto_chat()
        self.assertEqual(self._read()["text"], "Hello")
        self.assertEqual(_scheduled(app, "_inject_response_to_other"), [1000])

    def test_releasing_a_held_later_reply_sends_no_opener(self):
        app = _host(auto=False)
        app._response_count = 1
        app._first_message_text = "stray"
        _finish_reply(app, "Second answer", "Nigel")
        app._toggle_auto_chat()
        self.assertFalse(os.path.exists(self.inject))
        self.assertEqual(_scheduled(app, "_inject_response_to_other"), [1000])


# ── 5. two windows, end to end ──────────────────────────────────────────

@_needs_selfbot
class TwoWindowTests(_SharedFiles):
    OPENER = "Hi, who are you and what am I doing here?"
    REPLY = "Hi Shaun! I'm Nigel, a curious friend who lives inside this Lenovo laptop."
    ANSWER = "Hi Nigel! I checked as well."

    def _pair(self):
        # Names swap between the windows: instance 2's user is instance 1's friend.
        one = _host(instance2=False, pid=25932, label="Nigel")   # chatting with Shaun
        two = _host(instance2=True, pid=38872, label="Shaun")    # chatting with Nigel
        return one, two

    def test_the_reported_chat_opened_in_instance_2_shows_each_line_once(self):
        one, two = self._pair()
        two.input_field.text = self.OPENER          # the human types in instance 2
        two.send_message()
        _finish_reply(two, self.REPLY, "Nigel")
        one._poll_inject_file()                     # the opener crosses first...
        _run(two, "_inject_response_to_other")      # ...the reply 1 s later
        one._poll_auto_msg()
        _finish_reply(one, self.ANSWER, "Shaun")
        self.assertFalse(os.path.exists(self.inject))   # nothing handed back
        two._poll_inject_file()
        _run(one, "_inject_response_to_other")
        two._poll_auto_msg()

        self.assertEqual(two.chat_display.text.count(self.REPLY), 1)
        self.assertNotIn(f"Nigel: {self.REPLY}", two.chat_display.text)
        self.assertTrue(two.chat_display.text.startswith(f"Shaun:\n{self.OPENER}"))
        self.assertTrue(one.chat_display.text.startswith(f"Shaun: {self.OPENER}\n\n"))
        self.assertEqual(one.chat_display.text.count(self.REPLY), 1)
        self.assertEqual(one.chat_display.text.count(self.OPENER), 1)
        # The API histories are what they always were: the opener only in two's.
        self.assertEqual(two.messages[0]["content"], self.OPENER)
        self.assertEqual(one.messages[0]["content"], self.REPLY)

    def test_opened_in_instance_1_the_partner_shows_the_opener_as_before(self):
        one, two = self._pair()
        one.input_field.text = "Lets talk about where we are"
        one.send_message()
        _finish_reply(one, "Hey — good question.", "Shaun")
        two._poll_inject_file()
        _run(one, "_inject_response_to_other")
        two._poll_auto_msg()
        _finish_reply(two, "Ha, you jumped the gun.", "Nigel")
        self.assertFalse(os.path.exists(self.inject))
        one._poll_inject_file()

        self.assertTrue(two.chat_display.text.startswith(
            "Nigel: Lets talk about where we are\n\nShaun:\nHey — good question."))
        self.assertEqual(one.chat_display.text.count("Lets talk about where we are"), 1)
        self.assertEqual(one.chat_display.text.count("Hey — good question."), 1)


# ── 6. wiring ───────────────────────────────────────────────────────────

@_needs_selfbot
class WiringTests(unittest.TestCase):
    def test_both_instances_poll_for_the_opener(self):
        init = inspect.getsource(SelfBot.App.__init__)
        self.assertEqual(init.count("self._poll_inject_file"), 1)
        # At method-body level: not inside the `if self._is_second_instance:` block.
        self.assertRegex(init, r"\n {8}self\.root\.after\(500, self\._poll_inject_file\)")

    def test_only_the_auto_message_paths_send_as_the_partner(self):
        for name in ("_poll_auto_msg", "_auto_msg_delayed_send"):
            src = inspect.getsource(getattr(SelfBot.App, name))
            self.assertIn("self.send_message(from_peer=True)", src, name)
            self.assertNotIn("self.send_message()", src, name)
        for name in ("_send_as_enter", "_delayed_send"):
            src = inspect.getsource(getattr(SelfBot.App, name))
            self.assertIn("self.send_message()", src, name)

    def test_either_instance_removes_the_opener_file_at_close(self):
        close = inspect.getsource(SelfBot.App._finish_close)
        lines = [ln for ln in close.splitlines() if "INJECT_FILE" in ln]
        self.assertEqual(len(lines), 1)
        # Method-body level, after the instance-1-only lock-file block.
        self.assertTrue(re.match(r" {8}\S", lines[0]), lines[0])
        self.assertLess(close.index("os.remove(LOCK_FILE)"), close.index("INJECT_FILE"))


if __name__ == "__main__":
    unittest.main()
