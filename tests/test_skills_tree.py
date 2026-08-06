"""Characterization tests for the per-skill SKILL.md tree in
myagent/datapaths.py — frontmatter round-trips, tolerant parsing, the
one-shot skills.json→tree migration, per-file conflict-fork healing,
write-only diff-aware saves, and explicit deletion.

Same harness as test_datapaths.py: MYAGENT_DATA_DIR points the resolver at a
TemporaryDirectory, the module's repo-root constant and the one-shot
migration latch are repointed/cleared per test."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from myagent import datapaths as dp


ENTRY = {"content": "# Do the thing\nStep 1.", "mode": "on_demand",
         "description": "Does the thing. Use when the thing needs doing."}


class SkillsTreeCase(unittest.TestCase):
    def setUp(self):
        shared_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(shared_tmp.cleanup)
        self.shared = Path(shared_tmp.name) / "SharedStore"

        repo_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(repo_tmp.cleanup)
        self.repo = Path(repo_tmp.name)

        os.environ[dp.DATA_DIR_ENV] = str(self.shared)
        self.addCleanup(os.environ.pop, dp.DATA_DIR_ENV, None)

        orig_base = dp._BASE_DIR
        dp._BASE_DIR = str(self.repo)
        self.addCleanup(setattr, dp, "_BASE_DIR", orig_base)

        dp._migrated.clear()
        self.addCleanup(dp._migrated.clear)

        self.tree = Path(dp.resolve_skills_dir())

    def md_path(self, sub):
        return self.tree / sub / dp.SKILL_BASENAME

    def write_md(self, sub, text, basename=None):
        d = self.tree / sub
        d.mkdir(parents=True, exist_ok=True)
        p = d / (basename or dp.SKILL_BASENAME)
        p.write_text(text, encoding="utf-8")
        return p


class ResolveAndFormatTests(SkillsTreeCase):
    def test_resolve_prefers_shared_dir(self):
        self.assertEqual(str(self.tree), str(self.shared / dp.SKILLS_DIRNAME))

    def test_resolve_falls_back_to_repo_root(self):
        os.environ.pop(dp.DATA_DIR_ENV)
        orig = dp.find_onedrive_root
        dp.find_onedrive_root = lambda: None
        self.addCleanup(setattr, dp, "find_onedrive_root", orig)
        self.assertEqual(dp.resolve_skills_dir(),
                         str(self.repo / dp.SKILLS_DIRNAME))

    def test_serialize_shape(self):
        text = dp._serialize_skill_md("My Skill", ENTRY)
        self.assertTrue(text.startswith(
            "---\nname: My Skill\ndescription: Does the thing. "))
        self.assertIn("\nmode: on_demand\n---\n\n# Do the thing\n", text)

    def test_round_trip_exact(self):
        for entry in (ENTRY,
                      {"content": "no trailing newline", "mode": "disabled"},
                      {"content": "", "mode": "enabled"},
                      {"content": "keeps\n\ninner blanks\n", "mode": "disabled"}):
            name, parsed = dp._entry_from_md(
                dp._serialize_skill_md("A Name", entry), "fallback")
            self.assertEqual(name, "A Name")
            expected = dict(entry)
            # writer adds exactly one trailing newline, reader strips exactly
            # one — content without one round-trips exactly, content WITH one
            # is normalized once and is then stable
            if expected["content"].endswith("\n"):
                expected["content"] = expected["content"][:-1]
            self.assertEqual(parsed, expected)

    def test_parse_without_frontmatter_is_all_content(self):
        name, entry = dp._entry_from_md("just some text\nno fences", "Folder Name")
        self.assertEqual(name, "Folder Name")
        self.assertEqual(entry, {"content": "just some text\nno fences",
                                 "mode": "disabled"})

    def test_parse_unclosed_frontmatter_is_all_content(self):
        text = "---\nname: X\nnever closed"
        name, entry = dp._entry_from_md(text, "F")
        self.assertEqual((name, entry["content"]), ("F", text))

    def test_parse_folds_wrapped_description(self):
        text = ("---\nname: X\ndescription: first part\n  wrapped second part\n"
                "mode: enabled\n---\n\nbody")
        name, entry = dp._entry_from_md(text, "F")
        self.assertEqual(entry["description"], "first part wrapped second part")
        self.assertEqual(entry["mode"], "enabled")

    def test_invalid_mode_and_unknown_keys_tolerated(self):
        text = "---\nname: X\nmode: bogus\nlicense: MIT\n---\n\nbody"
        _, entry = dp._entry_from_md(text, "F")
        self.assertEqual(entry["mode"], "disabled")
        self.assertNotIn("license", entry)

    def test_dirname_sanitization(self):
        self.assertEqual(dp._skill_dirname('We/st: "pac"?'), 'We_st_ _pac__')
        self.assertEqual(dp._skill_dirname("..."), "_skill")
        self.assertEqual(dp._skill_dirname("CON"), "_CON")
        self.assertEqual(dp._skill_dirname("con.txt"), "_con.txt")


class SaveLoadTests(SkillsTreeCase):
    def test_save_then_load_round_trips(self):
        skills = {"Alpha": dict(ENTRY),
                  "Beta Two": {"content": "b", "mode": "disabled"}}
        dp.save_skills_tree(str(self.tree), skills)
        loaded = dp.load_skills_tree(str(self.tree))
        self.assertEqual(loaded, skills)
        self.assertTrue(self.md_path("Alpha").is_file())
        self.assertTrue(self.md_path("Beta Two").is_file())

    def test_hostile_name_round_trips_via_frontmatter(self):
        skills = {'Pay: "Anyone"/Now?': {"content": "x", "mode": "enabled"}}
        dp.save_skills_tree(str(self.tree), skills)
        self.assertEqual(dp.load_skills_tree(str(self.tree)), skills)

    def test_unchanged_save_does_not_rewrite(self):
        dp.save_skills_tree(str(self.tree), {"Alpha": dict(ENTRY)})
        before = os.stat(self.md_path("Alpha")).st_mtime_ns
        os.utime(self.md_path("Alpha"), ns=(before - 10**9, before - 10**9))
        stamped = os.stat(self.md_path("Alpha")).st_mtime_ns
        dp.save_skills_tree(str(self.tree), {"Alpha": dict(ENTRY)})
        self.assertEqual(os.stat(self.md_path("Alpha")).st_mtime_ns, stamped)

    def test_save_is_write_only(self):
        # A folder absent from the dict survives a save — a stale in-memory
        # dict must not wipe a skill another machine synced in.
        dp.save_skills_tree(str(self.tree), {"Keep": {"content": "k", "mode": "disabled"},
                                             "Other": {"content": "o", "mode": "disabled"}})
        dp.save_skills_tree(str(self.tree), {"Other": {"content": "o2", "mode": "disabled"}})
        loaded = dp._scan_skills_tree(str(self.tree))
        self.assertIn("Keep", loaded)
        self.assertEqual(loaded["Other"]["content"], "o2")

    def test_delete_removes_folder_and_resources(self):
        dp.save_skills_tree(str(self.tree), {"Gone": {"content": "g", "mode": "disabled"}})
        (self.tree / "Gone" / "helper.ps1").write_text("Write-Host hi", encoding="utf-8")
        dp.delete_skill_tree_entry(str(self.tree), "Gone")
        self.assertFalse((self.tree / "Gone").exists())

    def test_delete_matches_frontmatter_name_not_folder(self):
        self.write_md("weird_folder", dp._serialize_skill_md("True Name",
                                                             {"content": "x", "mode": "disabled"}))
        dp.delete_skill_tree_entry(str(self.tree), "True Name")
        self.assertFalse((self.tree / "weird_folder").exists())

    def test_two_folders_claiming_one_name_keep_both(self):
        self.write_md("A", dp._serialize_skill_md("Same", {"content": "one", "mode": "disabled"}))
        self.write_md("B", dp._serialize_skill_md("Same", {"content": "two", "mode": "disabled"}))
        loaded = dp._scan_skills_tree(str(self.tree))
        self.assertEqual(loaded["Same"]["content"], "one")
        self.assertEqual(loaded["Same__B"]["content"], "two")

    def test_write_claims_sibling_when_folder_owned_by_other_name(self):
        # Two names sanitizing to the same folder: second gets a numbered sibling
        dp.save_skills_tree(str(self.tree), {"A/B": {"content": "1", "mode": "disabled"}})
        dp.save_skills_tree(str(self.tree), {"A?B": {"content": "2", "mode": "disabled"}})
        loaded = dp._scan_skills_tree(str(self.tree))
        self.assertEqual(loaded["A/B"]["content"], "1")
        self.assertEqual(loaded["A?B"]["content"], "2")

    def test_bom_tolerated_on_read(self):
        text = dp._serialize_skill_md("Bommed", {"content": "b", "mode": "enabled"})
        p = self.tree / "Bommed"
        p.mkdir(parents=True)
        (p / dp.SKILL_BASENAME).write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
        self.assertEqual(dp._scan_skills_tree(str(self.tree))["Bommed"]["mode"],
                         "enabled")


class MigrationTests(SkillsTreeCase):
    def json_store(self, data):
        path = Path(dp.resolve_store("skills.json"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_json_splits_into_tree_and_parks_bak(self):
        path = self.json_store({"One": {"content": "c1", "mode": "on_demand",
                                        "description": "d1"},
                                "Two": {"content": "c2", "enabled": True}})
        loaded = dp.load_skills_tree(str(self.tree))
        self.assertEqual(loaded["One"]["description"], "d1")
        # legacy {enabled: true} normalizes to mode
        self.assertEqual(loaded["Two"]["mode"], "enabled")
        self.assertFalse(path.exists())
        self.assertTrue(path.with_suffix(".json" + dp._MIGRATED_SUFFIX).exists())

    def test_deleted_skill_does_not_resurrect_next_process(self):
        self.json_store({"Doomed": {"content": "x", "mode": "disabled"}})
        dp.load_skills_tree(str(self.tree))
        dp.delete_skill_tree_entry(str(self.tree), "Doomed")
        dp._migrated.clear()  # simulate the next process
        self.assertEqual(dp.load_skills_tree(str(self.tree)), {})

    def test_existing_tree_wins_json_conflict_preserved_as_variant(self):
        dp.save_skills_tree(str(self.tree),
                            {"Dup": {"content": "tree version", "mode": "enabled"}})
        self.json_store({"Dup": {"content": "json version", "mode": "disabled"},
                         "Fresh": {"content": "new", "mode": "disabled"}})
        loaded = dp.load_skills_tree(str(self.tree))
        self.assertEqual(loaded["Dup"]["content"], "tree version")
        self.assertEqual(loaded["Dup__json"]["content"], "json version")
        self.assertEqual(loaded["Fresh"]["content"], "new")

    def test_repo_root_json_composes_through_load_store(self):
        # legacy repo-root skills.json (pre-OneDrive machines) still lands in
        # the tree via load_store's own migration
        (self.repo / "skills.json").write_text(
            json.dumps({"Old": {"content": "legacy", "mode": "disabled"}}),
            encoding="utf-8")
        loaded = dp.load_skills_tree(str(self.tree))
        self.assertEqual(loaded["Old"]["content"], "legacy")
        self.assertTrue((self.repo / ("skills.json" + dp._MIGRATED_SUFFIX)).exists())


class ForkHealingTests(SkillsTreeCase):
    def test_identical_fork_is_deleted_silently(self):
        text = dp._serialize_skill_md("S", {"content": "same", "mode": "disabled"})
        self.write_md("S", text)
        fork = self.write_md("S", text, basename="SKILL-DESKTOP-NAMOR.md")
        loaded = dp.load_skills_tree(str(self.tree))
        self.assertEqual(list(loaded), ["S"])
        self.assertFalse(fork.exists())

    def test_different_fork_materializes_variant_skill(self):
        self.write_md("S", dp._serialize_skill_md("S", {"content": "mine", "mode": "disabled"}))
        fork = self.write_md("S", dp._serialize_skill_md("S", {"content": "theirs", "mode": "disabled"}),
                             basename="SKILL-Mac-mini.md")
        loaded = dp.load_skills_tree(str(self.tree))
        self.assertEqual(loaded["S"]["content"], "mine")
        self.assertEqual(loaded["S__Mac-mini"]["content"], "theirs")
        self.assertFalse(fork.exists())
        # the variant persists as its own folder for the next launch
        dp._migrated.clear()
        self.assertIn("S__Mac-mini", dp.load_skills_tree(str(self.tree)))

    def test_orphaned_fork_promoted_to_main(self):
        d = self.tree / "S"
        d.mkdir(parents=True)
        (d / "SKILL-Laptop.md").write_text(
            dp._serialize_skill_md("S", {"content": "only copy", "mode": "enabled"}),
            encoding="utf-8")
        loaded = dp.load_skills_tree(str(self.tree))
        self.assertEqual(loaded["S"]["content"], "only copy")
        self.assertTrue((d / dp.SKILL_BASENAME).is_file())
        self.assertFalse((d / "SKILL-Laptop.md").exists())


if __name__ == "__main__":
    unittest.main()
