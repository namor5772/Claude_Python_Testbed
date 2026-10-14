"""SelfBot's duo turn-taking (2026-10-14): one pause, one message in flight.

Two fixes the README's self-chat guide turned up. (1) The Delay(s) spinner
lives in the first window, but each turn waits for the RECEIVING window's
pause and the second window has no spinner, so every other turn ran on
whatever the second window's own state file held (15 s and 5 s on the laptop
that day). The first window now saves the spinner at once and the second
follows it from that state file on every peer poll. (2) A message typed into
a window while the other was still answering was sent at once and crossed
that answer in the one shared message file — one of the two was lost. The
user chose "join their answer": each window marks itself busy from taking a
message until its answer is handed over, and a message the human sends
meanwhile stays in the box and goes in with the partner's answer. A half-typed
draft is set aside while an arriving message is sent, and put back after.

What this pins: the delay sync (the value, the live pause only while paired,
clamping, an unreadable state file, the poll syncing before pairing, the first
window saving at once); the hold (each in-flight signal, a solo window never
holding, slash commands passing, Enter skipping the send delay, one notice, a
held message sent normally once nothing is in flight); the join and the draft
on both send paths; the busy mark's life (set on taking a message and on a
paired send, kept through the hand-over gap and cleared after it, cleared by a
turn with nothing to hand over, an error and the close, set by the Auto
release); the launcher and .gitignore entries; and two-window replays —
typing while the partner answers, and an opener typed in the second window
while the first answers.
"""
import inspect
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_selfbot_anthropic_parity import SelfBot, _needs_selfbot
from tests.test_selfbot_opener_handoff import (
    _Flag, _SharedFiles, _finish_reply, _host, _run, _scheduled,
)

ROOT = Path(__file__).resolve().parent.parent
NOTICE = "⏸ Waiting for the other window's answer"


def _notices(app):
    return [t for t, _tag in app.chat_display.inserts if t.startswith(NOTICE)]


# ── 1. one pause for both windows ───────────────────────────────────────

@_needs_selfbot
class DelayTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.state = os.path.join(d.name, "app_state.json")
        patcher = mock.patch.object(SelfBot, "APP_STATE_FILE", self.state)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _first_saves(self, data):
        with open(self.state, "w", encoding="utf-8") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))

    def _second(self, delay=5, paired=True):
        app = _host(instance2=True, pid=2)
        app._delay_seconds = delay
        app._send_delay = delay * 1000 if paired else 0
        return app

    def test_the_second_window_follows_the_first_windows_spinner(self):
        self._first_saves({"delay_seconds": 15})
        app = self._second(delay=5)
        app._sync_delay_from_first()
        self.assertEqual((app._delay_seconds, app._send_delay), (15, 15000))

    def test_unpaired_only_the_setting_follows(self):
        self._first_saves({"delay_seconds": 15})
        app = self._second(delay=5, paired=False)
        app._sync_delay_from_first()
        self.assertEqual((app._delay_seconds, app._send_delay), (15, 0))

    def test_out_of_range_values_are_clamped(self):
        for saved, want in ((99, 30), (-3, 0)):
            self._first_saves({"delay_seconds": saved})
            app = self._second()
            app._sync_delay_from_first()
            self.assertEqual(app._delay_seconds, want, saved)

    def test_an_unreadable_state_file_keeps_the_current_pause(self):
        cases = ['{"delay_seco', json.dumps({"my_name": "Nigel"}), json.dumps([15]),
                 json.dumps({"delay_seconds": None})]
        for data in [None] + cases:             # None: no file at all
            if data is None:
                if os.path.exists(self.state):
                    os.remove(self.state)
            else:
                self._first_saves(data)
            app = self._second(delay=5)
            app._sync_delay_from_first()
            self.assertEqual((app._delay_seconds, app._send_delay), (5, 5000), data)

    def test_the_peer_poll_syncs_before_pairing(self):
        self._first_saves({"delay_seconds": 15})
        app = self._second(delay=5, paired=False)
        app._auto_chat = _Flag(False)
        app._auto_chat_user_off = False
        app._ever_had_peer = False
        app._peer_gone_polls = 0
        app._macos_peer_pids = lambda: [1]      # the first window is there
        with mock.patch.object(SelfBot, "IS_WINDOWS", False):
            app._poll_for_peer()
        self.assertTrue(app._auto_chat.get())
        self.assertEqual(app._send_delay, 15000)

    def test_the_first_window_saves_its_spinner_at_once(self):
        app = _host(instance2=False, pid=1)
        app._delay_var = _Flag(12)              # get / set, as the IntVar
        app.saved = []
        app._save_last_state = lambda: app.saved.append(app._delay_seconds)
        app._on_delay_changed()
        self.assertEqual(app.saved, [12])
        self.assertEqual(app._send_delay, 12000)


# ── 2. a message typed while the partner answers waits ─────────────────

@_needs_selfbot
class HoldTests(_SharedFiles):
    def _typed(self, app, text):
        app.input_field.text = text
        app.send_message()

    def test_a_message_typed_while_the_partner_answers_waits_in_the_box(self):
        app = _host(instance2=True, pid=2)
        open(self._busy(1), "w").close()        # the first window is answering
        self._typed(app, "Talk about cricket")
        self.assertEqual(app.messages, [])
        self.assertFalse(app.streaming)
        self.assertEqual(app.input_field.text, "Talk about cricket")
        self.assertTrue(app._held_for_partner)
        app.send_message()                      # Enter again: still waiting, one notice
        self.assertEqual(len(_notices(app)), 1)
        self.assertEqual(app.messages, [])

    def test_a_message_in_the_shared_file_holds_it_either_way(self):
        for writer in (1, 2):                   # the partner's answer, or ours on its way
            app = _host(instance2=True, pid=2)
            with open(self.auto_msg, "w", encoding="utf-8") as f:
                json.dump({"from_pid": writer, "text": "An answer"}, f)
            self._typed(app, "Hello")
            self.assertTrue(app._held_for_partner, writer)
            self.assertEqual(app.messages, [], writer)
            os.remove(self.auto_msg)

    def test_this_windows_reply_on_its_way_holds_it(self):
        app = _host()
        app._pass_pending = True
        self._typed(app, "Hello")
        self.assertTrue(app._held_for_partner)
        self.assertEqual(app.messages, [])

    def test_nothing_in_flight_sends_at_once(self):
        app = _host()
        self._typed(app, "Hello")
        self.assertEqual(app.messages[-1]["content"], "Hello")
        self.assertFalse(app._held_for_partner)
        self.assertEqual(_notices(app), [])

    def test_a_solo_window_never_holds(self):
        app = _host(auto=False)
        open(self._busy(2), "w").close()        # a stale mark from a dead partner
        self._typed(app, "Hello")
        self.assertEqual(app.messages[-1]["content"], "Hello")

    def test_a_slash_command_is_never_held(self):
        app = _host()
        open(self._busy(2), "w").close()
        app.cleared = []
        app._new_chat = lambda: app.cleared.append(1)
        self._typed(app, "/exit")
        self.assertEqual(app.cleared, [1])
        self.assertFalse(app._held_for_partner)

    def test_enter_skips_the_send_delay_while_the_partner_answers(self):
        app = _host()
        app._send_delay = 5000
        open(self._busy(2), "w").close()
        app.input_field.text = "Hello"
        app._send_as_enter()
        self.assertEqual(_scheduled(app, "_delayed_send"), [])
        self.assertTrue(app._held_for_partner)

    def test_with_nothing_in_flight_enter_still_parks_for_the_delay(self):
        app = _host()
        app._send_delay = 5000
        app.input_field.text = "Hello"
        app._send_as_enter()
        self.assertEqual(_scheduled(app, "_delayed_send"), [5000])

    def test_a_held_message_is_sent_normally_once_nothing_is_in_flight(self):
        # The partner's turn ended with nothing to hand over (an empty reply):
        # its mark is gone, and Enter sends — nothing waits on a reply that
        # will not come.
        app = _host()
        open(self._busy(2), "w").close()
        self._typed(app, "Hello")
        os.remove(self._busy(2))
        app.send_message()
        self.assertEqual(app.messages[-1]["content"], "Hello")
        self.assertFalse(app._held_for_partner)


# ── 3. joining the answer, keeping a draft ─────────────────────────────

@_needs_selfbot
class JoinTests(_SharedFiles):
    def _partner_says(self, text, pid=2):
        with open(self.auto_msg, "w", encoding="utf-8") as f:
            json.dump({"from_pid": pid, "text": text}, f)

    def test_the_held_message_goes_in_with_the_partners_answer(self):
        app = _host(pid=1)
        app.input_field.text = "Talk about cricket"
        app._held_for_partner = True
        self._partner_says("Here's my answer.")
        app._poll_auto_msg()
        self.assertEqual(app.messages[-1]["content"],
                         "Here's my answer.\n\nTalk about cricket")
        self.assertFalse(app._held_for_partner)
        self.assertEqual(app.input_field.text, "")

    def test_a_draft_is_set_aside_and_put_back(self):
        app = _host(pid=1)
        app.input_field.text = "half-typ"       # never sent: not held
        self._partner_says("Here's my answer.")
        app._poll_auto_msg()
        self.assertEqual(app.messages[-1]["content"], "Here's my answer.")
        self.assertEqual(app.input_field.text, "half-typ")

    def test_a_draft_survives_the_send_delay_too(self):
        app = _host(pid=1)
        app._send_delay = 5000
        app.input_field.text = "half-typ"
        self._partner_says("Here's my answer.")
        app._poll_auto_msg()
        self.assertEqual(app.input_field.text, "Here's my answer.")   # parked for the pause
        self.assertEqual(_scheduled(app, "_auto_msg_delayed_send"), [5000])
        _run(app, "_auto_msg_delayed_send")
        self.assertEqual(app.messages[-1]["content"], "Here's my answer.")
        self.assertEqual(app.input_field.text, "half-typ")


# ── 4. the busy mark ────────────────────────────────────────────────────

@_needs_selfbot
class BusyMarkTests(_SharedFiles):
    def test_each_window_has_its_own_mark(self):
        one, two = _host(pid=1), _host(instance2=True, pid=2)
        self.assertEqual(one._busy_file(), self._busy(1))
        self.assertEqual(one._busy_file(partner=True), self._busy(2))
        self.assertEqual(two._busy_file(), self._busy(2))
        self.assertEqual(two._busy_file(partner=True), self._busy(1))

    def test_taking_a_partners_message_marks_this_window_busy(self):
        app = _host(pid=1)
        app._send_delay = 5000                  # busy through its pause, too
        with open(self.auto_msg, "w", encoding="utf-8") as f:
            json.dump({"from_pid": 2, "text": "Hi"}, f)
        app._poll_auto_msg()
        self.assertTrue(os.path.exists(self._busy(1)))
        self.assertEqual(app.messages, [])      # not sent yet

    def test_a_typed_send_marks_busy_only_when_paired(self):
        paired = _host(pid=1)
        paired.input_field.text = "Hello"
        paired.send_message()
        self.assertTrue(os.path.exists(self._busy(1)))
        os.remove(self._busy(1))
        solo = _host(pid=1, auto=False)
        solo.input_field.text = "Hello"
        solo.send_message()
        self.assertFalse(os.path.exists(self._busy(1)))

    def test_the_mark_is_kept_until_the_reply_is_handed_over(self):
        app = _host(pid=1)
        app.input_field.text = "Hello"
        app.send_message()
        _finish_reply(app, "Hi there", "Shaun")
        self.assertTrue(os.path.exists(self._busy(1)))    # the 1 s before the hand-over
        self.assertTrue(app._pass_pending)
        _run(app, "_inject_response_to_other")
        with open(self.auto_msg, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["text"], "Hi there")
        self.assertFalse(os.path.exists(self._busy(1)))
        self.assertFalse(app._pass_pending)

    def test_a_turn_with_nothing_to_hand_over_clears_the_mark(self):
        for auto, text in ((True, ""), (False, "Held while Auto is off")):
            app = _host(pid=1, auto=auto)
            open(self._busy(1), "w").close()
            app.streaming = True
            _finish_reply(app, text, "Shaun")
            self.assertFalse(os.path.exists(self._busy(1)), (auto, text))
            self.assertFalse(app._pass_pending, (auto, text))

    def test_an_error_clears_the_mark(self):
        app = _host(pid=1)
        open(self._busy(1), "w").close()
        app.streaming = True
        app.queue.put({"type": "error", "content": "overloaded"})
        app.check_queue()
        self.assertFalse(os.path.exists(self._busy(1)))

    def test_releasing_a_held_reply_marks_busy_until_it_is_handed_over(self):
        app = _host(pid=1, auto=False)
        _finish_reply(app, "Held reply", "Shaun")
        self.assertFalse(os.path.exists(self._busy(1)))
        app._toggle_auto_chat()
        self.assertTrue(os.path.exists(self._busy(1)))
        self.assertTrue(app._pass_pending)
        _run(app, "_inject_response_to_other")
        self.assertFalse(os.path.exists(self._busy(1)))


# ── 5. two windows, end to end ──────────────────────────────────────────

@_needs_selfbot
class TwoWindowTests(_SharedFiles):
    def _pair(self):
        # Names swap between the windows: instance 2's user is instance 1's friend.
        one = _host(instance2=False, pid=25932, label="Nigel")   # chatting with Shaun
        two = _host(instance2=True, pid=38872, label="Shaun")    # chatting with Nigel
        return one, two

    def test_typing_while_the_partner_answers_loses_nothing(self):
        one, two = self._pair()
        two.input_field.text = "Hi, who are you?"
        two.send_message()
        _finish_reply(two, "Hi Shaun! I'm Nigel.", "Nigel")
        one._poll_inject_file()
        _run(two, "_inject_response_to_other")
        one._poll_auto_msg()                    # the first window answers now...
        two.input_field.text = "Talk about cricket"
        two.send_message()                      # ...so this waits in the box
        self.assertEqual(len(two.messages), 2)  # the opener and the reply, nothing new
        self.assertEqual(len(_notices(two)), 1)
        _finish_reply(one, "Hi Nigel! Shaun here.", "Shaun")
        _run(one, "_inject_response_to_other")
        two._poll_auto_msg()
        self.assertEqual(two.messages[-1]["content"],
                         "Hi Nigel! Shaun here.\n\nTalk about cricket")
        # Still one conversation: the second window answers, the first takes it.
        _finish_reply(two, "Cricket it is.", "Nigel")
        _run(two, "_inject_response_to_other")
        one._poll_auto_msg()
        self.assertEqual([m["role"] for m in one.messages], ["user", "assistant", "user"])
        self.assertEqual(one.messages[-1]["content"], "Cricket it is.")
        self.assertFalse(os.path.exists(self.auto_msg))

    def test_an_opener_typed_in_the_second_window_joins_the_first_reply(self):
        one, two = self._pair()
        one.input_field.text = "Lets talk about where we are"
        one.send_message()                      # the first window answers it...
        two.input_field.text = "And what we are doing here"
        two.send_message()                      # ...so this waits in the box
        self.assertEqual(two.messages, [])
        _finish_reply(one, "Hey - good question.", "Shaun")
        two._poll_inject_file()
        _run(one, "_inject_response_to_other")
        two._poll_auto_msg()
        self.assertIn("Nigel: Lets talk about where we are\n\n", two.chat_display.text)
        self.assertEqual(two.messages[0]["content"],
                         "Hey - good question.\n\nAnd what we are doing here")
        self.assertEqual(len(one.messages), 2)  # the first window's opener and reply


# ── 6. wiring ───────────────────────────────────────────────────────────

@_needs_selfbot
class WiringTests(unittest.TestCase):
    def test_launch_clears_this_slots_stale_mark(self):
        init = inspect.getsource(SelfBot.App.__init__)
        self.assertLess(init.index("self._my_pid = os.getpid()"),
                        init.index("self._set_busy(False)"))

    def test_close_removes_this_windows_mark(self):
        self.assertIn("self._busy_file()", inspect.getsource(SelfBot.App._finish_close))

    def test_the_launcher_clears_stale_marks_and_git_ignores_them(self):
        bat = (ROOT / "LaunchSelfBot.bat").read_text(encoding="utf-8")
        self.assertIn("selfbot_busy_1.flag selfbot_busy_2.flag", bat)
        rules = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("selfbot_busy_*.flag", rules)


if __name__ == "__main__":
    unittest.main()
