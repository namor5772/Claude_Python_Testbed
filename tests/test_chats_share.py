"""MyAgent's chat files live in the OneDrive share (2026-10-08).

Until then every run's <name>.json + <name>.txt (and the code-interpreter
outputs) went to the repo-root saved_chats/ — gitignored, so per machine —
while the instruction store, the skills tree and the cost log had moved to
<OneDrive>/MyAppShare long before. Now datapaths.resolve_chats_dir puts them
in <shared>/saved_chats through the same _ensured_shared_dir fallback as the
rest (MYAGENT_DATA_DIR override, repo root without OneDrive), constants
CHATS_DIR is what it returns, and every writer — the chat save, the periodic
autosave, the code-interpreter file saves — already went through CHATS_DIR
or now does. One folder for every machine's runs, so the transcript the cost
log's CHAT column names can be opened from any of them.

The history follows, as the stores' and the cost log's did: at launch
chat_mixin._fold_local_chats_in runs datapaths.migrate_local_chats on a
daemon thread, which moves ONLY MyAgent's chats out of the repo-root folder
— SelfBot writes the same folder and stays per machine by design (its duo
chats are the files git tracks), told apart by content: a MyAgent chat
carries agent_instruction_name at its top level, SelfBot's never does — the
.txt twin with its .json, nothing ever overwritten (same bytes → absorbed,
other content → <stem>__<machine label>, numbered), a chat touched in the
last two minutes left for the next launch, every launch repeating the pass
until nothing is left to move.
"""

import os
import queue
import re
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import myagent.chat_mixin as cm
from myagent import constants
from myagent import datapaths as dp
from tests._util import stub
from tests.test_datapaths import DatapathsCase

REPO = Path(__file__).resolve().parents[1]
OLD = 600  # seconds — comfortably past CHATS_SETTLE_SECS


def _myagent_chat(name, text="hello"):
    """The dict chat_mixin._auto_save_on_close writes (its key set)."""
    return {"messages": [{"role": "user", "content": text}], "tools": [],
            "system_prompt": "s", "agent_instruction_name": "Balance Westpac",
            "provider": "Anthropic", "model": "claude-sonnet-5", "temperature": 1.0,
            "thinking_enabled": False, "thinking_effort": "", "thinking_budget": 0,
            "thinking_mode": "off", "name": name}


def _selfbot_chat(name, text="hello"):
    """SelfBot's chat dict: a system_prompt_name, never an instruction."""
    return {"messages": [{"role": "user", "content": text}], "tools": [],
            "system_prompt": "s", "system_prompt_name": "Default",
            "metadata": {"terminal_user": "Roman"}, "preamble": "", "conclusion": "",
            "model": "claude-sonnet-5", "temperature": 1.0, "thinking_enabled": False,
            "thinking_effort": "", "thinking_budget": 0, "name": name}


# ── the resolver ─────────────────────────────────────────────────────────────

class ResolveTests(DatapathsCase):

    def test_with_a_shared_dir_the_chats_sit_in_it_and_nothing_is_created(self):
        self.assertEqual(dp.resolve_chats_dir(), str(self.shared / "saved_chats"))
        self.assertFalse((self.shared / "saved_chats").exists())  # the first save creates it

    def test_without_one_they_stay_at_the_repo_root(self):
        os.environ.pop(dp.DATA_DIR_ENV)
        orig = dp.find_onedrive_root
        dp.find_onedrive_root = lambda: None
        self.addCleanup(setattr, dp, "find_onedrive_root", orig)
        self.assertEqual(dp.resolve_chats_dir(), str(self.repo / "saved_chats"))

    def test_the_chats_sit_beside_the_instruction_store_either_way(self):
        self.assertEqual(os.path.dirname(dp.resolve_chats_dir()),
                         os.path.dirname(dp.resolve_store("agent_instructions.json")))
        os.environ.pop(dp.DATA_DIR_ENV)
        orig = dp.find_onedrive_root
        dp.find_onedrive_root = lambda: None
        self.addCleanup(setattr, dp, "find_onedrive_root", orig)
        self.assertEqual(os.path.dirname(dp.resolve_chats_dir()),
                         os.path.dirname(dp.resolve_store("agent_instructions.json")))

    def test_the_folder_name_is_the_one_it_always_had(self):
        self.assertEqual(dp.CHATS_DIRNAME, "saved_chats")


class ConstantsTests(unittest.TestCase):
    """constants.CHATS_DIR is the resolver's answer; LOCAL_CHATS_DIR the
    repo-root folder the history waits in (and SelfBot keeps)."""

    def test_chats_dir_is_the_saved_chats_folder_beside_the_instruction_store(self):
        self.assertEqual(os.path.basename(constants.CHATS_DIR), "saved_chats")
        self.assertEqual(os.path.dirname(constants.CHATS_DIR),
                         os.path.dirname(constants.INSTRUCTIONS_FILE))

    def test_local_chats_dir_is_the_repo_root_folder(self):
        self.assertEqual(constants.LOCAL_CHATS_DIR,
                         os.path.join(constants._BASE_DIR, "saved_chats"))
        self.assertEqual(Path(constants._BASE_DIR), REPO)


# ── the migration ────────────────────────────────────────────────────────────

class MigrationCase(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.local = Path(tmp.name) / "repo" / "saved_chats"
        self.shared = Path(tmp.name) / "MyAppShare" / "saved_chats"
        self.local.mkdir(parents=True)
        self.now = time.time()

    def put(self, folder, name, content, age=OLD):
        """Write a chat file (a dict → JSON, a str → text) with its mtime
        `age` seconds in the past."""
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        if isinstance(content, dict):
            import json
            path.write_text(json.dumps(content, indent=2), encoding="utf-8")
        else:
            path.write_text(content, encoding="utf-8")
        stamp = self.now - age
        os.utime(path, (stamp, stamp))
        return path

    def run_pass(self, **kw):
        kw.setdefault("label", "LAB")
        kw.setdefault("now", self.now)
        return dp.migrate_local_chats(str(self.local), str(self.shared), **kw)

    def names(self, folder):
        return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


class MigrationTests(MigrationCase):

    def test_a_myagent_chat_and_its_txt_move(self):
        self.put(self.local, "A.json", _myagent_chat("A"))
        self.put(self.local, "A.txt", "pane text")
        summary = self.run_pass()
        self.assertEqual(summary, {"moved": 2, "absorbed": 0, "waiting": 0, "errors": 0})
        self.assertEqual(self.names(self.local), [])
        self.assertEqual(self.names(self.shared), ["A.json", "A.txt"])
        self.assertEqual((self.shared / "A.txt").read_text(encoding="utf-8"), "pane text")

    def test_a_json_without_a_txt_moves_alone(self):
        self.put(self.local, "A.json", _myagent_chat("A"))
        self.assertEqual(self.run_pass()["moved"], 1)
        self.assertEqual(self.names(self.shared), ["A.json"])

    def test_selfbot_chats_ci_outputs_junk_and_an_orphan_txt_stay(self):
        self.put(self.local, "Hi mate.json", _selfbot_chat("Hi mate"))
        self.put(self.local, "Hi mate.txt", "duo transcript")
        self.put(self.local, "ci_output_20260301_120000.png", "not really a png")
        self.put(self.local, "bad.json", "{not json")
        self.put(self.local, "list.json", "[1, 2, 3]")
        self.put(self.local, "orphan.txt", "a txt whose json never landed")
        self.put(self.local, "note.md", "neither")
        before = self.names(self.local)
        summary = self.run_pass()
        self.assertEqual(summary, {"moved": 0, "absorbed": 0, "waiting": 0, "errors": 0})
        self.assertEqual(self.names(self.local), before)
        self.assertFalse(self.shared.exists())  # nothing to move → nothing created

    def test_only_the_top_level_key_counts(self):
        # A SelfBot chat whose text QUOTES the key (the model read MyAgent's
        # source) is still SelfBot's.
        self.put(self.local, "S.json", _selfbot_chat("S", text='"agent_instruction_name": "x"'))
        self.assertEqual(self.run_pass()["moved"], 0)
        self.assertEqual(self.names(self.local), ["S.json"])

    def test_a_chat_touched_recently_waits_for_the_next_launch(self):
        self.put(self.local, "A.json", _myagent_chat("A"), age=10)
        self.put(self.local, "A.txt", "pane", age=10)
        summary = self.run_pass()
        self.assertEqual(summary["waiting"], 1)
        self.assertEqual(summary["moved"], 0)
        self.assertEqual(self.names(self.local), ["A.json", "A.txt"])

    def test_a_recent_txt_holds_its_old_json_back_too(self):
        self.put(self.local, "A.json", _myagent_chat("A"))
        self.put(self.local, "A.txt", "pane", age=10)
        self.assertEqual(self.run_pass()["waiting"], 1)
        self.assertEqual(self.names(self.local), ["A.json", "A.txt"])

    def test_the_settle_window_is_two_minutes(self):
        self.assertEqual(dp.CHATS_SETTLE_SECS, 120)
        self.put(self.local, "A.json", _myagent_chat("A"), age=119)
        self.assertEqual(self.run_pass()["waiting"], 1)
        self.put(self.local, "B.json", _myagent_chat("B"), age=121)
        self.assertEqual(self.run_pass()["moved"], 1)
        self.assertEqual(self.names(self.shared), ["B.json"])

    def test_the_same_bytes_in_the_share_absorb_the_local_copy(self):
        self.put(self.local, "A.json", _myagent_chat("A"))
        self.put(self.local, "A.txt", "pane")
        self.put(self.shared, "A.json", _myagent_chat("A"))
        self.put(self.shared, "A.txt", "pane")
        summary = self.run_pass()
        self.assertEqual(summary, {"moved": 0, "absorbed": 2, "waiting": 0, "errors": 0})
        self.assertEqual(self.names(self.local), [])
        self.assertEqual(self.names(self.shared), ["A.json", "A.txt"])

    def test_other_content_in_the_share_is_never_overwritten(self):
        self.put(self.local, "A.json", _myagent_chat("A", text="laptop"))
        self.put(self.local, "A.txt", "laptop pane")
        self.put(self.shared, "A.json", _myagent_chat("A", text="desktop"))
        self.put(self.shared, "A.txt", "desktop pane")
        summary = self.run_pass()
        self.assertEqual(summary["moved"], 2)
        self.assertEqual(self.names(self.shared),
                         ["A.json", "A.txt", "A__LAB.json", "A__LAB.txt"])
        self.assertIn("desktop", (self.shared / "A.json").read_text(encoding="utf-8"))
        self.assertEqual((self.shared / "A.txt").read_text(encoding="utf-8"), "desktop pane")
        self.assertIn("laptop", (self.shared / "A__LAB.json").read_text(encoding="utf-8"))
        self.assertEqual((self.shared / "A__LAB.txt").read_text(encoding="utf-8"), "laptop pane")
        self.assertEqual(self.names(self.local), [])

    def test_a_taken_variant_numbers_from_2(self):
        self.put(self.local, "A.json", _myagent_chat("A", text="third"))
        self.put(self.shared, "A.json", _myagent_chat("A", text="first"))
        self.put(self.shared, "A__LAB.json", _myagent_chat("A", text="second"))
        self.run_pass()
        self.assertEqual(self.names(self.shared), ["A.json", "A__LAB.json", "A__LAB_2.json"])
        self.put(self.local, "A.json", _myagent_chat("A", text="fourth"))
        self.run_pass()
        self.assertEqual(self.names(self.shared),
                         ["A.json", "A__LAB.json", "A__LAB_2.json", "A__LAB_3.json"])

    def test_the_txt_follows_its_json_to_the_variant_stem(self):
        self.put(self.local, "A.json", _myagent_chat("A", text="laptop"))
        self.put(self.local, "A.txt", "laptop pane")
        self.put(self.shared, "A.json", _myagent_chat("A", text="desktop"))  # no A.txt there
        self.run_pass()
        self.assertEqual(self.names(self.shared), ["A.json", "A__LAB.json", "A__LAB.txt"])

    def test_the_label_defaults_to_this_machine(self):
        self.put(self.local, "A.json", _myagent_chat("A", text="laptop"))
        self.put(self.shared, "A.json", _myagent_chat("A", text="desktop"))
        dp.migrate_local_chats(str(self.local), str(self.shared), now=self.now)
        self.assertEqual(self.names(self.shared),
                         ["A.json", f"A__{dp.machine_label()}.json"])

    def test_the_pass_is_idempotent(self):
        self.put(self.local, "A.json", _myagent_chat("A"))
        self.put(self.local, "A.txt", "pane")
        self.put(self.local, "S.json", _selfbot_chat("S"))
        first = self.run_pass()
        second = self.run_pass()
        self.assertEqual(first["moved"], 2)
        self.assertEqual(second, {"moved": 0, "absorbed": 0, "waiting": 0, "errors": 0})
        self.assertEqual(self.names(self.local), ["S.json"])
        self.assertEqual(self.names(self.shared), ["A.json", "A.txt"])

    def test_the_same_folder_moves_nothing(self):
        self.put(self.local, "A.json", _myagent_chat("A"))
        summary = dp.migrate_local_chats(str(self.local), str(self.local), now=self.now)
        self.assertEqual(summary, {"moved": 0, "absorbed": 0, "waiting": 0, "errors": 0})
        self.assertEqual(self.names(self.local), ["A.json"])

    def test_a_missing_local_folder_is_nothing(self):
        shutil.rmtree(self.local)
        self.assertEqual(self.run_pass(), {"moved": 0, "absorbed": 0, "waiting": 0, "errors": 0})
        self.assertFalse(self.shared.exists())

    def test_a_cross_volume_move_copies_and_keeps_the_mtime(self):
        # The repo and OneDrive's File Provider volume on macOS: os.rename is
        # EXDEV, so the file is copied, its modification time carried over,
        # then deleted.
        src = self.put(self.local, "A.json", _myagent_chat("A"))
        src_mtime = os.path.getmtime(src)
        real_rename = os.rename

        def no_rename(a, b):
            raise OSError(18, "Invalid cross-device link")

        with mock.patch.object(dp.os, "rename", no_rename):
            summary = self.run_pass()
        self.assertEqual(summary["moved"], 1)
        self.assertEqual(self.names(self.local), [])
        self.assertEqual(self.names(self.shared), ["A.json"])
        self.assertAlmostEqual(os.path.getmtime(self.shared / "A.json"), src_mtime, delta=2)
        self.assertIs(os.rename, real_rename)

    def test_a_failed_copy_leaves_no_partial_file_and_the_local_one_in_place(self):
        src = self.put(self.local, "A.json", _myagent_chat("A"))
        self.put(self.local, "A.txt", "pane")

        def no_rename(a, b):
            raise OSError(18, "Invalid cross-device link")

        def broken_copy(fsrc, fdst, length=0):
            fdst.write(b"partial")
            raise OSError(28, "No space left on device")

        with mock.patch.object(dp.os, "rename", no_rename), \
             mock.patch.object(dp.shutil, "copyfileobj", broken_copy):
            summary = self.run_pass()
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["moved"], 0)
        self.assertTrue(src.exists())
        self.assertEqual(self.names(self.local), ["A.json", "A.txt"])
        self.assertEqual(self.names(self.shared), [])  # the partial copy was removed

    def test_an_error_on_one_chat_does_not_stop_the_others(self):
        self.put(self.local, "A.json", _myagent_chat("A"))
        self.put(self.local, "B.json", _myagent_chat("B"))
        real_rename = os.rename

        def rename_but_not_a(a, b):
            if os.path.basename(a) == "A.json":
                raise PermissionError(13, "locked")
            return real_rename(a, b)

        def no_copy(fsrc, fdst, length=0):
            raise PermissionError(13, "locked")

        with mock.patch.object(dp.os, "rename", rename_but_not_a), \
             mock.patch.object(dp.shutil, "copyfileobj", no_copy):
            summary = self.run_pass()
        self.assertEqual((summary["moved"], summary["errors"]), (1, 1))
        self.assertEqual(self.names(self.local), ["A.json"])
        self.assertEqual(self.names(self.shared), ["B.json"])


# ── the fold-in at launch ────────────────────────────────────────────────────

class FoldInTests(MigrationCase):
    """chat_mixin._fold_local_chats_in on a bare host: the move runs on a
    daemon thread against LOCAL_CHATS_DIR → CHATS_DIR, and the pane gets one
    Activity line when files moved."""

    def host(self, with_queue=True):
        attrs = {"queue": queue.Queue()} if with_queue else {}
        return stub(cm.ChatMixin, **attrs)

    def fold(self, host):
        with mock.patch.object(cm, "LOCAL_CHATS_DIR", str(self.local)), \
             mock.patch.object(cm, "CHATS_DIR", str(self.shared)):
            thread = host._fold_local_chats_in()
            self.assertIsNotNone(thread)
            self.assertTrue(thread.daemon)
            thread.join(10)
            self.assertFalse(thread.is_alive())
        return thread

    def test_the_chats_move_on_a_thread_and_the_pane_is_told(self):
        self.put(self.local, "A.json", _myagent_chat("A"))
        self.put(self.local, "A.txt", "pane")
        self.put(self.local, "S.json", _selfbot_chat("S"))
        host = self.host()
        self.fold(host)
        self.assertEqual(self.names(self.shared), ["A.json", "A.txt"])
        self.assertEqual(self.names(self.local), ["S.json"])
        msg = host.queue.get_nowait()
        self.assertEqual(msg["type"], "tool_info")
        self.assertIn("Moved 2 earlier chat file(s)", msg["content"])
        self.assertIn(str(self.local), msg["content"])
        self.assertIn(str(self.shared), msg["content"])
        self.assertNotIn("could not be moved", msg["content"])
        self.assertTrue(host.queue.empty())

    def test_nothing_to_move_says_nothing(self):
        self.put(self.local, "S.json", _selfbot_chat("S"))
        host = self.host()
        self.fold(host)
        self.assertTrue(host.queue.empty())
        self.assertFalse(self.shared.exists())

    def test_a_host_without_a_queue_still_moves(self):
        self.put(self.local, "A.json", _myagent_chat("A"))
        self.fold(self.host(with_queue=False))
        self.assertEqual(self.names(self.shared), ["A.json"])

    def test_errors_are_named_in_the_line(self):
        self.put(self.local, "A.json", _myagent_chat("A"))
        self.put(self.local, "B.json", _myagent_chat("B"))
        real_rename = os.rename

        def rename_but_not_a(a, b):
            if os.path.basename(a) == "A.json":
                raise PermissionError(13, "locked")
            return real_rename(a, b)

        def no_copy(fsrc, fdst, length=0):
            raise PermissionError(13, "locked")

        host = self.host()
        with mock.patch.object(dp.os, "rename", rename_but_not_a), \
             mock.patch.object(dp.shutil, "copyfileobj", no_copy):
            self.fold(host)
        msg = host.queue.get_nowait()
        self.assertIn("Moved 1 earlier chat file(s)", msg["content"])
        self.assertIn("1 could not be moved", msg["content"])

    def test_no_shared_dir_means_no_thread(self):
        # Where the chats are at the repo root as ever (LOCAL == CHATS),
        # nothing runs.
        self.put(self.local, "A.json", _myagent_chat("A"))
        host = self.host()
        with mock.patch.object(cm, "LOCAL_CHATS_DIR", str(self.local)), \
             mock.patch.object(cm, "CHATS_DIR", str(self.local)):
            self.assertIsNone(host._fold_local_chats_in())
        self.assertEqual(self.names(self.local), ["A.json"])
        self.assertTrue(host.queue.empty())

    def test_an_exception_inside_the_pass_is_swallowed(self):
        self.put(self.local, "A.json", _myagent_chat("A"))
        host = self.host()
        with mock.patch.object(cm, "migrate_local_chats", side_effect=RuntimeError("boom")):
            self.fold(host)
        self.assertTrue(host.queue.empty())


# ── wiring ───────────────────────────────────────────────────────────────────

class WiringTests(unittest.TestCase):

    def src(self, rel):
        return (REPO / rel).read_text(encoding="utf-8")

    def test_no_module_names_the_folder_by_a_relative_literal(self):
        # The code-interpreter saves used os.path.join("saved_chats", …) —
        # relative to the process's CWD, not even the repo root. The one
        # definition is datapaths.CHATS_DIRNAME; a path built from a quoted
        # "saved_chats" argument anywhere in the package is a regression.
        for path in sorted((REPO / "myagent").glob("*.py")):
            src = path.read_text(encoding="utf-8")
            with self.subTest(module=path.name):
                self.assertEqual(src.count('("saved_chats"') + src.count("('saved_chats'"), 0)
        self.assertEqual(self.src("myagent/datapaths.py").count('"saved_chats"'), 1)

    def test_constants_take_the_resolver(self):
        src = self.src("myagent/constants.py")
        self.assertIn("CHATS_DIR = resolve_chats_dir()", src)
        self.assertIn("LOCAL_CHATS_DIR = os.path.join(_BASE_DIR, CHATS_DIRNAME)", src)

    def test_every_writer_goes_through_chats_dir(self):
        chat = self.src("myagent/chat_mixin.py")
        self.assertIn("os.makedirs(CHATS_DIR, exist_ok=True)", chat)
        self.assertIn("os.path.join(CHATS_DIR, self._sanitize_filename(name, '.txt'))", chat)
        loop = self.src("myagent/event_loop_mixin.py")
        self.assertIn("from myagent.constants import CHATS_DIR", loop)
        self.assertIn("os.makedirs(CHATS_DIR, exist_ok=True)", loop)
        self.assertEqual(loop.count("os.path.join(CHATS_DIR, f\"ci_output_"), 2)
        self.assertIn("is in {CHATS_DIR}.", self.src("myagent/skills_mixin.py"))

    def test_the_app_folds_the_history_in_at_launch(self):
        src = self.src("MyAgent.py")
        init = src[src.index("def __init__("):src.index('if __name__ == "__main__"')]
        fold = init.index("self._fold_local_chats_in()")
        self.assertGreater(fold, init.index("self._run_progress_fold_in()"))
        self.assertLess(fold, init.index("self.root.after(50, self.check_queue)"))

    def test_selfbot_keeps_its_repo_root_folder(self):
        # SelfBot is per machine by design (its duo chats are the files git
        # tracks): its CHATS_DIR is untouched.
        self.assertIn(
            'CHATS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "saved_chats")',
            self.src("SelfBot.py"))
        self.assertNotIn("resolve_chats_dir", self.src("SelfBot.py"))

    def test_the_fold_in_is_not_run_at_import(self):
        # A test run imports constants; the first pass moves hundreds of
        # files, so the app — not the resolver — starts it.
        self.assertNotIn("migrate_local_chats", self.src("myagent/constants.py"))
        dp_src = self.src("myagent/datapaths.py")
        body = dp_src[dp_src.index("def resolve_chats_dir("):dp_src.index("def _is_myagent_chat(")]
        self.assertNotIn("migrate_local_chats", body)
        self.assertNotIn("Thread", body)

    def test_the_gitignore_still_covers_the_local_folder(self):
        self.assertRegex(self.src(".gitignore"), re.compile(r"^saved_chats/$", re.M))


if __name__ == "__main__":
    unittest.main()
