"""Characterization tests for the Agent Request's message in the output pane
(2026-09-22).

When a run asks the user something, the Agent Request dialog shows a message —
the model's own (the user_prompt tool) or MyAgent's stock one (Convo mode).
Only the REPLY used to be copied into the main window ("You:"); the question
lived in the dialog alone and was gone once answered — from the pane and from
the saved .txt transcript, which is a dump of the pane. Now
safety_mixin.do_user_prompt queues it as `user_prompt_request` and
event_loop_mixin.check_queue draws it under an "Agent Request:" heading, in
the look of the "Agent:" text.

Pinned here:
* heading / body / spacing, on the REAL setup_ui's output pane and tags;
* the body's OWN tag — it looks like "assistant" but must not be it, because
  _post_process_latex rewrites every assistant range (lone $, backslashes and
  braces go) and a request must stay what the dialog showed;
* one blank line between paragraphs whatever came before (_ensure_blank_line),
  the reply echo's spacing unchanged;
* the REAL do_user_prompt queues the request whatever happens to the dialog:
  dismissed (no echo follows) or answered (request first, then the echo).

The pane tests build the real main window on a withdrawn Tk root. The dialog
tests run the real dialog, fully transparent, inside a real mainloop with
do_user_prompt on a worker thread — as stream_worker calls it, and because a
Tk call from another thread needs the main thread IN mainloop. All skip
without a display; the answered-dialog test also where the display will not
grant the keyboard focus a synthetic Return needs.
"""

import queue
import threading
import tkinter as tk
import unittest

from myagent.chat_mixin import ChatMixin
from myagent.event_loop_mixin import EventLoopMixin
from myagent.safety_mixin import SafetyMixin
from myagent.ui_mixin import UIMixin
from tests._util import stub

DISMISSED = "[User dismissed the dialog without responding]"


class _Window(UIMixin, EventLoopMixin, ChatMixin):
    """The real main window plus the queue renderer and the pane helpers."""


class PaneTests(unittest.TestCase):

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:  # headless box, no display
            self.skipTest(f"Tk unavailable: {exc}")
        self.root.withdraw()
        app = stub(_Window, root=self.root, provider="OpenAI", model="m",
                   available_models=["m"], temperature=1.0, queue=queue.Queue())
        for name in ("debug_enabled", "tool_calls_enabled", "show_activity",
                     "show_thinking", "save_thinking", "diag_enabled"):
            setattr(app, name, tk.BooleanVar(master=self.root, value=False))
        app._update_title = lambda: None
        app._get_display_name = lambda model_id: model_id
        app.open_instruction_editor = app._start_agent = app._stop_agent = lambda: None
        app._voice_setup_from_main = lambda: None
        app.setup_ui()
        self.app = app
        self.pane = app.chat_display

    def tearDown(self):
        self.root.destroy()

    def feed(self, *messages):
        for message in messages:
            self.app.queue.put(message)
        self.app.check_queue()

    def text(self):
        return self.pane.get("1.0", "end-1c")

    def tags_of(self, needle):
        start = self.pane.search(needle, "1.0")
        self.assertTrue(start, f"{needle!r} is not in the pane")
        return set(self.pane.tag_names(start))

    @staticmethod
    def request(content):
        return {"type": "user_prompt_request", "content": content}

    @staticmethod
    def echo(content):
        return {"type": "user_prompt_echo", "content": content}

    # ── the request ─────────────────────────────────────────────────────

    def test_the_request_is_a_paragraph_of_its_own_under_an_agent_request_heading(self):
        self.feed({"type": "label"},
                  {"type": "text_delta", "content": "I need one detail."},
                  self.request("Which city?"))
        self.assertEqual(self.text(),
                         "Agent:\nI need one detail.\n\nAgent Request:\nWhich city?\n\n")
        self.assertEqual(self.tags_of("Agent Request:"), {"assistant_label"})
        self.assertEqual(self.tags_of("Which city?"), {"agent_request"})

    def test_it_looks_exactly_like_the_agents_own_text(self):
        for option in ("foreground", "background", "font"):
            self.assertEqual(self.pane.tag_cget("agent_request", option),
                             self.pane.tag_cget("assistant", option), option)
        self.assertEqual(self.pane.tag_cget("agent_request", "foreground"), "#2e7d32")

    def test_request_then_reply_then_the_next_agent_text_are_one_blank_line_apart(self):
        self.feed(self.request("Which city?"), self.echo("Sydney"), {"type": "label"},
                  {"type": "text_delta", "content": "Sunny."})
        self.assertEqual(self.text(),
                         "Agent Request:\nWhich city?\n\nYou:\nSydney\n\nAgent:\nSunny.")

    def test_a_dismissed_request_still_stands_clear_of_what_follows(self):
        # No echo is queued for a dismissed dialog: the next thing in the pane
        # is the model's next turn.
        self.feed(self.request("Which city?"), {"type": "label"})
        self.assertEqual(self.text(), "Agent Request:\nWhich city?\n\nAgent:\n")

    def test_the_request_stays_what_the_dialog_showed_through_the_latex_pass(self):
        asked = r"Pay $250.00 to {ACME} from C:\Users\me\bills?"
        self.feed({"type": "label"},
                  {"type": "text_delta", "content": r"The total is $5 at C:\temp."},
                  self.request(asked), self.echo("yes"),
                  {"type": "post_process_latex"})
        # The control: the agent's own text IS rewritten by that pass...
        self.assertIn("The total is 5 at C:temp.", self.text())
        # ...the request is not — and neither is the reply.
        self.assertIn(f"Agent Request:\n{asked}\n\nYou:\nyes\n\n", self.text())
        self.assertNotIn("assistant", self.tags_of(asked))

    def test_trailing_whitespace_goes_and_a_multi_line_request_keeps_its_lines(self):
        self.feed(self.request("Pick one:\n1. Rome\n2. Oslo\n\n  "), self.echo("2"))
        self.assertEqual(self.text(),
                         "Agent Request:\nPick one:\n1. Rome\n2. Oslo\n\nYou:\n2\n\n")

    def test_an_empty_request_is_the_heading_alone(self):
        self.feed(self.request(""), self.echo("hello"))
        self.assertEqual(self.text(), "Agent Request:\n\nYou:\nhello\n\n")

    def test_a_null_message_from_a_sloppy_model_is_shown_not_dropped(self):
        # check_queue swallows exceptions, so None + str would vanish silently.
        self.feed(self.request(None))
        self.assertEqual(self.text(), "Agent Request:\nNone\n\n")

    # ── the echo's spacing is what it was ───────────────────────────────

    def test_the_reply_echo_keeps_one_blank_line_above_it(self):
        self.app.show_activity.set(True)
        self.feed({"type": "label"}, {"type": "text_delta", "content": "One moment"},
                  self.echo("first"))                       # after text with no newline
        self.feed({"type": "tool_info", "content": "Requesting user input...\n"},
                  self.echo("second"))                      # after a finished line
        self.assertEqual(self.text(),
                         "Agent:\nOne moment\n\nYou:\nfirst\n\n"
                         "Requesting user input...\n\nYou:\nsecond\n\n")

    # ── _ensure_blank_line ──────────────────────────────────────────────

    def test_ensure_blank_line_adds_only_what_is_missing(self):
        cases = (("", ""), ("x", "x\n\n"), ("x\n", "x\n\n"), ("x\n\n", "x\n\n"),
                 ("x\n\n\n", "x\n\n\n"), ("\n", "\n"))
        self.pane.config(state="normal")
        for before, after in cases:
            self.pane.delete("1.0", tk.END)
            self.pane.insert("1.0", before)
            self.app._ensure_blank_line()
            self.assertEqual(self.text(), after, repr(before))


class _Dictation:
    """Stands in for voice_mixin's _VoiceDictation: never recording."""
    state = "idle"

    def toggle(self):
        pass

    def shutdown(self):
        pass


class _DialogHost(SafetyMixin):
    """do_user_prompt's host: the dialog is the real one, the parts other
    mixins contribute (voice row, geometry persistence) are stand-ins."""

    # No transient() / grab_set(): the parent is an invisible test window.
    _headless = True
    _prompt_dialog = None

    def __init__(self, root):
        self.root = root
        self.queue = queue.Queue()
        self.dictation_auto_send = tk.BooleanVar(master=root, value=False)
        self.send = None    # what the real row calls when Auto-send is ticked

    def _voice_build_row(self, dlg, resp_text, auto_send, send):
        row = tk.Frame(dlg)
        mike = tk.Button(row, text="Mike")
        mike.pack(side=tk.LEFT)
        auto = tk.Checkbutton(row, text="Auto-send", variable=auto_send)
        auto.pack(side=tk.LEFT)
        self.send = send
        return row, mike, auto, _Dictation()

    def _place_window(self, win, kind, default_size, **_kwargs):
        win.attributes("-alpha", 0.0)   # mapped and focusable, never seen
        win.geometry("420x420+0+0")

    def _remember_geometry(self, kind, win):
        pass


def _walk(widget):
    for child in widget.winfo_children():
        yield child
        yield from _walk(child)


class DialogTests(unittest.TestCase):

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:  # headless box, no display
            self.skipTest(f"Tk unavailable: {exc}")
        self.root.geometry("240x240+0+0")
        self.root.attributes("-alpha", 0.0)

    def tearDown(self):
        self.root.destroy()

    def run_dialog(self, message, act):
        """Run the real do_user_prompt(message) on a worker thread and hand
        the dialog, once it is on screen, to act(dialog). Returns the host and
        {"reply": ...} (or {"error": ...})."""
        host = self.host = _DialogHost(self.root)
        outcome = {}

        def work():
            try:
                outcome["reply"] = host.do_user_prompt(message)
            except Exception as exc:  # reported by the caller's assertions
                outcome["error"] = exc

        worker = threading.Thread(target=work, daemon=True)

        def wait_for_dialog():
            dialog = host._prompt_dialog
            if dialog is not None and dialog.winfo_ismapped():
                act(dialog)
            elif worker.is_alive():
                self.root.after(20, wait_for_dialog)

        def wait_for_worker():
            if worker.is_alive():
                self.root.after(20, wait_for_worker)
            else:
                self.root.quit()

        self.root.after(0, worker.start)
        self.root.after(20, wait_for_dialog)
        self.root.after(40, wait_for_worker)
        watchdog = self.root.after(20000, self.root.quit)
        self.root.mainloop()
        self.root.after_cancel(watchdog)
        self.assertNotIn("error", outcome)
        self.assertIn("reply", outcome, "the dialog never closed")
        return host, outcome

    @staticmethod
    def dismiss(dialog):
        # What the window's [X] runs.
        dialog.tk.call(dialog.protocol("WM_DELETE_WINDOW"))

    @staticmethod
    def queued(host):
        items = []
        while not host.queue.empty():
            items.append(host.queue.get_nowait())
        return items

    def test_the_dialogs_message_is_queued_for_the_pane_even_when_nobody_answers(self):
        asked = "Reply, or type empty / 'quit' / 'exit' / 'stop' to end."   # Convo mode's
        shown = []

        def act(dialog):
            boxes = [w for w in _walk(dialog) if isinstance(w, tk.Text)]
            shown.append(boxes[0].get("1.0", "end-1c"))     # the read-only message box
            self.dismiss(dialog)

        host, outcome = self.run_dialog(asked, act)
        self.assertEqual(outcome["reply"], DISMISSED)
        self.assertEqual(shown, [asked])
        # The pane gets the very text the dialog showed; a dismissal echoes nothing.
        self.assertEqual(self.queued(host),
                         [{"type": "user_prompt_request", "content": asked}])

    def test_an_answered_dialog_queues_the_request_and_then_the_reply(self):
        asked = "Pay $250.00 to ACME?"
        state = {}

        def act(dialog):
            box = [w for w in _walk(dialog)
                   if isinstance(w, tk.Text) and str(w.cget("state")) == "normal"][0]
            box.insert("1.0", "yes, go ahead")
            box.focus_force()
            self.root.after(150, lambda: send(dialog, box))

        def send(dialog, box):
            if self.root.focus_get() is not box:
                state["no_focus"] = True
                self.dismiss(dialog)
            else:
                box.event_generate("<Return>")               # Enter sends the reply

        host, outcome = self.run_dialog(asked, act)
        if state.get("no_focus"):
            self.skipTest("the display will not give this process the keyboard focus")
        self.assertEqual(outcome["reply"], "yes, go ahead")
        self.assertEqual(self.queued(host),
                         [{"type": "user_prompt_request", "content": asked},
                          {"type": "user_prompt_echo", "content": "yes, go ahead"}])

    def test_a_transcript_sent_by_auto_send_answers_the_dialog_like_enter(self):
        # The dialog hands the voice row its own send path (on_inject, which
        # is defined AFTER the row is built — hence a late-bound lambda) for
        # the Auto-send box (2026-09-23). Here the transcript "lands" by hand
        # and the row's send is called, as voice_mixin does once it has.
        asked = "Which account?"

        def act(dialog):
            box = [w for w in _walk(dialog)
                   if isinstance(w, tk.Text) and str(w.cget("state")) == "normal"][0]
            box.insert("1.0", "the joint account")
            self.host.send()

        host, outcome = self.run_dialog(asked, act)
        self.assertEqual(outcome["reply"], "the joint account")
        self.assertEqual(self.queued(host),
                         [{"type": "user_prompt_request", "content": asked},
                          {"type": "user_prompt_echo", "content": "the joint account"}])


if __name__ == "__main__":
    unittest.main()
