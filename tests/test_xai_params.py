"""Characterization tests for the xAI (Grok) parameter surface (2026-09-23).

Pins every layer a Grok model's parameters pass through, against the
contract probed live with the real key that day: grok-4.7 (and 4.5 / 4.6)
accept reasoning.effort low/medium/high/xhigh ONLY ("none" is HTTP 400),
grok-4.3 none..xhigh, temperature is accepted alongside reasoning on all of
them, the pinned grok-4.20 variants and grok-build-0.1 reject the effort
("does not support parameter reasoningEffort") but accept — and the
reasoning ones honour — reasoning.summary on its own.

  * exposed  — _model_supports_thinking / _xai_reasoning_values (the
               Reasoning combobox rungs) and _get_model_param_summary (the
               title-bar / cost-log params string);
  * used     — the api_kwargs _stream_xai_call hands the SDK, captured at
               the _stream_xai_events seam, the 400 downgrade ladder, and
               the Debug payload mirror;
  * saved    — _restore_model_params on an instruction entry, headless (no
               widgets), including a stale "max" carried over from an
               Anthropic instruction, which must reach the wire as "xhigh".
"""

import queue
import unittest

import httpx
import openai

from myagent.constants import _HAS_DESKTOP
from myagent.openai_mixin import OpenAIMixin
from myagent.streaming_mixin import StreamingMixin
from myagent.ui_mixin import UIMixin
from myagent.xai_mixin import XAIMixin

TOOL = {"name": "run_command", "description": "d",
        "input_schema": {"type": "object", "properties": {}}}
LOW_XHIGH = ["low", "medium", "high", "xhigh"]


class _Var:
    def __init__(self, v=None):
        self._v = v

    def get(self):
        return self._v

    def set(self, v):
        self._v = v


def _bad_request(message):
    return openai.BadRequestError(
        message, response=httpx.Response(400, request=httpx.Request("POST", "https://api.x.ai/v1")),
        body={"error": message})


class _ReqHost(StreamingMixin, XAIMixin):
    """_stream_xai_call host — the provider call is stubbed at
    _stream_xai_events, which records the api_kwargs it was handed and can
    refuse the first N attempts with a 400."""

    def __init__(self, model="grok-4.7", effort="medium", desktop=False, refusals=()):
        self.provider = "xAI"
        self.model = model
        self.thinking_effort = effort
        self.thinking_mode = effort
        self.thinking_enabled = effort not in ("off", "none")
        self.temperature = 0.7
        self.desktop_enabled = _Var(desktop)
        self.stop_requested = False
        self.queue = queue.Queue()
        self.xai_client = object()
        self.sent = []
        self._refusals = list(refusals)

    def _build_system_prompt(self):
        return "SYS"

    def _get_tools(self):
        return [dict(TOOL)]

    def _stream_xai_events(self, api_kwargs, label_emitted):
        self.sent.append({k: (dict(v) if isinstance(v, dict) else v) for k, v in api_kwargs.items()})
        if self._refusals:
            raise _bad_request(self._refusals.pop(0))
        return "", "end_turn", [], False, label_emitted, {"input_tokens": 1, "output_tokens": 1}

    def call(self):
        return self._stream_xai_call([{"role": "user", "content": "hi"}], 5, True)

    def infos(self):
        out = []
        while True:
            try:
                m = self.queue.get_nowait()
            except queue.Empty:
                return out
            if m["type"] == "tool_info" and not m["content"].startswith("Waiting"):
                out.append(m["content"])


class RequestParams(unittest.TestCase):

    def test_every_rung_is_sent_with_temperature_and_summary(self):
        for rung in LOW_XHIGH:
            with self.subTest(rung=rung):
                h = _ReqHost(effort=rung)
                h.call()
                (kw,) = h.sent
                self.assertEqual(kw["model"], "grok-4.7")
                self.assertEqual(kw["reasoning"], {"effort": rung, "summary": "auto"})
                self.assertEqual(kw["temperature"], 0.7)
                self.assertEqual(kw["instructions"], "SYS")
                self.assertIs(kw["store"], False)
                types = [t["type"] for t in kw["tools"]]
                self.assertEqual(types, ["function", "web_search", "x_search", "code_interpreter"])
                self.assertEqual(kw["tools"][0]["name"], "run_command")
                self.assertEqual(h.infos(), [])

    def test_stale_effort_reaches_the_wire_as_the_nearest_rung(self):
        for model, effort, expected in (("grok-4.7", "max", "xhigh"), ("grok-4.7", "none", "low"),
                                        ("grok-4.7", "off", "low"), ("grok-4.3", "off", "none"),
                                        ("grok-4.3", "max", "xhigh"), ("grok-4.5", "adaptive", "low")):
            with self.subTest(model=model, effort=effort):
                h = _ReqHost(model=model, effort=effort)
                h.call()
                self.assertEqual(h.sent[0]["reasoning"], {"effort": expected, "summary": "auto"})

    def test_knobless_models_send_the_summary_alone(self):
        for model in ("grok-4.20-0309-reasoning", "grok-4.20-0309-non-reasoning",
                      "grok-build-0.1", "grok-latest"):
            with self.subTest(model=model):
                h = _ReqHost(model=model, effort="high")
                h.call()
                (kw,) = h.sent
                self.assertEqual(kw["reasoning"], {"summary": "auto"})
                self.assertEqual(kw["temperature"], 0.7)

    def test_code_interpreter_is_gated_by_desktop_tools(self):
        h = _ReqHost(desktop=True)
        h.call()
        types = [t["type"] for t in h.sent[0]["tools"]]
        self.assertEqual("code_interpreter" not in types, bool(_HAS_DESKTOP))


class DowngradeLadder(unittest.TestCase):

    def test_summary_refused_on_a_knob_model_keeps_the_effort(self):
        h = _ReqHost(effort="high", refusals=["reasoning summary is not supported"])
        h.call()
        self.assertEqual([kw["reasoning"] for kw in h.sent],
                         [{"effort": "high", "summary": "auto"}, {"effort": "high"}])
        self.assertEqual(h.infos(), ["Model rejected reasoning summary — retrying with effort only...\n"])

    def test_summary_refused_on_a_knobless_model_drops_the_parameter_whole(self):
        # Never an empty {"reasoning": {}} on the retry
        h = _ReqHost(model="grok-build-0.1", refusals=["Model does not support parameter reasoning"])
        h.call()
        self.assertEqual(h.sent[0]["reasoning"], {"summary": "auto"})
        self.assertNotIn("reasoning", h.sent[1])
        self.assertEqual(h.infos(), ["Model rejected the reasoning summary — retrying without it...\n"])

    def test_effort_refused_twice_ends_without_reasoning(self):
        h = _ReqHost(effort="low", refusals=["Invalid reasoning effort.",
                                             "does not support parameter reasoningEffort"])
        h.call()
        self.assertEqual([kw.get("reasoning") for kw in h.sent],
                         [{"effort": "low", "summary": "auto"}, {"effort": "low"}, None])

    def test_temperature_refused_is_dropped_first(self):
        h = _ReqHost(refusals=["temperature is not supported"])
        h.call()
        self.assertIn("temperature", h.sent[0])
        self.assertNotIn("temperature", h.sent[1])
        self.assertEqual(h.sent[1]["reasoning"], {"effort": "medium", "summary": "auto"})


class DebugPayload(unittest.TestCase):

    def test_payload_mirrors_the_request(self):
        h = _ReqHost(effort="max")
        text = h._payload_for_display([{"role": "user", "content": "hi"}])
        self.assertIn('"effort": "xhigh"', text)          # nearest-rung like the request
        self.assertIn('"summary": "auto"', text)
        self.assertIn('"temperature": 0.7', text)
        self.assertIn('"type": "x_search"', text)
        self.assertIn('"type": "web_search"', text)
        h = _ReqHost(model="grok-build-0.1", effort="high")
        text = h._payload_for_display([{"role": "user", "content": "hi"}])
        self.assertNotIn('"effort"', text)
        self.assertIn('"summary": "auto"', text)
        h = _ReqHost(desktop=True)
        text = h._payload_for_display([{"role": "user", "content": "hi"}])
        self.assertEqual('"type": "code_interpreter"' not in text, bool(_HAS_DESKTOP))

    def test_dump_and_wire_come_from_one_builder(self):
        h = _ReqHost(effort="off", )
        h.call()
        text = h._payload_for_display([{"role": "user", "content": "hi"}])
        self.assertIn('"effort": "low"', text)
        self.assertEqual(h.sent[0]["reasoning"], {"effort": "low", "summary": "auto"})


class _UIHost(UIMixin, XAIMixin, OpenAIMixin):
    """UIMixin helpers consult the XAIMixin detection helpers, and the
    no-thinking branch of _get_model_param_summary asks OpenAIMixin whether
    the model is a gpt-5 -chat variant — all three in the MRO, like App.
    Widget-free: _has_model_widgets says so."""

    def __init__(self, model="grok-4.7", mode="medium", caps=None):
        self.provider = "xAI"
        self.model = model
        self.thinking_mode = mode
        self.thinking_effort = mode
        self.thinking_enabled = mode not in ("off", "none")
        self.thinking_budget = 8192
        self.text_verbosity = "medium"
        self.temperature = 0.7
        self._xai_caps = caps or {}
        # _restore_model_params plumbing
        self._has_xai = True
        self.available_models = ["grok-4.3", "grok-4.7", "grok-build-0.1"]
        self.queue = queue.Queue()
        for name in ("_provider_var", "_model_var", "_temp_var", "_thinking_var",
                     "_thinking_mode_var", "_thinking_strength_var", "_text_verbosity_var"):
            setattr(self, name, _Var())

    def _has_model_widgets(self):
        return False

    def _get_display_name(self, mid):
        return mid

    def _save_last_state(self):
        pass

    def _update_title(self):
        pass


class Exposed(unittest.TestCase):

    def test_reasoning_combobox_kind(self):
        h = _UIHost()
        for mid in ("grok-4.3", "grok-4.5", "grok-4.6", "grok-4.7", "grok-4.20-multi-agent-0309"):
            self.assertEqual(h._model_supports_thinking(mid), "extended", mid)
        for mid in ("grok-4.20-0309-reasoning", "grok-4.20-0309-non-reasoning",
                    "grok-build-0.1", "grok-latest"):
            self.assertIsNone(h._model_supports_thinking(mid), mid)

    def test_reasoning_combobox_rungs(self):
        h = _UIHost()
        self.assertEqual(h._xai_reasoning_values("grok-4.3"), ["none", "low", "medium", "high", "xhigh"])
        for mid in ("grok-4.5", "grok-4.6", "grok-4.7", "grok-4.20-multi-agent-0309"):
            self.assertEqual(h._xai_reasoning_values(mid), LOW_XHIGH, mid)

    def test_param_summary_reports_the_wire_effort(self):
        self.assertEqual(_UIHost(mode="high")._get_model_param_summary(), "reasoning=High temp=0.7")
        # A stale max / none reports what is actually sent
        self.assertEqual(_UIHost(mode="max")._get_model_param_summary(), "reasoning=Xhigh temp=0.7")
        self.assertEqual(_UIHost(mode="none")._get_model_param_summary(), "reasoning=Low temp=0.7")
        self.assertEqual(_UIHost(model="grok-4.3", mode="off")._get_model_param_summary(),
                         "reasoning=None temp=0.7")
        # A knobless model: no reasoning part, temperature only
        self.assertEqual(_UIHost(model="grok-build-0.1", mode="high")._get_model_param_summary(),
                         "temp=0.7")

    def test_live_listing_exposes_a_new_tier(self):
        caps = {"grok-4.8": {"reasoning_effort": ["low", "high"], "vision": True}}
        h = _UIHost(model="grok-4.8", mode="max", caps=caps)
        self.assertEqual(h._model_supports_thinking(), "extended")
        self.assertEqual(h._xai_reasoning_values(), ["low", "high"])
        self.assertEqual(h._get_model_param_summary(), "reasoning=High temp=0.7")


class SavedEntry(unittest.TestCase):
    ENTRY = {"provider": "xAI", "model": "grok-4.7", "temperature": 1.0,
             "thinking_enabled": True, "thinking_effort": "high", "thinking_budget": 8192,
             "thinking_mode": "high", "text_verbosity": "medium"}

    def test_instruction_entry_restores_headless(self):
        h = _UIHost(mode="low")
        h._restore_model_params(dict(self.ENTRY))
        self.assertEqual(h.model, "grok-4.7")
        self.assertEqual((h.thinking_mode, h.thinking_effort, h.thinking_enabled),
                         ("high", "high", True))
        self.assertEqual(h._thinking_mode_var.get(), "High")
        self.assertEqual(h._model_drift_warnings, [])
        self.assertEqual(h._get_model_param_summary(), "reasoning=High temp=1")

    def test_stale_max_entry_reaches_the_wire_as_xhigh(self):
        # An instruction saved on an Anthropic model at Max and re-pointed
        # at grok-4.7: the combobox coercion never runs headless, so the
        # request builder's nearest-rung rule is what keeps it from a 400
        entry = dict(self.ENTRY, thinking_effort="max", thinking_mode="max")
        h = _UIHost(mode="low")
        h._restore_model_params(entry)
        self.assertEqual(h.thinking_effort, "max")           # saved value kept
        self.assertEqual(h._xai_effective_effort(), "xhigh")  # sent value
        self.assertEqual(h._get_model_param_summary(), "reasoning=Xhigh temp=1")
        self.assertEqual(h._xai_model_params(),
                         {"temperature": 1.0, "reasoning": {"effort": "xhigh", "summary": "auto"}})


if __name__ == "__main__":
    unittest.main()
