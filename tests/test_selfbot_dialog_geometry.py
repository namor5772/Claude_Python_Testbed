"""Characterization tests for SelfBot's dialog geometry persistence (2026-09-16).

The three auxiliary windows — System Prompt Editor, Skills Manager and the
Safety — Confirm Patterns dialog — remember their size and position across
launches, per instance (each instance writes its own state file:
app_state.json / app_state_2.json) and PER MONITOR LAYOUT. What this pins:

1. ``_monitor_layout_key`` names the current arrangement (every monitor's rect,
   sorted); ``_save_last_state`` files the dialog geometries under it in a
   ``dialog_geometries`` dict, keeping other layouts' entries; ``_load_last_state``
   (via ``_dialog_geometries_from_state``) restores only the current layout's
   entry, never another's, adopting a pre-2026-09-16 file's flat keys once.
2. ``_sanitize_geometry`` validates a saved position against EVERY monitor
   (``_get_display_rects``), not the primary screen alone. The old test
   ``x < screen_width and x + w > 0`` threw away any position on a monitor to
   the LEFT of the primary (negative x), so a dialog parked there came back
   wherever the WM chose — the "dialogs don't seem persistent" report.
3. ``_place_dialog`` is the one placement routine: the saved position when a
   monitor still shows it, else the saved SIZE at the default, else the default
   size — the default being centred on the main window, shrunk and clamped onto
   the monitor holding the main window (``_default_dialog_geometry``). That is
   the safe fallback for a monitor count / resolution change between runs.
4. The System Prompt Editor is wired like the other two (it had no persistence
   before): withdraw → build → place → deiconify, geometry captured on both the
   [X] close and the Apply-to-Chat close.

SelfBot is importable in-process (module import builds no Tk root); the bare
``App.__new__`` stub from tests/_util.py serves its methods.
"""

import inspect
import json
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

# Primary 2560x1440 at the origin, a second 2560x1440 monitor to its LEFT.
TWO_MONITORS = [(0, 0, 2560, 1440), (-2560, 0, 0, 1440)]
TWO_KEY = "-2560,0,0,1440|0,0,2560,1440"
# The laptop undocked: one 1920x1080 panel.
ONE_MONITOR = [(0, 0, 1920, 1080)]
ONE_KEY = "0,0,1920,1080"


class _Var:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value


class _Root:
    def __init__(self, geometry="804x930+620+119", x=620, y=119, w=804, h=930, mapped=True):
        self._geometry, self._x, self._y, self._w, self._h = geometry, x, y, w, h
        self._mapped = mapped
        self.set_to = None

    def geometry(self, geo=None):
        if geo is None:
            return self._geometry
        self.set_to = geo

    def winfo_ismapped(self):
        return self._mapped

    def update_idletasks(self):
        pass

    def winfo_screenwidth(self):
        return 2560

    def winfo_screenheight(self):
        return 1440

    def winfo_x(self):
        return self._x

    def winfo_y(self):
        return self._y

    def winfo_width(self):
        return self._w

    def winfo_height(self):
        return self._h


class _Dialog:
    """A stand-in for a Toplevel: exists, reports a geometry, records what is set."""

    def __init__(self, geometry=""):
        self._geometry = geometry
        self.set_to = None

    def winfo_exists(self):
        return True

    def geometry(self, geo=None):
        if geo is None:
            return self._geometry
        self.set_to = geo


def _app(**attrs):
    attrs.setdefault("root", _Root())
    return stub(SelfBot.App, **attrs)


def _rects(rects):
    return mock.patch.object(SelfBot.App, "_get_display_rects", staticmethod(lambda: rects))


@_needs_selfbot
class LayoutKeyTests(unittest.TestCase):
    def test_key_is_every_monitor_rect_sorted(self):
        with _rects(TWO_MONITORS):
            self.assertEqual(_app()._monitor_layout_key(), TWO_KEY)
        with _rects(list(reversed(TWO_MONITORS))):
            self.assertEqual(_app()._monitor_layout_key(), TWO_KEY)   # order-independent

    def test_key_changes_with_count_or_resolution(self):
        with _rects(ONE_MONITOR):
            self.assertEqual(_app()._monitor_layout_key(), ONE_KEY)
        with _rects([(0, 0, 2560, 1440), (-1920, 0, 0, 1080)]):
            self.assertNotEqual(_app()._monitor_layout_key(), TWO_KEY)

    def test_primary_screen_when_enumeration_fails(self):
        with _rects([]):
            self.assertEqual(_app()._monitor_layout_key(), "0,0,2560,1440")


@_needs_selfbot
class SanitizeGeometryTests(unittest.TestCase):
    def _sanitize(self, geo, rects=TWO_MONITORS, **kw):
        with _rects(rects):
            return _app()._sanitize_geometry(geo, **kw)

    def test_left_monitor_position_is_kept(self):
        # Negative x — the case the primary-only test rejected.
        self.assertEqual(self._sanitize("900x500+-1800+300"), "900x500+-1800+300")

    def test_primary_monitor_position_is_kept(self):
        self.assertEqual(self._sanitize("560x760+905+309"), "560x760+905+309")

    def test_far_off_position_is_dropped_but_size_kept(self):
        self.assertEqual(self._sanitize("600x400+-9000+73"), "600x400")
        self.assertEqual(self._sanitize("600x400+3000+73"), "600x400")

    def test_title_strip_below_every_monitor_is_dropped(self):
        self.assertEqual(self._sanitize("600x400+100+1500"), "600x400")

    def test_minimum_size_is_enforced(self):
        self.assertEqual(self._sanitize("300x200+10+10"), "400x300+10+10")
        self.assertEqual(self._sanitize("300x200+10+10", min_w=200, min_h=150), "300x200+10+10")

    def test_junk_falls_back_to_a_default_size(self):
        self.assertEqual(self._sanitize(None), "900x500")
        self.assertEqual(self._sanitize("nonsense"), "900x500")

    def test_primary_screen_fallback_when_enumeration_fails(self):
        # No display rects → only the primary screen counts, as before.
        self.assertEqual(self._sanitize("900x500+-1800+300", rects=[]), "900x500")
        self.assertEqual(self._sanitize("900x500+506+318", rects=[]), "900x500+506+318")


@_needs_selfbot
class DefaultPlacementTests(unittest.TestCase):
    def test_centred_on_the_main_window_on_its_own_monitor(self):
        # Main window on the LEFT monitor → the dialog is centred there, not on the primary.
        app = _app(root=_Root(x=-1800, y=300, w=800, h=900))
        with _rects(TWO_MONITORS):
            self.assertEqual(app._default_dialog_geometry(560, 760), "560x760+-1680+370")

    def test_oversized_dialog_is_shrunk_and_clamped_onto_the_monitor(self):
        app = _app(root=_Root(x=100, y=100, w=800, h=600))
        with _rects(ONE_MONITOR):
            # 3000x2000 cannot fit 1920x1080 → full monitor, at its origin.
            self.assertEqual(app._default_dialog_geometry(3000, 2000), "1920x1080+0+0")

    def test_dialog_hanging_off_the_edge_is_pulled_back(self):
        # Main window near the bottom-right corner: centring would overflow → clamped.
        app = _app(root=_Root(x=1500, y=700, w=800, h=600))
        with _rects(ONE_MONITOR):
            self.assertEqual(app._default_dialog_geometry(560, 760), "560x760+1360+320")

    def test_main_window_off_every_monitor_falls_back_to_the_primary(self):
        app = _app(root=_Root(x=-9000, y=-9000, w=800, h=600))
        with _rects(TWO_MONITORS):
            w, h, x, y = map(int, __import__("re").match(
                r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", app._default_dialog_geometry(560, 760)).groups())
            self.assertTrue(0 <= x and x + w <= 2560 and 0 <= y and y + h <= 1440)


@_needs_selfbot
class PlaceDialogTests(unittest.TestCase):
    def test_saved_position_still_visible_is_used(self):
        app = _app(_last_skills_dialog_geometry="900x500+-1800+300")
        win = _Dialog()
        with _rects(TWO_MONITORS):
            self.assertEqual(app._place_dialog(win, "skills_dialog", (900, 500)), "900x500+-1800+300")
        self.assertEqual(win.set_to, "900x500+-1800+300")

    def test_saved_position_on_a_vanished_monitor_keeps_the_size_at_the_default_spot(self):
        # Saved on the left monitor; now undocked to one 1920x1080 panel.
        app = _app(_last_skills_dialog_geometry="700x450+-1800+300",
                   root=_Root(x=100, y=100, w=800, h=600))
        win = _Dialog()
        with _rects(ONE_MONITOR):
            geo = app._place_dialog(win, "skills_dialog", (900, 500))
        self.assertEqual(geo, "700x450+150+175")           # saved size, centred on the main window
        self.assertEqual(win.set_to, geo)

    def test_nothing_saved_uses_the_default_size_at_the_default_spot(self):
        app = _app(_last_prompt_editor_geometry=None, root=_Root(x=100, y=100, w=800, h=600))
        with _rects(ONE_MONITOR):
            self.assertEqual(app._place_dialog(_Dialog(), "prompt_editor", (650, 500)), "650x500+175+150")

    def test_saved_size_below_the_minimum_is_raised(self):
        app = _app(_last_ps_safety_geometry="100x100+10+10", root=_Root(x=100, y=100, w=800, h=600))
        with _rects(ONE_MONITOR):
            self.assertEqual(app._place_dialog(_Dialog(), "ps_safety", (560, 760)), "400x300+10+10")


def _state_attrs(**overrides):
    toggles = {k: _Var(False) for k in (
        "save_thinking", "desktop_enabled", "browser_enabled", "meta_enabled",
        "mcp_enabled", "google_enabled", "proton_enabled", "outlook_enabled",
        "pause_enabled")}
    attrs = dict(
        system_prompt_name="P", model="claude-opus-5", temperature=1.0,
        thinking_enabled=False, thinking_effort="high", thinking_budget=8192,
        my_name_entry=_Var("A"), my_friend_entry=_Var("B"), _delay_seconds=5,
        _disabled_confirm_patterns=set(), _duo_mode=False,
        prompt_editor_window=None, skills_editor_window=None, _ps_safety_dialog=None,
        _last_prompt_editor_geometry=None, _last_skills_dialog_geometry=None,
        _last_ps_safety_geometry=None, **toggles)
    attrs.update(overrides)
    return attrs


@_needs_selfbot
class SaveStateDialogGeometryTests(unittest.TestCase):
    def _save(self, rects=TWO_MONITORS, existing=None, **overrides):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "app_state_2.json")
            if existing is not None:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(existing, f)
            app = _app(_state_file=path, **_state_attrs(**overrides))
            with _rects(rects):
                app._save_last_state()
            with open(path, encoding="utf-8") as f:
                return json.load(f)

    def test_open_dialogs_are_captured_live_under_the_layout_key(self):
        state = self._save(
            prompt_editor_window=_Dialog("650x500+-1500+200"),
            skills_editor_window=_Dialog("900x500+506+318"),
            _ps_safety_dialog=_Dialog("560x760+905+309"),
            _last_prompt_editor_geometry="1x1+0+0")  # stale — the live value must win
        self.assertEqual(state["dialog_geometries"], {TWO_KEY: {
            "prompt_editor": "650x500+-1500+200",
            "skills_dialog": "900x500+506+318",
            "ps_safety": "560x760+905+309"}})
        # The legacy flat keys are no longer written.
        for legacy in SelfBot.DIALOG_LEGACY_KEYS.values():
            self.assertNotIn(legacy, state)

    def test_closed_dialogs_keep_their_last_known_geometry(self):
        state = self._save(
            _last_prompt_editor_geometry="700x520+-1200+150",
            _last_skills_dialog_geometry="900x500+506+318")
        entry = state["dialog_geometries"][TWO_KEY]
        self.assertEqual(entry["prompt_editor"], "700x520+-1200+150")
        self.assertEqual(entry["skills_dialog"], "900x500+506+318")
        self.assertNotIn("ps_safety", entry)                # never opened → no fabricated placement

    def test_other_layouts_entries_are_preserved(self):
        existing = {"dialog_geometries": {ONE_KEY: {"skills_dialog": "800x400+100+100"}}}
        state = self._save(existing=existing, _last_skills_dialog_geometry="900x500+-1800+300")
        self.assertEqual(state["dialog_geometries"], {
            ONE_KEY: {"skills_dialog": "800x400+100+100"},
            TWO_KEY: {"skills_dialog": "900x500+-1800+300"}})

    def test_nothing_to_save_writes_no_dict_but_keeps_an_existing_one(self):
        self.assertNotIn("dialog_geometries", self._save())
        existing = {"dialog_geometries": {ONE_KEY: {"skills_dialog": "800x400+100+100"}}}
        self.assertEqual(self._save(existing=existing)["dialog_geometries"], existing["dialog_geometries"])

    def test_state_goes_to_the_instance_own_file(self):
        # Instance 2 writes app_state_2.json, so its dialogs persist independently.
        self.assertNotEqual(SelfBot.APP_STATE_FILE, SelfBot.APP_STATE_FILE_2)
        self.assertTrue(SelfBot.APP_STATE_FILE_2.endswith("app_state_2.json"))


@_needs_selfbot
class LoadStateDialogGeometryTests(unittest.TestCase):
    def test_current_layout_entry_is_restored(self):
        state = {"dialog_geometries": {
            TWO_KEY: {"skills_dialog": "900x500+-1800+300", "ps_safety": "560x760+905+309"},
            ONE_KEY: {"skills_dialog": "800x400+100+100"}}}
        self.assertEqual(SelfBot.App._dialog_geometries_from_state(state, TWO_KEY),
                         {"skills_dialog": "900x500+-1800+300", "ps_safety": "560x760+905+309"})

    def test_another_layout_entry_is_never_used(self):
        # Docked geometries exist, the laptop is undocked → nothing restored, defaults apply.
        state = {"dialog_geometries": {TWO_KEY: {"skills_dialog": "900x500+-1800+300"}}}
        self.assertEqual(SelfBot.App._dialog_geometries_from_state(state, ONE_KEY), {})

    def test_legacy_flat_keys_are_adopted_once(self):
        state = {"skills_dialog_geometry": "900x500+506+318",
                 "ps_safety_dialog_geometry": "560x760+905+309",
                 "prompt_editor_geometry": "650x500+10+10"}
        self.assertEqual(SelfBot.App._dialog_geometries_from_state(state, TWO_KEY), {
            "prompt_editor": "650x500+10+10",
            "skills_dialog": "900x500+506+318",
            "ps_safety": "560x760+905+309"})

    def test_legacy_keys_are_ignored_once_per_layout_data_exists(self):
        state = {"skills_dialog_geometry": "900x500+506+318",
                 "dialog_geometries": {ONE_KEY: {"skills_dialog": "800x400+100+100"}}}
        self.assertEqual(SelfBot.App._dialog_geometries_from_state(state, TWO_KEY), {})

    def test_junk_entries_are_ignored(self):
        self.assertEqual(SelfBot.App._dialog_geometries_from_state({"dialog_geometries": "x"}, TWO_KEY), {})
        self.assertEqual(SelfBot.App._dialog_geometries_from_state(
            {"dialog_geometries": {TWO_KEY: {"bogus": "1x1+0+0", "skills_dialog": ""}}}, TWO_KEY), {})

    def test_load_last_state_uses_the_helper_and_the_layout_key(self):
        src = inspect.getsource(SelfBot.App._load_last_state)
        self.assertIn("_dialog_geometries_from_state(state, self._monitor_layout_key())", src)
        for kind in SelfBot.DIALOG_WINDOWS:
            self.assertIn(f"_last_{kind}_geometry", inspect.getsource(SelfBot.App.__init__))


@_needs_selfbot
class MainGeometryFromStateTests(unittest.TestCase):
    STATE = {"main_geometries": {TWO_KEY: {"solo": "804x930+-1900+119", "duo": "735x898+-2400+148"},
                                 ONE_KEY: {"solo": "800x600+100+100"}},
             "geometry": "1x1+0+0", "duo_geometry": "1x1+0+0"}

    def test_current_layout_entry_per_mode(self):
        self.assertEqual(SelfBot.App._main_geometry_from_state(self.STATE, TWO_KEY, "solo"), "804x930+-1900+119")
        self.assertEqual(SelfBot.App._main_geometry_from_state(self.STATE, TWO_KEY, "duo"), "735x898+-2400+148")
        self.assertIsNone(SelfBot.App._main_geometry_from_state(self.STATE, ONE_KEY, "duo"))

    def test_another_layout_is_never_used(self):
        state = {"main_geometries": {TWO_KEY: {"solo": "804x930+-1900+119"}}, "geometry": "804x930+620+119"}
        self.assertIsNone(SelfBot.App._main_geometry_from_state(state, ONE_KEY, "solo"))

    def test_legacy_flat_keys_when_no_per_layout_data(self):
        state = {"geometry": "804x930+620+119", "duo_geometry": "735x898+788+148"}
        self.assertEqual(SelfBot.App._main_geometry_from_state(state, TWO_KEY, "solo"), "804x930+620+119")
        self.assertEqual(SelfBot.App._main_geometry_from_state(state, TWO_KEY, "duo"), "735x898+788+148")
        self.assertIsNone(SelfBot.App._main_geometry_from_state({}, TWO_KEY, "solo"))

    def test_junk_is_ignored(self):
        self.assertIsNone(SelfBot.App._main_geometry_from_state({"main_geometries": "x"}, TWO_KEY, "solo"))
        self.assertIsNone(SelfBot.App._main_geometry_from_state({"main_geometries": {TWO_KEY: "x"}}, TWO_KEY, "solo"))


@_needs_selfbot
class UsableMainGeometryTests(unittest.TestCase):
    def test_left_monitor_position_is_usable(self):
        with _rects(TWO_MONITORS):
            self.assertEqual(_app()._usable_main_geometry("804x930+-1900+119"), (804, 930, -1900, 119))

    def test_off_every_monitor_or_too_small_is_not(self):
        with _rects(TWO_MONITORS):
            self.assertIsNone(_app()._usable_main_geometry("804x930+-9000+119"))
            self.assertIsNone(_app()._usable_main_geometry("1x1+620+119"))
            self.assertIsNone(_app()._usable_main_geometry("804x930"))
            self.assertIsNone(_app()._usable_main_geometry(None))

    def test_left_monitor_position_is_rejected_once_that_monitor_is_gone(self):
        with _rects(ONE_MONITOR):
            self.assertIsNone(_app()._usable_main_geometry("804x930+-1900+119"))


@_needs_selfbot
class SaveMainGeometryTests(unittest.TestCase):
    def _save(self, rects=TWO_MONITORS, existing=None, root=None, **overrides):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "app_state.json")
            if existing is not None:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(existing, f)
            attrs = _state_attrs(**overrides)
            if root is not None:
                attrs["root"] = root
            app = _app(_state_file=path, **attrs)
            with _rects(rects):
                app._save_last_state()
            with open(path, encoding="utf-8") as f:
                return json.load(f)

    def test_solo_geometry_is_filed_under_the_layout_key(self):
        state = self._save(root=_Root(geometry="804x930+-1900+119"))
        self.assertEqual(state["main_geometries"], {TWO_KEY: {"solo": "804x930+-1900+119"}})
        self.assertEqual(state["geometry"], "804x930+-1900+119")     # flat key still written

    def test_duo_geometry_joins_the_same_layout_entry_and_other_layouts_survive(self):
        existing = {"main_geometries": {TWO_KEY: {"solo": "804x930+-1900+119"},
                                        ONE_KEY: {"solo": "800x600+100+100"}}}
        state = self._save(existing=existing, root=_Root(geometry="735x898+-2400+148"), _duo_mode=True)
        self.assertEqual(state["main_geometries"], {
            TWO_KEY: {"solo": "804x930+-1900+119", "duo": "735x898+-2400+148"},
            ONE_KEY: {"solo": "800x600+100+100"}})

    def test_unmapped_or_tiny_root_never_overwrites_a_good_entry(self):
        existing = {"main_geometries": {TWO_KEY: {"solo": "804x930+-1900+119"}}}
        for root in (_Root(geometry="1x1+0+0"), _Root(geometry="804x930+620+119", mapped=False)):
            state = self._save(existing=existing, root=root)
            self.assertEqual(state["main_geometries"], existing["main_geometries"])


@_needs_selfbot
class MainRestoreWiringTests(unittest.TestCase):
    def test_load_restores_through_the_per_layout_helpers(self):
        src = inspect.getsource(SelfBot.App._load_last_state)
        self.assertIn('_main_geometry_from_state(state, layout_key, "duo")', src)
        self.assertIn('_main_geometry_from_state(state, layout_key, "solo")', src)
        self.assertIn("_usable_main_geometry(", src)
        # The primary-only test is gone.
        self.assertNotIn("x < cur_sw", src)
        self.assertNotIn("saved_sw == cur_sw", src)


@_needs_selfbot
class DialogWiringTests(unittest.TestCase):
    """All three dialogs go through _place_dialog; the prompt editor follows the pattern."""

    def test_every_dialog_is_placed_by_the_one_routine(self):
        for func, kind in ((SelfBot.App.open_prompt_editor, "prompt_editor"),
                           (SelfBot.App.open_skills_editor, "skills_dialog"),
                           (SelfBot.App._open_ps_safety_dialog, "ps_safety")):
            src = inspect.getsource(func)
            self.assertIn("_place_dialog(", src)
            self.assertIn(f'"{kind}"', src)
            self.assertIn(".withdraw()", src)
            self.assertIn(".deiconify()", src)
            self.assertIn('protocol("WM_DELETE_WINDOW"', src)

    def test_editor_no_longer_has_the_fixed_placement(self):
        self.assertNotIn('win.geometry("650x500")', inspect.getsource(SelfBot.App.open_prompt_editor))

    def test_apply_captures_geometry_before_destroying(self):
        src = inspect.getsource(SelfBot.App._apply_prompt)
        capture = src.index("self._last_prompt_editor_geometry = self.prompt_editor_window.geometry()")
        destroy = src.index("self.prompt_editor_window.destroy()")
        self.assertLess(capture, destroy)


if __name__ == "__main__":
    unittest.main()
