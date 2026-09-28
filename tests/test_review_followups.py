"""Regression tests for the follow-up fixes of 2026-09-28 — the problems the
README fact-check turned up outside the README:

- an instruction's / a system prompt's skill modes are session-only, but any
  later skill save wrote them into the shared skills tree (both apps);
- the Instruction Editor's [X] left a browsed page's environment live under
  the applied instruction (and the app's close saved that mix);
- SelfBot treated Claude Opus 5.5 as an ordinary adaptive model (an Off that
  the API refuses, no preserved-thinking binding / refusal fallbacks);
- SelfBot's command safety consulted only the first matching confirm
  pattern, and its registry block patterns matched nothing;
- SelfBot saved over an unreadable system_prompts.json;
- MCP startup blocked the Tk thread for as long as the servers took, and a
  "_"-prefixed server in mcp_servers.json was connected despite the example
  file's "delete the underscore to activate";
- refresh_launcher_icons.command skipped the TodoList aliases rebuild.sh makes.
"""

import asyncio
import json
import os
import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import SelfBot
from myagent import constants as C
from myagent import datapaths as dp
from myagent import mcp_mixin, skills_mixin
from myagent.event_loop_mixin import EventLoopMixin
from myagent.instructions_mixin import InstructionsMixin
from myagent.mcp_mixin import MCPMixin
from myagent.skills_mixin import SkillsMixin
from myagent.ui_mixin import UIMixin
from tests._util import stub
from tests.test_skills_tree import SkillsTreeCase

REPO = Path(__file__).resolve().parent.parent


class _Var:
    """A Tk variable stand-in: get / set only."""

    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def _modes_on_disk(tree):
    return {n: e["mode"] for n, e in dp.load_skills_tree(str(tree)).items()}


# ── Skill modes: session-only, never written into the tree ────────────────

class SkillModesStayOutOfTheTree(SkillsTreeCase):
    def setUp(self):
        super().setUp()
        self.write_md("alpha", "---\nname: alpha\nmode: enabled\n---\n\nA\n")
        self.write_md("beta", "---\nname: beta\nmode: disabled\n---\n\nB\n")

    def _check(self, host, module):
        with mock.patch.object(module, "SKILLS_DIR", str(self.tree)):
            host.skills = host._load_skills()
            # Session modes, as an instruction / prompt restore sets them
            host.skills["alpha"]["mode"] = "disabled"
            host.skills["beta"]["mode"] = "on_demand"
            host._save_skills()                    # any later skill save
            self.assertEqual(_modes_on_disk(self.tree),
                             {"alpha": "enabled", "beta": "disabled"})
            # An explicit change reaches the tree; the session keeps the rest
            host._set_skill_mode("beta", "enabled")
            host._save_skills()
            self.assertEqual(_modes_on_disk(self.tree),
                             {"alpha": "enabled", "beta": "enabled"})
            self.assertEqual(host.skills["alpha"]["mode"], "disabled")

    def test_myagent(self):
        self._check(stub(SkillsMixin), skills_mixin)

    def test_selfbot(self):
        self._check(stub(SelfBot.App), SelfBot)

    def test_manage_skills_mode_changes_are_explicit(self):
        host = stub(SkillsMixin, _post_skill_ui_refresh=lambda: None)
        with mock.patch.object(skills_mixin, "SKILLS_DIR", str(self.tree)):
            host.skills = host._load_skills()
            host.skills["alpha"]["mode"] = "disabled"          # a session mode
            self.assertIn("updated", host.do_manage_skills(
                {"action": "update", "name": "beta", "mode": "on_demand"}))
            self.assertIn("created", host.do_manage_skills(
                {"action": "create", "name": "gamma", "content": "G", "mode": "enabled"}))
            self.assertEqual(_modes_on_disk(self.tree),
                             {"alpha": "enabled", "beta": "on_demand", "gamma": "enabled"})

    def test_a_host_that_never_loaded_the_tree_writes_its_modes(self):
        host = stub(SkillsMixin, skills={"alpha": {"content": "A", "mode": "on_demand"}})
        with mock.patch.object(skills_mixin, "SKILLS_DIR", str(self.tree)):
            host._save_skills()
        self.assertEqual(_modes_on_disk(self.tree)["alpha"], "on_demand")


# ── Instruction Editor: [X] discards a browsed page's environment ─────────

class EditorCloseDiscardsTheBrowsedEnvironment(unittest.TestCase):
    def _host(self, **extra):
        restored = []
        attrs = dict(
            provider="Anthropic", model="claude-sonnet-5", temperature=1.0,
            thinking_enabled=True, thinking_effort="low", thinking_budget=8192,
            thinking_mode="low", text_verbosity="medium", fast_mode=False,
            skills={"alpha": {"content": "A", "mode": "enabled"}},
            _disabled_confirm_patterns={"p1"}, _blocked_tools={"run_command"},
            dictation_auto_send=_Var(False), upgrade_target=None,
            agent_instruction_name="", streaming=False,
            _restore_model_params=lambda entry, state_file=False:
                restored.append((dict(entry), state_file)),
            _update_skills_button=lambda: None,
            _update_ps_safety_button=lambda: None,
            _update_title=lambda: None,
            _load_saved_instructions=lambda: {},
        )
        attrs.update(extra)
        return stub(InstructionsMixin, **attrs), restored

    @staticmethod
    def _browse(host):
        """What a page selection does to the live environment."""
        host.model = "claude-opus-5"
        host.skills["alpha"]["mode"] = "disabled"
        host._disabled_confirm_patterns = set()
        host._blocked_tools = set()
        host.dictation_auto_send.set(True)
        host.upgrade_target = {"model": "claude-fable-5-1", "level": "Max"}
        host._editor_env_replaced = True

    def test_a_replaced_environment_comes_back(self):
        host, restored = self._host()
        host._editor_mark_env()
        self._browse(host)
        host._editor_discard_env()
        snap, state_file = restored[0]
        self.assertTrue(state_file)                 # the quiet restore path
        self.assertEqual(snap["last_model"], "claude-sonnet-5")
        self.assertEqual(snap["thinking_mode"], "low")
        self.assertEqual(host.skills["alpha"]["mode"], "enabled")
        self.assertEqual(host._disabled_confirm_patterns, {"p1"})
        self.assertEqual(host._blocked_tools, {"run_command"})
        self.assertFalse(host.dictation_auto_send.get())
        self.assertIsNone(host.upgrade_target)
        self.assertIsNone(host._editor_env)

    def test_nothing_happens_without_a_page_selection_or_clear(self):
        host, restored = self._host()
        host._editor_mark_env()
        host.model = "claude-opus-5"                # an explicit edit on the page
        host._editor_discard_env()
        self.assertEqual(restored, [])
        self.assertEqual(host.model, "claude-opus-5")

    def test_never_under_a_running_run(self):
        host, restored = self._host(streaming=True)
        host._editor_mark_env()
        self._browse(host)
        host._editor_discard_env()
        self.assertEqual(restored, [])
        self.assertEqual(host._blocked_tools, set())

    def test_written_through_settings_come_from_the_applied_entry(self):
        target = {"model": "gpt-6-astra", "level": "Max"}
        host, _ = self._host(
            agent_instruction_name="A",
            _load_saved_instructions=lambda: {
                "A": {"dictation_auto_send": True, "upgrade_target": target}})
        host._editor_mark_env()
        self._browse(host)
        host._editor_discard_env()
        self.assertTrue(host.dictation_auto_send.get())
        self.assertEqual(host.upgrade_target, target)

    def test_the_app_close_discards_before_saving_the_state(self):
        host = EventLoopMixin.__new__(EventLoopMixin)
        host.streaming = False
        calls = []
        for name in ("_editor_discard_env", "_save_last_state", "_auto_save_on_close",
                     "_cleanup_browser", "_disconnect_mcp_servers",
                     "_release_instance_lock"):
            setattr(host, name, (lambda n=name: calls.append(n)))
        host.root = mock.Mock()
        host._finish_close()
        self.assertLess(calls.index("_editor_discard_env"),
                        calls.index("_save_last_state"))


# ── SelfBot: Claude Opus 5.5 is always-on ─────────────────────────────────

class SelfBotOpus55(unittest.TestCase):
    IDS = ("claude-opus-5-5", "claude-opus-5-6", "claude-opus-6", "claude-opus-5",
           "claude-opus-5-20260724", "claude-sonnet-5", "claude-fable-5-1",
           "claude-mythos-5", "claude-opus-4-8")

    @staticmethod
    def _host(model):
        return stub(SelfBot.App, model=model, _anthropic_unsupported=set())

    def test_opus_5_5_has_no_off_and_gets_the_fable_surface(self):
        host = self._host("claude-opus-5-5")
        self.assertTrue(host._is_always_on_thinking())
        values = host._anthropic_mode_values()
        self.assertNotIn("Off", values)
        self.assertIn("Max", values)
        self.assertTrue(host._rejects_temperature())
        self.assertIsNotNone(host._fable_features())
        self.assertIn("claude-opus-5-5", SelfBot.FALLBACK_MODELS)

    def test_opus_5_and_its_dated_snapshot_keep_off(self):
        for mid in ("claude-opus-5", "claude-opus-5-20260724", "claude-sonnet-5"):
            with self.subTest(mid=mid):
                host = self._host(mid)
                self.assertFalse(host._is_always_on_thinking())
                self.assertIn("Off", host._anthropic_mode_values())
                self.assertIsNone(host._fable_features())

    def test_agrees_with_myagent(self):
        for mid in self.IDS:
            with self.subTest(mid=mid):
                myagent = stub(UIMixin, provider="Anthropic", model=mid)
                self.assertEqual(self._host(mid)._is_always_on_thinking(),
                                 myagent._is_anthropic_always_on_thinking())


# ── SelfBot: command safety matches MyAgent's ──────────────────────────────

class SelfBotCommandSafety(unittest.TestCase):
    def test_pattern_lists_match_myagent(self):
        self.assertEqual(SelfBot.COMMAND_BLOCKED, C.COMMAND_BLOCKED)
        self.assertEqual(SelfBot.COMMAND_CONFIRM, C.COMMAND_CONFIRM)

    def test_every_matching_confirm_pattern_counts(self):
        if C.IS_WINDOWS:
            cmd, first, second = "Remove-Item x -Recurse", r"\bRemove-Item\b", r"(?<!\S)-Recurse\b"
        else:
            cmd, first, second = "sudo rm x", r"\brm\b", r"\bsudo\b"
        host = stub(SelfBot.App, _disabled_confirm_patterns={first})
        self.assertEqual(host._check_command_safety(cmd), ("confirm", second))
        host._disabled_confirm_patterns = {first, second}
        self.assertEqual(host._check_command_safety(cmd)[0], "skipped")

    @unittest.skipUnless(C.IS_WINDOWS, "the Windows pattern list")
    def test_registry_deletes_and_shutdown_are_blocked(self):
        host = stub(SelfBot.App, _disabled_confirm_patterns=set())
        for cmd in (r"Remove-ItemProperty -Path HKLM:\Software\X -Name Y",
                    r"Remove-Item HKCU:\Software\X -Recurse",
                    r"reg delete HKLM\Software\X /f",
                    "shutdown /s /t 0"):
            with self.subTest(cmd=cmd):
                self.assertEqual(host._check_command_safety(cmd)[0], "blocked")
        self.assertNotEqual(host._check_command_safety("shutdown /a")[0], "blocked")


# ── SelfBot: an unreadable system_prompts.json is never saved over ────────

class SelfBotUnreadablePromptStore(SkillsTreeCase):
    BAD = "{ not json"

    def setUp(self):
        super().setUp()
        self.shared.mkdir(parents=True, exist_ok=True)
        self.path = self.shared / "sb_prompts_test.json"
        self.path.write_text(self.BAD, encoding="utf-8")
        for target, attr, value in ((SelfBot, "PROMPTS_FILE", str(self.path)),
                                    (SelfBot.messagebox, "showerror", mock.Mock())):
            patcher = mock.patch.object(target, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.queue = queue.Queue()
        self.host = stub(SelfBot.App, queue=self.queue, model="claude-sonnet-5",
                         temperature=1.0, thinking_enabled=False, thinking_effort="high",
                         thinking_budget=8192, thinking_mode="off", skills={},
                         _disabled_confirm_patterns=set())

    def test_the_stand_in_is_never_saved(self):
        prompts = self.host._load_saved_prompts()
        self.assertIsInstance(prompts, SelfBot._UnreadableStore)
        self.assertIn("Default", prompts)
        prompts["x"] = {"text": "t"}
        self.assertFalse(self.host._save_prompts_to_disk(prompts))
        self.assertEqual(self.path.read_text(encoding="utf-8"), self.BAD)
        self.assertIn("NOT saved", self.queue.get_nowait()["content"])

    def test_manage_prompts_reports_instead_of_writing(self):
        for params in ({"action": "create", "name": "x", "text": "t"},
                       {"action": "update", "name": "Default", "text": "t"}):
            with self.subTest(action=params["action"]):
                out = self.host.do_manage_prompts(params)
                self.assertTrue(out.startswith("Error: the system prompt store could not be read"), out)
        self.assertEqual(self.path.read_text(encoding="utf-8"), self.BAD)

    def test_a_readable_store_still_saves(self):
        self.path.write_text(json.dumps({"Default": {"text": "d"}}), encoding="utf-8")
        prompts = self.host._load_saved_prompts()
        self.assertNotIsInstance(prompts, SelfBot._UnreadableStore)
        prompts["x"] = {"text": "t"}
        self.assertTrue(self.host._save_prompts_to_disk(prompts))
        self.assertIn("x", json.loads(self.path.read_text(encoding="utf-8")))


# ── MCP: disabled "_" servers; a startup that never blocks the Tk thread ──

class McpStartup(unittest.TestCase):
    def test_underscore_servers_are_disabled(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "mcp_servers.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"_README": "x", "servers": {
                    "filesystem": {"command": "npx"},
                    "_example_with_secret_from_shell_env": {"command": "npx"}}}, f)
            with mock.patch.object(mcp_mixin, "MCP_SERVERS_PATH", path):
                self.assertEqual(list(stub(MCPMixin)._load_mcp_config()), ["filesystem"])

    def test_connect_returns_at_once_and_a_run_waits_on_its_worker(self):
        logged = []
        host = stub(MCPMixin, queue=queue.Queue(), root=None, stop_requested=False,
                    mcp_enabled=_Var(True), _mcp_log=logged.append)
        host._init_mcp_state()

        async def slow_runner(config):
            host._mcp_shutdown_event = asyncio.Event()
            await asyncio.sleep(0.6)            # servers still connecting
            host._mcp_ready_event.set()
            await host._mcp_shutdown_event.wait()

        host._mcp_runner = slow_runner
        host._load_mcp_config = lambda: {"srv": {"command": "x"}}
        with mock.patch.object(mcp_mixin, "_HAS_MCP", True):
            started = time.monotonic()
            host._connect_mcp_servers()
            self.addCleanup(host._disconnect_mcp_servers)
            self.assertLess(time.monotonic() - started, 0.4)
            self.assertFalse(host._mcp_ready_event.is_set())
            host._mcp_wait_ready()              # the run's worker waits instead
            self.assertTrue(host._mcp_ready_event.is_set())
        self.assertTrue(any("Waiting for the MCP servers" in m for m in logged))

    def test_the_wait_is_skipped_when_mcp_is_off_and_ended_by_stop(self):
        host = stub(MCPMixin, _mcp_ready_event=threading.Event(), mcp_enabled=_Var(False),
                    stop_requested=False, _mcp_startup_began=time.monotonic(),
                    _mcp_log=lambda message: None)
        with mock.patch.object(mcp_mixin, "_HAS_MCP", True):
            started = time.monotonic()
            host._mcp_wait_ready()
            self.assertLess(time.monotonic() - started, 0.1)
            host.mcp_enabled = _Var(True)
            host.stop_requested = True
            started = time.monotonic()
            host._mcp_wait_ready()
            self.assertLess(time.monotonic() - started, 1.0)


# ── macOS launchers: the icon refresher covers every alias rebuild.sh makes ──

class LauncherIconPairs(unittest.TestCase):
    @staticmethod
    def _pairs(name):
        text = (REPO / "desktop_launchers" / name).read_text(encoding="utf-8")
        block = text.split("<<'PAIRS'\n", 1)[1].split("\nPAIRS", 1)[0]
        return {line.strip() for line in block.splitlines() if line.strip()}

    def test_refresh_covers_every_rebuild_alias(self):
        missing = self._pairs("rebuild.sh") - self._pairs("refresh_launcher_icons.command")
        self.assertEqual(missing, set())


if __name__ == "__main__":
    unittest.main()
