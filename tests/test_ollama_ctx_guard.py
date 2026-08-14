"""Characterization test: the Ollama small-ctx warm-trap guard (OllamaMixin).

Locks the 2026-08-14 fix for the live incident where a muse-glimmer instance
warmed at 8K ctx (by a probe's keep_alive re-warm) served a whole MyAgent run
requesting 32768 — Ollama 0.32.x never reloads for a bigger requested num_ctx,
so --context-shift silently shed the system prompt and the run took 2,663s vs
an 802-1,561s baseline. Guard contract:

- _ollama_loaded_context reads /api/ps (dict- or object-shaped) and returns
  the loaded instance's context_length, or None for not-loaded / field
  missing / query failed / no client.
- _ollama_ctx_guard pokes generate(keep_alive=0) ONLY when the loaded context
  is smaller than the run's num_ctx, posts an always-shown "warning", and is
  best-effort (a failing poke or ps never raises). Equal or LARGER loaded
  contexts are left alone.
- _get_ollama_model_caps caches only SUCCESSFUL show() lookups — a transient
  failure used to pin empty caps (→ no num_ctx sent → server-default small
  load, pinned by keep_alive) for the whole session.
"""
import unittest
from types import SimpleNamespace

from tests._util import stub
from myagent.ollama_mixin import OllamaMixin


class FakeQueue:
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)


class FakeClient:
    def __init__(self, ps_models=None, ps_raises=False,
                 show_responses=None):
        self._ps_models = ps_models or []
        self._ps_raises = ps_raises
        # show_responses: list popped per call — an Exception instance raises.
        self._show_responses = list(show_responses or [])
        self.ps_calls = 0
        self.show_calls = 0
        self.generate_calls = []

    def ps(self):
        self.ps_calls += 1
        if self._ps_raises:
            raise ConnectionError("server down")
        return {"models": self._ps_models}

    def show(self, model):
        self.show_calls += 1
        resp = self._show_responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp

    def generate(self, **kwargs):
        self.generate_calls.append(kwargs)


def host(client, model="muse-glimmer:30b-mlx"):
    return stub(OllamaMixin, model=model, ollama_client=client,
                queue=FakeQueue())


class TestLoadedContext(unittest.TestCase):
    def test_dict_shaped_ps(self):
        c = FakeClient(ps_models=[
            {"model": "qwen3:32b-q4_K_M", "context_length": 4096},
            {"model": "muse-glimmer:30b-mlx", "context_length": 8192},
        ])
        self.assertEqual(host(c)._ollama_loaded_context(), 8192)

    def test_object_shaped_ps(self):
        c = FakeClient(ps_models=[
            SimpleNamespace(model="muse-glimmer:30b-mlx", name=None,
                            context_length=32768),
        ])
        self.assertEqual(host(c)._ollama_loaded_context(), 32768)

    def test_not_loaded(self):
        c = FakeClient(ps_models=[{"model": "other", "context_length": 8192}])
        self.assertIsNone(host(c)._ollama_loaded_context())

    def test_field_missing(self):
        c = FakeClient(ps_models=[{"model": "muse-glimmer:30b-mlx"}])
        self.assertIsNone(host(c)._ollama_loaded_context())

    def test_ps_failure(self):
        c = FakeClient(ps_raises=True)
        self.assertIsNone(host(c)._ollama_loaded_context())

    def test_no_client(self):
        h = stub(OllamaMixin, model="m", ollama_client=None, queue=FakeQueue())
        self.assertIsNone(h._ollama_loaded_context())


class TestCtxGuard(unittest.TestCase):
    def _guarded_host(self, loaded_ctx):
        models = [] if loaded_ctx is None else [
            {"model": "muse-glimmer:30b-mlx", "context_length": loaded_ctx}]
        c = FakeClient(ps_models=models)
        return host(c), c

    def test_smaller_instance_dropped_with_warning(self):
        h, c = self._guarded_host(8192)
        h._ollama_ctx_guard(32768)
        self.assertEqual(len(c.generate_calls), 1)
        call = c.generate_calls[0]
        self.assertEqual(call["model"], "muse-glimmer:30b-mlx")
        self.assertEqual(call["keep_alive"], 0)
        self.assertEqual(len(h.queue.items), 1)
        msg = h.queue.items[0]
        self.assertEqual(msg["type"], "warning")
        self.assertIn("8192", msg["content"])
        self.assertIn("32768", msg["content"])

    def test_equal_instance_untouched(self):
        h, c = self._guarded_host(32768)
        h._ollama_ctx_guard(32768)
        self.assertEqual(c.generate_calls, [])
        self.assertEqual(h.queue.items, [])

    def test_larger_instance_untouched(self):
        h, c = self._guarded_host(65536)
        h._ollama_ctx_guard(32768)
        self.assertEqual(c.generate_calls, [])
        self.assertEqual(h.queue.items, [])

    def test_not_loaded_no_poke(self):
        h, c = self._guarded_host(None)
        h._ollama_ctx_guard(32768)
        self.assertEqual(c.generate_calls, [])

    def test_ps_failure_no_poke(self):
        c = FakeClient(ps_raises=True)
        h = host(c)
        h._ollama_ctx_guard(32768)
        self.assertEqual(c.generate_calls, [])

    def test_failed_poke_never_raises(self):
        c = FakeClient(ps_models=[
            {"model": "muse-glimmer:30b-mlx", "context_length": 8192}])
        c.generate = lambda **kw: (_ for _ in ()).throw(ConnectionError("down"))
        h = host(c)
        h._ollama_ctx_guard(32768)  # must not raise
        self.assertEqual(h.queue.items, [])  # warning only after a real drop


class TestCapsCaching(unittest.TestCase):
    SHOW_OK = {"capabilities": ["completion", "tools", "thinking"],
               "modelinfo": {"general.architecture": "qwen3",
                             "qwen3.context_length": 40960}}

    def test_success_is_cached(self):
        c = FakeClient(show_responses=[self.SHOW_OK])
        h = host(c, model="qwen3:32b-q4_K_M")
        first = h._get_ollama_model_caps()
        second = h._get_ollama_model_caps()
        self.assertEqual(c.show_calls, 1)
        self.assertEqual(first["context_length"], 40960)
        self.assertEqual(first["capabilities"],
                         {"completion", "tools", "thinking"})
        self.assertIs(first, second)

    def test_failure_not_cached_and_recovers(self):
        c = FakeClient(show_responses=[ConnectionError("blip"), self.SHOW_OK])
        h = host(c, model="qwen3:32b-q4_K_M")
        first = h._get_ollama_model_caps()
        self.assertIsNone(first["context_length"])
        self.assertEqual(first["capabilities"], set())
        second = h._get_ollama_model_caps()  # re-queries instead of serving the failure
        self.assertEqual(c.show_calls, 2)
        self.assertEqual(second["context_length"], 40960)


if __name__ == "__main__":
    unittest.main()
