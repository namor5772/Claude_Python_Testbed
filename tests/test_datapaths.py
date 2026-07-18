"""Characterization tests for myagent/datapaths.py — shared-store resolution,
atomic saves, key-level union, the one-shot repo→shared migration, and
OneDrive conflict-fork absorption behind agent_instructions.json /
skills.json / system_prompts.json.

The resolver honours the MYAGENT_DATA_DIR override, so every test points it
at a TemporaryDirectory. The module's repo-root constant and the one-shot
migration latch are repointed/cleared per test — the same repoint-the-global
pattern as the Heartbeat log-rotation tests."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from myagent import datapaths as dp


class DatapathsCase(unittest.TestCase):
    def setUp(self):
        shared_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(shared_tmp.cleanup)
        self.shared = Path(shared_tmp.name) / "SharedStore"

        repo_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(repo_tmp.cleanup)
        self.repo = Path(repo_tmp.name)  # stand-in for the repo root

        os.environ[dp.DATA_DIR_ENV] = str(self.shared)
        self.addCleanup(os.environ.pop, dp.DATA_DIR_ENV, None)

        orig_base = dp._BASE_DIR
        dp._BASE_DIR = str(self.repo)
        self.addCleanup(setattr, dp, "_BASE_DIR", orig_base)

        dp._migrated.clear()
        self.addCleanup(dp._migrated.clear)

    # ── helpers ──────────────────────────────────────────────────────────

    def write(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")

    def read(self, path):
        return json.loads(path.read_text(encoding="utf-8"))


class ResolveTests(DatapathsCase):
    def test_env_override_wins_and_creates_dir(self):
        path = dp.resolve_store("skills.json")
        self.assertEqual(path, str(self.shared / "skills.json"))
        self.assertTrue(self.shared.is_dir())

    def test_no_shared_dir_falls_back_to_repo_root(self):
        os.environ.pop(dp.DATA_DIR_ENV)
        orig = dp.find_onedrive_root
        dp.find_onedrive_root = lambda: None
        self.addCleanup(setattr, dp, "find_onedrive_root", orig)
        self.assertEqual(dp.resolve_store("skills.json"),
                         str(self.repo / "skills.json"))

    def test_onedrive_discovery_used_without_override(self):
        os.environ.pop(dp.DATA_DIR_ENV)
        fake_root = self.shared / "FakeOneDrive"
        orig = dp.find_onedrive_root
        dp.find_onedrive_root = lambda: str(fake_root)
        self.addCleanup(setattr, dp, "find_onedrive_root", orig)
        self.assertEqual(dp.resolve_store("skills.json"),
                         str(fake_root / dp.SHARED_SUBDIR / "skills.json"))

    def test_is_shared(self):
        self.assertTrue(dp.is_shared(str(self.shared / "skills.json")))
        self.assertFalse(dp.is_shared(str(self.repo / "skills.json")))

    def test_machine_label_is_filesystem_safe(self):
        label = dp.machine_label()
        self.assertTrue(label)
        self.assertNotRegex(label, r"[^A-Za-z0-9._-]")


class LegacyDirAdoptionTests(DatapathsCase):
    """The 2026-07-19 MyAgent → MyAppShare rename: _shared_dir adopts a
    legacy-named dir by renaming it in place, before any makedirs can strand
    the data behind an empty new-name dir."""

    def setUp(self):
        super().setUp()
        os.environ.pop(dp.DATA_DIR_ENV)  # exercise the OneDrive discovery path
        self.fake_root = self.shared.parent / "FakeOneDrive"
        self.fake_root.mkdir(parents=True)
        orig = dp.find_onedrive_root
        dp.find_onedrive_root = lambda: str(self.fake_root)
        self.addCleanup(setattr, dp, "find_onedrive_root", orig)
        self.legacy = self.fake_root / "MyAgent"
        self.new = self.fake_root / dp.SHARED_SUBDIR

    def test_legacy_dir_adopted_by_rename(self):
        self.legacy.mkdir()
        (self.legacy / "skills.json").write_text('{"a": 1}', encoding="utf-8")
        path = dp.resolve_store("skills.json")
        self.assertEqual(path, str(self.new / "skills.json"))
        self.assertFalse(self.legacy.exists())
        self.assertEqual(json.loads((self.new / "skills.json").read_text(encoding="utf-8")),
                         {"a": 1})

    def test_both_exist_colliding_file_stays_shared_wins(self):
        self.legacy.mkdir()
        (self.legacy / "skills.json").write_text('{"old": 1}', encoding="utf-8")
        self.new.mkdir()
        (self.new / "skills.json").write_text('{"new": 1}', encoding="utf-8")
        path = dp.resolve_store("skills.json")
        self.assertEqual(path, str(self.new / "skills.json"))
        self.assertEqual(json.loads((self.new / "skills.json").read_text(encoding="utf-8")),
                         {"new": 1})  # the live store is untouched
        # colliding legacy file stays put for manual review (dir survives)
        self.assertTrue((self.legacy / "skills.json").exists())

    def test_both_exist_noncolliding_files_fold_in_and_legacy_dir_removed(self):
        self.legacy.mkdir()
        (self.legacy / "system_prompts.json").write_text('{"p": 1}', encoding="utf-8")
        self.new.mkdir()
        (self.new / "skills.json").write_text('{"new": 1}', encoding="utf-8")
        dp.resolve_store("skills.json")
        self.assertEqual(json.loads((self.new / "system_prompts.json").read_text(encoding="utf-8")),
                         {"p": 1})
        self.assertFalse(self.legacy.exists())  # emptied and removed

    def test_failed_rename_falls_back_to_legacy_dir(self):
        self.legacy.mkdir()
        (self.legacy / "skills.json").write_text('{"a": 1}', encoding="utf-8")
        with mock.patch("myagent.datapaths.os.rename", side_effect=OSError("locked")):
            path = dp.resolve_store("skills.json")
        # Keeps serving the legacy data rather than an empty new-name store;
        # the rename is retried at the next launch.
        self.assertEqual(path, str(self.legacy / "skills.json"))
        self.assertTrue((self.legacy / "skills.json").exists())
        self.assertFalse(self.new.exists())


class UnionTests(DatapathsCase):
    def test_new_keys_adopted(self):
        primary = {"a": 1}
        self.assertTrue(dp.union_stores(primary, {"b": 2}, "m"))
        self.assertEqual(primary, {"a": 1, "b": 2})

    def test_identical_entries_dedupe(self):
        primary = {"a": {"text": "x"}}
        self.assertFalse(dp.union_stores(primary, {"a": {"text": "x"}}, "m"))
        self.assertEqual(primary, {"a": {"text": "x"}})

    def test_conflict_preserved_as_labelled_variant(self):
        primary = {"a": {"text": "mine"}}
        self.assertTrue(dp.union_stores(primary, {"a": {"text": "theirs"}}, "PC"))
        self.assertEqual(primary["a"], {"text": "mine"})
        self.assertEqual(primary["a__PC"], {"text": "theirs"})

    def test_reunion_is_idempotent(self):
        primary = {"a": {"text": "mine"}, "a__PC": {"text": "theirs"}}
        self.assertFalse(dp.union_stores(primary, {"a": {"text": "theirs"}}, "PC"))
        self.assertEqual(len(primary), 2)

    def test_variant_numbering_when_label_slot_holds_other_content(self):
        primary = {"a": 1, "a__PC": 2}
        self.assertTrue(dp.union_stores(primary, {"a": 3}, "PC"))
        self.assertEqual(primary["a__PC2"], 3)


class SaveLoadTests(DatapathsCase):
    def test_save_roundtrip_and_unicode_unescaped(self):
        path = self.shared / "skills.json"
        dp.save_store(str(path), {"café": {"text": "über"}})
        self.assertEqual(self.read(path), {"café": {"text": "über"}})
        self.assertIn("café", path.read_text(encoding="utf-8"))  # ensure_ascii=False

    def test_save_recreates_missing_parent_dir(self):
        path = self.shared / "skills.json"
        dp.save_store(str(path), {"a": 1})  # creates dir
        os.remove(path)
        os.rmdir(self.shared)  # simulate OneDrive GC of the empty dir
        dp.save_store(str(path), {"a": 2})
        self.assertEqual(self.read(path), {"a": 2})

    def test_save_leaves_no_tmp_files(self):
        path = self.shared / "skills.json"
        dp.save_store(str(path), {"a": 1})
        self.assertEqual([p.name for p in self.shared.iterdir()], ["skills.json"])

    def test_load_missing_corrupt_and_nondict(self):
        path = self.shared / "skills.json"
        self.assertEqual(dp.load_store(str(path)), {})
        self.shared.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        self.assertEqual(dp.load_store(str(path)), {})
        path.write_text("[1, 2]", encoding="utf-8")
        self.assertEqual(dp.load_store(str(path)), {})


class MigrationTests(DatapathsCase):
    def test_seed_when_shared_slot_empty(self):
        local = self.repo / "skills.json"
        self.write(local, {"a": {"text": "x"}})
        shared = self.shared / "skills.json"
        data = dp.load_store(str(shared))
        self.assertEqual(data, {"a": {"text": "x"}})
        self.assertEqual(self.read(shared), {"a": {"text": "x"}})
        self.assertFalse(local.exists())
        self.assertTrue(local.with_name("skills.json.migrated.bak").exists())

    def test_union_when_both_exist(self):
        self.write(self.repo / "skills.json",
                   {"a": {"text": "local"}, "b": {"text": "only-local"}})
        shared = self.shared / "skills.json"
        self.write(shared, {"a": {"text": "shared"}})
        data = dp.load_store(str(shared))
        self.assertEqual(data["a"], {"text": "shared"})  # shared wins its name
        self.assertEqual(data["b"], {"text": "only-local"})
        self.assertEqual(data[f"a__{dp.machine_label()}"], {"text": "local"})
        self.assertEqual(self.read(shared), data)  # merge was persisted
        self.assertFalse((self.repo / "skills.json").exists())

    def test_unreadable_shared_file_is_never_overwritten(self):
        local = self.repo / "skills.json"
        self.write(local, {"a": 1})
        shared = self.shared / "skills.json"
        self.shared.mkdir(parents=True, exist_ok=True)
        shared.write_text("{half-synced", encoding="utf-8")
        self.assertEqual(dp.load_store(str(shared)), {})
        self.assertEqual(shared.read_text(encoding="utf-8"), "{half-synced")
        self.assertTrue(local.exists())  # not renamed — next launch retries

    def test_migration_runs_once_per_process(self):
        shared = self.shared / "skills.json"
        dp.load_store(str(shared))  # latches with no local file present
        self.write(self.repo / "skills.json", {"late": 1})
        self.assertEqual(dp.load_store(str(shared)), {})  # not re-migrated

    def test_repo_root_store_never_migrates(self):
        local = self.repo / "skills.json"
        self.write(local, {"a": 1})
        self.assertEqual(dp.load_store(str(local)), {"a": 1})
        self.assertTrue(local.exists())  # untouched — solo-machine fallback


class AbsorbTests(DatapathsCase):
    def test_fork_merged_and_deleted(self):
        path = self.shared / "skills.json"
        self.write(path, {"a": {"text": "main"}})
        fork = self.shared / "skills-DESKTOP-FOO.json"
        self.write(fork, {"a": {"text": "forked"}, "b": {"text": "new"}})
        data = self.read(path)
        self.assertTrue(dp.absorb_conflict_forks(str(path), data))
        self.assertEqual(data["a"], {"text": "main"})
        self.assertEqual(data["b"], {"text": "new"})
        self.assertEqual(data["a__DESKTOP-FOO"], {"text": "forked"})
        self.assertFalse(fork.exists())

    def test_identical_fork_deleted_without_change(self):
        path = self.shared / "skills.json"
        self.write(path, {"a": 1})
        fork = self.shared / "skills-PC.json"
        self.write(fork, {"a": 1})
        data = self.read(path)
        self.assertFalse(dp.absorb_conflict_forks(str(path), data))
        self.assertFalse(fork.exists())

    def test_unreadable_fork_kept_for_retry(self):
        path = self.shared / "skills.json"
        self.write(path, {"a": 1})
        fork = self.shared / "skills-PC.json"
        self.shared.mkdir(parents=True, exist_ok=True)
        fork.write_text("{half", encoding="utf-8")
        data = self.read(path)
        self.assertFalse(dp.absorb_conflict_forks(str(path), data))
        self.assertTrue(fork.exists())

    def test_repo_root_fallback_is_never_absorbed(self):
        path = self.repo / "skills.json"
        self.write(path, {"a": 1})
        bystander = self.repo / "skills-notes.json"  # unrelated repo file
        self.write(bystander, {"b": 2})
        data = self.read(path)
        self.assertFalse(dp.absorb_conflict_forks(str(path), data))
        self.assertEqual(data, {"a": 1})
        self.assertTrue(bystander.exists())


if __name__ == "__main__":
    unittest.main()
