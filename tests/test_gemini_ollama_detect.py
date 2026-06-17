"""Characterization test: Gemini (UIMixin) and Ollama (OllamaMixin) detection.

Locks the 'lite' exclusion for Gemini thinking and the overlapping
qwen3vl case (thinking AND vision). Values are ACTUAL captured outputs."""
import unittest

from tests._util import stub
from myagent.ui_mixin import UIMixin
from myagent.ollama_mixin import OllamaMixin

GEMINI = {
    "gemini-2.5-pro": True,
    "gemini-2.5-flash-lite": False,
    "gemini-3-pro": True,
    "gemini-3-flash": True,
    "gemini-2.0-flash": False,
    "gemini-1.5-pro": False,
    "gemini-flash-latest": True,
    "gemini-flash-lite-latest": False,
}

# model -> (thinking, vision)
OLLAMA = {
    "qwen3": (True, False),
    "qwen3:8b": (True, False),
    "deepseek-r1": (True, False),
    "gpt-oss": (True, False),
    "qwen2.5vl": (False, True),
    "qwen2.5-vl:32b": (False, True),
    "qwen3vl": (True, True),
    "llava": (False, True),
    "llama3.2": (False, False),
}


class TestGeminiDetect(unittest.TestCase):
    def setUp(self):
        self.u = stub(UIMixin, provider="Gemini", model="gemini-2.5-pro")

    def test_thinking(self):
        for model, expected in GEMINI.items():
            with self.subTest(model=model):
                self.assertEqual(self.u._is_gemini_thinking_model(model), expected)


class TestOllamaDetect(unittest.TestCase):
    def setUp(self):
        self.o = stub(OllamaMixin, model="qwen3")

    def test_capabilities(self):
        for model, (thinking, vision) in OLLAMA.items():
            with self.subTest(model=model):
                self.assertEqual(self.o._is_ollama_thinking_model(model), thinking)
                self.assertEqual(self.o._is_ollama_vision_model(model), vision)


if __name__ == "__main__":
    unittest.main()
