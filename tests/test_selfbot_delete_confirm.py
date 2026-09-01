"""Characterization tests for SelfBot's GUI-only delete confirmation (2026-09-02).

SelfBot's three DELETE buttons — the toolbar's saved-chat DELETE
(`_delete_chat`), the prompt editor's (`_delete_prompt`) and the Skills
Manager's (a Tk closure inside `open_skills_editor`, verified live rather than
here) — ask an askyesno (default No) before touching disk or the shared stores.
The tool-driven deletes — `manage_prompts` / `manage_skills` `delete` — must NOT
prompt: a tool delete is the model acting on an explicit instruction, and a
dialog would block it. These tests pin both halves.

SelfBot IS importable in-process (module import builds no Tk root — `App` is
only constructed under `__main__`); the bare `App.__new__` stub from
tests/_util.py serves its methods exactly as it serves the myagent mixins. The
import is guarded so a machine without SelfBot's GUI dependencies skips
rather than errors.
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

from tests._util import stub

_saved_argv = sys.argv
sys.argv = ["SelfBot.py"]
try:
    import SelfBot
    _IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - a box without the GUI deps
    SelfBot = None
    _IMPORT_ERROR = exc
finally:
    sys.argv = _saved_argv

_needs_selfbot = unittest.skipIf(SelfBot is None, f"SelfBot not importable: {_IMPORT_ERROR}")


class _Var:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _Entry:
    def __init__(self, text=""):
        self.text = text

    def get(self):
        return self.text

    def delete(self, *_):
        self.text = ""


@_needs_selfbot
class ChatDelete(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.object(SelfBot, "CHATS_DIR", tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.json_path = os.path.join(tmp.name, SelfBot.App._sanitize_filename("My Chat"))
        self.txt_path = os.path.join(tmp.name, SelfBot.App._sanitize_filename("My Chat", ".txt"))
        for path in (self.json_path, self.txt_path):
            with open(path, "w", encoding="utf-8") as f:
                f.write("x")
        self.host = stub(SelfBot.App, _chat_combo_var=_Var("My Chat"),
                         chat_name_entry=_Entry(), root=object())
        self.host._refresh_chat_list = lambda: None

    def test_no_keeps_both_files(self):
        with mock.patch.object(SelfBot.messagebox, "askyesno", return_value=False) as ask:
            self.host._delete_chat()
        ask.assert_called_once()
        self.assertEqual(ask.call_args.kwargs.get("default"), "no")
        self.assertIs(ask.call_args.kwargs.get("parent"), self.host.root)
        self.assertTrue(os.path.exists(self.json_path))
        self.assertTrue(os.path.exists(self.txt_path))
        self.assertEqual(self.host._chat_combo_var.get(), "My Chat")

    def test_yes_removes_json_and_transcript(self):
        with mock.patch.object(SelfBot.messagebox, "askyesno", return_value=True):
            self.host._delete_chat()
        self.assertFalse(os.path.exists(self.json_path))
        self.assertFalse(os.path.exists(self.txt_path))
        self.assertEqual(self.host._chat_combo_var.get(), "")

    def test_missing_chat_warns_without_prompting(self):
        self.host._chat_combo_var.set("Ghost")
        with mock.patch.object(SelfBot.messagebox, "askyesno") as ask, \
                mock.patch.object(SelfBot.messagebox, "showwarning") as warn:
            self.host._delete_chat()
        ask.assert_not_called()
        warn.assert_called_once()
        self.assertTrue(os.path.exists(self.json_path))


def _prompt_host(selected="Persona"):
    host = stub(SelfBot.App, _prompt_combo_var=_Var(selected), _prompt_name_entry=_Entry(),
                prompt_editor_window=object(), saved=[])
    store = {"Persona": {"text": "p"}, "Default": {"text": "d"}}
    host._load_saved_prompts = lambda: dict(store)
    host._save_prompts_to_disk = lambda data: host.saved.append(dict(data))
    host._refresh_prompt_list = lambda: None
    return host


@_needs_selfbot
class PromptEditorDelete(unittest.TestCase):
    def test_no_leaves_the_store_untouched(self):
        host = _prompt_host()
        with mock.patch.object(SelfBot.messagebox, "askyesno", return_value=False) as ask:
            host._delete_prompt()
        ask.assert_called_once()
        self.assertEqual(ask.call_args.kwargs.get("default"), "no")
        self.assertIn("Persona", ask.call_args.args[1])
        self.assertEqual(host.saved, [])

    def test_yes_deletes_exactly_that_prompt(self):
        host = _prompt_host()
        with mock.patch.object(SelfBot.messagebox, "askyesno", return_value=True):
            host._delete_prompt()
        self.assertEqual(host.saved, [{"Default": {"text": "d"}}])
        self.assertEqual(host._prompt_combo_var.get(), "")


@_needs_selfbot
class ToolDeletesNeverPrompt(unittest.TestCase):
    def test_manage_prompts_delete_is_unprompted(self):
        host = _prompt_host()
        with mock.patch.object(SelfBot.messagebox, "askyesno") as ask:
            result = host.do_manage_prompts({"action": "delete", "name": "Persona"})
        ask.assert_not_called()
        self.assertEqual(result, "Deleted system prompt 'Persona'.")
        self.assertEqual(host.saved, [{"Default": {"text": "d"}}])

    def test_manage_skills_delete_is_unprompted(self):
        host = stub(SelfBot.App, skills={"tidy-up": {"content": "x", "mode": "disabled"}},
                    saved=0)
        host._save_skills = lambda: setattr(host, "saved", host.saved + 1)
        host._post_skill_ui_refresh = lambda: None
        with mock.patch.object(SelfBot.messagebox, "askyesno") as ask, \
                mock.patch.object(SelfBot, "_delete_skill_tree_entry") as remove:
            result = host.do_manage_skills({"action": "delete", "name": "tidy-up"})
        ask.assert_not_called()
        remove.assert_called_once()
        self.assertEqual(result, "Skill 'tidy-up' deleted.")
        self.assertEqual(host.skills, {})
        self.assertEqual(host.saved, 1)


if __name__ == "__main__":
    unittest.main()
