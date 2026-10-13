"""Tool parity with MyAgent (2026-10-14).

The user asked for the same tools in SelfBot as in MyAgent — the extra Desktop
ones, Excel and Physical, and whatever else it lacked. SelfBot now inherits
MyAgent's Document, File, Desktop, Browser, Excel, Physical and Voice mixins
(the way it already inherited the MCP and mail ones), takes the Desktop and
Browser schemas from myagent.constants instead of its own copies, and gained
Excel and Physical checkboxes (plus MyAgent's Diag toggle). Its own desktop
and browser implementations are gone, so the two apps' tools cannot drift.

What this pins: every MyAgent tool an Anthropic-only chat can use is offered
(the exceptions are listed, with the reason); the file and document tools need
no checkbox; each set follows its checkbox and its package flag; the dispatch
routes every new family to the mixin and refuses it when its checkbox is off;
the real document / file / view_image tools run through SelfBot's App; the
toggles persist and bundle; the in-file copies and the v1 DPI bootstrap are
gone; and, on Windows, a real screenshot comes back through the inherited
pipeline.
"""

import inspect
import os
import queue
import re
import sys
import tempfile
import unittest
from unittest import mock

from myagent import constants as c
from myagent.browser_mixin import BrowserMixin
from myagent.desktop_mixin import DesktopMixin
from myagent.document_mixin import DocumentMixin
from myagent.excel_mixin import ExcelMixin
from myagent.file_mixin import FileMixin
from myagent.helpers import _ToolBlock
from myagent.physical_mixin import PhysicalMixin
from myagent.voice_mixin import VoiceMixin

_saved_argv = sys.argv
sys.argv = ["SelfBot.py"]
try:
    import SelfBot
    _IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover
    SelfBot = None
    _IMPORT_ERROR = exc
finally:
    sys.argv = _saved_argv

_needs_selfbot = unittest.skipIf(SelfBot is None, f"SelfBot not importable: {_IMPORT_ERROR}")

# MyAgent tools an Anthropic-only chat has no use for, and why.
NOT_FOR_SELFBOT = {
    "web_search", "fetch_webpage",          # Anthropic's server-side web tools serve instead
    "user_prompt",                          # the chat IS the conversation
    "manage_instructions", "run_instruction",   # MyAgent's instruction system
    "find_element",                         # Gemini's pointing API
}
MYAGENT_SETS = (c.TOOLS, c.FILE_TOOLS, c.DESKTOP_TOOLS, c.BROWSER_TOOLS,
                c.EXCEL_TOOLS, c.PHYSICAL_TOOLS, c.META_TOOLS)
MYAGENT_NAMES = {t["name"] for s in MYAGENT_SETS for t in s}
TOGGLES = ("desktop", "browser", "meta", "mcp", "google", "proton", "outlook",
           "excel", "physical", "pause")
ALL_FLAGS = {"_HAS_DESKTOP": True, "_HAS_EXCEL": True, "_HAS_PHYSICAL": True,
             "_HAS_CAMERA": True, "_HAS_MICROPHONE": True}


class _Var:
    def __init__(self, value=False):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def _app(**toggles):
    """A bare SelfBot App (no Tk) with the toggle variables and the coordinate
    state DesktopMixin keeps."""
    app = SelfBot.App.__new__(SelfBot.App)
    app.queue = queue.Queue()
    app.skills = {}
    app.diag_enabled = _Var(False)
    for name in TOGGLES:
        setattr(app, f"{name}_enabled", _Var(toggles.get(name, False)))
    app._anthropic_unsupported = set()
    app._screenshot_scale, app._screenshot_offset, app._screenshot_dims = 1.0, (0, 0), (0, 0)
    app._display_states, app._display_images = {}, {}
    app._display_full_states, app._display_full_images = {}, {}
    app._last_screenshot_bytes = None
    app._file_read_paths = set()
    return app


def _flags(**overrides):
    values = dict(ALL_FLAGS, **overrides)
    patches = [mock.patch.object(SelfBot, name, value) for name, value in values.items()]
    return _Patches(patches)


class _Patches:
    def __init__(self, patches):
        self.patches = patches

    def __enter__(self):
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self.patches:
            p.stop()
        return False


def _names(app):
    return {t["name"] for t in app._get_tools()}


def _call(app, name, inp):
    return app._execute_tool(_ToolBlock(name, "toolu_1", inp))


def _source():
    with open(SelfBot.__file__, encoding="utf-8") as f:
        return f.read()


# ── the catalogue ───────────────────────────────────────────────────────

@_needs_selfbot
class CatalogueTests(unittest.TestCase):
    def test_every_myagent_tool_an_anthropic_chat_can_use_is_offered(self):
        app = _app(desktop=True, browser=True, excel=True, physical=True, meta=True, pause=True)
        with _flags():
            names = _names(app)
        missing = (MYAGENT_NAMES - NOT_FOR_SELFBOT) - names
        self.assertEqual(missing, set(), f"MyAgent tools SelfBot does not offer: {sorted(missing)}")
        self.assertNotIn("find_element", names)
        self.assertTrue({"manage_prompts", "pause_conversation"} <= names)   # SelfBot's own

    def test_the_file_and_document_tools_need_no_checkbox(self):
        with _flags():
            names = _names(_app())
        self.assertTrue({t["name"] for t in c.FILE_TOOLS} <= names)
        self.assertIn("read_document", names)
        for name in ("screenshot", "browser_open", "excel_read", "camera_capture"):
            self.assertNotIn(name, names)

    def test_each_set_follows_its_checkbox_and_its_package(self):
        excel = {t["name"] for t in c.EXCEL_TOOLS}
        with _flags():
            self.assertTrue(excel <= _names(_app(excel=True)))
            self.assertEqual(excel & _names(_app(excel=False)), set())
            physical = _names(_app(physical=True))
            self.assertTrue({"camera_capture", "microphone_listen"} <= physical)
        with _flags(_HAS_EXCEL=False):
            self.assertEqual(excel & _names(_app(excel=True)), set())
        with _flags(_HAS_MICROPHONE=False):
            physical = _names(_app(physical=True))
            self.assertIn("camera_capture", physical)
            self.assertNotIn("microphone_listen", physical)
        with _flags(_HAS_CAMERA=False, _HAS_MICROPHONE=False, _HAS_PHYSICAL=False):
            self.assertEqual({"camera_capture", "microphone_listen"} & _names(_app(physical=True)), set())
        with _flags(_HAS_DESKTOP=False):
            self.assertNotIn("screenshot", _names(_app(desktop=True)))

    def test_the_schemas_are_myagents_own(self):
        self.assertIs(SelfBot.DESKTOP_TOOLS, c.DESKTOP_TOOLS)
        self.assertIs(SelfBot.BROWSER_TOOLS, c.BROWSER_TOOLS)
        self.assertIs(SelfBot.EXCEL_TOOLS, c.EXCEL_TOOLS)
        self.assertIs(SelfBot.PHYSICAL_TOOLS, c.PHYSICAL_TOOLS)
        self.assertIs(SelfBot.FILE_TOOLS, c.FILE_TOOLS)
        self.assertEqual(SelfBot.READ_DOCUMENT_TOOLS, [t for t in c.TOOLS if t["name"] == "read_document"])

    def test_the_bases_and_the_gone_copies(self):
        for mixin in (DocumentMixin, FileMixin, DesktopMixin, BrowserMixin, ExcelMixin,
                      PhysicalMixin, VoiceMixin):
            self.assertTrue(issubclass(SelfBot.App, mixin), mixin.__name__)
        src = _source()
        for gone in (r"^    def do_screenshot\(", r"^    def do_browser_open\(", r"^    def _ensure_browser\(",
                     r"^    KNOWN_APPS = \{", r"^DESKTOP_TOOLS = \[", r"^BROWSER_TOOLS = \[",
                     r"^    def do_view_image\(", r"^    def _macos_display_screenshot\("):
            self.assertIsNone(re.search(gone, src, re.M), gone)
        # the desktop pipeline's own implementation is DesktopMixin's
        self.assertIs(SelfBot.App.do_screenshot, DesktopMixin.do_screenshot)
        self.assertIs(SelfBot.App.do_browser_download, BrowserMixin.do_browser_download)

    def test_the_dpi_bootstrap_is_myagents(self):
        src = _source()
        self.assertIn("SetProcessDpiAwarenessContext", src)
        self.assertIn("c_void_p(-4)", src)
        self.assertLess(src.index("SetProcessDpiAwarenessContext"), src.index("import tkinter as tk"))


# ── the dispatch ────────────────────────────────────────────────────────

@_needs_selfbot
class DispatchTests(unittest.TestCase):
    def test_excel_routes_to_the_mixin_and_follows_its_gate(self):
        app = _app(excel=True)
        app.do_excel_read = lambda inp: f"read {inp['range']}"
        with _flags():
            self.assertEqual(_call(app, "excel_read", {"range": "A1"}), "read A1")
            app.excel_enabled.set(False)
            self.assertIn("Excel checkbox", _call(app, "excel_read", {"range": "A1"}))
        with _flags(_HAS_EXCEL=False):
            self.assertIn("xlwings", _call(app, "excel_read", {"range": "A1"}))

    def test_physical_routes_to_the_mixin_and_follows_its_gate(self):
        app = _app(physical=True)
        app.do_camera_capture = lambda inp: ["photo", inp]
        app.do_microphone_listen = lambda inp: f"heard for {inp.get('seconds')}"
        with _flags():
            self.assertEqual(_call(app, "camera_capture", {"delay_seconds": 2}), ["photo", {"delay_seconds": 2}])
            self.assertEqual(_call(app, "microphone_listen", {"seconds": 3}), "heard for 3")
            app.physical_enabled.set(False)
            self.assertIn("Physical checkbox", _call(app, "camera_capture", {}))
            self.assertIn("Physical checkbox", _call(app, "microphone_listen", {}))
        with _flags(_HAS_CAMERA=False):
            self.assertIn("OpenCV", _call(_app(physical=True), "camera_capture", {}))
        with _flags(_HAS_MICROPHONE=False):
            self.assertIn("sounddevice", _call(_app(physical=True), "microphone_listen", {}))

    def test_the_real_document_and_file_tools_run_through_selfbot(self):
        app = _app()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "notes.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("alpha\nbeta\n")
            with _flags():
                doc = _call(app, "read_document", {"path": path})
                self.assertIn("alpha", doc)
                self.assertIn("beta", _call(app, "read_file", {"path": path}))
                found = _call(app, "glob_files", {"pattern": os.path.join(tmp, "*.txt")})
                self.assertIn("notes.txt", found)
                edited = _call(app, "edit_file", {"path": path, "old_string": "beta", "new_string": "gamma"})
                self.assertNotIn("error", edited.lower())
            with open(path, encoding="utf-8") as f:
                self.assertEqual(f.read(), "alpha\ngamma\n")
            self.assertIn(os.path.normcase(os.path.abspath(path)),
                          {os.path.normcase(p) for p in app._file_read_paths})

    def test_view_image_and_the_desktop_gate(self):
        app = _app(desktop=True)
        with tempfile.TemporaryDirectory() as tmp:
            from PIL import Image
            path = os.path.join(tmp, "dot.png")
            Image.new("RGB", (8, 8), (10, 200, 30)).save(path, format="PNG")
            with _flags():
                result = _call(app, "view_image", {"path": path})
                self.assertEqual([b["type"] for b in result], ["text", "image"])
                self.assertTrue(result[0]["text"].startswith("Image file"))
                app.desktop_enabled.set(False)
                self.assertIn("Desktop checkbox", _call(app, "view_image", {"path": path}))
                self.assertIn("Desktop checkbox", _call(app, "screenshot", {}))

    def test_browser_download_routes_with_its_arguments(self):
        app = _app(browser=True)
        app.do_browser_download = lambda **kw: kw
        with _flags():
            kw = _call(app, "browser_download", {"selector": "a.pdf", "save_path": "C:/x/y.pdf", "timeout_s": 5})
        self.assertEqual(kw, {"save_path": "C:/x/y.pdf", "selector": "a.pdf", "text": None, "timeout_s": 5})
        app.browser_enabled.set(False)
        self.assertIn("Browser checkbox", _call(app, "browser_download", {}))

    def test_a_stray_find_element_is_refused_in_words(self):
        with _flags():
            self.assertIn("not available in SelfBot", _call(_app(desktop=True), "find_element", {"description": "x"}))

    def test_the_parallel_safe_set_is_myagents(self):
        src = inspect.getsource(SelfBot.App.stream_worker)
        self.assertIn('PARALLEL_SAFE = {"csv_search", "get_skill"} | set(PARALLEL_SAFE_TOOLS)', src)
        self.assertTrue({"read_document", "read_file", "glob_files", "grep_files", "view_image"}
                        <= SelfBot.PARALLEL_SAFE_TOOLS)


# ── persistence and bundling ────────────────────────────────────────────

@_needs_selfbot
class PersistenceTests(unittest.TestCase):
    def test_the_toggles_persist_and_bundle(self):
        self.assertTrue({"excel", "physical"} <= set(SelfBot.App._PROMPT_TOGGLE_KEYS))
        schema = next(t for t in SelfBot.SELFBOT_META_TOOLS if t["name"] == "manage_prompts")
        self.assertTrue({"excel", "physical"} <= set(schema["input_schema"]["properties"]))
        save = inspect.getsource(SelfBot.App._save_last_state)
        load = inspect.getsource(SelfBot.App._load_last_state)
        for key in ("excel_enabled", "physical_enabled", "diag_enabled"):
            self.assertIn(f'"{key}": self.{key}.get()', save, key)
            self.assertIn(f'state.get("{key}")', load, key)
        capture = inspect.getsource(SelfBot.App._capture_prompt_settings)
        apply = inspect.getsource(SelfBot.App._apply_prompt_settings)
        for key in ("excel", "physical"):
            self.assertIn(f'"{key}": self.{key}_enabled.get()', capture, key)
            self.assertIn(f'if "{key}" in entry', apply, key)

    def test_the_checkboxes_exist_in_the_rows(self):
        src = inspect.getsource(SelfBot.App.setup_ui)
        for text, var in (("Excel", "excel_enabled"), ("Physical", "physical_enabled"), ("Diag", "diag_enabled")):
            self.assertIn(f'text="{text}", variable=self.{var}', src, text)

    def test_a_new_chat_resets_the_file_tools_memory(self):
        self.assertIn("self._file_read_paths = set()", inspect.getsource(SelfBot.App._new_chat))

    def test_the_stt_clients_exist_with_the_package_for_transcription_only(self):
        # the Mike button and Voice Setup need them too since the voice port
        # (2026-10-14); sounddevice is imported at the first press, never here
        src = inspect.getsource(SelfBot.App.__init__)
        self.assertIn("self.openai_client = self.xai_client = self.gemini_client = None", src)
        self.assertIn("if _HAS_MYAGENT_TOOLS:\n            try:\n                import openai", src)
        self.assertNotIn("_HAS_MICROPHONE:", src)


# ── a real capture through the inherited pipeline ───────────────────────

@_needs_selfbot
@unittest.skipUnless(sys.platform == "win32" and c._HAS_DESKTOP, "a Windows display")
class LiveScreenshotTests(unittest.TestCase):
    def test_a_screenshot_comes_back_through_desktop_mixin(self):
        app = _app(desktop=True)
        with _flags():
            result = _call(app, "screenshot", {"display": 0, "grid": True})
        self.assertIsInstance(result, list, result)
        kinds = [b["type"] for b in result]
        self.assertIn("image", kinds)
        self.assertEqual(result[[i for i, k in enumerate(kinds) if k == "image"][0]]["source"]["media_type"],
                         "image/png")
        self.assertNotEqual(app._screenshot_dims, (0, 0))
        self.assertIn(0, app._display_states)


if __name__ == "__main__":
    unittest.main()
