"""Characterization tests for the gpt-6-astra parameter surface (2026-09-06).

Pins every layer the model's parameters pass through, against the contract
probed live with the real key: reasoning.effort accepts low/medium/high/
xhigh/max ONLY (none and minimal are HTTP 400), temperature is rejected
unconditionally, text.verbosity is accepted.

  * exposed  — _model_supports_thinking / _openai_reasoning_values (the
               Reasoning combobox rungs) and _get_model_param_summary (the
               title-bar / cost-log params string);
  * used     — the api_kwargs _stream_responses_call hands the SDK, captured
               at the _stream_responses seam, and the Debug payload mirror;
  * saved    — _restore_model_params on an instruction entry, headless (no
               widgets), including a stale "none" carried over from a 5.x
               instruction, which must reach the wire as "low".

The GPT-5.6 cases alongside guard the ordering: the GPT-6 branch sits first
in _stream_responses_call and must not swallow the 5.x paths.
"""

import queue
import unittest

from myagent.constants import _HAS_DESKTOP
from myagent.openai_mixin import OpenAIMixin
from myagent.streaming_mixin import StreamingMixin
from myagent.ui_mixin import UIMixin

RUNGS = ["low", "medium", "high", "xhigh", "max"]
TOOL = {"name": "run_command", "description": "d",
        "input_schema": {"type": "object", "properties": {}}}


class _Var:
    def __init__(self, v=None):
        self._v = v

    def get(self):
        return self._v

    def set(self, v):
        self._v = v


class _ReqHost(StreamingMixin, OpenAIMixin):
    """_stream_responses_call host — the provider call is stubbed at
    _stream_responses, which records the api_kwargs it was handed."""

    def __init__(self, model="gpt-6-astra", effort="medium", enabled=True,
                 verbosity="medium", desktop=False):
        self.provider = "OpenAI"
        self.model = model
        self.thinking_effort = effort
        self.thinking_mode = effort
        self.thinking_enabled = enabled
        self.temperature = 0.7
        self.text_verbosity = verbosity
        self.desktop_enabled = _Var(desktop)
        self._openai_unsupported_tools = {}
        self.queue = queue.Queue()
        self.sent = []

    def _build_system_prompt(self):
        return "SYS"

    def _get_tools(self):
        return [dict(TOOL)]

    def _stream_responses(self, api_kwargs, label_emitted):
        self.sent.append(api_kwargs)
        return "", "end_turn", [], False, label_emitted, {
            "input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": 0}

    def call(self):
        return self._stream_responses_call([{"role": "user", "content": "hi"}], 3, True)

    def infos(self):
        out = []
        while True:
            try:
                m = self.queue.get_nowait()
            except queue.Empty:
                return out
            if m["type"] == "tool_info":
                out.append(m["content"])


class RequestParams(unittest.TestCase):

    def test_every_rung_is_sent_verbatim_without_temperature(self):
        for rung in RUNGS:
            with self.subTest(rung=rung):
                h = _ReqHost(effort=rung, verbosity="high")
                h.call()
                (kw,) = h.sent
                self.assertEqual(kw["model"], "gpt-6-astra")
                self.assertEqual(kw["reasoning"], {"effort": rung, "summary": "auto"})
                self.assertNotIn("temperature", kw)
                self.assertEqual(kw["text"], {"verbosity": "high"})
                self.assertEqual(kw["instructions"], "SYS")
                self.assertIs(kw["store"], False)
                self.assertEqual(kw["include"], ["code_interpreter_call.outputs"])
                types = [t["type"] for t in kw["tools"]]
                self.assertEqual(types, ["function", "web_search_preview", "code_interpreter"])
                self.assertEqual(kw["tools"][0]["name"], "run_command")
                self.assertEqual(h.infos(), [])          # nothing to coerce, no notice
                self.assertEqual((h.thinking_effort, h.thinking_mode, h.thinking_enabled),
                                 (rung, rung, True))   # state untouched

    def test_code_interpreter_is_gated_by_desktop_tools(self):
        h = _ReqHost(desktop=True)
        h.call()
        types = [t["type"] for t in h.sent[0]["tools"]]
        # Stripped only when desktop tools are really on (pyautogui present)
        self.assertEqual("code_interpreter" not in types, bool(_HAS_DESKTOP))
        self.assertIn("web_search_preview", types)

    def test_stale_none_is_floor_coerced_and_written_back_once(self):
        # A 'none' saved by a GPT-5.x instruction (headless runs never pass
        # through the combobox coercion): the wire gets 'low', the live state
        # is rewritten so the title / cost-log params report it, and the
        # Activity notice fires exactly once per run.
        h = _ReqHost(effort="none", enabled=False)
        h.call()
        self.assertEqual(h.sent[0]["reasoning"], {"effort": "low", "summary": "auto"})
        self.assertNotIn("temperature", h.sent[0])
        self.assertEqual((h.thinking_effort, h.thinking_mode, h.thinking_enabled),
                         ("low", "low", True))
        notices = h.infos()
        self.assertEqual(len(notices), 1)
        self.assertIn("no reasoning='none' rung", notices[0])
        h.call()                                        # second call of the run
        self.assertEqual(h.sent[1]["reasoning"]["effort"], "low")
        self.assertEqual(h.infos(), [])                 # already coerced — quiet

    def test_minimal_and_off_coerce_too(self):
        for stale in ("minimal", "off"):
            with self.subTest(stale=stale):
                h = _ReqHost(effort=stale, enabled=False)
                h.call()
                self.assertEqual(h.sent[0]["reasoning"]["effort"], "low")

    def test_gpt56_paths_are_untouched(self):
        # GPT-5.6 with reasoning=None: 'none' goes through and temperature is
        # sent (5.4+ accept it at none) — the GPT-6 branch must not intercept.
        h = _ReqHost(model="gpt-5.6-terra", effort="none", enabled=False)
        h.call()
        self.assertEqual(h.sent[0]["reasoning"], {"effort": "none", "summary": "auto"})
        self.assertEqual(h.sent[0]["temperature"], 0.7)
        self.assertEqual(h.sent[0]["text"], {"verbosity": "medium"})
        self.assertEqual(h.infos(), [])
        h = _ReqHost(model="gpt-5.6-sol", effort="max")
        h.call()
        self.assertEqual(h.sent[0]["reasoning"]["effort"], "max")
        self.assertNotIn("temperature", h.sent[0])

    def test_other_openai_families_keep_their_shapes(self):
        # One shared builder feeds the wire and the dump — pin the per-family
        # shapes it must keep producing (captured from the pre-refactor code).
        cases = {
            # gpt-5.1 at none: reasoning + temperature fixed at 1.0 (pre-5.4)
            ("gpt-5.1", "none", False): {"reasoning": {"effort": "none", "summary": "auto"},
                                         "temperature": 1.0, "text": {"verbosity": "medium"}},
            # GPT-5.0: reasoning when enabled, temperature always 1.0
            ("gpt-5", "high", True): {"reasoning": {"effort": "high", "summary": "auto"},
                                      "temperature": 1.0, "text": {"verbosity": "medium"}},
            ("gpt-5", "high", False): {"temperature": 1.0, "text": {"verbosity": "medium"}},
            # o-series: reasoning only when enabled, never temperature/verbosity
            ("o3", "medium", True): {"reasoning": {"effort": "medium", "summary": "auto"}},
            ("o3", "medium", False): {},
            # gpt-5.x-chat Instant: no reasoning, no temperature, verbosity yes
            ("gpt-5.1-chat-latest", "none", False): {"text": {"verbosity": "medium"}},
            # gpt-4.1: plain sampling model
            ("gpt-4.1", "none", False): {"temperature": 0.7},
        }
        for (model, effort, enabled), expected in cases.items():
            with self.subTest(model=model, enabled=enabled):
                h = _ReqHost(model=model, effort=effort, enabled=enabled)
                params, notice = h._openai_model_params()
                self.assertEqual(params, expected)
                self.assertIsNone(notice)


class DebugPayload(unittest.TestCase):

    def test_payload_mirrors_the_request(self):
        h = _ReqHost(effort="none", enabled=False, verbosity="low")
        text = h._payload_for_display([{"role": "user", "content": "hi"}])
        self.assertIn('"effort": "low"', text)          # floor-coerced like the request
        self.assertIn('"verbosity": "low"', text)
        self.assertNotIn("temperature", text)
        self.assertIn("web_search_preview", text)
        # The tool DECLARATION (the include list always names code_interpreter_call)
        self.assertIn('"type": "code_interpreter"', text)
        h = _ReqHost(effort="max", desktop=True)
        text = h._payload_for_display([{"role": "user", "content": "hi"}])
        self.assertIn('"effort": "max"', text)
        self.assertEqual('"type": "code_interpreter"' not in text, bool(_HAS_DESKTOP))


class _UIHost(UIMixin, OpenAIMixin):
    """UIMixin helpers consult the OpenAIMixin detection helpers — both in the
    MRO, like App. Widget-free: _has_model_widgets says so."""

    def __init__(self, model="gpt-6-astra", mode="medium", verbosity="medium"):
        self.provider = "OpenAI"
        self.model = model
        self.thinking_mode = mode
        self.thinking_effort = mode
        self.thinking_enabled = mode not in ("off", "none")
        self.thinking_budget = 8192
        self.text_verbosity = verbosity
        self.temperature = 0.7
        # _restore_model_params plumbing
        self._has_openai = True
        self.available_models = ["gpt-5.6-terra", "gpt-6-astra"]
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
        self.assertEqual(h._model_supports_thinking("gpt-6-astra"), "extended")
        self.assertEqual(h._model_supports_thinking("gpt-5.6-terra"), "extended")
        self.assertEqual(h._model_supports_thinking("gpt-5"), "adaptive")
        self.assertIsNone(h._model_supports_thinking("gpt-4.1"))

    def test_reasoning_combobox_rungs(self):
        h = _UIHost()
        self.assertEqual(h._openai_reasoning_values("gpt-6-astra"),
                         ["Low", "Medium", "High", "Xhigh", "Max"])
        self.assertEqual(h._openai_reasoning_values("gpt-6.1-nova"),
                         ["Low", "Medium", "High", "Xhigh", "Max"])
        self.assertEqual(h._openai_reasoning_values("gpt-5.6-terra"),
                         ["None", "Low", "Medium", "High", "Xhigh", "Max"])
        self.assertEqual(h._openai_reasoning_values("gpt-5.5"),
                         ["None", "Low", "Medium", "High", "Xhigh"])
        self.assertEqual(h._openai_reasoning_values("gpt-5.5-pro"), ["Medium", "High", "Xhigh"])
        self.assertEqual(h._openai_reasoning_values("gpt-5.2-mini"), ["None", "Low", "Medium", "High"])
        self.assertEqual(h._openai_reasoning_values("gpt-5.1"), ["None", "Low", "Medium", "High"])

    def test_param_summary_reports_the_wire_effort(self):
        self.assertEqual(_UIHost(mode="max", verbosity="high")._get_model_param_summary(),
                         "reasoning=Max verbosity=high")
        # A stale none on GPT-6 reports the Low that is actually sent
        self.assertEqual(_UIHost(mode="none")._get_model_param_summary(),
                         "reasoning=Low verbosity=medium")
        # ...while GPT-5.6 keeps its real None (+ temperature, sent at none)
        self.assertEqual(_UIHost(model="gpt-5.6-terra", mode="none")._get_model_param_summary(),
                         "reasoning=None verbosity=medium temp=0.7")


class SavedEntry(unittest.TestCase):
    ENTRY = {"provider": "OpenAI", "model": "gpt-6-astra", "temperature": 1.0,
             "thinking_enabled": True, "thinking_effort": "max", "thinking_budget": 8192,
             "thinking_mode": "max", "text_verbosity": "high"}

    def test_instruction_entry_restores_headless(self):
        h = _UIHost(mode="low")
        h._restore_model_params(dict(self.ENTRY))
        self.assertEqual(h.model, "gpt-6-astra")
        self.assertEqual((h.thinking_mode, h.thinking_effort, h.thinking_enabled),
                         ("max", "max", True))
        self.assertEqual(h.text_verbosity, "high")
        self.assertEqual(h._thinking_mode_var.get(), "Max")
        self.assertEqual(h._model_drift_warnings, [])
        self.assertEqual(h._get_model_param_summary(), "reasoning=Max verbosity=high")

    def test_stale_none_entry_reaches_the_wire_as_low(self):
        entry = dict(self.ENTRY, thinking_enabled=False, thinking_effort="none",
                     thinking_mode="none", text_verbosity="medium")
        h = _UIHost(mode="low")
        h._restore_model_params(entry)
        # Headless restore keeps the saved value (no combobox to coerce it)...
        self.assertEqual((h.thinking_mode, h.thinking_enabled), ("none", False))
        # ...but every reader of "what goes on the wire" already says low
        self.assertEqual(h._openai_effective_effort(), "low")
        self.assertEqual(h._get_model_param_summary(), "reasoning=Low verbosity=medium")

    def test_legacy_entry_without_thinking_mode(self):
        entry = {"provider": "OpenAI", "model": "gpt-6-astra", "thinking_enabled": True,
                 "thinking_effort": "xhigh"}
        h = _UIHost()
        h._restore_model_params(entry)
        self.assertEqual((h.thinking_mode, h.thinking_effort), ("xhigh", "xhigh"))
        self.assertEqual(h.text_verbosity, "medium")   # default when absent


if __name__ == "__main__":
    unittest.main()
