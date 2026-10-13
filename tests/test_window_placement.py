"""Window placement across the three single-file apps (2026-10-13).

MyBackup.py holds the originals — the helpers that remember the window's
geometry per monitor layout and apply a saved position only where a
connected monitor shows it (behaviour pinned in tests/test_mybackup.py);
CSVEditor.py and TodoList.py carry in-file copies, the family's rule for
single-file apps (SelfBot's copies of MyAgent helpers are pinned the same
way). This module pins the copies byte-identical to the originals, the
per-app size constants, the wiring of every app's state load / save, and the
laptop case — a position saved on a docked second monitor — on each copy.

All three modules build no Tk root at import; TodoList resolves its shared
todos.json at import, so TODOLIST_DATA_DIR points it at a temp dir first.
"""

import inspect
import os
import tempfile
import unittest

_TMP = tempfile.TemporaryDirectory()
_OLD_DATA_DIR = os.environ.get("TODOLIST_DATA_DIR")
os.environ["TODOLIST_DATA_DIR"] = _TMP.name  # keep TodoList's import off the real OneDrive share
try:
    import CSVEditor
    import MyBackup
    import TodoList
finally:
    if _OLD_DATA_DIR is None:
        os.environ.pop("TODOLIST_DATA_DIR", None)
    else:
        os.environ["TODOLIST_DATA_DIR"] = _OLD_DATA_DIR

HELPERS = ("monitor_rects", "layout_key", "parse_geometry", "geometry_visible", "primary_rect",
           "centred_geometry", "restore_geometry", "state_for_save", "load_state_file")
CONSTANTS = ("GEOMETRY_VISIBLE_W", "GEOMETRY_TITLE_STRIP", "GEOMETRY_TITLE_VISIBLE_H")
APPS = {"CSVEditor": CSVEditor, "TodoList": TodoList, "MyBackup": MyBackup}
COPIES = {"CSVEditor": CSVEditor, "TodoList": TodoList}

LAPTOP = [(0, 0, 1707, 1067)]
DOCKED = [(0, 0, 1707, 1067), (2541, 0, 4461, 1080)]
SAVED_DOCKED = "1920x1009+2541+17"


def tearDownModule():
    _TMP.cleanup()


class CopyTests(unittest.TestCase):
    def test_helpers_are_byte_identical_to_mybackups(self):
        for name in HELPERS:
            original = inspect.getsource(getattr(MyBackup, name))
            for app, module in COPIES.items():
                with self.subTest(app=app, helper=name):
                    self.assertEqual(inspect.getsource(getattr(module, name)), original)

    def test_monitors_method_is_byte_identical(self):
        original = inspect.getsource(MyBackup.App._monitors)
        for app, module in COPIES.items():
            with self.subTest(app=app):
                self.assertEqual(inspect.getsource(module.App._monitors), original)

    def test_visibility_constants_agree(self):
        for name in CONSTANTS:
            for app, module in COPIES.items():
                with self.subTest(app=app, constant=name):
                    self.assertEqual(getattr(module, name), getattr(MyBackup, name))


class ConstantTests(unittest.TestCase):
    def test_sizes(self):
        self.assertEqual(CSVEditor.DEFAULT_SIZE, (1000, 600))
        self.assertEqual(CSVEditor.MIN_SIZE, (600, 400))
        self.assertEqual(TodoList.DEFAULT_SIZE, (900, 550))
        self.assertEqual(TodoList.MIN_SIZE, (700, 400))
        self.assertEqual(MyBackup.DEFAULT_SIZE, (1000, 620))
        self.assertEqual(MyBackup.MIN_SIZE, (720, 460))
        for app, module in APPS.items():
            with self.subTest(app=app):
                self.assertGreaterEqual(module.DEFAULT_SIZE[0], module.MIN_SIZE[0])
                self.assertGreaterEqual(module.DEFAULT_SIZE[1], module.MIN_SIZE[1])

    def test_init_uses_the_constants(self):
        for app, module in APPS.items():
            src = inspect.getsource(module.App.__init__)
            with self.subTest(app=app):
                self.assertIn('self.root.geometry("%dx%d" % DEFAULT_SIZE)', src)
                self.assertIn("self.root.minsize(*MIN_SIZE)", src)


class WiringTests(unittest.TestCase):
    def test_load_and_save_go_through_the_helpers(self):
        for app, module in APPS.items():
            load = inspect.getsource(module.App._load_state)
            save = inspect.getsource(module.App._save_state)
            with self.subTest(app=app):
                self.assertIn("restore_geometry(state, layout_key(monitors), monitors)", load)
                self.assertNotIn('state.get("geometry")', load, "the raw restore is gone")
                self.assertIn("state_for_save(load_state_file(STATE_FILE), layout_key(self._monitors()), "
                              "self.root.geometry()", save)
                self.assertNotIn('"geometry": self.root.geometry()', save)

    def test_import_builds_no_tk_root(self):
        import tkinter
        self.assertIsNone(getattr(tkinter, "_default_root", None))
        for module in APPS.values():
            self.assertTrue(callable(module.App))


class BehaviourTests(unittest.TestCase):
    """The laptop case on every app: each copy must behave as the original."""

    def test_the_docked_position_is_never_restored_undocked(self):
        for app, module in APPS.items():
            with self.subTest(app=app):
                geo = module.restore_geometry({"geometry": SAVED_DOCKED}, module.layout_key(LAPTOP), LAPTOP)
                self.assertNotEqual(geo, SAVED_DOCKED)
                self.assertTrue(module.geometry_visible(geo, LAPTOP))
                self.assertEqual(module.restore_geometry({"geometry": SAVED_DOCKED}, module.layout_key(DOCKED), DOCKED),
                                 SAVED_DOCKED, "docked again, the position comes back")

    def test_each_apps_own_fields_ride_along(self):
        fields = {"filter_done": "active", "sort_col": None}
        key = TodoList.layout_key(LAPTOP)
        state = TodoList.state_for_save({"geometry": "1131x627+269+148"}, key, "1131x627+269+148", fields)
        self.assertEqual(state, {"geometry": "1131x627+269+148", "geometries": {key: "1131x627+269+148"},
                                 "filter_done": "active", "sort_col": None})
        # The native ports' six-key file (geometry first) is read as before and extended, not replaced.
        native = {"geometry": "900x550+10+20", "filter_done": "all", "filter_priority": "All",
                  "filter_category": "All", "sort_col": None, "sort_reverse": False}
        self.assertEqual(TodoList.restore_geometry(native, "k", LAPTOP), "900x550+10+20")
        self.assertEqual(list(TodoList.state_for_save(native, "k", "901x551+1+2", {})),
                         ["geometry", "geometries", "filter_done", "filter_priority", "filter_category",
                          "sort_col", "sort_reverse"])
        # CSVEditor's legacy single-filter keys are kept for its loader's legacy branch.
        legacy = {"geometry": "1000x600+5+5", "filter_col": "Date", "filter_val": "2026"}
        saved = CSVEditor.state_for_save(legacy, "k", "1000x600+5+5", {"filepath": "x.csv"})
        self.assertEqual((saved["filter_col"], saved["filter_val"], saved["filepath"]), ("Date", "2026", "x.csv"))


if __name__ == "__main__":
    unittest.main()
