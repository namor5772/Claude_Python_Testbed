"""SelfBot's voice input — MyAgent's VoiceMixin, adjusted (2026-10-14).

The Mike button, the Auto-send box and the status line sit under SelfBot's
message box (the row MyAgent's Agent Request dialog carries under its reply
box, built by the inherited `_voice_build_row` over SelfBot's own send path),
and a Voice Setup button sits at the left of the Debug row, opening the
inherited dialog over the per-user settings file MyAgent's uses. Four points
are SelfBot's own: no end-word rule (a dictated "Quit." is a message), the
Auto-send box kept like the other toggles (state file + prompt bundle, no
instruction store), the key-status wording, and `_place_window` as a shim
over `_place_dialog`. The Enter rule of the dialog applies to the message
box: Enter while recording ends the recording, while transcribing is
swallowed.
"""
import inspect
import time
import tkinter as tk
import unittest
from types import SimpleNamespace

from myagent.voice_mixin import VoiceMixin, _VoiceDictation
from tests.test_selfbot_anthropic_parity import SelfBot, _needs_selfbot
from tests.test_voice import _FakeRecorder, _FakeSD


def _bare(root=None, recorder=None, transcribe=None):
    """A bare SelfBot App over the voice test fakes: the real inherited row,
    dialog and state machine, with the microphone, the transcription and the
    user's settings file stood in for."""
    app = SelfBot.App.__new__(SelfBot.App)
    app.root = root
    app._send_delay = 0
    app.sent, app.state_saves = [], []
    app.recorder = recorder or _FakeRecorder()
    app.cfg = VoiceMixin._voice_sanitize_config({})
    app._voice_load_config = lambda path=None: app.cfg      # never the user's real file
    app._voice_sd = lambda rescan=False: _FakeSD
    app._voice_new_recorder = lambda sd: app.recorder
    app._voice_transcribe = transcribe or (lambda cfg, wav: ("Quit.", {
        "model": cfg["models"][cfg["provider"]], "note": "", "elapsed": 0.1}))
    app._save_last_state = lambda: app.state_saves.append(1)
    app.send_message = lambda: app.sent.append(app.input_field.get("1.0", "end-1c"))
    app.openai_client = app.xai_client = app.gemini_client = None
    if root is not None:
        app.dictation_auto_send = tk.BooleanVar(master=root, value=False)
        app.input_field = tk.Text(root)
    return app


def _find(widget, cls, text):
    for child in widget.winfo_children():
        if child.winfo_class() == cls and str(child.cget("text")) == text:
            return child
        found = _find(child, cls, text)
        if found is not None:
            return found
    return None


# ── 1. the row under the message box ──────────────────────────────────────

@_needs_selfbot
class RowTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:  # headless box, no display
            self.skipTest(f"Tk unavailable: {exc}")
        self.root.withdraw()
        self.apps = []
        _VoiceDictation.live = 0

    def tearDown(self):
        _VoiceDictation.live = 0
        for app in self.apps:        # drop the BooleanVar on the Tk thread (test_voice's note)
            app.dictation_auto_send = None
        self.root.destroy()

    def build(self, **kw):
        app = _bare(self.root, **kw)
        self.apps.append(app)
        row, mike, auto, dictation = app._voice_build_row(
            self.root, app.input_field, app.dictation_auto_send, app._send_as_enter)
        app._dictation = dictation
        return app, row, mike, auto, dictation

    def dictate(self, mike, dictation, timeout=5.0):
        mike.invoke()
        mike.invoke()
        end = time.monotonic() + timeout
        while dictation.state != "idle" and time.monotonic() < end:
            self.root.update()
            time.sleep(0.01)
        self.root.update()
        self.assertEqual(dictation.state, "idle")

    def test_the_row_is_myagents_mike_auto_send_and_status(self):
        app, row, mike, auto, _ = self.build()
        status = row.winfo_children()[-1]
        self.assertEqual(row.winfo_children(), [mike, auto, status])
        self.assertEqual((mike.cget("text"), auto.cget("text")), ("Mike", "Auto-send"))
        self.assertEqual(str(auto.cget("variable")), str(app.dictation_auto_send))

    def test_a_dictated_end_word_lands_as_heard_not_bare(self):
        # MyAgent lands "Quit." as the bare Convo-mode word; SelfBot has no such word
        self.assertEqual(VoiceMixin._voice_end_word("Quit."), "quit")
        self.assertEqual(SelfBot.App._voice_end_word("Quit."), "")
        app, _, mike, _, d = self.build()
        self.dictate(mike, d)
        self.assertEqual(app.input_field.get("1.0", "end-1c"), "Quit.")
        self.assertEqual(app.sent, [])

    def test_ticked_the_transcript_goes_through_selfbots_enter_path(self):
        app, _, mike, auto, d = self.build()
        app.input_field.insert("1.0", "Shaun,")
        auto.invoke()
        self.assertTrue(app.dictation_auto_send.get())
        self.assertEqual(app.state_saves, [1])          # the tick saved the state file
        self.dictate(mike, d)
        self.assertEqual(app.sent, ["Shaun, Quit."])    # sent once, after it landed

    def test_ticked_with_the_duo_delay_the_send_is_parked_like_enter(self):
        app, _, mike, auto, d = self.build()
        app._send_delay = 60000
        auto.invoke()
        self.dictate(mike, d)
        self.assertEqual(app.sent, [])
        self.assertEqual(str(app.input_field.cget("state")), "disabled")
        self.root.after_cancel(app._send_timer)

    def test_nothing_landed_nothing_sent(self):
        app, _, mike, auto, d = self.build(recorder=_FakeRecorder(seconds=0.2))
        auto.invoke()
        self.dictate(mike, d)
        self.assertEqual(app.sent, [])
        self.assertEqual(app.input_field.get("1.0", "end-1c"), "")


# ── 2. the Enter rule on the message box ──────────────────────────────────

@_needs_selfbot
class EnterRuleTests(unittest.TestCase):
    def _app(self, state):
        app = _bare()
        app.toggles = []
        app._dictation = None if state is None else SimpleNamespace(
            state=state, toggle=lambda: app.toggles.append(1))
        app.input_field = SimpleNamespace(get=lambda *a: "hi", config=lambda **k: app.cfgs.append(k))
        app.cfgs, app.afters = [], []
        app.root = SimpleNamespace(after=lambda ms, fn: app.afters.append((ms, fn)) or "t1")
        app.send_message = lambda: app.sent.append("sent")
        return app

    def test_enter_while_recording_ends_the_recording(self):
        app = self._app("recording")
        self.assertEqual(app.on_enter_key(SimpleNamespace(state=0)), "break")
        self.assertEqual((app.toggles, app.sent), ([1], []))

    def test_enter_while_transcribing_is_swallowed(self):
        app = self._app("transcribing")
        self.assertEqual(app.on_enter_key(SimpleNamespace(state=0)), "break")
        self.assertEqual((app.toggles, app.sent), ([], []))

    def test_enter_otherwise_sends_as_before(self):
        for state in (None, "idle"):
            app = self._app(state)
            self.assertEqual(app.on_enter_key(SimpleNamespace(state=0)), "break")
            self.assertEqual(app.sent, ["sent"])
            self.assertIsNone(app.on_enter_key(SimpleNamespace(state=1)))   # Shift: a newline
            self.assertEqual(app.sent, ["sent"])

    def test_the_duo_delay_parks_the_send(self):
        app = self._app("idle")
        app._send_delay = 500
        app.on_enter_key(SimpleNamespace(state=0))
        self.assertEqual(app.sent, [])
        self.assertEqual(app.cfgs, [{"state": "disabled"}])
        self.assertEqual(app.afters[0][0], 500)
        self.assertEqual(app.afters[0][1], app._delayed_send)
        self.assertEqual(app._send_timer, "t1")


# ── 3. the overrides ──────────────────────────────────────────────────────

@_needs_selfbot
class OverrideTests(unittest.TestCase):
    def test_the_key_status_names_selfbot(self):
        app = _bare()
        message, missing = app._voice_key_status("xAI")
        self.assertTrue(missing)
        self.assertTrue(message.endswith("then restart SelfBot)."), message)
        theirs = VoiceMixin._voice_key_status(app, "xAI")[0]
        self.assertEqual(message, theirs.replace("restart MyAgent", "restart SelfBot"))
        app.xai_client = object()
        self.assertEqual(app._voice_key_status("xAI"), ("XAI_API_KEY is set.", False))

    def test_auto_send_is_kept_like_the_other_toggles(self):
        app = _bare()
        app._voice_auto_send_changed()
        self.assertEqual(app.state_saves, [1])
        src = inspect.getsource(SelfBot.App._voice_auto_send_changed)
        self.assertNotIn("_load_saved_instructions", src)
        self.assertNotIn("_save_instructions_to_disk", src)

    def test_place_window_is_a_shim_over_place_dialog(self):
        app = _bare()
        calls = []

        def place_dialog(win, kind, default_size, min_size=(400, 300)):
            calls.append((kind, default_size, min_size))
            return "500x400+10+20"

        app._place_dialog = place_dialog
        self.assertEqual(app._place_window("w", "voice_setup", (500, 400), parent=object()),
                         "500x400+10+20")
        self.assertEqual(app._place_window("w", "voice_setup", (500, 400), min_size=(1, 2)),
                         "500x400+10+20")
        self.assertEqual(calls, [("voice_setup", (500, 400), (400, 300)),
                                 ("voice_setup", (500, 400), (1, 2))])
        self.assertEqual(app._parse_geometry("500x400+10+20"), (500, 400, 10, 20))


# ── 4. the real Voice Setup dialog, in SelfBot ────────────────────────────

@_needs_selfbot
class SetupDialogTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:  # headless box, no display
            self.skipTest(f"Tk unavailable: {exc}")
        # a transient of a withdrawn root never maps: transparent, mapped
        self.root.attributes("-alpha", 0.0)
        self.root.geometry("300x200+40+40")
        self.root.update()
        _VoiceDictation.live = 0

    def tearDown(self):
        _VoiceDictation.live = 0
        self.root.destroy()

    def test_the_dialog_opens_centred_through_place_dialog_and_cancel_closes_it(self):
        app = _bare(self.root)
        app._voice_setup_dialog = None
        placed = []
        real_place = SelfBot.App._place_dialog

        def spy(win, kind, default_size, min_size=(400, 300)):
            geo = real_place(app, win, kind, default_size, min_size=min_size)
            placed.append((kind, geo))
            return geo

        app._place_dialog = spy
        seen = {}

        def cancel():
            dlg = app._voice_setup_dialog
            if dlg is None or not dlg.winfo_exists():
                self.root.after(50, cancel)
                return
            seen["title"] = dlg.title()
            seen["provider"] = _find(dlg, "TCombobox", "") is not None
            button = _find(dlg, "Button", "Cancel")
            seen["cancel"] = button is not None
            button.invoke()

        self.root.after(300, cancel)
        app._voice_setup_from_main()            # blocks in wait_window until Cancel
        self.assertEqual([k for k, _ in placed], ["voice_setup"])
        self.assertRegex(placed[0][1], r"^\d+x\d+\+-?\d+\+-?\d+$")
        self.assertIsNone(app._voice_setup_dialog)
        self.assertTrue(seen.get("cancel"))
        self.assertIn("Voice", seen.get("title", ""))
        app.dictation_auto_send = None


# ── 5. persistence and wiring ─────────────────────────────────────────────

@_needs_selfbot
class WiringTests(unittest.TestCase):
    def test_the_app_inherits_voicemixin(self):
        self.assertIn(VoiceMixin, SelfBot.App.__mro__)

    def test_init_creates_the_setting_and_the_slot(self):
        src = inspect.getsource(SelfBot.App.__init__)
        self.assertIn("self.dictation_auto_send = tk.BooleanVar(value=False)", src)
        self.assertIn("self._dictation = None", src)

    def test_setup_ui_builds_the_row_under_the_message_box_and_the_button_on_the_debug_row(self):
        src = inspect.getsource(SelfBot.App.setup_ui)
        row = src.index("self._voice_build_row(self.root, self.input_field,")
        self.assertLess(src.index("self.input_field.bind(\"<Return>\", self.on_enter_key)"), row)
        self.assertIn("self.dictation_auto_send, self._send_as_enter))", src)
        self.assertIn("self.voice_row.grid(row=5,", src)
        self.assertLess(src.rindex("if _HAS_MYAGENT_TOOLS:", 0, row), row)   # no row without the package
        for anchor in ("button_frame.grid(row=6,", "checkbox_frame.grid(row=7,",
                       "tools_frame.grid(row=8,", "self.attach_label.grid(row=9,"):
            self.assertIn(anchor, src)
        setup = src.index("text=\"Voice Setup\"")
        self.assertLess(src.index("checkbox_frame.grid(row=7,"), setup)
        self.assertLess(setup, src.index("text=\"Debug\""))               # created first: Tab first
        self.assertIn("command=lambda: self._voice_setup_from_main()", src)
        self.assertIn("self.voice_setup_button.config(state=tk.DISABLED)", src)

    def test_the_setting_rides_the_state_file_the_prompt_bundle_and_the_tool(self):
        self.assertIn("dictation_auto_send", SelfBot.App._PROMPT_TOGGLE_KEYS)
        whole = inspect.getsource(SelfBot)
        self.assertIn("\"dictation_auto_send\": self.dictation_auto_send.get(),", whole)
        self.assertIn("if state.get(\"dictation_auto_send\"):", whole)
        self.assertIn("if \"dictation_auto_send\" in entry:", whole)
        self.assertEqual(whole.count("\"dictation_auto_send\": self.dictation_auto_send.get(),"), 2)
        tool = [t for t in SelfBot.SELFBOT_META_TOOLS if t["name"] == "manage_prompts"][0]
        self.assertEqual(tool["input_schema"]["properties"]["dictation_auto_send"]["type"], "boolean")

    def test_the_stt_clients_are_built_with_the_package_alone(self):
        src = inspect.getsource(SelfBot.App.__init__)
        self.assertIn("if _HAS_MYAGENT_TOOLS:\n            try:\n                import openai", src)
        self.assertNotIn("_HAS_MICROPHONE:", src)


# ── 6. the hands-free loop: Mike holds the focus after every answer ───────

class _Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


@_needs_selfbot
class FocusForReplyTests(unittest.TestCase):
    """With the Auto-send box ticked, a reply's end puts the focus on Mike so
    Enter or Space starts the next dictation (the user's request, later on
    2026-10-14) — unless something is already typed, where Enter must keep
    meaning send; unticked, the message box takes it as before."""

    def _app(self, ticked, typed="", mike=True):
        app = _bare()
        app.focused = []
        app.dictation_auto_send = _Var(ticked)
        app.input_field = SimpleNamespace(get=lambda *a: typed,
                                          focus_set=lambda: app.focused.append("box"))
        if mike:
            app.mike_button = SimpleNamespace(focus_set=lambda: app.focused.append("mike"))
        return app

    def test_ticked_and_nothing_typed_mike_takes_the_focus(self):
        app = self._app(True)
        self.assertTrue(app._focus_for_reply())
        self.assertEqual(app.focused, ["mike"])

    def test_ticked_but_text_already_typed_the_box_keeps_it(self):
        app = self._app(True, typed="Shaun, ")
        self.assertFalse(app._focus_for_reply())
        self.assertEqual(app.focused, ["box"])

    def test_unticked_the_box_takes_it_as_before(self):
        app = self._app(False)
        self.assertFalse(app._focus_for_reply())
        self.assertEqual(app.focused, ["box"])

    def test_without_the_row_the_box_takes_it(self):
        app = self._app(True, mike=False)
        self.assertFalse(app._focus_for_reply())
        self.assertEqual(app.focused, ["box"])

    def test_the_reply_tails_and_the_launch_restore_go_through_it(self):
        src = inspect.getsource(SelfBot.App.check_queue)
        complete = src.index('elif msg["type"] == "complete":')
        error = src.index('elif msg["type"] == "error":')
        self.assertIn("self._focus_for_reply()", src[complete:error])
        self.assertIn("self._focus_for_reply()", src[error:error + 400])
        self.assertNotIn("self.input_field.focus_set()", src[complete:error])
        load = inspect.getsource(SelfBot.App._load_last_state)
        self.assertIn("self.dictation_auto_send.set(True)\n            self._focus_for_reply()", load)


if __name__ == "__main__":
    unittest.main()
