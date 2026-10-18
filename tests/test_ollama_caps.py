"""Characterization tests: the Ollama audit of 2026-10-18 (OllamaMixin).

What that audit changed, and this module pins:
- Thinking and vision detection read /api/show's capability list FIRST (the
  per-model cache the model fetch fills) and fall back to the name-prefix
  tables only for a model the server has not described — so a family the
  tables never heard of (Gemma 4, Nemotron) gets its Thinking checkbox and
  escapes the text-only warning, and a prefix model whose capabilities lack
  `thinking` is not sent `think`.
- The picker offers only models that can run the agent loop: a listed model
  whose capabilities lack `tools` is hidden but stays SERVED (a pinned
  instruction still runs it), a failed query keeps the model listed, and
  nothing passing offers the whole list rather than uninstalled ids.
- The REAL _stream_ollama_call over a fake client: `think` on the wire
  exactly when the capability says so, no tools (and one warning) for a
  tools-less model, num_ctx capped, a `length` stop posting a cut-off ⚠.
- The client's read timeout is OLLAMA_READ_TIMEOUT (900 s, env-overridable)
  — the 300 s it was cut a 32B model's prompt prefill short on a base M4.
- The Model upgrade's probe carries the Ollama capability cache.
- ONE request builder (`_ollama_request_kwargs`) behind the live call and
  the Debug dump, which until then showed Ollama an Anthropic-shaped
  payload; the dump neither runs the ctx guard nor posts the tools-less
  warning. A name in OLLAMA_UNSUITABLE_PREFIXES (the qwen2.5vl-tools graft,
  which misread a screenshot delivered the desktop tools' way) leaves the
  picker although its capabilities say tools.
- Think LEVELS (gpt-oss, probed live: `think: false` is not honoured, "low" /
  "high" are): the request builder sends the saved effort's nearest rung with
  Thinking on and the quietest rung with it off (one notice per model), the
  editor shows the strength combo for such a model and hides it for a
  boolean one, the summary names the rung, Model Setup offers Off + the rungs.
"""
import json
import os
import re
import tkinter as tk
from tkinter import ttk
import unittest

from myagent import constants
from myagent.constants import OLLAMA_FALLBACK_MODELS, OLLAMA_NUM_CTX_CAP
from myagent.model_upgrade_mixin import ModelUpgradeMixin
from myagent.ollama_mixin import OllamaMixin
from myagent.openai_mixin import OpenAIMixin
from myagent.streaming_mixin import StreamingMixin
from myagent.ui_mixin import UIMixin
from tests._util import stub

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TOOL = {"name": "lookup_order", "description": "d",
        "input_schema": {"type": "object", "properties": {"order_id": {"type": "string"}}}}

ALL_FOUR = {"completion", "vision", "tools", "thinking"}


def caps(*names, ctx=131072):
    return {"capabilities": set(names), "context_length": ctx}


class FakeQueue:
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)

    def of(self, kind):
        return [i for i in self.items if i.get("type") == kind]


class FakeOllama:
    """list() / show() / ps() / chat() in the SDK's dict shapes.

    `caps`: model id -> capability list; None makes show() raise for it.
    `reply`: the message dicts chat() streams before its final chunk.
    """

    def __init__(self, models=(), caps=None, reply=None, done_reason="stop", ctx=131072,
                 loaded_ctx=None):
        self.models = list(models)
        self.caps = dict(caps or {})
        self.reply = reply if reply is not None else [{"content": "ok"}]
        self.done_reason = done_reason
        self.ctx = ctx
        self.loaded_ctx = loaded_ctx      # /api/ps: a warm instance's context
        self.think_values = None          # the 0.40 line's /api/show `thinking.values`
        self.chat_kwargs = []
        self.show_calls = []
        self.generate_calls = []
        self.list_raises = False

    def list(self):
        if self.list_raises:
            raise ConnectionError("down")
        return {"models": [{"model": m} for m in self.models]}

    def show(self, model):
        self.show_calls.append(model)
        c = self.caps.get(model, [])
        if c is None:
            raise ConnectionError("show failed")
        resp = {"capabilities": list(c), "modelinfo": {"x.context_length": self.ctx}}
        if self.think_values is not None:
            resp["thinking"] = {"values": list(self.think_values), "default": self.think_values[-1]}
        return resp

    def ps(self):
        if self.loaded_ctx is None:
            return {"models": []}
        return {"models": [{"model": m, "context_length": self.loaded_ctx} for m in self.caps]}

    def generate(self, **kw):
        self.generate_calls.append(kw)

    def chat(self, **kw):
        self.chat_kwargs.append(kw)
        for m in self.reply:
            yield {"message": dict(m)}
        yield {"message": {"content": ""}, "done": True, "done_reason": self.done_reason,
               "prompt_eval_count": 10, "eval_count": 3}


def host(client, model="qwen3:32b-q4_K_M", thinking=True, tools=(TOOL,), **extra):
    h = stub(OllamaMixin, model=model, ollama_client=client, queue=FakeQueue(),
             temperature=0.3, thinking_enabled=thinking, stop_requested=False, **extra)
    h._build_system_prompt = lambda: "sys"
    h._get_tools = lambda: list(tools)
    return h


class DetectionTests(unittest.TestCase):
    def test_the_capability_list_decides_for_a_described_model(self):
        h = stub(OllamaMixin, model="x", _ollama_model_caps={
            "newfamily:9b": caps(*ALL_FOUR),                 # no prefix knows it
            "qwen3:7b-nothink": caps("completion", "tools"),  # prefix says thinking
            "gemma3:1b": caps("completion", "tools"),         # prefix says vision
        })
        self.assertTrue(h._is_ollama_thinking_model("newfamily:9b"))
        self.assertTrue(h._is_ollama_vision_model("newfamily:9b"))
        self.assertFalse(h._is_ollama_thinking_model("qwen3:7b-nothink"))
        self.assertFalse(h._is_ollama_vision_model("gemma3:1b"))

    def test_the_prefixes_answer_for_an_undescribed_model(self):
        h = stub(OllamaMixin, model="x", _ollama_model_caps={"other": caps("completion")})
        for mid, (thinking, vision) in {
            "qwen3:8b": (True, False), "deepseek-r1": (True, False),
            "gemma4:26b-a4b": (True, True), "qwen3.8:27b": (True, True),
            "llava": (False, True), "llama3.3": (False, False),
        }.items():
            with self.subTest(mid=mid):
                self.assertEqual(h._is_ollama_thinking_model(mid), thinking)
                self.assertEqual(h._is_ollama_vision_model(mid), vision)

    def test_an_empty_cached_set_counts_as_unknown(self):
        h = stub(OllamaMixin, model="qwen3:8b", _ollama_model_caps={"qwen3:8b": caps()})
        self.assertTrue(h._is_ollama_thinking_model())
        self.assertIsNone(h._ollama_known_capabilities())

    def test_a_bare_host_without_the_cache_falls_back(self):
        h = stub(OllamaMixin, model="muse-glimmer:30b-mlx")
        self.assertTrue(h._is_ollama_thinking_model())
        self.assertTrue(h._is_ollama_vision_model())

    def test_the_editor_support_kind_reads_the_helper(self):
        class _H(UIMixin, OllamaMixin):
            pass
        h = stub(_H, provider="Ollama", model="x", _ollama_model_caps={
            "newfamily:9b": caps(*ALL_FOUR),
            "qwen3:7b-nothink": caps("completion", "tools"),
        })
        self.assertEqual(h._model_supports_thinking("newfamily:9b"), "manual")
        self.assertIsNone(h._model_supports_thinking("qwen3:7b-nothink"))
        self.assertEqual(h._model_supports_thinking("qwen3:8b"), "manual")   # prefix fallback
        self.assertIsNone(h._model_supports_thinking("llama3.3"))


class FetchTests(unittest.TestCase):
    MODELS = ["qwen3:32b-q4_K_M", "gemma3:27b", "muse-glimmer:30b-mlx", "qwen2.5vl:32b"]
    CAPS = {"qwen3:32b-q4_K_M": ["completion", "tools", "thinking"],
            "gemma3:27b": ["completion", "vision"],
            "muse-glimmer:30b-mlx": ["completion", "vision", "tools", "thinking"],
            "qwen2.5vl:32b": ["completion", "vision"]}

    def test_tools_less_models_leave_the_picker_but_stay_served(self):
        c = FakeOllama(self.MODELS, self.CAPS)
        h = stub(OllamaMixin, model="x", ollama_client=c)
        picker = h._fetch_ollama_models()
        self.assertEqual(picker, ["muse-glimmer:30b-mlx", "qwen3:32b-q4_K_M"])
        self.assertEqual(h._served_model_ids["Ollama"], set(self.MODELS))
        self.assertEqual(set(h._ollama_model_display_names), set(picker))
        # The fetch warmed the capability cache for EVERY listed model, so
        # the detection helpers answer from it without a server call.
        self.assertEqual(sorted(c.show_calls), sorted(self.MODELS))
        self.assertTrue(h._is_ollama_vision_model("gemma3:27b"))
        self.assertFalse(h._is_ollama_vision_model("qwen3:32b-q4_K_M"))

    def test_an_unsuitable_name_leaves_the_picker_despite_its_capabilities(self):
        models = self.MODELS + ["qwen2.5vl-tools:32b"]
        caps = dict(self.CAPS, **{"qwen2.5vl-tools:32b": ["completion", "vision", "tools"]})
        h = stub(OllamaMixin, model="x", ollama_client=FakeOllama(models, caps))
        self.assertEqual(h._fetch_ollama_models(), ["muse-glimmer:30b-mlx", "qwen3:32b-q4_K_M"])
        self.assertIn("qwen2.5vl-tools:32b", h._served_model_ids["Ollama"])
        self.assertEqual(constants.OLLAMA_UNSUITABLE_PREFIXES, ("qwen2.5vl-tools",))

    def test_a_failed_capability_query_keeps_the_model_listed(self):
        c = FakeOllama(self.MODELS, dict(self.CAPS, **{"gemma3:27b": None}))
        h = stub(OllamaMixin, model="x", ollama_client=c)
        self.assertEqual(h._fetch_ollama_models(),
                         ["gemma3:27b", "muse-glimmer:30b-mlx", "qwen3:32b-q4_K_M"])

    def test_nothing_passing_offers_the_whole_list(self):
        c = FakeOllama(["gemma3:27b", "qwen2.5vl:32b"], self.CAPS)
        h = stub(OllamaMixin, model="x", ollama_client=c)
        self.assertEqual(h._fetch_ollama_models(), ["gemma3:27b", "qwen2.5vl:32b"])

    def test_a_failed_list_serves_the_fallback_and_knows_nothing(self):
        c = FakeOllama(self.MODELS, self.CAPS)
        c.list_raises = True
        h = stub(OllamaMixin, model="x", ollama_client=c)
        self.assertEqual(h._fetch_ollama_models(), list(OLLAMA_FALLBACK_MODELS))
        self.assertIsNone(h._served_model_ids["Ollama"])

    def test_an_empty_server_and_no_client_serve_the_fallback(self):
        h = stub(OllamaMixin, model="x", ollama_client=FakeOllama([], {}))
        self.assertEqual(h._fetch_ollama_models(), list(OLLAMA_FALLBACK_MODELS))
        self.assertIsNone(h._served_model_ids["Ollama"])
        h2 = stub(OllamaMixin, model="x", ollama_client=None)
        self.assertEqual(h2._fetch_ollama_models(), list(OLLAMA_FALLBACK_MODELS))
        self.assertIsNone(h2._served_model_ids["Ollama"])


class WireTests(unittest.TestCase):
    """The REAL _stream_ollama_call over the fake client."""

    def run_call(self, h):
        return h._stream_ollama_call([{"role": "user", "content": "hi"}], 3, False)

    def test_think_follows_the_checkbox_on_a_capability_thinking_model(self):
        for enabled in (True, False):
            c = FakeOllama(caps={"qwen3:32b-q4_K_M": ["completion", "tools", "thinking"]})
            h = host(c, thinking=enabled)
            self.run_call(h)
            self.assertIs(c.chat_kwargs[0]["think"], enabled)

    def test_think_reaches_a_model_only_the_capability_list_knows(self):
        c = FakeOllama(caps={"newfamily:9b": ALL_FOUR})
        h = host(c, model="newfamily:9b", thinking=True)
        self.run_call(h)
        self.assertIs(c.chat_kwargs[0]["think"], True)

    def test_no_think_for_a_prefix_model_whose_capabilities_lack_it(self):
        c = FakeOllama(caps={"qwen3:32b-q4_K_M": ["completion", "tools"]})
        h = host(c, thinking=True)
        self.run_call(h)
        self.assertNotIn("think", c.chat_kwargs[0])

    def test_an_undescribed_prefix_model_still_gets_the_flag(self):
        c = FakeOllama(caps={"qwen3:32b-q4_K_M": None})   # show fails
        h = host(c, thinking=False)
        self.run_call(h)
        self.assertIs(c.chat_kwargs[0]["think"], False)

    def test_tools_withheld_from_a_tools_less_model_with_one_warning(self):
        c = FakeOllama(caps={"gemma3:27b": ["completion", "vision"]})
        h = host(c, model="gemma3:27b", thinking=False)
        self.run_call(h)
        self.run_call(h)
        self.assertTrue(all("tools" not in kw for kw in c.chat_kwargs))
        notes = [i["content"] for i in h.queue.of("tool_info") if "'tools' capability" in i["content"]]
        self.assertEqual(len(notes), 1)

    def test_num_ctx_is_the_capped_context(self):
        c = FakeOllama(caps={"qwen3:32b-q4_K_M": ["completion", "tools", "thinking"]}, ctx=131072)
        h = host(c)
        self.run_call(h)
        self.assertEqual(c.chat_kwargs[0]["options"]["num_ctx"], OLLAMA_NUM_CTX_CAP)
        c2 = FakeOllama(caps={"qwen3:32b-q4_K_M": ["completion", "tools", "thinking"]}, ctx=8192)
        h2 = host(c2)
        self.run_call(h2)
        self.assertEqual(c2.chat_kwargs[0]["options"]["num_ctx"], 8192)
        self.assertEqual(c2.chat_kwargs[0]["options"]["temperature"], 0.3)

    def test_a_context_full_stop_posts_a_cut_off_warning(self):
        c = FakeOllama(caps={"qwen3:32b-q4_K_M": ["completion", "tools", "thinking"]},
                       done_reason="length")
        h = host(c)
        stop, blocks, text, _, _, usage = self.run_call(h)
        warnings = [i["content"] for i in h.queue.of("warning")]
        self.assertTrue(any("cut off" in w and "32768" in w for w in warnings), warnings)
        self.assertEqual(usage, {"input_tokens": 10, "output_tokens": 3})
        c2 = FakeOllama(caps={"qwen3:32b-q4_K_M": ["completion", "tools", "thinking"]})
        h2 = host(c2)
        self.run_call(h2)
        self.assertEqual(h2.queue.of("warning"), [])

    def test_a_tool_call_is_parsed_into_a_tool_use_block(self):
        c = FakeOllama(caps={"qwen3:32b-q4_K_M": ["completion", "tools", "thinking"]},
                       reply=[{"content": "", "tool_calls": [
                           {"function": {"name": "lookup_order", "arguments": {"order_id": "4471"}}}]}])
        h = host(c)
        stop, blocks, text, *_ = self.run_call(h)
        self.assertEqual(stop, "tool_use")
        self.assertEqual(blocks, [{"type": "tool_use", "id": "ollama_lookup_order_0",
                                   "name": "lookup_order", "input": {"order_id": "4471"}}])
        self.assertEqual(len(c.chat_kwargs[0]["tools"]), 1)


class DumpTests(unittest.TestCase):
    """The Debug dump is the live request, from the one builder."""

    def make(self, c, model="qwen3:32b-q4_K_M", thinking=True):
        class _H(StreamingMixin, OllamaMixin):
            pass
        h = stub(_H, provider="Ollama", model=model, ollama_client=c, queue=FakeQueue(),
                 temperature=0.3, thinking_enabled=thinking, stop_requested=False)
        h._build_system_prompt = lambda: "sys"
        h._get_tools = lambda: [TOOL]
        return h

    IMAGE_MSG = [{"role": "user", "content": [
        {"type": "text", "text": "look"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "A" * 100}}]}]

    def test_the_dump_mirrors_the_wire_with_images_truncated(self):
        c = FakeOllama(caps={"qwen3:32b-q4_K_M": ["completion", "tools", "thinking"]})
        h = self.make(c)
        # The dump is rendered text; with no multi-line string in this fixture
        # it round-trips through json.loads (test_debug_render's contract).
        dump = json.loads(h._payload_for_display(self.IMAGE_MSG))
        h._stream_ollama_call(self.IMAGE_MSG, 3, False)
        wire = c.chat_kwargs[0]
        for key in ("model", "stream", "options", "tools", "think"):
            self.assertEqual(dump[key], wire[key], key)
        self.assertEqual(set(dump) - {"keep_alive"}, set(wire) - {"keep_alive"})
        self.assertNotIn("max_tokens", dump)
        self.assertNotIn("system", dump)
        self.assertEqual(dump["messages"][0], {"role": "system", "content": "sys"})
        self.assertEqual(dump["messages"][1]["images"], ["A" * 40 + "...[truncated]"])
        self.assertEqual(wire["messages"][1]["images"], ["A" * 100])   # the wire is whole

    def test_the_dump_neither_guards_nor_warns(self):
        # A warm 4096-ctx instance and a tools-less model: the live call drops
        # the instance (one generate poke) and warns once; the dump does neither.
        c = FakeOllama(caps={"gemma3:27b": ["completion", "vision"]}, loaded_ctx=4096)
        h = self.make(c, model="gemma3:27b", thinking=False)
        dump = json.loads(h._payload_for_display([{"role": "user", "content": "hi"}]))
        self.assertNotIn("tools", dump)
        self.assertNotIn("think", dump)
        self.assertEqual(c.generate_calls, [])
        self.assertEqual(h.queue.items, [])
        h._stream_ollama_call([{"role": "user", "content": "hi"}], 3, False)
        self.assertEqual(len(c.generate_calls), 1)
        self.assertTrue(any("warm at 4096" in i["content"] for i in h.queue.of("warning")))
        self.assertTrue(any("'tools' capability" in i["content"] for i in h.queue.of("tool_info")))


GPT_OSS = {"gpt-oss:20b": ["completion", "tools", "thinking"]}


class LevelTests(unittest.TestCase):
    def test_levels_by_name_and_by_published_values(self):
        h = stub(OllamaMixin, model="gpt-oss:20b")
        self.assertEqual(h._ollama_think_levels(), ["low", "medium", "high"])
        self.assertIsNone(h._ollama_think_levels("qwen3:32b-q4_K_M"))
        self.assertIsNone(h._ollama_think_levels("muse-glimmer:30b-mlx"))
        h2 = stub(OllamaMixin, model="future:1b", _ollama_model_caps={
            "future:1b": dict(caps("completion", "thinking"), think_levels=["low", "high"])})
        self.assertEqual(h2._ollama_think_levels(), ["low", "high"])

    def test_published_values_are_read_from_show_booleans_skipped(self):
        c = FakeOllama(caps={"future:1b": ["completion", "thinking", "tools"]})
        c.think_values = [False, "low", "medium", "high"]
        h = stub(OllamaMixin, model="future:1b", ollama_client=c)
        self.assertEqual(h._get_ollama_model_caps()["think_levels"], ["low", "medium", "high"])
        self.assertEqual(h._ollama_think_levels(), ["low", "medium", "high"])
        c2 = FakeOllama(caps=GPT_OSS)
        h2 = stub(OllamaMixin, model="gpt-oss:20b", ollama_client=c2)
        self.assertNotIn("think_levels", h2._get_ollama_model_caps())   # a 0.32.9 server

    def test_nearest_rung(self):
        n = OllamaMixin._ollama_nearest_level
        levels = ["low", "medium", "high"]
        for effort, want in (("high", "high"), ("low", "low"), ("Medium", "medium"),
                             ("max", "high"), ("xhigh", "high"), ("minimal", "low"),
                             ("adaptive", "medium"), ("", "medium"), (None, "medium"),
                             ("off", "medium")):
            with self.subTest(effort=effort):
                self.assertEqual(n(effort, levels), want)

    def test_the_wire_rung_follows_the_checkbox_and_the_saved_effort(self):
        for enabled, effort, want in ((True, "high", "high"), (True, "max", "high"),
                                      (True, "minimal", "low"), (True, "adaptive", "medium"),
                                      (True, "medium", "medium"), (False, "high", "low")):
            with self.subTest(enabled=enabled, effort=effort):
                c = FakeOllama(caps=GPT_OSS)
                h = host(c, model="gpt-oss:20b", thinking=enabled, thinking_effort=effort)
                h._stream_ollama_call([{"role": "user", "content": "hi"}], 3, False)
                self.assertEqual(c.chat_kwargs[0]["think"], want)

    def test_thinking_off_on_a_level_model_is_announced_once_per_model(self):
        c = FakeOllama(caps=GPT_OSS)
        h = host(c, model="gpt-oss:20b", thinking=False, thinking_effort="high")
        h._stream_ollama_call([{"role": "user", "content": "hi"}], 3, False)
        h._stream_ollama_call([{"role": "user", "content": "hi"}], 3, False)
        notes = [i["content"] for i in h.queue.of("tool_info") if "cannot stop thinking" in i["content"]]
        self.assertEqual(len(notes), 1)
        self.assertIn("think=low", notes[0])
        # A boolean model off: False on the wire, nothing announced.
        c2 = FakeOllama(caps={"qwen3:32b-q4_K_M": ["completion", "tools", "thinking"]})
        h2 = host(c2, thinking=False)
        h2._stream_ollama_call([{"role": "user", "content": "hi"}], 3, False)
        self.assertIs(c2.chat_kwargs[0]["think"], False)
        self.assertEqual([i for i in h2.queue.of("tool_info") if "cannot stop" in i["content"]], [])

    def test_the_dump_shows_the_rung_without_announcing(self):
        class _H(StreamingMixin, OllamaMixin):
            pass
        c = FakeOllama(caps=GPT_OSS)
        h = stub(_H, provider="Ollama", model="gpt-oss:20b", ollama_client=c, queue=FakeQueue(),
                 temperature=0.3, thinking_enabled=False, thinking_effort="high", stop_requested=False)
        h._build_system_prompt = lambda: "sys"
        h._get_tools = lambda: [TOOL]
        dump = json.loads(h._payload_for_display([{"role": "user", "content": "hi"}]))
        self.assertEqual(dump["think"], "low")
        self.assertEqual(h.queue.items, [])

    def test_the_summary_names_the_rung(self):
        class _H(UIMixin, OpenAIMixin, OllamaMixin):
            pass
        h = stub(_H, provider="Ollama", model="gpt-oss:20b", thinking_enabled=True,
                 thinking_effort="max", thinking_mode="", temperature=0.2, text_verbosity="medium")
        self.assertEqual(h._get_model_param_summary(), "thinking=high temp=0.2")
        h.thinking_enabled = False
        self.assertEqual(h._get_model_param_summary(), "thinking=off temp=0.2")
        h.model, h.thinking_enabled = "qwen3:32b-q4_K_M", True
        self.assertEqual(h._get_model_param_summary(), "thinking=on temp=0.2")

    def test_model_setup_offers_off_and_the_rungs(self):
        class _H(ModelUpgradeMixin, UIMixin, OllamaMixin):
            pass
        h = stub(_H, provider="Ollama", model="x")
        self.assertEqual(h._upgrade_level_values("Ollama", "gpt-oss:20b"),
                         ["Off", "Low", "Medium", "High"])
        params = h._upgrade_params_for("Ollama", "gpt-oss:20b", "High")
        self.assertTrue(params["thinking_enabled"])
        self.assertEqual(params["thinking_effort"], "high")
        self.assertEqual(h._upgrade_level_values("Ollama", "qwen3:8b"), ["Off", "On"])


class StrengthComboTests(unittest.TestCase):
    """The editor's controls on real widgets (withdrawn root): the strength
    combo appears for an Ollama level model only. Skips where no display."""

    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError as e:
            raise unittest.SkipTest(f"no display: {e}")
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def _host(self, model, enabled, effort="max"):
        class _W(UIMixin, OpenAIMixin, OllamaMixin):
            def _has_model_widgets(self):
                return True

            def _update_title(self):
                pass

            def _save_last_state(self):
                pass

            def _get_display_name(self, mid):
                return mid

        h = _W.__new__(_W)
        h.provider, h.model = "Ollama", model
        h.temperature, h.text_verbosity, h.fast_mode = 0.2, "medium", False
        h.thinking_effort, h.thinking_mode, h.thinking_enabled = effort, "", enabled
        h.thinking_budget = 8192
        h._anthropic_no_temperature = set()
        h._model_id_list = [model]
        frame = tk.Frame(self.root)
        h._model_var = tk.StringVar(value=model)
        h._thinking_var = tk.BooleanVar(value=enabled)
        h._thinking_strength_var = tk.StringVar(value="")
        h._thinking_mode_var = tk.StringVar(value="")
        h._text_verbosity_var = tk.StringVar(value="Medium")
        h._fast_var = tk.BooleanVar(value=False)
        h._thinking_check = tk.Checkbutton(frame, text="Thinking", variable=h._thinking_var)
        h._thinking_strength_combo = ttk.Combobox(frame, textvariable=h._thinking_strength_var)
        h._thinking_mode_label = tk.Label(frame, text="Thinking")
        h._thinking_mode_combo = ttk.Combobox(frame, textvariable=h._thinking_mode_var)
        h._verbosity_label = tk.Label(frame, text="Verbosity")
        h._verbosity_combo = ttk.Combobox(frame, textvariable=h._text_verbosity_var)
        h._fast_check = tk.Checkbutton(frame, text="Fast", variable=h._fast_var)
        h._temp_label = tk.Label(frame, text="Temp")
        h._temp_spin = tk.Spinbox(frame)
        return h

    @staticmethod
    def shown(widget):
        return bool(widget.winfo_manager())

    def test_a_level_model_shows_the_combo_with_its_rungs(self):
        h = self._host("gpt-oss:20b", enabled=True, effort="max")
        h._on_model_selected()
        self.assertTrue(self.shown(h._thinking_check))
        self.assertTrue(self.shown(h._thinking_strength_combo))
        self.assertEqual(list(h._thinking_strength_combo["values"]), ["low", "medium", "high"])
        self.assertEqual(h._thinking_strength_var.get(), "high")   # a saved max → the top rung
        self.assertEqual(h.thinking_effort, "high")                 # what a SAVE keeps
        self.assertTrue(self.shown(h._temp_spin))
        h._thinking_strength_var.set("low")
        h._on_thinking_strength_changed()
        self.assertEqual(h.thinking_effort, "low")

    def test_a_boolean_model_and_an_unticked_box_hide_it(self):
        h = self._host("qwen3:32b-q4_K_M", enabled=True)
        h._on_model_selected()
        self.assertTrue(self.shown(h._thinking_check))
        self.assertFalse(self.shown(h._thinking_strength_combo))
        self.assertTrue(self.shown(h._temp_spin))
        h2 = self._host("gpt-oss:20b", enabled=False)
        h2._on_model_selected()
        self.assertTrue(self.shown(h2._thinking_check))
        self.assertFalse(self.shown(h2._thinking_strength_combo))
        self.assertTrue(self.shown(h2._temp_spin))
        h3 = self._host("gemma3-tools:27b", enabled=True)
        h3._on_model_selected()                                     # no thinking at all
        self.assertFalse(self.shown(h3._thinking_check))
        self.assertFalse(self.shown(h3._thinking_strength_combo))
        self.assertTrue(self.shown(h3._temp_spin))


class UpgradeProbeTests(unittest.TestCase):
    def test_the_probe_carries_the_capability_cache(self):
        class _H(ModelUpgradeMixin, UIMixin, OllamaMixin):
            pass
        h = stub(_H, provider="Ollama", model="x",
                 _ollama_model_caps={"newfamily:9b": caps(*ALL_FOUR)})
        self.assertEqual(h._upgrade_level_values("Ollama", "newfamily:9b"), ["Off", "On"])
        self.assertEqual(h._upgrade_level_values("Ollama", "qwen3:8b"), ["Off", "On"])
        self.assertEqual(h._upgrade_level_values("Ollama", "llama3.3"), [])


class WiringTests(unittest.TestCase):
    def read(self, rel):
        with open(os.path.join(REPO, rel), encoding="utf-8") as f:
            return f.read()

    def test_the_read_timeout_is_the_constant(self):
        self.assertEqual(constants.OLLAMA_READ_TIMEOUT, 900.0)
        src = self.read("myagent/constants.py")
        self.assertIn('os.environ.get("OLLAMA_READ_TIMEOUT"', src)
        app = self.read("MyAgent.py")
        self.assertRegex(app, r"ollama\.Client\(\s*host=self\._ollama_base_url,\s*"
                              r"timeout=httpx\.Timeout\(OLLAMA_READ_TIMEOUT, connect=10\.0\)")
        self.assertNotIn("httpx.Timeout(300.0", app)

    def test_the_editor_asks_the_helper_not_the_prefix_table(self):
        src = self.read("myagent/ui_mixin.py")
        branch = src[src.index('if self.provider == "Ollama":', src.index("def _model_supports_thinking")):]
        branch = branch[:branch.index("return")]
        self.assertNotIn("OLLAMA_THINKING_PREFIXES", src)
        fn = src[src.index("def _model_supports_thinking"):]
        fn = fn[:fn.index("\n    def ")]
        self.assertIn("self._is_ollama_thinking_model(mid)", fn)

    def test_the_stream_call_and_fetch_are_wired(self):
        src = self.read("myagent/ollama_mixin.py")
        self.assertIn('request_kwargs["think"] = self._ollama_think_value(announce=announce)', src)
        self.assertIn('note_served_models(self, "Ollama", names)', src)
        self.assertIn("self._ollama_runs_agent_loop(n)", src)
        self.assertIn('== "length"', src)
        self.assertIn("request_kwargs = self._ollama_request_kwargs(messages)", src)
        dump = self.read("myagent/streaming_mixin.py")
        self.assertIn('elif self.provider == "Ollama":', dump)
        self.assertIn("self._ollama_request_kwargs(messages, announce=False)", dump)
        probe = self.read("myagent/model_upgrade_mixin.py")
        self.assertIn("probe._ollama_model_caps = dict(", probe)

    def test_the_prefix_tables_know_the_october_families(self):
        for family in ("gemma4", "nemotron"):
            self.assertTrue(any(family.startswith(p) or p.startswith(family)
                                for p in constants.OLLAMA_THINKING_PREFIXES), family)
        for family in ("gemma4", "qwen3.8", "qwen3.6", "qwen3.5"):
            self.assertIn(family, constants.OLLAMA_VISION_PREFIXES)
        self.assertEqual(constants.OLLAMA_THINK_LEVEL_PREFIXES, ("gpt-oss",))
        self.assertEqual(constants.OLLAMA_THINK_LEVELS, ("low", "medium", "high"))
        self.assertTrue(re.match(r"^\d+(\.\d+)?$", os.environ.get("OLLAMA_READ_TIMEOUT", "900")))


if __name__ == "__main__":
    unittest.main()
