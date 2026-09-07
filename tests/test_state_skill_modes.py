"""The applied-instruction snapshot in agent_state.json carries the live
per-skill modes, so a relaunch comes back on the skill layout the last
Apply / Save / -l run left (2026-09-07).

From 2026-06-04 to 2026-09-06 the snapshot deliberately omitted skill_modes:
a relaunch restored every part of the applied instruction EXCEPT its skill
layout and fell back to the skills tree's GLOBAL modes. Found live with
Act_on_unread_emails (saved with 0 always-on + 5 on-demand skills): every
launch showed the tree's 2 always-on skills until the instruction was
re-applied by hand, and re-saving it changed nothing because the next launch
ignored the saved modes again.

The restore stays session-only -- _restore_skill_modes never writes to the
skills tree -- which is what makes snapshotting the modes safe.
"""
import json
import os
import queue
import tempfile
import unittest

from myagent.skills_mixin import SkillsMixin
from myagent.state_mixin import StateMixin

TOGGLES = ("desktop_enabled", "browser_enabled", "excel_enabled", "meta_enabled",
           "mcp_enabled", "google_enabled", "proton_enabled", "outlook_enabled",
           "conversational_enabled")
DISPLAY = ("show_activity", "show_thinking", "save_thinking", "debug_enabled",
           "tool_calls_enabled", "diag_enabled")

# The user's instruction layout vs the skills tree's global modes (2026-09-07).
INSTRUCTION_LAYOUT = {
    "deathbook-workbook": "on_demand",
    "email-attachment-processing": "on_demand",
    "process-payment-emails": "on_demand",
    "westpac-login": "on_demand",
    "westpac-pay-anyone": "on_demand",
    "westpac-transfer-funds": "disabled",
    "anz-login": "disabled",
}
GLOBAL_TREE = {
    "deathbook-workbook": "disabled",
    "email-attachment-processing": "disabled",
    "process-payment-emails": "disabled",
    "westpac-login": "enabled",
    "westpac-pay-anyone": "disabled",
    "westpac-transfer-funds": "enabled",
    "anz-login": "disabled",
}


def skills_from(modes):
    return {name: {"content": f"body of {name}", "mode": mode}
            for name, mode in modes.items()}


def modes_of(skills):
    return {name: sk["mode"] for name, sk in skills.items()}


class _Var:
    def __init__(self, v=None):
        self._v = v

    def get(self):
        return self._v

    def set(self, v):
        self._v = v


class _Button:
    text = None

    def config(self, **kw):
        self.text = kw.get("text", self.text)


class _Host(StateMixin, SkillsMixin):
    """A process: StateMixin's save/restore over SkillsMixin's real
    _restore_skill_modes / _update_skills_button, everything Tk- or
    disk-bound stubbed. `skills` is what _load_skills() would have returned
    from the tree at launch; `disk` is the agent_instructions.json store."""

    def __init__(self, skills, state_file, disk=None, name="Act_on_unread_emails"):
        self.skills = skills_from(skills)
        self._state_file = state_file
        self._disk = disk if disk is not None else {}
        self.disk_loads = 0
        self.store_writes = 0
        self.queue = queue.Queue()
        self.skills_button = _Button()
        self.skills_editor_window = None
        self._skills_refresh_list = None
        self.provider = "OpenAI"
        self.model = "gpt-5.6-terra"
        self.temperature = 0.0
        self.thinking_enabled = True
        self.thinking_effort = "medium"
        self.thinking_budget = 8192
        self.thinking_mode = "medium"
        self.text_verbosity = "medium"
        self.agent_instruction = "Look at the most recent email"
        self.agent_instruction_name = name
        self.pending_images = []
        self._disabled_confirm_patterns = {"gmail_send"}
        self._blocked_tools = set()
        for attr in TOGGLES:
            setattr(self, attr, _Var(False))
        for attr in DISPLAY:
            setattr(self, attr, _Var(False))
        self._update_skills_button()

    # -- disk / tree / Tk plumbing ------------------------------------------
    def _load_saved_instructions(self):
        self.disk_loads += 1
        return dict(self._disk)

    def _save_skills(self):
        self.store_writes += 1

    def _restore_model_params(self, entry, state_file=False):
        pass

    def _update_title(self):
        pass

    def _get_monitor_config_key(self):
        return "layout-key"

    def _geometries_for_save(self, existing, key):
        return existing or {}

    def _select_geometry_entry(self, state, key):
        return None

    def _apply_geometry_entry(self, entry):
        pass


class _StateFileCase(unittest.TestCase):
    def setUp(self):
        fd, self.state_file = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(self.state_file)
        self.addCleanup(lambda: os.path.exists(self.state_file) and os.remove(self.state_file))

    def read_state(self):
        with open(self.state_file, encoding="utf-8") as f:
            return json.load(f)

    def write_legacy_state(self, name="Act_on_unread_emails"):
        """A pre-2026-09-07 agent_state.json: the applied snapshot without
        skill_modes, exactly the shape the old _save_last_state wrote."""
        state = {
            "last_instruction_name": name,
            "applied_instruction": {
                "text": "Look at the most recent email",
                "images": [],
                "provider": "OpenAI",
                "model": "gpt-5.6-terra",
                "disabled_confirm_patterns": ["gmail_send"],
                "blocked_tools": [],
            },
        }
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(state, f)


class TestSnapshotCarriesSkillModes(_StateFileCase):

    def test_save_writes_the_live_modes_into_the_applied_snapshot(self):
        h = _Host(INSTRUCTION_LAYOUT, self.state_file)
        h._save_last_state()
        snap = self.read_state()["applied_instruction"]
        self.assertEqual(snap["skill_modes"], INSTRUCTION_LAYOUT)
        # The rest of the applied environment is unchanged by the addition.
        self.assertEqual(snap["disabled_confirm_patterns"], ["gmail_send"])
        self.assertEqual(snap["model"], "gpt-5.6-terra")

    def test_relaunch_restores_the_applied_layout_over_the_global_tree(self):
        # Process 1: the instruction was applied (0+5) and the state saved.
        first = _Host(INSTRUCTION_LAYOUT, self.state_file)
        self.assertEqual(first.skills_button.text, "Skills (0+5)")
        first._save_last_state()
        # Process 2: _load_skills() came back with the tree's GLOBAL modes...
        second = _Host(GLOBAL_TREE, self.state_file)
        self.assertEqual(second.skills_button.text, "Skills (2)")
        # ...and the launch restore puts the applied layout back.
        second._load_last_state()
        self.assertEqual(modes_of(second.skills), INSTRUCTION_LAYOUT)
        self.assertEqual(second.skills_button.text, "Skills (0+5)")
        self.assertEqual(second.agent_instruction_name, "Act_on_unread_emails")
        # The snapshot carried the modes: no fallback read of the 7 MB store.
        self.assertEqual(second.disk_loads, 0)

    def test_restore_is_session_only(self):
        first = _Host(INSTRUCTION_LAYOUT, self.state_file)
        first._save_last_state()
        second = _Host(GLOBAL_TREE, self.state_file)
        second._load_last_state()
        # The tree keeps the user's global modes: nothing wrote to it.
        self.assertEqual(second.store_writes, 0)
        self.assertTrue(second.queue.empty())  # every skill was in the snapshot

    def test_skill_created_since_the_snapshot_is_forced_off_with_a_warning(self):
        first = _Host(INSTRUCTION_LAYOUT, self.state_file)
        first._save_last_state()
        tree = dict(GLOBAL_TREE, **{"brand-new": "enabled"})
        second = _Host(tree, self.state_file)
        second._load_last_state()
        self.assertEqual(second.skills["brand-new"]["mode"], "disabled")
        warning = second.queue.get_nowait()
        self.assertEqual(warning["type"], "warning")
        self.assertIn("'brand-new'", warning["content"])

    def test_resave_then_relaunch_round_trips(self):
        """The user's third symptom: SAVE after an explicit load did not
        'retain' the layout, because the next launch ignored it."""
        h = _Host(INSTRUCTION_LAYOUT, self.state_file)
        h._save_last_state()  # what _save_instruction ends with
        for _ in range(3):
            h = _Host(GLOBAL_TREE, self.state_file)
            h._load_last_state()
            h._save_last_state()  # the periodic save of the new process
        self.assertEqual(modes_of(h.skills), INSTRUCTION_LAYOUT)
        self.assertEqual(self.read_state()["applied_instruction"]["skill_modes"],
                         INSTRUCTION_LAYOUT)


class TestLegacySnapshotFallback(_StateFileCase):
    """A state file written before the fix has no skill_modes in its
    snapshot: the first launch reads them once from the named instruction on
    disk (the same source an explicit Apply uses)."""

    def test_legacy_snapshot_takes_the_instruction_modes_from_disk(self):
        self.write_legacy_state()
        disk = {"Act_on_unread_emails": {"text": "Look at the most recent email",
                                         "skill_modes": INSTRUCTION_LAYOUT}}
        h = _Host(GLOBAL_TREE, self.state_file, disk=disk)
        h._load_last_state()
        self.assertEqual(h.disk_loads, 1)
        self.assertEqual(modes_of(h.skills), INSTRUCTION_LAYOUT)
        self.assertEqual(h.skills_button.text, "Skills (0+5)")
        # ...and the next save closes the gap for good.
        h._save_last_state()
        self.assertEqual(self.read_state()["applied_instruction"]["skill_modes"],
                         INSTRUCTION_LAYOUT)

    def test_legacy_snapshot_without_a_disk_entry_keeps_the_global_modes(self):
        self.write_legacy_state(name="Deleted_Instruction")
        h = _Host(GLOBAL_TREE, self.state_file, disk={})
        h._load_last_state()
        self.assertEqual(h.disk_loads, 1)
        self.assertEqual(modes_of(h.skills), GLOBAL_TREE)
        self.assertEqual(h.skills_button.text, "Skills (2)")

    def test_legacy_snapshot_with_an_unnamed_instruction_skips_the_disk(self):
        self.write_legacy_state(name="")
        h = _Host(GLOBAL_TREE, self.state_file, disk={"X": {"skill_modes": {"anz-login": "enabled"}}})
        h._load_last_state()
        self.assertEqual(h.disk_loads, 0)
        self.assertEqual(modes_of(h.skills), GLOBAL_TREE)


class TestSkillsButtonLabel(unittest.TestCase):
    """The label the user reads: always-on count, '+' the on-demand count."""

    def label_for(self, modes):
        h = _Host(modes, state_file="unused")
        return h.skills_button.text

    def test_label_shapes(self):
        self.assertEqual(self.label_for({}), "Skills")
        self.assertEqual(self.label_for({"a": "enabled", "b": "enabled"}), "Skills (2)")
        self.assertEqual(self.label_for(INSTRUCTION_LAYOUT), "Skills (0+5)")
        self.assertEqual(self.label_for({"a": "enabled", "b": "on_demand", "c": "on_demand"}),
                         "Skills (1+2)")
        self.assertEqual(self.label_for({"a": "disabled"}), "Skills")


if __name__ == "__main__":
    unittest.main()
