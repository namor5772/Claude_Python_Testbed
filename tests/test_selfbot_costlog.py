"""SelfBot's cost collection, analogous to MyAgent's (2026-10-14).

The user noticed the cost log's TIME column empty on SelfBot's lines. Now the
line is written by MyAgent's own `_log_api_cost` (a byte-identical copy), from
one argument list (`_session_log_args`) the close and the in-progress record
share: the session's working seconds (every reply's wall-clock minus the
confirmation dialog's wait — MyAgent's run clock, per reply), the per-model
split (a mid-chat model switch, a refusal fallback), the had_usage flag with
the unpriced warnings, and `selfbot_run_<N>.json`, rewritten after every call
and folded in at the next launch, so a SelfBot killed from outside — the duo's
taskkill — still gets its line.

The loop tests drive the REAL `stream_worker` over the parity module's fake
client with `time.monotonic` replaced by a clock the fake client advances.
"""
import inspect
import json
import os
import pathlib
import queue
import re
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from myagent.streaming_mixin import StreamingMixin
from tests.test_selfbot_anthropic_parity import (
    OPUS55, SelfBot, _app, _drain, _final, _needs_selfbot, _reply, _usage,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
STAMP = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
OPUS5 = "claude-opus-5"


def _tool_use(name="no_such_tool", block_id="t1"):
    return SimpleNamespace(type="tool_use", name=name, id=block_id, input={})


def _two_call_steps(first_model=OPUS55, second_model=OPUS55):
    """A reply of two calls: a tool_use answered by the loop, then end_turn."""
    return [_final(stop_reason="tool_use", content=[_tool_use()], usage=_usage(100, 20, 0, 0),
                   model=first_model),
            _final(usage=_usage(10, 5, 0, 0), model=second_model)]


def _clocked_app(steps, call_secs=10.0, wait_secs=0.0, **kw):
    """The parity module's bare App whose fake client ADVANCES a clock on
    every call — the API's time — and, when asked, sits `wait_secs` inside
    `input_wait_timer` as a confirmation dialog would. The clock is what
    `time.monotonic` answers while a test patches it."""
    app = _app(steps=steps, **kw)
    clock = [1000.0]
    inner = app.client.beta.messages.stream

    def stream(betas, **api_kwargs):
        clock[0] += call_secs
        if wait_secs:
            with SelfBot.input_wait_timer(app):
                clock[0] += wait_secs
        return inner(betas, **api_kwargs)

    app.client.beta.messages.stream = stream
    return app, clock


def _warnings(msgs):
    return [m["content"] for m in msgs if m["type"] == "warning"]


def _cost_updates(msgs):
    return [m for m in msgs if m["type"] == "cost_update"]


class _TempLog(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = pathlib.Path(tmp.name)
        self.log = self.dir / "APICostLog_test.txt"
        for name, value in (("APICOST_LOG_FILE", str(self.log)),
                            ("SELFBOT_RUN_PREFIX", str(self.dir / "selfbot_run_"))):
            patcher = mock.patch.object(SelfBot, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def lines(self):
        if not self.log.exists():
            return []
        return self.log.read_text(encoding="utf-8").splitlines()

    def fields(self):
        lines = self.lines()
        self.assertEqual(len(lines), 1, lines)
        return lines[0].split(";")

    def records(self):
        return sorted(p.name for p in self.dir.glob("selfbot_run_*"))

    def record(self, num=1):
        with open(self.dir / f"selfbot_run_{num}.json", encoding="utf-8") as f:
            return json.load(f)


# ── 1. the copies ─────────────────────────────────────────────────────────

@_needs_selfbot
class CopyTests(unittest.TestCase):
    def test_the_writer_split_record_and_clear_are_byte_identical(self):
        for name in ("_log_api_cost", "_cost_split_field", "_run_progress_write",
                     "_run_progress_clear"):
            with self.subTest(name=name):
                self.assertEqual(inspect.getsource(getattr(SelfBot.App, name)),
                                 inspect.getsource(getattr(StreamingMixin, name)))

    def test_the_fold_in_is_myagents_with_selfbots_wording(self):
        ours = inspect.getsource(SelfBot.App._run_progress_fold_in)
        self.assertIn("The previous session of this SelfBot instance ended without", ours)
        self.assertIn("(SelfBot's\n        __init__)", ours)
        theirs = inspect.getsource(StreamingMixin._run_progress_fold_in)
        back = ours.replace(
            "once this process knows its instance slot (SelfBot's\n        __init__)",
            "once this process owns its instance slot (MyAgent.py\n        __init__)").replace(
            "                    f\"⚠ The previous session of this SelfBot instance ended without \"\n"
            "                    f\"its cost line (it was killed with calls unlogged — chat \"\n",
            "                    f\"⚠ The previous run of this instance ended without its cost \"\n"
            "                    f\"line (MyAgent was killed while it ran — chat \"\n")
        self.assertEqual(back, theirs)


# ── 2. the line ───────────────────────────────────────────────────────────

@_needs_selfbot
class LineTests(_TempLog):
    def _args_app(self, **attrs):
        app = _app()
        app._session_cost, app._session_had_usage, app._session_calls = 1.5, True, 4
        app._session_tokens_in, app._session_tokens_out = 400, 90
        app._session_tokens_cache_write, app._session_tokens_cache_read = 1200, 8000
        for k, v in attrs.items():
            setattr(app, k, v)
        return app

    def test_the_session_fields_land_in_myagents_positions(self):
        app = self._args_app(_session_work_secs=12.6, system_prompt_name="Shaun; v2",
                             _last_saved_chat_name="Hi mate")
        self.assertTrue(app._log_api_cost(**app._session_log_args()))
        f = self.fields()
        self.assertEqual(len(f), 14)
        self.assertRegex(f[0], STAMP)
        self.assertEqual(f[1:4], ["Anthropic", OPUS55, "1.5000"])
        self.assertEqual(f[5], "13")                       # TIME(sec), whole seconds
        self.assertEqual(f[6], "Shaun, v2")                # the prompt name, sanitised
        self.assertEqual(f[7:12], ["4", "400", "90", "1200", "8000"])
        self.assertEqual(f[12], "")                        # one model: a blank split
        self.assertEqual(f[13], "Hi mate")

    def test_a_session_two_models_served_carries_the_split(self):
        app = self._args_app(
            _session_per_model={OPUS55: [1.0, 300, 60, 1200, 8000], OPUS5: [0.5, 100, 30, 0, 0]})
        app._log_api_cost(**app._session_log_args())
        f = self.fields()
        self.assertEqual(len(f), 13)
        self.assertEqual(f[12], f"{OPUS55},1.000000,300,60,1200,8000|{OPUS5},0.500000,100,30,0,0")

    def test_a_session_served_only_by_another_model_is_split_too(self):
        # a chat switched to Opus 5.5 after its last call: logged under 5.5, served by 5
        app = self._args_app(_session_per_model={OPUS5: [1.5, 400, 90, 1200, 8000]})
        app._log_api_cost(**app._session_log_args())
        self.assertEqual(self.fields()[12], f"{OPUS5},1.500000,400,90,1200,8000")

    def test_without_a_chat_or_a_split_the_line_keeps_12_fields(self):
        app = self._args_app()
        app._log_api_cost(**app._session_log_args())
        self.assertEqual(len(self.fields()), 12)

    def test_a_session_that_priced_nothing_is_gated_with_a_warning(self):
        app = self._args_app(_session_cost=0.0)
        self.assertFalse(app._log_api_cost(**app._session_log_args()))
        self.assertEqual(self.lines(), [])
        warned = _warnings(_drain(app.queue))
        self.assertEqual(len(warned), 1)
        self.assertIn("NOT written to the API cost log", warned[0])
        # and a session with no usage at all is skipped in silence, as before
        app = self._args_app(_session_cost=0.0, _session_had_usage=False)
        self.assertFalse(app._log_api_cost(**app._session_log_args()))
        self.assertEqual(_warnings(_drain(app.queue)), [])


# ── 3. the real loop ──────────────────────────────────────────────────────

@_needs_selfbot
class RealLoopTests(_TempLog):
    def test_working_time_accumulates_per_reply_and_excludes_the_dialog_wait(self):
        app, clock = _clocked_app(_two_call_steps(), call_secs=10.0, wait_secs=30.0)
        app._state_file = "app_state.json"
        with mock.patch("time.monotonic", lambda: clock[0]):
            _reply(app)
            self.assertEqual(app._session_work_secs, 20.0)   # two calls, two waits left out
            self.assertIsNone(app._reply_started)
            self.assertEqual(app._input_wait_secs, 60.0)
            _reply(app, [{"role": "user", "content": "again"}])
        self.assertEqual(app._session_work_secs, 30.0)       # the second reply's one call
        self.assertEqual(app._session_log_args()["duration_secs"], 30.0)
        # the record written after call 1 carried the time up to that call
        self.assertEqual(app._session_calls, 3)

    def test_the_record_follows_every_call_and_the_close_removes_it(self):
        seen = []
        app, clock = _clocked_app(_two_call_steps(), call_secs=10.0, wait_secs=30.0)
        app._state_file = "app_state.json"
        app._last_saved_chat_name = "Hi mate"
        inner = app.client.beta.messages.stream

        def stream(betas, **api_kwargs):
            path = app._run_progress_path()
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    seen.append(json.load(f))
            else:
                seen.append(None)
            return inner(betas, **api_kwargs)

        app.client.beta.messages.stream = stream
        with mock.patch("time.monotonic", lambda: clock[0]):
            _reply(app)
        self.assertIsNone(seen[0])                              # nothing before call 1
        self.assertEqual(seen[1]["calls"], 1)                   # call 1's totals before call 2
        self.assertEqual(seen[1]["duration_secs"], 10.0)        # the wait excluded
        self.assertEqual(seen[1]["tokens"], [100, 20, 0, 0])
        self.assertEqual(self.records(), ["selfbot_run_1.json"])
        rec = self.record()
        self.assertEqual((rec["provider"], rec["model"], rec["calls"]), ("Anthropic", OPUS55, 2))
        self.assertEqual(rec["tokens"], [110, 25, 0, 0])
        self.assertEqual(rec["duration_secs"], 20.0)
        self.assertEqual(rec["chat"], "Hi mate")
        self.assertEqual(rec["instruction"], "Default")
        self.assertAlmostEqual(rec["total_cost"], app._session_cost)
        self.assertEqual(list(rec["split"]), [OPUS55])
        self.assertRegex(rec["written"], STAMP)
        # the close: the line, then the record goes
        self.assertTrue(app._log_api_cost(**app._session_log_args()))
        app._run_progress_clear()
        self.assertEqual(self.records(), [])
        f = self.fields()
        self.assertEqual((f[5], f[7], f[13]), ("20", "2", "Hi mate"))

    def test_the_split_is_keyed_by_the_serving_model(self):
        # call 1 served by the configured model, call 2 by an Opus-tier fallback,
        # then a reply served by a dated snapshot of the configured model
        steps = _two_call_steps(OPUS55, OPUS5) + [_final(usage=_usage(7, 3, 0, 0),
                                                         model=f"{OPUS55}-20260921")]
        app = _app(steps=steps)
        _reply(app)
        _reply(app, [{"role": "user", "content": "again"}])
        split = app._session_per_model
        self.assertEqual(list(split), [OPUS55, OPUS5])
        self.assertEqual(split[OPUS55][1:], [107, 23, 0, 0])
        self.assertEqual(split[OPUS5][1:], [10, 5, 0, 0])
        self.assertAlmostEqual(split[OPUS55][0] + split[OPUS5][0], app._session_cost)
        self.assertTrue(app._session_had_usage)
        app._session_had_usage = True
        with mock.patch.object(SelfBot, "APICOST_LOG_FILE", str(self.log)):
            app._log_api_cost(**app._session_log_args())
        parts = self.fields()[12].split("|")
        self.assertEqual([p.split(",")[0] for p in parts], [OPUS55, OPUS5])

    def test_an_unpriced_model_warns_once_shows_its_tokens_and_logs_nothing(self):
        steps = _two_call_steps("claude-zz-9", "claude-zz-9")
        app = _app(model="claude-zz-9", steps=steps)
        msgs = _reply(app)
        warned = [w for w in _warnings(msgs) if "no row in the Anthropic pricing table" in w]
        self.assertEqual(len(warned), 1)
        updates = _cost_updates(msgs)
        self.assertEqual([u["call_cost"] for u in updates], [None, None])
        self.assertEqual([u["input_tokens"] for u in updates], [100, 10])
        self.assertEqual(app._session_cost, 0.0)
        self.assertTrue(app._session_had_usage)
        self.assertEqual((app._session_tokens_in, app._session_tokens_out), (110, 25))
        self.assertFalse(app._log_api_cost(**app._session_log_args()))
        self.assertEqual(self.lines(), [])

    def test_a_bare_host_without_a_state_file_keeps_no_record(self):
        app = _app(steps=_two_call_steps())
        self.assertIsNone(app._run_progress_path())
        _reply(app)
        self.assertEqual(self.records(), [])

    def test_the_second_instance_keeps_its_own_record(self):
        app = _app(steps=[_final()])
        app._state_file, app._is_second_instance = "app_state_2.json", True
        _reply(app)
        self.assertEqual(self.records(), ["selfbot_run_2.json"])


# ── 4. the fold-in ────────────────────────────────────────────────────────

@_needs_selfbot
class FoldInTests(_TempLog):
    def _record(self, **over):
        rec = {"version": 1, "written": "2026-10-14 09:42:25", "provider": "Anthropic",
               "model": OPUS55, "params": "mode=Adaptive", "total_cost": 0.59,
               "had_usage": True, "duration_secs": 41.7, "instruction": "SHAUN_friend",
               "calls": 6, "tokens": [12, 1277, 58445, 290000], "split": {}, "chat": "Hi mate"}
        rec.update(over)
        with open(self.dir / "selfbot_run_1.json", "w", encoding="utf-8") as f:
            json.dump(rec, f)

    def test_a_record_left_behind_becomes_the_line_with_selfbots_wording(self):
        self._record()
        app = _app()
        app._state_file = "app_state.json"
        self.assertTrue(app._run_progress_fold_in())
        f = self.fields()
        self.assertEqual(f[0], "2026-10-14 09:42:25")           # the record's own time
        # the params joined like a live line's: comma-separated, one token each
        self.assertEqual(f[1:5], ["Anthropic", OPUS55, "0.5900", "mode=Adaptive, unfinished@call6"])
        self.assertEqual(f[5:12], ["42", "SHAUN_friend", "6", "12", "1277", "58445", "290000"])
        self.assertEqual((f[12], f[13]), ("", "Hi mate"))
        self.assertEqual(self.records(), [])
        warned = _warnings(_drain(app.queue))
        self.assertEqual(len(warned), 1)
        self.assertIn("SelfBot instance ended without its cost line", warned[0])
        self.assertIn("chat Hi mate", warned[0])
        self.assertIn("unfinished@call6", warned[0])

    def test_no_record_means_nothing_happens(self):
        app = _app()
        app._state_file = "app_state.json"
        self.assertFalse(app._run_progress_fold_in())
        self.assertEqual(self.lines(), [])
        self.assertEqual(_drain(app.queue), [])

    def test_an_unreadable_record_is_removed_with_a_warning_and_no_line(self):
        (self.dir / "selfbot_run_1.json").write_text("{not json", encoding="utf-8")
        app = _app()
        app._state_file = "app_state.json"
        self.assertFalse(app._run_progress_fold_in())
        self.assertEqual(self.records(), [])
        self.assertEqual(self.lines(), [])
        self.assertIn("Could not fold in", _warnings(_drain(app.queue))[0])

    def test_an_unpriced_record_is_gated_like_a_live_session(self):
        self._record(total_cost=0.0)
        app = _app()
        app._state_file = "app_state.json"
        self.assertFalse(app._run_progress_fold_in())
        self.assertEqual(self.lines(), [])
        self.assertEqual(self.records(), [])


# ── 5. the wiring ─────────────────────────────────────────────────────────

@_needs_selfbot
class WiringTests(unittest.TestCase):
    def test_the_close_saves_then_logs_then_clears_the_record(self):
        close = inspect.getsource(SelfBot.App._finish_close)
        save = close.index("self._auto_save_on_close()")
        log = close.index("self._log_api_cost(**self._session_log_args())")
        clear = close.index("self._run_progress_clear()")
        self.assertLess(save, log)
        self.assertLess(log, clear)
        self.assertNotIn("_log_api_cost(self._session_cost)", close)

    def test_init_folds_in_once_the_queue_and_the_slot_exist_and_before_the_poll(self):
        init = inspect.getsource(SelfBot.App.__init__)
        fold = init.index("self._run_progress_fold_in()")
        self.assertLess(init.index("self.queue = queue.Queue()"), fold)
        self.assertLess(init.index("self._state_file = APP_STATE_FILE"), fold)
        self.assertLess(fold, init.index("self.root.after(50, self.check_queue)"))
        for attr in ("self.provider = \"Anthropic\"", "self._session_had_usage = False",
                     "self._session_per_model = {}", "self._session_work_secs = 0.0",
                     "self._reply_started = None", "self._input_wait_secs = 0.0",
                     "self._unpriced_noted = set()"):
            self.assertIn(attr, init)

    def test_stream_worker_clocks_the_reply_and_writes_the_record_after_accounting(self):
        src = inspect.getsource(SelfBot.App.stream_worker)
        self.assertLess(src.index("self._reply_started = time.monotonic()"), src.index("try:"))
        self.assertIn("self._input_wait_secs = 0.0", src)
        write = src.index("self._run_progress_write(**self._session_log_args())")
        self.assertLess(src.rindex("\"type\": \"cost_update\""), write)
        self.assertLess(write, src.index("if final_message.stop_reason == \"tool_use\":"))
        self.assertLess(src.index("self._session_had_usage = True"), src.index("ci = getattr(usage"))
        self.assertLess(src.index("self._thaw_system_prompt()"),
                        src.index("self._session_work_secs += max("))

    def test_the_confirmation_dialog_waits_inside_the_timer(self):
        src = inspect.getsource(SelfBot.App._request_confirmation)
        self.assertIn("with input_wait_timer(self):\n            event.wait()", src)

    def test_the_cost_line_renders_an_unpriced_call(self):
        src = inspect.getsource(SelfBot.App.check_queue)
        self.assertIn("if call_cost is None:", src)
        self.assertIn("unpriced this call  |  no pricing row", src)

    def test_record_files_are_gitignored(self):
        rules = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("selfbot_run_*.json", rules)
        self.assertIn("selfbot_run_*.json.*.tmp", rules)
        self.assertTrue(SelfBot.SELFBOT_RUN_PREFIX.endswith("selfbot_run_"))

    def test_the_helper_import_and_its_stub(self):
        src = (ROOT / "SelfBot.py").read_text(encoding="utf-8")
        self.assertIn("strip_old_images, input_wait_timer\n", src)
        self.assertIn("    def input_wait_timer(app):\n        return _NoWaitTimer()\n", src)


if __name__ == "__main__":
    unittest.main()
