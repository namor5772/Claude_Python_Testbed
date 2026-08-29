"""Window geometry persistence (myagent/state_mixin.py): the parse / visibility /
sanitize helpers, the capture guards that keep junk out of agent_state*.json
(the never-mapped '1x1' root, the Windows maximized chimera, iconified windows),
the per-layout entry selection with its most-recent-visible fallback, the
restore path (incl. migrating a legacy chimera entry to a maximized restore)
and dialog placement.

Display rects are stubbed so every check is layout-deterministic on any machine.
"""
import unittest
from unittest import mock

from myagent.state_mixin import StateMixin, GEOMETRY_KINDS
from myagent.constants import DEFAULT_GEOMETRY
from tests._util import stub

# The desk this was debugged on: a 2560x1440 primary with a second 2560x1440
# to its LEFT (negative x), both at 100% scaling
DUAL = [(0, 0, 2560, 1440), (-2560, 0, 0, 1440)]
SINGLE = [(0, 0, 2560, 1440)]
# L-shaped: a 1080p monitor hanging off the primary's right edge, lower down
LSHAPE = [(0, 0, 2560, 1440), (2560, 600, 4480, 1680)]
KEY = "-2560,0,0,1440|0,0,2560,1440"


def rects(layout):
    return mock.patch.object(StateMixin, "_get_display_rects",
                             staticmethod(lambda: list(layout)))


class FakeWin:
    """Just enough of a Tk toplevel for the geometry layer."""

    def __init__(self, geometry="600x400+100+100", state="normal", mapped=True, exists=True):
        self._geometry, self._state = geometry, state
        self._mapped, self._exists = mapped, exists
        self.calls = []

    def winfo_exists(self):
        return self._exists

    def winfo_ismapped(self):
        return self._mapped

    def state(self, value=None):
        if value is None:
            return self._state
        self.calls.append(("state", value))
        self._state = value

    def geometry(self, value=None):
        if value is None:
            return self._geometry
        self.calls.append(("geometry", value))
        self._geometry = value

    def _parsed(self):
        return StateMixin._parse_geometry(self._geometry) or (1, 1, 0, 0)

    def winfo_x(self):
        return self._parsed()[2]

    def winfo_y(self):
        return self._parsed()[3]

    def winfo_width(self):
        return self._parsed()[0]

    def winfo_height(self):
        return self._parsed()[1]


def host(**attrs):
    return stub(StateMixin, **attrs)


class ParseAndVisibility(unittest.TestCase):

    def test_parse(self):
        self.assertEqual(StateMixin._parse_geometry("679x930+-723+73"), (679, 930, -723, 73))
        self.assertEqual(StateMixin._parse_geometry("=1x1+0+0"), (1, 1, 0, 0))
        for bad in ("1050x930", "", None, "abc", "600x400-10+20", "600x400+10"):
            self.assertIsNone(StateMixin._parse_geometry(bad), bad)

    def test_on_disk_keys_are_stable(self):
        # existing agent_state*.json files restore through these exact names
        self.assertEqual(GEOMETRY_KINDS, {
            "main": "geometry", "editor": "editor_geometry",
            "ps_safety": "ps_safety_dialog_geometry", "prompt": "prompt_dialog_geometry",
            "confirm": "confirm_dialog_geometry", "skills": "skills_dialog_geometry"})

    def test_config_key_is_the_sorted_rects(self):
        with rects(DUAL):
            self.assertEqual(StateMixin._get_monitor_config_key(), KEY)

    def test_visible_on_the_left_monitor(self):
        with rects(DUAL):
            self.assertTrue(StateMixin._geometry_visible(-723, 73, 679, 930))

    def test_left_monitor_gone(self):
        with rects(SINGLE):
            self.assertFalse(StateMixin._geometry_visible(-723, 73, 679, 930))

    def test_dead_corner_of_an_l_shape_is_not_visible(self):
        # inside the virtual desktop's bounding box, yet on no monitor
        with rects(LSHAPE):
            self.assertFalse(StateMixin._geometry_visible(3000, 100, 600, 400))
            self.assertTrue(StateMixin._geometry_visible(3000, 700, 600, 400))

    def test_title_bar_above_every_monitor_is_not_grabbable(self):
        with rects(SINGLE):
            self.assertFalse(StateMixin._geometry_visible(100, -25, 600, 400))
            # the -8 offset a maximized-style frame reports is fine
            self.assertTrue(StateMixin._geometry_visible(100, -8, 600, 400))

    def test_sliver_at_the_right_edge(self):
        with rects(SINGLE):
            self.assertTrue(StateMixin._geometry_visible(2500, 100, 600, 400))   # 60 px showing
            self.assertFalse(StateMixin._geometry_visible(2530, 100, 600, 400))  # 30 px showing


class Sanitize(unittest.TestCase):

    def test_returns_the_normalised_string_or_none(self):
        with rects(DUAL):
            self.assertEqual(StateMixin._sanitize_geometry("679x930+-723+73"), "679x930+-723+73")
            self.assertIsNone(StateMixin._sanitize_geometry("1x1+-723+73"))
            self.assertIsNone(StateMixin._sanitize_geometry("600x400+-9000+73"))
            self.assertIsNone(StateMixin._sanitize_geometry(DEFAULT_GEOMETRY))
            self.assertIsNone(StateMixin._sanitize_geometry(None))

    def test_dialog_min_sizes(self):
        with rects(DUAL):
            self.assertIsNone(StateMixin._sanitize_geometry("380x290+10+10", min_w=400, min_h=300))
            self.assertEqual(StateMixin._sanitize_geometry("400x300+10+10", min_w=400, min_h=300),
                             "400x300+10+10")

    def test_never_falls_back_to_the_main_window_default(self):
        # the old helper returned DEFAULT_GEOMETRY for an off-screen DIALOG, which
        # gave that dialog the main window's 1050x930 size at (0,0)
        with rects(SINGLE):
            self.assertIsNone(StateMixin._sanitize_geometry("560x1100+-1187+336"))


class ClampAndPlace(unittest.TestCase):

    def test_clamp_keeps_a_window_on_its_monitor(self):
        with rects(DUAL):
            self.assertEqual(StateMixin._clamp_to_monitor(-200, 1300, 600, 400), (-600, 1040))
            self.assertEqual(StateMixin._clamp_to_monitor(2400, -50, 600, 400), (1960, 0))
            # a point on no monitor clamps onto the primary
            self.assertEqual(StateMixin._clamp_to_monitor(9000, 9000, 600, 400), (1960, 1040))

    def test_place_uses_the_saved_geometry_when_visible(self):
        h = host(root=FakeWin("800x600+100+100"),
                 _geometry_cache={"confirm": "611x304+-1500+467"})
        dlg = FakeWin()
        with rects(DUAL):
            self.assertEqual(h._place_window(dlg, "confirm", (500, 400)), "611x304+-1500+467")
        self.assertEqual(dlg.calls, [("geometry", "611x304+-1500+467")])

    def test_place_centres_the_default_on_the_parent_when_saved_is_off_screen(self):
        h = host(root=FakeWin("800x600+-1400+200"),
                 _geometry_cache={"confirm": "611x304+1916+467"})
        dlg = FakeWin()
        with rects([(-2560, 0, 0, 1440)]):   # only the left monitor remains
            geo = h._place_window(dlg, "confirm", (500, 400))
        # parent centre (-1000, 500) → top-left (-1250, 300)
        self.assertEqual(geo, "500x400+-1250+300")

    def test_place_clamps_the_default_onto_the_parents_monitor(self):
        h = host(root=FakeWin("800x600+2300+1200"))
        dlg = FakeWin()
        with rects(SINGLE):
            self.assertEqual(h._place_window(dlg, "ps_safety", (560, 1100)), "560x1100+2000+340")

    def test_place_shrinks_the_default_to_the_monitor(self):
        h = host(root=FakeWin("800x600+100+100"))
        dlg = FakeWin()
        with rects([(0, 0, 1920, 1080)]):
            self.assertEqual(h._place_window(dlg, "ps_safety", (560, 1100)), "560x1080+220+0")

    def test_place_on_the_primary_when_the_parent_is_withdrawn(self):
        # --headless: the root is withdrawn, so its position means nothing
        h = host(root=FakeWin("679x930+-723+73", state="withdrawn", mapped=False))
        dlg = FakeWin()
        with rects(DUAL):
            self.assertEqual(h._place_window(dlg, "prompt", (500, 400)), "500x400+1030+520")

    def test_place_honours_an_explicit_parent(self):
        editor = FakeWin("700x640+-2000+300")
        h = host(root=FakeWin("800x600+100+100"))
        dlg = FakeWin()
        with rects(DUAL):
            self.assertEqual(h._place_window(dlg, "skills", (900, 500), parent=editor),
                             "900x500+-2100+370")


class Remember(unittest.TestCase):

    def test_a_normal_mapped_window_is_cached_and_marks_dirty(self):
        h = host()
        self.assertEqual(h._remember_geometry("editor", FakeWin("700x640+-2000+300")),
                         "700x640+-2000+300")
        self.assertEqual(h._geo_cache(), {"editor": "700x640+-2000+300"})
        self.assertTrue(h._geometry_dirty)

    def test_the_never_mapped_root_is_ignored(self):
        # the save in __init__: state 'normal' but not yet mapped, geometry '1x1+X+Y'
        h = host(_geometry_cache={"main": "679x930+-723+73"})
        self.assertIsNone(h._remember_geometry("main", FakeWin("1x1+-723+73", mapped=False)))
        self.assertEqual(h._geo_cache()["main"], "679x930+-723+73")
        self.assertFalse(getattr(h, "_geometry_dirty", False))

    def test_a_maximized_main_keeps_the_normal_geometry_and_sets_the_flag(self):
        h = host(_geometry_cache={"main": "679x930+-723+73"})
        self.assertIsNone(h._remember_geometry("main", FakeWin("2560x1417+-723+73", state="zoomed")))
        self.assertEqual(h._geo_cache()["main"], "679x930+-723+73")
        self.assertTrue(h._main_zoomed)

    def test_un_maximizing_clears_the_flag(self):
        h = host(_main_zoomed=True)
        h._remember_geometry("main", FakeWin("679x930+-723+73"))
        self.assertFalse(h._main_zoomed)

    def test_iconified_leaves_cache_and_flag_alone(self):
        h = host(_geometry_cache={"main": "679x930+-723+73"}, _main_zoomed=True)
        self.assertIsNone(h._remember_geometry(
            "main", FakeWin("679x930+-723+73", state="iconic", mapped=False)))
        self.assertEqual(h._geo_cache()["main"], "679x930+-723+73")
        self.assertTrue(h._main_zoomed)

    def test_a_destroyed_window(self):
        h = host()
        self.assertIsNone(h._remember_geometry("confirm", FakeWin(exists=False)))
        self.assertEqual(h._geo_cache(), {})

    def test_a_maximized_dialog_is_not_captured(self):
        h = host(_geometry_cache={"skills": "900x500+10+10"})
        h._remember_geometry("skills", FakeWin("2560x1417+10+10", state="zoomed"))
        self.assertEqual(h._geo_cache()["skills"], "900x500+10+10")
        self.assertFalse(hasattr(h, "_main_zoomed"))


class SaveEntry(unittest.TestCase):

    def test_entry_keys_and_flags(self):
        h = host(root=FakeWin("679x930+-723+73"), _main_zoomed=False,
                 _geometry_cache={"editor": "700x640+-2000+300"})
        entry = h._build_geometry_entry()
        self.assertEqual(entry["geometry"], "679x930+-723+73")
        self.assertEqual(entry["editor_geometry"], "700x640+-2000+300")
        self.assertIs(entry["main_zoomed"], False)
        self.assertIn("saved_at", entry)
        self.assertEqual(set(entry) - {"main_zoomed", "saved_at"},
                         {"geometry", "editor_geometry"})

    def test_open_windows_are_recaptured_over_the_cache(self):
        h = host(root=FakeWin("679x930+-723+73"), _prompt_dialog=FakeWin("611x400+1012+129"),
                 _geometry_cache={"prompt": "500x400+0+0"})
        self.assertEqual(h._build_geometry_entry()["prompt_dialog_geometry"], "611x400+1012+129")

    def test_a_maximized_main_writes_the_normal_geometry_plus_the_flag(self):
        h = host(root=FakeWin("2560x1417+-723+73", state="zoomed"),
                 _geometry_cache={"main": "679x930+-723+73"})
        entry = h._build_geometry_entry()
        self.assertEqual(entry["geometry"], "679x930+-723+73")
        self.assertIs(entry["main_zoomed"], True)

    def test_empty_when_nothing_real_was_ever_captured(self):
        h = host(root=FakeWin("1x1+0+0", mapped=False))
        self.assertEqual(h._build_geometry_entry(), {})

    def test_not_dirty_passes_existing_entries_through_verbatim(self):
        # --headless, or still unmapped: the file's entries are left untouched
        existing = {"0,0,2560,1440": {"geometry": "752x1196+1683+52"}}
        h = host(root=FakeWin("679x930+-723+73"), _geometry_cache={"main": "679x930+-723+73"})
        self.assertEqual(h._geometries_for_save(existing, KEY), existing)
        self.assertEqual(h._geometries_for_save(None, KEY), {})

    def test_dirty_replaces_only_this_layouts_entry(self):
        existing = {"0,0,2560,1440": {"geometry": "752x1196+1683+52"},
                    KEY: {"geometry": "2560x1417+-723+73"}}
        h = host(root=FakeWin("679x930+-723+73"), _geometry_dirty=True)
        out = h._geometries_for_save(existing, KEY)
        self.assertEqual(out["0,0,2560,1440"], {"geometry": "752x1196+1683+52"})
        self.assertEqual(out[KEY]["geometry"], "679x930+-723+73")
        self.assertEqual(existing[KEY], {"geometry": "2560x1417+-723+73"})  # input untouched


class SelectEntry(unittest.TestCase):

    def test_this_layouts_own_entry_wins(self):
        state = {"geometries": {
            KEY: {"geometry": "679x930+-723+73"},
            "0,0,2560,1440": {"geometry": "752x1196+1683+52", "saved_at": "2099-01-01T00:00:00"}}}
        with rects(DUAL):
            self.assertEqual(host()._select_geometry_entry(state, KEY)["geometry"],
                             "679x930+-723+73")

    def test_legacy_flat_fields(self):
        state = {"geometry": "700x600+10+10", "editor_geometry": "700x640+20+20", "provider": "x"}
        self.assertEqual(host()._select_geometry_entry(state, KEY),
                         {"geometry": "700x600+10+10", "editor_geometry": "700x640+20+20"})

    def test_falls_back_to_the_most_recent_layout_still_visible_here(self):
        state = {"geometries": {
            "0,0,1920,1080": {"geometry": "739x828+1023+66", "saved_at": "2026-08-01T00:00:00"},
            "0,0,2560,1440": {"geometry": "752x1196+1683+52", "saved_at": "2026-08-20T00:00:00"},
            "-2560,0,0,1440|0,0,1920,1080": {"geometry": "1050x930+-2400+61",
                                             "saved_at": "2026-08-25T00:00:00"},
        }}
        # the left monitor is off: the newest entry's position is invisible here
        with rects(SINGLE):
            self.assertEqual(host()._select_geometry_entry(state, KEY)["geometry"],
                             "752x1196+1683+52")

    def test_nothing_usable(self):
        state = {"geometries": {"0,0,1920,1080": {"geometry": "739x828+-9000+66"}}, "junk": 1}
        with rects(SINGLE):
            self.assertEqual(host()._select_geometry_entry(state, KEY), {})
        self.assertEqual(host()._select_geometry_entry({"geometries": "bogus"}, KEY), {})


class ApplyEntry(unittest.TestCase):

    def setUp(self):
        self.root = FakeWin("1050x930+0+0", mapped=False)
        self.h = host(root=self.root)

    def test_a_visible_main_geometry_is_applied_and_dialogs_cached(self):
        with rects(DUAL):
            self.h._apply_geometry_entry({
                "geometry": "679x930+-723+73", "editor_geometry": "700x640+-2000+300",
                "prompt_dialog_geometry": "garbage", "main_zoomed": False})
        self.assertEqual(self.root.calls, [("geometry", "679x930+-723+73")])
        self.assertEqual(self.h._geo_cache(),
                         {"main": "679x930+-723+73", "editor": "700x640+-2000+300"})
        self.assertFalse(self.h._main_zoomed)
        # a restore is not a capture: nothing is written back until the window is real
        self.assertFalse(getattr(self.h, "_geometry_dirty", False))

    def test_an_off_screen_main_falls_back_to_the_default_centred_on_primary(self):
        with rects(SINGLE):
            self.h._apply_geometry_entry({"geometry": "679x930+-723+73", "main_zoomed": False})
        # centred on the single 2560x1440 primary, not a bare WM-placed default
        self.assertEqual(self.root.calls, [("geometry", "1050x930+755+255")])
        self.assertNotIn("main", self.h._geo_cache())

    def test_the_default_is_explicitly_positioned_on_the_primary(self):
        with rects(DUAL):
            self.assertEqual(self.h._default_main_geometry(), "1050x930+755+255")
        # a small primary shrinks and clamps the default rather than overflowing
        with rects([(0, 0, 800, 600)]):
            self.assertEqual(self.h._default_main_geometry(), "800x600+0+0")

    def test_zoomed_restores_the_normal_geometry_then_maximizes(self):
        with rects(DUAL):
            self.h._apply_geometry_entry({"geometry": "679x930+-723+73", "main_zoomed": True})
        self.assertEqual(self.root.calls, [("geometry", "679x930+-723+73"), ("state", "zoomed")])
        self.assertTrue(self.h._main_zoomed)

    def test_a_legacy_chimera_is_migrated_to_a_maximized_restore(self):
        # written by the old code from a maximized window: zoomed size + normal position
        with rects(DUAL):
            self.h._apply_geometry_entry({"geometry": "2560x1417+-723+73"})
        self.assertEqual(self.root.calls, [("geometry", "1050x930+-723+73"), ("state", "zoomed")])
        self.assertTrue(self.h._geometry_dirty)  # the first save rewrites the healed entry

    def test_a_legacy_normal_entry_is_not_treated_as_zoomed(self):
        with rects(DUAL):
            self.h._apply_geometry_entry({"geometry": "679x930+-723+73"})
        self.assertEqual(self.root.calls, [("geometry", "679x930+-723+73")])
        self.assertFalse(self.h._main_zoomed)

    def test_an_empty_entry(self):
        with rects(DUAL):
            self.h._apply_geometry_entry({})
        self.assertEqual(self.root.calls, [("geometry", "1050x930+755+255")])


class Tracking(unittest.TestCase):

    def test_only_the_roots_own_configure_is_captured(self):
        class Root(FakeWin):
            def bind(self, seq, fn, add=None):
                self.bound = (seq, fn, add)

        root = Root("679x930+-723+73")
        h = host(root=root)
        h._bind_geometry_tracking()
        seq, fn, add = root.bound
        self.assertEqual((seq, add), ("<Configure>", "+"))
        fn(mock.Mock(widget=FakeWin("100x20+5+5")))   # a child widget's <Configure>
        self.assertEqual(h._geo_cache(), {})
        fn(mock.Mock(widget=root))
        self.assertEqual(h._geo_cache(), {"main": "679x930+-723+73"})


if __name__ == "__main__":
    unittest.main()
