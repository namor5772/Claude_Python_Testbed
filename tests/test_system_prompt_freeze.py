"""The system prompt is fixed for the length of a run (2026-09-29).

Both apps composed the system prompt before EVERY API call, from the live
skills — so a skill changed mid-run (a manage_skills update, or the Skills
Manager) changed the prompt between two calls of one run. The system prompt
heads the cached prefix (tools → system → messages): the next call re-wrote
the whole prompt cache, and on the models that bind thinking blocks to that
prefix (Fable 5.1, Mythos 5.1, Opus 5.5) the API dropped every earlier
thinking block (prefix_binding_mismatch). Found on a MyAgent JustChat run
upgraded to Fable 5.1: a manage_skills update of an on-demand skill's
description made the next call write 50,976 tokens to the cache — $0.64 of a
$0.66 call — and drop 3 thinking blocks.

Now stream_worker freezes the prompt as it starts and lets go of it as it
ends — MyAgent's run (START to the end of the loop, Convo mode included) and
SelfBot's reply — and _build_system_prompt returns the frozen text meanwhile.
SelfBot's four methods are byte-identical copies of SkillsMixin's.
"""

import inspect
import queue
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import SelfBot
from myagent.helpers import _ToolBlock
from myagent.skills_mixin import SkillsMixin
from tests._util import stub
from tests.test_costlog_run_fields import _Host, _Var
from tests.test_fast_mode import _FakeStream, _WireHost, _drain

ROOT = Path(__file__).resolve().parent.parent
METHODS = ("_build_system_prompt", "_compose_system_prompt",
           "_freeze_system_prompt", "_thaw_system_prompt")


def _skills():
    return {
        "physical-interface": {"mode": "on_demand", "description": "Record audio.",
                               "content": "# Recording"},
        "house-style": {"mode": "enabled", "description": "", "content": "Be brief."},
        "unused": {"mode": "disabled", "description": "", "content": "x"},
    }


def _edit_skills(host):
    """What a run's manage_skills calls do to the live skills: a new
    description on an on-demand skill (the case that was found), new text in
    an always-on one, and a skill created."""
    host.skills["physical-interface"]["description"] = "Record audio to OneDrive."
    host.skills["house-style"]["content"] = "Be very brief."
    host.skills["new-skill"] = {"mode": "on_demand", "description": "New.", "content": "y"}


class BuilderTests(unittest.TestCase):

    def host(self):
        return stub(SkillsMixin, system_prompt="You are an agent.", skills=_skills())

    def test_outside_a_run_the_prompt_follows_the_live_skills(self):
        h = self.host()
        self.assertIn("Record audio.", h._build_system_prompt())
        _edit_skills(h)
        prompt = h._build_system_prompt()
        self.assertIn("Record audio to OneDrive.", prompt)
        self.assertIn("Be very brief.", prompt)
        self.assertIn("new-skill", prompt)

    def test_a_frozen_prompt_ignores_every_change(self):
        h = self.host()
        h._freeze_system_prompt()
        frozen = h._build_system_prompt()
        _edit_skills(h)
        h.skills["unused"]["mode"] = "enabled"
        h.system_prompt = "You are someone else."
        self.assertEqual(h._build_system_prompt(), frozen)

    def test_thawing_returns_to_the_live_skills(self):
        h = self.host()
        h._freeze_system_prompt()
        _edit_skills(h)
        h._thaw_system_prompt()
        self.assertIsNone(h._run_system_prompt)
        self.assertEqual(h._build_system_prompt(), h._compose_system_prompt())
        self.assertIn("Record audio to OneDrive.", h._build_system_prompt())

    def test_an_empty_prompt_is_frozen_too(self):
        h = stub(SkillsMixin, system_prompt="", skills={})
        h._freeze_system_prompt()
        h.skills["late"] = {"mode": "enabled", "content": "z"}
        self.assertEqual(h._build_system_prompt(), "")


class _PromptHost(_Host, SkillsMixin):
    """test_costlog_run_fields' two-call stream_worker host, composing its
    system prompt from real skills: each call records the prompt it would
    send, and the tool call between the two edits the skills the way a
    manage_skills update does."""

    def __init__(self, second_call="end"):
        super().__init__(second_call=second_call)
        self.system_prompt = "You are an agent."
        self.skills = _skills()
        self.prompts_sent = []

    def _stream_anthropic_call(self, messages, max_retries, label_emitted):
        self.prompts_sent.append(self._build_system_prompt())
        return super()._stream_anthropic_call(messages, max_retries, label_emitted)

    def _execute_tool(self, block):
        _edit_skills(self)
        return super()._execute_tool(block)


class StreamWorkerTests(unittest.TestCase):

    def run_host(self, host):
        with mock.patch.object(host, "_log_api_cost"):
            host.stream_worker([{"role": "user", "content": "go"}])
        return host

    def test_every_call_of_a_run_sends_the_prompt_it_started_with(self):
        host = self.run_host(_PromptHost())
        self.assertEqual(len(host.prompts_sent), 2)
        self.assertEqual(host.prompts_sent[1], host.prompts_sent[0])
        self.assertIn("Record audio.", host.prompts_sent[1])
        self.assertNotIn("new-skill", host.prompts_sent[1])

    def test_the_change_reaches_the_prompt_once_the_run_ends(self):
        host = self.run_host(_PromptHost())
        self.assertIsNone(host._run_system_prompt)
        self.assertIn("Record audio to OneDrive.", host._build_system_prompt())

    def test_the_next_run_starts_from_the_edited_skills(self):
        host = self.run_host(_PromptHost())
        host._calls_made, host.prompts_sent = 0, []
        self.run_host(host)
        self.assertIn("Record audio to OneDrive.", host.prompts_sent[0])
        self.assertIn("new-skill", host.prompts_sent[0])

    def test_a_failed_run_lets_go_too(self):
        host = self.run_host(_PromptHost(second_call="raise"))
        self.assertTrue([m for m in _drain(host.queue) if m["type"] == "error"])
        self.assertEqual(host.prompts_sent[1], host.prompts_sent[0])
        self.assertIsNone(host._run_system_prompt)


class _WireSkillsHost(SkillsMixin, _WireHost):
    """test_fast_mode's wire host — the REAL _stream_anthropic_call over a
    fake client — with SkillsMixin's prompt builder instead of its stub."""


class WireTests(unittest.TestCase):

    def test_the_request_carries_the_frozen_prompt(self):
        host = _WireSkillsHost(fast=False)
        host.system_prompt, host.skills = "You are an agent.", _skills()
        host._freeze_system_prompt()
        frozen = host._build_system_prompt()
        _edit_skills(host)
        host._stream_anthropic_call([{"role": "user", "content": "hi"}], 3, False)
        _betas, kwargs = host.calls[0]
        self.assertEqual(kwargs["system"][0]["text"], frozen)


def _final(stop_reason, content):
    usage = SimpleNamespace(input_tokens=10, output_tokens=5,
                            cache_creation_input_tokens=0, cache_read_input_tokens=0,
                            iterations=None)
    return SimpleNamespace(content=content, stop_reason=stop_reason,
                           model="claude-sonnet-5", usage=usage,
                           input_transformations=None)


def _selfbot_app(second="end"):
    """A bare SelfBot App (no Tk) whose client plays a two-call reply — a
    manage_skills tool call, then the end of the turn (or an error) — and
    records the `system` of every request."""
    app = SelfBot.App.__new__(SelfBot.App)
    app.queue = queue.Queue()
    app._temp_var = _Var(1.0)
    app.temperature = 1.0
    app.model = "claude-sonnet-5"
    app.thinking_enabled = False
    app.thinking_mode, app.thinking_effort, app.thinking_budget = "off", "high", 8192
    app._no_temperature, app._anthropic_unsupported = set(), set()
    app._session_calls, app._session_cost = 0, 0.0
    app._session_tokens_in = app._session_tokens_out = 0
    app._session_tokens_cache_write = app._session_tokens_cache_read = 0
    app.system_prompt, app.skills, app.messages = "You are Shaun.", _skills(), []
    app._mcp_wait_ready = lambda: None
    app._payload_for_display = lambda messages: ""
    app._get_tools = lambda: []

    def execute(block):
        _edit_skills(app)
        return "ok"

    app._execute_tool = execute
    app.systems_sent = []
    steps = [_final("tool_use", [_ToolBlock("manage_skills", "toolu_1", {"action": "update"})]),
             RuntimeError("boom on call 2") if second == "raise" else _final("end_turn", [])]

    class _Messages:
        @staticmethod
        def stream(betas, **api_kwargs):
            app.systems_sent.append(api_kwargs["system"])
            step = steps.pop(0)
            if isinstance(step, Exception):
                raise step
            return _FakeStream(step)

    app.client = SimpleNamespace(beta=SimpleNamespace(messages=_Messages))
    return app


class SelfBotTests(unittest.TestCase):

    def test_the_four_methods_are_byte_identical_to_skills_mixin(self):
        for name in METHODS:
            with self.subTest(method=name):
                self.assertEqual(inspect.getsource(getattr(SelfBot.App, name)),
                                 inspect.getsource(getattr(SkillsMixin, name)))

    def test_every_call_of_a_reply_sends_the_prompt_it_started_with(self):
        app = _selfbot_app()
        app.stream_worker([{"role": "user", "content": "update the skill"}])
        kinds = [m["type"] for m in _drain(app.queue)]
        self.assertNotIn("error", kinds)
        self.assertIn("complete", kinds)
        self.assertEqual(len(app.systems_sent), 2)
        self.assertEqual(app.systems_sent[1], app.systems_sent[0])
        self.assertIn("Record audio.", app.systems_sent[1][0]["text"])
        # ...and the next message's reply starts from the edited skills.
        self.assertIsNone(app._run_system_prompt)
        self.assertIn("Record audio to OneDrive.", app._build_system_prompt())

    def test_a_failed_reply_lets_go_too(self):
        app = _selfbot_app(second="raise")
        app.stream_worker([{"role": "user", "content": "update the skill"}])
        self.assertIn("error", [m["type"] for m in _drain(app.queue)])
        self.assertEqual(app.systems_sent[1], app.systems_sent[0])
        self.assertIsNone(app._run_system_prompt)


class NoCallSiteBypassesTheFreeze(unittest.TestCase):
    """Only the freeze and the builder itself may compose the prompt; every
    request builder and Debug dump goes through _build_system_prompt."""

    def test_compose_is_called_only_by_the_two_helpers(self):
        for path in [*sorted((ROOT / "myagent").glob("*.py")), ROOT / "SelfBot.py"]:
            expected = 2 if path.name in ("skills_mixin.py", "SelfBot.py") else 0
            with self.subTest(file=path.name):
                self.assertEqual(path.read_text(encoding="utf-8")
                                 .count("self._compose_system_prompt()"), expected)


if __name__ == "__main__":
    unittest.main()
