"""SelfBot carries MyAgent's October 2026 Anthropic-path changes (2026-10-14).

SelfBot is Anthropic-only, so every change MyAgent's Anthropic caller got
this month applies to it, and this module pins the port — through the REAL
SelfBot.App.stream_worker over a scripted fake client, the pattern of
tests/test_system_prompt_freeze.py:

1. The output ceiling (MyAgent 2026-10-11): every call sends the model's
   OWN max_tokens — the startup listing's figure, else the family table —
   thinking on or off (it sent 32,768 / 8,192); a 400 naming a lower
   ceiling learns it and retries.
2. A connection error is retried on the 429 schedule (2026-10-13) instead
   of ending the reply — in a duo chat, the conversation.
3. HTTP 413 drops the oldest images and retries (2026-10-13).
4. The dropped-thinking notice is worded by reason and said once per
   conversation per shape (2026-10-11).
5. Claude Haiku 5.5 is wired (2026-10-08): adaptive-only, Off = an explicit
   disable with no binding, no temperature, the binding without fallbacks.
6. Haiku 5.5's second rate card is applied per call by prompt size
   (2026-10-08), with one warning per process.
7. run_command's children get the venv first on PATH (2026-10-06) and
   PowerShell's output comes back as UTF-8 (2026-09-28).
8. The view_image Desktop tool (2026-10-13).
9. The cost log's 14th CHAT field names the chat the close saved (2026-10-07).

The helpers that are byte-identical copies of MyAgent's are pinned so.
"""

import copy
import inspect
import os
import queue
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import anthropic

from myagent.anthropic_mixin import AnthropicMixin
from myagent.helpers import FILE_IMAGE_MARKER, IMAGE_REMOVED_NOTE
from myagent.safety_mixin import SafetyMixin
from tests.test_fast_mode import _FakeStream

_saved_argv = sys.argv
sys.argv = ["SelfBot.py"]
try:
    import SelfBot
    _IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover
    SelfBot = None
    _IMPORT_ERROR = exc
finally:
    sys.argv = _saved_argv

_needs_selfbot = unittest.skipIf(SelfBot is None, f"SelfBot not importable: {_IMPORT_ERROR}")

FABLE = "claude-fable-5-1"
HAIKU55 = "claude-haiku-5-5"
HAIKU45 = "claude-haiku-4-5-20251001"
OPUS55 = "claude-opus-5-5"
DROP = {"type": "thinking_dropped", "path": "messages.1.content.0",
        "reason": "model_binding_mismatch"}
PIXEL = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}}


class _Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


def _usage(ci=10, co=5, cw=0, cr=0):
    return SimpleNamespace(input_tokens=ci, output_tokens=co, cache_creation_input_tokens=cw,
                           cache_read_input_tokens=cr, iterations=None, server_tool_use=None)


def _final(stop_reason="end_turn", content=None, usage=None, model=OPUS55, transformations=None):
    return SimpleNamespace(content=content or [], stop_reason=stop_reason, model=model,
                           usage=usage or _usage(), input_transformations=transformations)


def _bad_request(message):
    e = anthropic.BadRequestError.__new__(anthropic.BadRequestError)
    e.message = message
    return e


def _status_error(status, message=None):
    e = anthropic.APIStatusError.__new__(anthropic.APIStatusError)
    e.message = message or f"HTTP {status}"
    e.status_code = status
    return e


def _connection_error(message="Connection error.", cls=anthropic.APIConnectionError):
    e = cls.__new__(cls)
    e.message = message
    return e


def _app(model=OPUS55, mode="adaptive", enabled=True, steps=None, caps=None):
    """A bare SelfBot App (no Tk) whose client plays `steps` — a final message
    per call, or an exception to raise on that attempt — and records every
    attempt's (betas, api_kwargs)."""
    app = SelfBot.App.__new__(SelfBot.App)
    app.queue = queue.Queue()
    app._temp_var = _Var(0.7)
    app.temperature = 0.7
    app.model = model
    app.thinking_enabled = enabled
    app.thinking_mode, app.thinking_effort, app.thinking_budget = mode, "high", 8192
    app._no_temperature, app._anthropic_unsupported = set(), set()
    app._anthropic_output_caps = dict(caps or {})
    app._thinking_drops_noted = None
    app._long_context_noted = False
    app._session_calls, app._session_cost = 0, 0.0
    app._session_tokens_in = app._session_tokens_out = 0
    app._session_tokens_cache_write = app._session_tokens_cache_read = 0
    app.system_prompt, app.skills, app.messages = "You are Shaun.", {}, []
    app.system_prompt_name = "Default"
    app._run_system_prompt = None
    app._mcp_wait_ready = lambda: None
    app._payload_for_display = lambda messages: ""
    app._get_tools = lambda: []
    app.calls = []
    script = list(steps if steps is not None else [_final()])

    class _Messages:
        @staticmethod
        def stream(betas, **api_kwargs):
            app.calls.append((list(betas), dict(api_kwargs)))
            step = script.pop(0)
            if isinstance(step, Exception):
                raise step
            return _FakeStream(step)

    app.client = SimpleNamespace(beta=SimpleNamespace(messages=_Messages))
    return app


def _drain(q):
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            return out


def _reply(app, messages=None):
    app.stream_worker(messages if messages is not None else [{"role": "user", "content": "hi"}])
    return _drain(app.queue)


def _kinds(msgs):
    return [m["type"] for m in msgs]


def _texts(msgs, kind):
    return [m.get("content", "") for m in msgs if m["type"] == kind]


# ── 1. the output ceiling ───────────────────────────────────────────────

@_needs_selfbot
class CopyTests(unittest.TestCase):
    def test_the_cap_helpers_are_byte_identical_to_anthropic_mixins(self):
        for name in ("_anthropic_output_cap_for", "_anthropic_output_cap"):
            with self.subTest(helper=name):
                self.assertEqual(inspect.getsource(getattr(SelfBot.App, name)),
                                 inspect.getsource(getattr(AnthropicMixin, name)))

    def test_run_command_env_is_byte_identical_to_safety_mixins(self):
        self.assertEqual(inspect.getsource(SelfBot.App._run_command_env),
                         inspect.getsource(SafetyMixin._run_command_env))

    def test_the_old_constants_are_gone(self):
        for name in ("MAX_TOKENS", "MAX_TOKENS_THINKING", "MODEL_MAX_OUTPUT_TOKENS"):
            self.assertFalse(hasattr(SelfBot, name), name)


@_needs_selfbot
class CeilingWireTests(unittest.TestCase):
    def test_fable_at_max_sends_its_own_ceiling(self):
        app = _app(FABLE, "max")
        msgs = _reply(app)
        self.assertIn("complete", _kinds(msgs))
        _betas, kw = app.calls[0]
        self.assertEqual(kw["max_tokens"], 128000)
        self.assertEqual(kw["thinking"]["type"], "adaptive")
        self.assertEqual(kw["output_config"], {"effort": "max"})
        self.assertNotIn("temperature", kw)

    def test_thinking_off_sends_the_ceiling_too(self):
        app = _app("claude-opus-5", "off", enabled=False)
        _reply(app)
        _betas, kw = app.calls[0]
        self.assertEqual(kw["max_tokens"], 128000)       # was 8,192
        self.assertEqual(kw["thinking"], {"type": "disabled"})

    def test_the_dated_4_5_ids_take_64k_and_a_budget_fits_under_it(self):
        app = _app(HAIKU45, "off", enabled=False)
        _reply(app)
        _betas, kw = app.calls[0]
        self.assertEqual(kw["max_tokens"], 64000)
        self.assertNotIn("thinking", kw)                 # not on by default
        self.assertEqual(kw["temperature"], 0.7)
        app = _app("claude-sonnet-4-5", "high", enabled=True)
        app.thinking_budget = SelfBot.BUDGET_PRESETS["32K"]
        _reply(app)
        _betas, kw = app.calls[0]
        self.assertEqual(kw["thinking"], {"type": "enabled", "budget_tokens": 32768})
        self.assertGreater(kw["max_tokens"], kw["thinking"]["budget_tokens"])

    def test_the_listings_figure_wins_over_the_family_table(self):
        app = _app(OPUS55, caps={OPUS55: 50000})
        _reply(app)
        self.assertEqual(app.calls[0][1]["max_tokens"], 50000)
        self.assertEqual(SelfBot.App._anthropic_output_cap_for("claude-zeta-9"),
                         SelfBot.ANTHROPIC_MAX_OUTPUT_DEFAULT)
        self.assertEqual(SelfBot.App._anthropic_output_cap_for("claude-haiku-4-5-20251001"), 64000)

    def test_the_debug_builder_reads_the_same_helper(self):
        app = _app(HAIKU45, "off", enabled=False, caps={HAIKU45: 12345})
        self.assertEqual(app._apply_thinking_params({})["max_tokens"], 12345)

    def test_a_refused_ceiling_is_learned_and_retried(self):
        app = _app("claude-zeta-9", "high", steps=[
            _bad_request("max_tokens: 128000 > 64000, which is the maximum allowed number "
                         "of output tokens for claude-zeta-9"),
            _final(model="claude-zeta-9")])
        msgs = _reply(app)
        self.assertIn("complete", _kinds(msgs))
        self.assertEqual([kw["max_tokens"] for _b, kw in app.calls], [128000, 64000])
        self.assertEqual(app._anthropic_output_caps["claude-zeta-9"], 64000)
        self.assertTrue(any("64,000" in t for t in _texts(msgs, "tool_info")))

    def test_an_overflow_400_is_not_this_rung(self):
        app = _app(OPUS55, steps=[_bad_request("prompt is too long: 5 tokens > 3 maximum")])
        msgs = _reply(app)
        self.assertIn("error", _kinds(msgs))        # one round only: nothing to trim
        self.assertEqual(app._anthropic_output_caps, {})

    def test_the_listing_fills_the_caps(self):
        app = _app()
        data = [SimpleNamespace(id=OPUS55, display_name="Opus 5.5", max_tokens=128000),
                SimpleNamespace(id="claude-opus-4-1", display_name="old", max_tokens=32000),
                SimpleNamespace(id="claude-x", display_name="x")]
        app.client = SimpleNamespace(models=SimpleNamespace(list=lambda limit: SimpleNamespace(data=data)))
        self.assertEqual(app._fetch_available_models(), [OPUS55, "claude-x"])
        self.assertEqual(app._anthropic_output_caps, {OPUS55: 128000, "claude-opus-4-1": 32000})

        def boom(limit):
            raise RuntimeError("offline")
        app.client = SimpleNamespace(models=SimpleNamespace(list=boom))
        self.assertEqual(app._fetch_available_models(), list(SelfBot.FALLBACK_MODELS))
        self.assertEqual(app._anthropic_output_caps, {OPUS55: 128000, "claude-opus-4-1": 32000})


# ── 2. connection errors ────────────────────────────────────────────────

@_needs_selfbot
class ConnectionRetryTests(unittest.TestCase):
    def test_a_connection_error_is_retried_on_the_429_schedule(self):
        app = _app(steps=[_connection_error(), _final()])
        with mock.patch("SelfBot.time.sleep") as slept:
            msgs = _reply(app)
        self.assertIn("complete", _kinds(msgs))
        self.assertEqual(len(app.calls), 2)
        self.assertEqual([c.args[0] for c in slept.call_args_list], [5])
        self.assertTrue(any("Connection error. — retrying in 5s" in t
                            for t in _texts(msgs, "tool_info")))

    def test_a_timeout_rides_the_same_rung(self):
        app = _app(steps=[_connection_error("Request timed out.", anthropic.APITimeoutError),
                          _final()])
        with mock.patch("SelfBot.time.sleep"):
            msgs = _reply(app)
        self.assertIn("complete", _kinds(msgs))
        self.assertEqual(len(app.calls), 2)

    def test_the_last_attempt_raises(self):
        app = _app(steps=[_connection_error()] * 10)
        with mock.patch("SelfBot.time.sleep") as slept:
            msgs = _reply(app)
        self.assertIn("error", _kinds(msgs))
        self.assertEqual(len(app.calls), 10)
        self.assertEqual([c.args[0] for c in slept.call_args_list],
                         [5, 10, 20, 40, 60, 60, 60, 60, 60])

    def test_a_500_is_not_this_rung(self):
        app = _app(steps=[_status_error(500)])
        with mock.patch("SelfBot.time.sleep") as slept:
            msgs = _reply(app)
        self.assertIn("error", _kinds(msgs))
        self.assertEqual(len(app.calls), 1)
        slept.assert_not_called()


# ── 3. HTTP 413 ─────────────────────────────────────────────────────────

def _history_with_images():
    return [{"role": "user", "content": "look at the screen"},
            {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                          "content": [{"type": "text", "text": "shot 1"},
                                                      copy.deepcopy(PIXEL)]}]},
            {"role": "assistant", "content": [{"type": "text", "text": "and"}]},
            {"role": "user", "content": [copy.deepcopy(PIXEL), {"type": "text", "text": "shot 2"}]}]


@_needs_selfbot
class TooLargeTests(unittest.TestCase):
    def test_a_413_drops_the_oldest_image_and_retries(self):
        messages = _history_with_images()
        app = _app(steps=[_status_error(413, "Error code: 413 - request_too_large"), _final()])
        msgs = _reply(app, messages)
        self.assertIn("complete", _kinds(msgs))
        self.assertEqual(len(app.calls), 2)
        self.assertEqual(messages[2]["content"][0]["content"][1],
                         {"type": "text", "text": IMAGE_REMOVED_NOTE})   # the oldest went
        self.assertEqual(messages[4]["content"][0]["type"], "image")      # the newest stays
        warnings = _texts(msgs, "warning")
        self.assertEqual(len(warnings), 1)
        self.assertIn("HTTP 413", warnings[0])

    def test_nothing_left_to_drop_ends_the_reply_with_an_error(self):
        app = _app(steps=[_status_error(413, "Error code: 413 - request_too_large")])
        msgs = _reply(app)
        self.assertIn("error", _kinds(msgs))
        self.assertIn("413", _texts(msgs, "error")[0])


# ── 4. the dropped-thinking notice ──────────────────────────────────────

@_needs_selfbot
class DroppedThinkingNoticeTests(unittest.TestCase):
    def test_worded_by_reason_and_said_once_per_shape(self):
        app = _app(steps=[_final(transformations=[DROP, DROP]),
                          _final(transformations=[DROP, DROP]),
                          _final(transformations=[DROP, DROP, DROP])])
        first = _texts(_reply(app), "tool_info")
        notices = [t for t in first if "replayed thinking" in t]
        self.assertEqual(len(notices), 1)
        self.assertIn("written by another model", notices[0])
        self.assertIn("2 replayed", notices[0])
        self.assertNotIn("earlier history changed", notices[0])
        second = [t for t in _texts(_reply(app), "tool_info") if "replayed thinking" in t]
        self.assertEqual(second, [], "the same shape again is not repeated")
        third = [t for t in _texts(_reply(app), "tool_info") if "replayed thinking" in t]
        self.assertEqual(len(third), 1)
        self.assertIn("3 replayed", third[0])

    def test_the_wording(self):
        why = SelfBot.App._thinking_drop_why
        self.assertIn("another model", why(["model_binding_mismatch"]))
        self.assertIn("earlier history changed", why(["prefix_binding_mismatch"]))
        self.assertIn("reason: odd", why(["odd"]))

    def test_a_new_chat_resets_the_memory(self):
        src = inspect.getsource(SelfBot.App._new_chat)
        self.assertIn("self._thinking_drops_noted = None", src)


# ── 5. Claude Haiku 5.5 ─────────────────────────────────────────────────

@_needs_selfbot
class Haiku55Tests(unittest.TestCase):
    def test_off_is_an_explicit_disable_with_nothing_else(self):
        app = _app(HAIKU55, "off", enabled=False)
        _reply(app)
        betas, kw = app.calls[0]
        self.assertEqual(kw["thinking"], {"type": "disabled"})
        self.assertNotIn("temperature", kw)
        self.assertEqual(kw["max_tokens"], 128000)
        self.assertNotIn("extra_body", kw)                                   # no fallbacks
        self.assertNotIn(SelfBot.ANTHROPIC_THINKING_BINDING_BETA, betas)      # no binding beside a disable
        self.assertNotIn(SelfBot.ANTHROPIC_SERVER_FALLBACK_BETA, betas)

    def test_adaptive_low_carries_the_binding_and_the_effort_but_no_fallbacks(self):
        app = _app(HAIKU55, "low")
        _reply(app)
        betas, kw = app.calls[0]
        self.assertEqual(kw["thinking"], {"type": "adaptive", "display": "summarized",
                                          "block_binding": {"prefix_mismatch_behavior": "drop_block"}})
        self.assertEqual(kw["output_config"], {"effort": "low"})
        self.assertNotIn("temperature", kw)
        self.assertNotIn("extra_body", kw)
        self.assertIn(SelfBot.ANTHROPIC_THINKING_BINDING_BETA, betas)
        self.assertNotIn(SelfBot.ANTHROPIC_SERVER_FALLBACK_BETA, betas)

    def test_the_helpers(self):
        app = _app(HAIKU55)
        self.assertTrue(app._is_haiku_5())
        self.assertTrue(app._is_haiku_5("claude-haiku-5-5-20261001"))
        self.assertFalse(app._is_haiku_5(HAIKU45))
        self.assertEqual(app._model_supports_thinking(HAIKU55), "adaptive")
        self.assertEqual(app._model_supports_thinking(HAIKU45), "manual")
        self.assertEqual(app._anthropic_mode_values(HAIKU55),
                         ["Off", "Adaptive", "Low", "Medium", "High", "Xhigh", "Max"])
        self.assertTrue(app._rejects_temperature(HAIKU55))
        self.assertTrue(app._thinking_on_by_default(HAIKU55))
        self.assertFalse(app._is_always_on_thinking(HAIKU55))
        features = app._fable_features()
        self.assertEqual(features["betas"], [SelfBot.ANTHROPIC_THINKING_BINDING_BETA])
        self.assertIsNone(features["fallbacks"])
        self.assertIn(HAIKU55, SelfBot.FALLBACK_MODELS)
        self.assertIn(HAIKU55, SelfBot.ADAPTIVE_THINKING_MODELS)
        tools, _betas = app._anthropic_server_tools()
        self.assertEqual(tools[0]["type"], "web_search_20260209")             # the current triple

    def test_the_opus_control_is_unchanged(self):
        app = _app(OPUS55, "low")
        _reply(app)
        betas, kw = app.calls[0]
        self.assertIn("block_binding", kw["thinking"])
        self.assertEqual(kw["extra_body"], {"fallbacks": "default"})
        self.assertIn(SelfBot.ANTHROPIC_SERVER_FALLBACK_BETA, betas)


# ── 6. the second rate card ─────────────────────────────────────────────

@_needs_selfbot
class LongContextTests(unittest.TestCase):
    def test_the_card_is_selected_by_prompt_size(self):
        over = SelfBot.App._get_pricing(HAIKU55, 100_001)
        self.assertEqual(over["long_context"], 100_000)
        self.assertAlmostEqual(over["input"] * 1e6, 0.50)
        under = SelfBot.App._get_pricing(HAIKU55, 100_000)
        self.assertNotIn("long_context", under)
        self.assertAlmostEqual(under["input"] * 1e6, 0.10)
        self.assertNotIn("long_context", SelfBot.App._get_pricing(OPUS55, 500_000))
        self.assertNotIn("long_context", SelfBot.App._get_pricing(HAIKU55))

    def test_a_long_call_is_priced_on_it_and_warned_once(self):
        usage = _usage(ci=90_000, co=1_000, cw=0, cr=20_000)
        app = _app(HAIKU55, "low", steps=[_final(usage=usage, model=HAIKU55),
                                          _final(usage=usage, model=HAIKU55)])
        msgs = _reply(app)
        cost = [m for m in msgs if m["type"] == "cost_update"][0]["call_cost"]
        self.assertAlmostEqual(cost, 90_000 * 0.50e-6 + 1_000 * 2.50e-6 + 20_000 * 0.05e-6)
        warnings = [t for t in _texts(msgs, "warning") if "second rate card" in t]
        self.assertEqual(len(warnings), 1)
        self.assertIn("110,000 tokens", warnings[0])
        again = [t for t in _texts(_reply(app), "warning") if "second rate card" in t]
        self.assertEqual(again, [])


# ── 7. run_command ──────────────────────────────────────────────────────

@_needs_selfbot
class RunCommandTests(unittest.TestCase):
    def test_the_child_environment_and_utf8_decoding_are_wired(self):
        src = inspect.getsource(SelfBot.App.run_powershell)
        self.assertIn("env=self._run_command_env()", src)
        self.assertIn('encoding="utf-8"', src)
        self.assertIn('errors="replace"', src)
        self.assertIn('_PS_UTF8_OUTPUT + "\\n" + command', src)
        self.assertIn("[Console]::OutputEncoding", SelfBot._PS_UTF8_OUTPUT)

    def test_the_venv_bin_leads_the_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            venv = os.path.join(tmp, "venv")
            bin_dir = os.path.join(venv, "bin")
            os.makedirs(bin_dir)
            open(os.path.join(venv, "pyvenv.cfg"), "w").close()
            exe = os.path.join(bin_dir, "python")
            env = SelfBot.App._run_command_env({"PATH": os.pathsep.join(["/usr/bin", bin_dir])}, exe)
            self.assertEqual(env["PATH"].split(os.pathsep), [bin_dir, "/usr/bin"])
            self.assertEqual(env["VIRTUAL_ENV"], venv)
            plain = SelfBot.App._run_command_env({"PATH": "/usr/bin"}, os.path.join(tmp, "python"))
            self.assertEqual(plain, {"PATH": "/usr/bin"})


# ── 8. view_image ───────────────────────────────────────────────────────

@_needs_selfbot
class ViewImageTests(unittest.TestCase):
    def setUp(self):
        self.app = _app()
        self.app._screenshot_scale = 2.0
        self.app._screenshot_offset = (7, 9)
        self.app._screenshot_dims = (640, 480)
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _png(self, name, size=(10, 10)):
        from PIL import Image
        path = os.path.join(self.tmp.name, name)
        Image.new("RGB", size, (200, 30, 30)).save(path, format="PNG")
        return path

    def test_a_file_comes_back_as_the_text_image_pair(self):
        path = self._png("small.png")
        result = self.app.do_view_image(path)
        self.assertEqual([b["type"] for b in result], ["text", "image"])
        self.assertTrue(result[0]["text"].startswith(FILE_IMAGE_MARKER))
        self.assertIn("small.png", result[0]["text"])
        self.assertIn("NOT the screen", result[0]["text"])
        self.assertEqual(result[1]["source"]["media_type"], "image/png")
        # no click-surface state was touched
        self.assertEqual((self.app._screenshot_scale, self.app._screenshot_offset,
                          self.app._screenshot_dims), (2.0, (7, 9), (640, 480)))

    def test_a_large_image_is_resized_to_the_api_cap(self):
        import base64
        import io
        from PIL import Image
        path = self._png("big.png", (3200, 1800))
        result = self.app.do_view_image(path)
        shown = Image.open(io.BytesIO(base64.b64decode(result[1]["source"]["data"])))
        # the 1.15 MP cap bites before the 1568 px edge on a 16:9 image
        self.assertEqual(shown.size, (1430, 804))
        self.assertIn("shown at 1430x804", result[0]["text"])

    def test_the_error_sentences(self):
        self.assertIn("file not found", self.app.do_view_image(os.path.join(self.tmp.name, "no.png")))
        self.assertIn("is a directory", self.app.do_view_image(self.tmp.name))
        self.assertIn("no path", self.app.do_view_image(""))
        bad = os.path.join(self.tmp.name, "bad.png")
        with open(bad, "w") as f:
            f.write("not an image")
        self.assertIn("could not read", self.app.do_view_image(bad))

    def test_the_schema_dispatch_and_caps(self):
        names = [t["name"] for t in SelfBot.DESKTOP_TOOLS]
        self.assertIn("view_image", names)
        self.assertEqual(SelfBot.DESKTOP_TOOLS[names.index("view_image")]["input_schema"]["required"], ["path"])
        src = inspect.getsource(SelfBot.App._execute_tool)
        self.assertIn('"view_image"', src)
        self.assertIn('if block.name == "view_image":', src)
        # view_image is in MyAgent's PARALLEL_SAFE_TOOLS, which SelfBot's set takes whole
        self.assertIn("set(PARALLEL_SAFE_TOOLS)",
                      inspect.getsource(SelfBot.App.stream_worker).split("PARALLEL_SAFE = ")[1][:80])
        self.assertIn("view_image", SelfBot.PARALLEL_SAFE_TOOLS)
        # DesktopMixin's one cap table (SelfBot inherits it since 2026-10-14):
        # Anthropic's, since SelfBot has no provider attribute
        self.assertEqual(self.app._api_image_caps(), (1568, 1_150_000))
        self.assertIn("self._api_image_caps()", inspect.getsource(SelfBot.App._capture_single_display))


# ── 9. the cost log's CHAT field ────────────────────────────────────────

@_needs_selfbot
class CostLogChatTests(unittest.TestCase):
    def _line(self, app):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "APICostLog.txt")
            with mock.patch.object(SelfBot, "APICOST_LOG_FILE", log):
                app._log_api_cost(1.5)
            with open(log, encoding="utf-8") as f:
                return f.read().rstrip("\n").split(";")

    def test_the_saved_chats_stem_is_the_14th_field(self):
        app = _app()
        app._session_calls = 3
        app._last_saved_chat_name = "Shaun & Nigel; finale"
        fields = self._line(app)
        self.assertEqual(len(fields), 14)
        self.assertEqual(fields[12], "")                              # no per-model split
        self.assertEqual(fields[13], "Shaun & Nigel, finale")
        self.assertEqual(fields[1:4], ["Anthropic", OPUS55, "1.5000"])

    def test_without_a_saved_chat_the_line_keeps_12_fields(self):
        app = _app()
        self.assertEqual(len(self._line(app)), 12)

    def test_the_close_records_the_stem_before_logging(self):
        src = inspect.getsource(SelfBot.App._auto_save_on_close)
        self.assertIn("self._last_saved_chat_name = ", src)
        close = inspect.getsource(SelfBot.App._finish_close)
        self.assertLess(close.index("self._auto_save_on_close()"), close.index("self._log_api_cost("))


if __name__ == "__main__":
    unittest.main()
