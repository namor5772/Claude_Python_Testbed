"""MyBackup's GUI layout persistence (2026-11-08).

state.json kept the window geometry (per monitor layout), the setup file and
the FROM / TO column widths; it now keeps the LOG PANE'S HEIGHT too — the
sash between the table and the log — and the row-number column's width,
and on macOS Cmd+Q runs _on_close like [X] (Tk's Quit ended the process
without it, so a session quit that way saved nothing at all).

The sash arithmetic is pure. RoundTripTests build the REAL App — twice, as
two launches — on a transparent root with every config path pointed at a
temp folder (App.__init__ lays the window out, so a withdrawn root is no
option), and skip where no display exists.
"""
import inspect
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

import tkinter as tk

import MyBackup as mb
from myagent import keyboard


class SashMathTests(unittest.TestCase):
    def test_exact_when_the_pane_has_room(self):
        # 860 px pane, 5 px sash, the log 200 → the table 655
        self.assertEqual(mb.sash_for_log_height(860, 5, 200), 655)

    def test_clamped_to_the_minimums(self):
        self.assertEqual(mb.sash_for_log_height(860, 5, 100000), mb.MIN_PANE, "a huge log keeps the table at MIN_PANE")
        self.assertEqual(mb.sash_for_log_height(860, 5, 1), 860 - 5 - mb.MIN_PANE, "a tiny log keeps itself at MIN_PANE")

    def test_none_where_nothing_sensible_can_be_set(self):
        self.assertIsNone(mb.sash_for_log_height(1, 0, 200), "the pane is not laid out yet")
        self.assertIsNone(mb.sash_for_log_height(2 * mb.MIN_PANE + 4, 5, 50), "too short for two minimums")
        for junk in (None, 0, -3, "200", 200.0, True):
            with self.subTest(junk=junk):
                self.assertIsNone(mb.sash_for_log_height(860, 5, junk))

    def test_tree_columns_name_every_column_once(self):
        self.assertEqual(mb.TREE_COLUMNS, (("#0", "#"), ("from", "FROM"), ("to", "TO")))
        src = inspect.getsource(mb.App._capture_col_widths) + inspect.getsource(mb.App._load_state)
        self.assertEqual(src.count("in TREE_COLUMNS"), 2, "the capture and the restore both walk the tuple")
        self.assertNotIn('zip(("from", "to"), HEADERS)', src)


class QuitHandlerTests(unittest.TestCase):
    def test_copies_are_byte_identical_to_keyboard_pys(self):
        for name in ("is_aqua", "install_quit_handler"):
            with self.subTest(helper=name):
                self.assertEqual(inspect.getsource(getattr(mb, name)), inspect.getsource(getattr(keyboard, name)))

    def test_installed_beside_the_close_protocol(self):
        src = inspect.getsource(mb.App.__init__)
        protocol = src.index('self.root.protocol("WM_DELETE_WINDOW", self._on_close)')
        quit_ = src.index("install_quit_handler(self.root, self._on_close)")
        self.assertLess(protocol, quit_)


class SaveStateStubTests(unittest.TestCase):
    """_save_state on a host whose window was never laid out keeps the file's log_height."""

    def test_unlaid_out_window_keeps_the_saved_height(self):
        class FakeTree:
            def column(self, cid, option):
                return {"#0": 44, "from": 440, "to": 440}[cid]

        class FakePane:
            def winfo_height(self):
                return 1

        class FakeRoot:
            def geometry(self):
                return "1000x620+10+20"

        with tempfile.TemporaryDirectory() as tmp:
            state_file = os.path.join(tmp, "state.json")
            with open(state_file, "w", encoding="utf-8") as f:
                json.dump({"geometry": "1000x620+10+20", "log_height": 333, "col_widths": {"FROM": 1, "TO": 2}}, f)
            app = mb.App.__new__(mb.App)
            app.root, app.tree, app._log_pane = FakeRoot(), FakeTree(), FakePane()
            app.filepath, app._col_widths, app._log_height = "s.csv", {}, None
            app._monitors = lambda: [(0, 0, 2560, 1440)]
            with mock.patch.object(mb, "STATE_FILE", state_file), mock.patch.object(mb, "CONFIG_DIR", tmp):
                app._save_state()
            with open(state_file, encoding="utf-8") as f:
                state = json.load(f)
        self.assertEqual(state["log_height"], 333, "no live height → the file's value survives")
        self.assertEqual(state["col_widths"], {"#": 44, "FROM": 440, "TO": 440})
        self.assertEqual(state["filepath"], "s.csv")


class _TransparentApp(mb.App):
    def _build_ui(self):
        self.root.attributes("-alpha", 0.0)
        super()._build_ui()


class RoundTripTests(unittest.TestCase):
    """The REAL App, launched twice over a temp config folder."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for name in ("CONFIG_DIR", "STATE_FILE", "DEFAULT_SETUP_FILE", "LOG_FILE"):
            target = self.tmp.name if name == "CONFIG_DIR" else os.path.join(self.tmp.name, os.path.basename(getattr(mb, name)))
            patcher = mock.patch.object(mb, name, target)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.app = None

    def tearDown(self):
        if self.app is not None:
            try:
                self.app.root.destroy()
            except tk.TclError:
                pass

    def launch(self):
        try:
            self.app = _TransparentApp()
        except tk.TclError as exc:  # headless box, no display
            self.skipTest(f"Tk unavailable: {exc}")
        self.app.root.update_idletasks()
        return self.app

    def write_state(self, **fields):
        with open(mb.STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(fields, f)

    def read_state(self):
        with open(mb.STATE_FILE, encoding="utf-8") as f:
            return json.load(f)

    def widths(self, app):
        return {header: int(app.tree.column(cid, "width")) for cid, header in mb.TREE_COLUMNS}

    def test_sash_and_columns_survive_a_relaunch(self):
        app = self.launch()
        paned, table, log = app._paned, app._table_pane, app._log_pane
        height = paned.winfo_height()
        thickness = height - table.winfo_height() - log.winfo_height()
        self.assertGreater(height, 2 * mb.MIN_PANE, "the window is laid out")
        # The user drags the sash: the log gets 200 px.
        paned.sashpos(0, mb.sash_for_log_height(height, thickness, 200))
        app.root.update_idletasks()
        self.assertEqual(log.winfo_height(), 200)
        # ... and drags two column dividers, the row-number one included.
        defaults = self.widths(app)
        app.tree.column("#0", width=70)
        app.tree.column("from", width=300)
        app.root.update_idletasks()
        widths = self.widths(app)  # what the widget settled on: ttk re-stretches FROM / TO to fill the tree
        self.assertEqual(widths["#"], 70)
        self.assertNotEqual(widths, defaults)
        geometry = app.root.geometry()
        app._finish_close()
        self.app = None

        state = self.read_state()
        self.assertEqual(state["log_height"], 200)
        self.assertEqual(state["col_widths"], widths)
        self.assertEqual(state["geometry"], geometry)

        again = self.launch()
        self.assertEqual(again.root.geometry(), geometry, "position and size")
        self.assertEqual(again._log_pane.winfo_height(), 200, "the sash")
        self.assertEqual(self.widths(again), widths, "all three columns")

    def test_no_saved_height_leaves_the_weights_in_charge_and_saves_the_live_one(self):
        app = self.launch()
        self.assertIsNone(app._log_height)
        table, log = app._table_pane.winfo_height(), app._log_pane.winfo_height()
        self.assertGreater(table, log, "weight 3 against 1")
        app._finish_close()
        self.app = None
        self.assertEqual(self.read_state()["log_height"], log)

    def test_a_saved_height_too_big_for_the_window_is_clamped(self):
        self.write_state(log_height=100000)
        app = self.launch()
        self.assertEqual(app._table_pane.winfo_height(), mb.MIN_PANE)
        height = app._paned.winfo_height()
        self.assertGreater(app._log_pane.winfo_height(), height // 2)

    def test_junk_in_the_file_is_ignored(self):
        self.write_state(log_height="tall", col_widths={"#": "wide", "FROM": -5, "TO": 500})
        app = self.launch()
        self.assertIsNone(app._log_height)
        widths = self.widths(app)
        self.assertGreater(widths["TO"], widths["FROM"], "the one good width was applied (both start at 440)")
        self.assertEqual(widths["#"], 44, "the default")

    @unittest.skipUnless(sys.platform == "darwin", "Cmd+Q is a Tk-on-aqua matter")
    def test_cmd_q_runs_on_close(self):
        app = self.launch()
        self.assertIn("::tk::mac::Quit", app.root.tk.splitlist(app.root.tk.call("info", "commands", "::tk::mac::Quit")))


if __name__ == "__main__":
    unittest.main()
