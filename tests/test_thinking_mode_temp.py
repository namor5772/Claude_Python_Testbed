"""The Reasoning / Thinking mode combobox's temperature rule, on real widgets
(withdrawn root): _on_thinking_mode_changed packs the temperature widgets
after the mode combo only where the API takes temperature — xAI always,
Moonshot NEVER (every Kimi model fixes sampling server-side; kimi-k3's None
rung, new on 2026-09-23, would otherwise have fallen into the generic "a
none mode shows temperature" rule and shown it), OpenAI's 5.4+ / gpt-6-sol
tiers at None only, Anthropic Opus 4.7+ never. Skips where no display exists."""
import unittest
import tkinter as tk

from myagent.kimi_mixin import KimiMixin
from myagent.openai_mixin import OpenAIMixin
from myagent.ui_mixin import UIMixin
from myagent.xai_mixin import XAIMixin


class _Host(UIMixin, OpenAIMixin, XAIMixin, KimiMixin):
    """UIMixin's mode handler plus the provider detection helpers it asks."""

    def _has_model_widgets(self):
        return True

    def _update_title(self):
        pass

    def _save_last_state(self):
        pass


class TempAfterModeTests(unittest.TestCase):
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

    def _host(self, provider, model, mode):
        h = _Host.__new__(_Host)
        h.provider, h.model = provider, model
        h.temperature, h.text_verbosity = 0.7, "medium"
        h.thinking_effort, h.thinking_mode, h.thinking_enabled = "high", "high", True
        h._anthropic_no_temperature = set()
        frame = tk.Frame(self.root)
        h._thinking_mode_var = tk.StringVar(value=mode)
        h._thinking_var = tk.BooleanVar(value=True)
        h._thinking_mode_label = tk.Label(frame, text="Reasoning")
        h._thinking_mode_combo = tk.Label(frame)
        h._thinking_mode_label.pack(side=tk.LEFT)
        h._thinking_mode_combo.pack(side=tk.LEFT)
        h._temp_label = tk.Label(frame, text="Temp")
        h._temp_spin = tk.Spinbox(frame)
        return h

    def _temp_shown(self, h):
        h._on_thinking_mode_changed()
        return bool(h._temp_spin.winfo_manager())

    def test_moonshot_never_shows_temperature(self):
        for mode in ("None", "Low", "Max"):
            with self.subTest(mode=mode):
                self.assertFalse(self._temp_shown(self._host("Moonshot", "kimi-k3", mode)))

    def test_xai_always_shows_temperature(self):
        for model, mode in (("grok-4.3", "None"), ("grok-4.7", "Xhigh")):
            with self.subTest(model=model, mode=mode):
                self.assertTrue(self._temp_shown(self._host("xAI", model, mode)))

    def test_openai_shows_temperature_at_none_where_the_model_takes_it(self):
        self.assertTrue(self._temp_shown(self._host("OpenAI", "gpt-5.6-terra", "None")))
        self.assertTrue(self._temp_shown(self._host("OpenAI", "gpt-6-sol", "None")))
        self.assertFalse(self._temp_shown(self._host("OpenAI", "gpt-6-sol", "Low")))
        self.assertFalse(self._temp_shown(self._host("OpenAI", "gpt-5.2", "None")))   # pre-5.4: fixed 1.0
        self.assertFalse(self._temp_shown(self._host("OpenAI", "gpt-6-astra", "Low")))

    def test_anthropic_off_hides_it_on_models_that_reject_it(self):
        self.assertFalse(self._temp_shown(self._host("Anthropic", "claude-opus-5", "Off")))
        self.assertTrue(self._temp_shown(self._host("Anthropic", "claude-opus-4-6", "Off")))
        self.assertFalse(self._temp_shown(self._host("Anthropic", "claude-opus-4-6", "High")))


if __name__ == "__main__":
    unittest.main()
