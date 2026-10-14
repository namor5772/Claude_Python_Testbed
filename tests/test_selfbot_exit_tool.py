"""SelfBot's Exit tool — a chatbot that can close itself (2026-10-14).

The user asked for the Pause box's wiring to kill the process instead of
resting the chat ("in that way a chatbot can kill itself"). The box is Exit
now and its tool `exit_conversation`: a call hands `_on_close` — the [X]
handler — to the Tk loop from the streaming worker, so the close WAITS for the
reply in flight (the model's closing note), then saves the chat, logs the cost
line, closes the peer with it and destroys the window, exactly as [X] does.
The pause / rest machinery (`pause_conversation`, `_model_paused`,
`_resume_from_model_pause`) is gone.

What this pins: the schema under the toggle; the dispatch and its guard; the
tool on a bare host — `_on_close` scheduled at once, never a bare destroy, the
⏻ notice with the reason, the result's wording; the REAL stream_worker over
the parity module's fake client finishing the reply after the call; `_on_close`
waiting for streaming before `_finish_close` and the close's order; the state
and prompt-bundle keys with the old `pause` ones still read; and a source scan
for the old names.
"""
import inspect
import os
import queue
import re
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from myagent import constants as c
from tests.test_selfbot_anthropic_parity import (
    SelfBot, _app, _drain, _final, _needs_selfbot, _usage,
)
from tests.test_selfbot_tool_parity import _app as _toggle_app, _flags, _names


class _Flag:
    def __init__(self, value=False):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _Recorder:
    """A callable that records every call's positional arguments."""

    def __init__(self):
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)


def _tool_use(name="exit_conversation", block_id="t1", **inp):
    return SimpleNamespace(type="tool_use", name=name, id=block_id, input=dict(inp))


def _bare(**attrs):
    app = SelfBot.App.__new__(SelfBot.App)
    app.queue = queue.Queue()
    for k, v in attrs.items():
        setattr(app, k, v)
    return app


def _warnings(msgs):
    return [m["content"] for m in msgs if m["type"] == "warning"]


def _source():
    with open(SelfBot.__file__, encoding="utf-8") as f:
        return f.read()


# ── the catalogue ───────────────────────────────────────────────────────

@_needs_selfbot
class CatalogueTests(unittest.TestCase):
    def test_exit_tools_is_one_tool_with_an_optional_reason(self):
        self.assertEqual([t["name"] for t in SelfBot.EXIT_TOOLS], ["exit_conversation"])
        tool = SelfBot.EXIT_TOOLS[0]
        self.assertEqual(set(tool["input_schema"]["properties"]), {"reason"})
        self.assertNotIn("required", tool["input_schema"])
        for phrase in ("closing this chatbot instance", "the process exits", "saves its chat",
                       "partner instance is closed", "NOT delivered", "no resume"):
            self.assertIn(phrase, tool["description"])

    def test_the_exit_box_gates_the_tool(self):
        with _flags():
            self.assertIn("exit_conversation", _names(_toggle_app(exit=True)))
            self.assertNotIn("exit_conversation", _names(_toggle_app(exit=False)))
            self.assertNotIn("pause_conversation", _names(_toggle_app(exit=True)))

    def test_the_tool_is_not_parallel_safe(self):
        # It mutates the shared close state, so the loop runs it alone, in order.
        self.assertNotIn("exit_conversation", c.PARALLEL_SAFE_TOOLS)
        self.assertIn('PARALLEL_SAFE = {"csv_search", "get_skill"} | set(PARALLEL_SAFE_TOOLS)',
                      inspect.getsource(SelfBot.App.stream_worker))


# ── the dispatch ────────────────────────────────────────────────────────

@_needs_selfbot
class DispatchTests(unittest.TestCase):
    def test_the_dispatch_hands_the_reason_to_do_exit_conversation(self):
        done = _Recorder()
        app = _bare(exit_enabled=_Flag(True), do_exit_conversation=done)
        app._execute_tool(_tool_use(reason="All said."))
        self.assertEqual(done.calls, [("All said.",)])
        self.assertEqual([m for m in _drain(app.queue) if m["type"] == "tool_info"],
                         [{"type": "tool_info", "content": "exit_conversation\n"}])

    def test_no_reason_is_an_empty_string(self):
        done = _Recorder()
        app = _bare(exit_enabled=_Flag(True), do_exit_conversation=done)
        app._execute_tool(_tool_use())
        self.assertEqual(done.calls, [("",)])

    def test_an_unticked_box_refuses_the_call(self):
        done = _Recorder()
        app = _bare(exit_enabled=_Flag(False), do_exit_conversation=done)
        result = app._execute_tool(_tool_use(reason="x"))
        self.assertIn("not enabled", result)
        self.assertEqual(done.calls, [])


# ── the tool on a bare host ─────────────────────────────────────────────

@_needs_selfbot
class ExitToolTests(unittest.TestCase):
    def _call(self, reason):
        after = _Recorder()
        app = _bare(root=SimpleNamespace(after=after))
        result = app.do_exit_conversation(reason)
        return app, after, result, _drain(app.queue)

    def test_the_call_hands_on_close_to_the_tk_loop(self):
        app, after, _, _ = self._call("")
        # The [X] handler itself, scheduled at once (delay 0) — never a bare
        # destroy, which would skip the chat save, the cost line and the peer.
        self.assertEqual(after.calls, [(0, app._on_close)])

    def test_the_notice_carries_the_reason_and_the_result_says_what_happens(self):
        _, _, result, msgs = self._call("  We have said it all.  ")
        self.assertEqual(_warnings(msgs), [
            "⏻ Conversation ended by the model — We have said it all. This instance closes "
            "when this reply ends — the chat is saved and the cost logged first; a connected "
            "partner closes with it.\n"])
        self.assertTrue(result.startswith("Closing — this instance (and its partner, if one is connected) "
                                          "closes when this turn ends; the chat is saved first."))
        self.assertIn("not delivered to your partner", result)

    def test_no_reason_no_dash(self):
        _, _, _, msgs = self._call("")
        self.assertTrue(_warnings(msgs)[0].startswith("⏻ Conversation ended by the model. "))

    def test_nothing_else_is_touched_before_the_close(self):
        # Auto-chat, the button, the pending injection: _on_close's business, on
        # the Tk thread. The worker-side tool sets nothing itself.
        app, _, _, _ = self._call("")
        for name in ("_auto_chat", "_auto_chat_user_off", "_pending_injection", "_closing"):
            self.assertFalse(hasattr(app, name), name)


# ── the real loop ───────────────────────────────────────────────────────

@_needs_selfbot
class RealLoopTests(unittest.TestCase):
    def test_the_reply_runs_to_its_end_after_the_call(self):
        """The close is scheduled during the tool round; the model still gets
        its closing round (the second call) and the reply ends normally — the
        `complete` event is what lets `_on_close`'s wait proceed."""
        app = _app(steps=[
            _final(stop_reason="tool_use", content=[_tool_use(reason="All said.")],
                   usage=_usage(100, 20, 0, 0)),
            _final(usage=_usage(10, 5, 0, 0)),
        ])
        after = _Recorder()
        app.exit_enabled = _Flag(True)
        app.root = SimpleNamespace(after=after)
        app.stream_worker([{"role": "user", "content": "Goodbye?"}])
        msgs = _drain(app.queue)
        self.assertEqual(len(app.calls), 2)
        self.assertEqual(after.calls, [(0, app._on_close)])
        self.assertEqual(_warnings(msgs), [
            "⏻ Conversation ended by the model — All said. This instance closes when this "
            "reply ends — the chat is saved and the cost logged first; a connected partner "
            "closes with it.\n"])
        self.assertEqual([m["type"] for m in msgs][-1], "complete")
        # The second call carries the tool result the model answers with its closing note.
        last = app.calls[1][1]["messages"][-1]
        self.assertEqual(last["role"], "user")
        results = [b for b in last["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["tool_use_id"], "t1")
        self.assertTrue(results[0]["content"].startswith("Closing — this instance"))


# ── the close path the tool rides ───────────────────────────────────────

@_needs_selfbot
class ClosePathTests(unittest.TestCase):
    def _host(self, streaming, msg_file):
        after, finish = _Recorder(), _Recorder()
        app = _bare(streaming=streaming, _auto_chat=_Flag(True), _auto_chat_user_off=False,
                    root=SimpleNamespace(after=after), _finish_close=finish)
        return app, after, finish

    def test_on_close_waits_for_the_reply_in_flight(self):
        with tempfile.TemporaryDirectory() as d:
            msg_file = os.path.join(d, "selfbot_auto_msg.json")
            open(msg_file, "w").close()
            app, after, finish = self._host(True, msg_file)
            with mock.patch.object(SelfBot, "AUTO_MSG_FILE", msg_file):
                app._on_close()
            self.assertTrue(app._closing)
            self.assertFalse(app._auto_chat.get())         # no closing reply is injected
            self.assertTrue(app._auto_chat_user_off)       # and the peer poll can't re-arm it
            self.assertFalse(os.path.exists(msg_file))     # a queued peer message is dropped
            self.assertEqual(after.calls, [(200, finish)]) # the wait: _finish_close in 200 ms
            self.assertEqual(finish.calls, [])             # not yet
            # A second request while closing is a no-op (the re-entrancy guard).
            app._on_close()
            self.assertEqual(after.calls, [(200, finish)])

    def test_on_close_with_nothing_in_flight_finishes_at_once(self):
        with tempfile.TemporaryDirectory() as d:
            app, after, finish = self._host(False, os.path.join(d, "none.json"))
            with mock.patch.object(SelfBot, "AUTO_MSG_FILE", os.path.join(d, "none.json")):
                app._on_close()
            self.assertEqual(after.calls, [])
            self.assertEqual(finish.calls, [()])

    def test_finish_close_keeps_waiting_while_streaming(self):
        after = _Recorder()
        app = _bare(streaming=True, root=SimpleNamespace(after=after))
        app._finish_close()
        self.assertEqual(after.calls, [(200, app._finish_close)])

    def test_the_close_saves_the_chat_logs_the_cost_then_destroys(self):
        close = inspect.getsource(SelfBot.App._finish_close)
        save, log = close.index("self._auto_save_on_close()"), close.index("self._log_api_cost(")
        clear, destroy = close.index("self._run_progress_clear()"), close.index("self.root.destroy()")
        self.assertLess(save, log)
        self.assertLess(log, clear)
        self.assertLess(clear, destroy)
        self.assertIn("if self.streaming:", inspect.getsource(SelfBot.App._on_close))


# ── persistence ─────────────────────────────────────────────────────────

@_needs_selfbot
class PersistenceTests(unittest.TestCase):
    def _bundle_host(self, exit_on=False):
        return _bare(exit_enabled=_Flag(exit_on), _is_second_instance=False, available_models=[],
                     _on_model_selected=lambda: None)

    def test_a_prompt_bundle_restores_exit(self):
        app = self._bundle_host()
        app._apply_prompt_settings({"exit": True})
        self.assertTrue(app.exit_enabled.get())
        app._apply_prompt_settings({"exit": False})
        self.assertFalse(app.exit_enabled.get())

    def test_a_prompt_saved_as_pause_still_restores(self):
        app = self._bundle_host()
        app._apply_prompt_settings({"pause": True})         # the key's name until 2026-10-14
        self.assertTrue(app.exit_enabled.get())
        app._apply_prompt_settings({"exit": False, "pause": True})   # the new key wins
        self.assertFalse(app.exit_enabled.get())

    def test_a_bundle_without_the_key_leaves_the_box_alone(self):
        app = self._bundle_host(exit_on=True)
        app._apply_prompt_settings({"text": "legacy"})
        self.assertTrue(app.exit_enabled.get())

    def test_the_capture_bundles_exit_not_pause(self):
        app = _bare(my_name_entry=_Flag("A"), my_friend_entry=_Flag("B"), model="claude-opus-5-5",
                    temperature=1.0, thinking_enabled=True, thinking_effort="high",
                    thinking_budget=8192, thinking_mode="adaptive", skills={},
                    _disabled_confirm_patterns=set(),
                    **{k: _Flag(False) for k in (
                        "desktop_enabled", "browser_enabled", "meta_enabled", "mcp_enabled",
                        "google_enabled", "proton_enabled", "outlook_enabled", "excel_enabled",
                        "physical_enabled", "dictation_auto_send")},
                    exit_enabled=_Flag(True))
        captured = app._capture_prompt_settings()
        self.assertIs(captured["exit"], True)
        self.assertNotIn("pause", captured)

    def test_the_state_file_key_and_the_one_it_replaced(self):
        self.assertIn('"exit_enabled": self.exit_enabled.get()',
                      inspect.getsource(SelfBot.App._save_last_state))
        load = inspect.getsource(SelfBot.App._load_last_state)
        self.assertIn('state.get("exit_enabled", state.get("pause_enabled"))', load)
        self.assertNotIn('"pause_enabled":', inspect.getsource(SelfBot.App._save_last_state))

    def test_manage_prompts_knows_exit(self):
        schema = next(t for t in SelfBot.SELFBOT_META_TOOLS if t["name"] == "manage_prompts")
        props = schema["input_schema"]["properties"]
        self.assertIn("exit", props)
        self.assertNotIn("pause", props)
        self.assertIn("exit_conversation", props["exit"]["description"])
        self.assertIn("exit", SelfBot.App._PROMPT_TOGGLE_KEYS)
        self.assertNotIn("pause", SelfBot.App._PROMPT_TOGGLE_KEYS)


# ── wiring ──────────────────────────────────────────────────────────────

@_needs_selfbot
class WiringTests(unittest.TestCase):
    def test_the_exit_box_is_on_the_tool_row(self):
        src = _source()
        self.assertRegex(src, r'self\.exit_toggle = tk\.Checkbutton\(\s*tools_frame, text="Exit", '
                              r'variable=self\.exit_enabled')
        self.assertIn("self.exit_enabled = tk.BooleanVar(value=False)", src)

    def test_get_tools_offers_the_set_under_the_box(self):
        src = inspect.getsource(SelfBot.App._get_tools)
        self.assertRegex(src, r"if self\.exit_enabled\.get\(\):\s*tools\.extend\(copy\.deepcopy\(EXIT_TOOLS\)\)")

    def test_the_tool_schedules_the_x_handler(self):
        self.assertIn("self.root.after(0, self._on_close)",
                      inspect.getsource(SelfBot.App.do_exit_conversation))

    def test_the_pause_machinery_is_gone(self):
        src = _source()
        for name in ("pause_conversation", "PAUSE_TOOLS", "do_pause_conversation", "pause_toggle",
                     "_model_paused", "_resume_from_model_pause"):
            self.assertNotIn(name, src, name)
        # The only surviving mentions of the old key are the two legacy reads.
        self.assertEqual(len(re.findall(r'"pause_enabled"', src)), 1)
        self.assertEqual(len(re.findall(r'entry\["pause"\]', src)), 1)


if __name__ == "__main__":
    unittest.main()
