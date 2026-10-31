"""run_instruction's three additions (2026-10-31): `prepend_text`,
`show_window` and `answers`.

* `prepend_text` travels like `extra_text`, by temp file, as --prepend-file:
  the text goes plain IN FRONT of the child's saved instruction for that run
  (Heartbeat's way of handing a phone message to a standing instruction);
* `show_window` appends --show-window after --headless (the flag it
  qualifies) and is dropped when the child is not headless;
* `answers` launches the child with a relay folder of its own (--relay-dir,
  the protocol of myagent/relay.py that Heartbeat's listener uses for the
  phone) and answers each Agent Request the child opens with the next entry,
  the moment the request appears — `_relay_serve`, called from the waited
  poll loop or, for a fire-and-forget child, from a daemon thread
  (`_relay_server`). The list spent, the next question gets the EMPTY reply,
  which stops the child's run as an empty reply typed at the PC does; [] stops
  it at its first question. A request the PC answered first is recorded and
  consumes no answer. The waited report lists every question and what it got
  (`_relay_report`); the folder is removed when the child exits, on every
  path.

Pinned here over a fake child whose `poll()` is where it "runs": it asks its
questions through the relay folder one at a time, each after the previous got
its reply, and exits once answered or stopped — subprocess.Popen mocked out,
`time.sleep` a no-op, so the tests take milliseconds.
"""

import json
import os
import pathlib
import unittest
from unittest import mock

from myagent import constants
from myagent import relay
from myagent import skills_mixin
from myagent.skills_mixin import SkillsMixin

REPO = pathlib.Path(__file__).resolve().parents[1]


def _read(*parts):
    return (REPO / pathlib.Path(*parts)).read_text(encoding="utf-8")


class _Host(SkillsMixin):
    def __init__(self):
        self.stop_requested = False

    def _load_saved_instructions(self):
        return {"Child": {"text": "t"}}


class _Child:
    """A fake child process. `poll()` is where it runs: with a relay folder it
    asks `questions` one by one — each after the previous was answered — and
    exits (0) once every question got a reply, or at once on the empty reply
    (the run stopped). `pc` names the questions the PC answers first: the
    child then clears the request and leaves answered.json, as MyAgent's
    `_relay_settle` does."""

    def __init__(self, questions=(), result=None, pc=()):
        self.pid = 42
        self.returncode = None
        self.questions = list(questions)
        self.pc = set(pc)
        self.asked = 0
        self.replies = []            # what each question got: the text, "" = stopped, None = PC
        self.relay_dir = None
        self.result_path = None
        self.result = result if result is not None else {"status": "completed", "final_text": "done"}
        self.terminated = False
        self.polls = 0

    def bind(self, cmd, **_kw):
        if "--relay-dir" in cmd:
            self.relay_dir = cmd[cmd.index("--relay-dir") + 1]
        if "--result-file" in cmd:
            self.result_path = cmd[cmd.index("--result-file") + 1]
        return self

    def poll(self):
        self.polls += 1
        if self.returncode is not None:
            return self.returncode
        if self.relay_dir is not None:
            if self.asked > len(self.replies):            # a question is open
                text = relay.read_reply(self.relay_dir, self.asked)
                if text is None:
                    return None                           # still waiting for the parent
                self.replies.append(text)
                relay.clear(self.relay_dir)
                if text == "":
                    return self._exit("stopped")          # the empty reply stops the run
            if self.asked < len(self.questions):
                self.asked += 1
                if self.asked in self.pc:                 # answered at the PC: no relay reply
                    relay.write_request(self.relay_dir, self.asked, self.questions[self.asked - 1])
                    relay.clear(self.relay_dir)
                    relay.mark_answered(self.relay_dir, self.asked)
                    self.replies.append(None)
                    return None
                relay.write_request(self.relay_dir, self.asked, self.questions[self.asked - 1])
                return None
        return self._exit()

    def _exit(self, status=None):
        if self.result_path:
            result = dict(self.result)
            if status:
                result["status"] = status
            with open(self.result_path, "w", encoding="utf-8") as f:
                json.dump(result, f)
        self.returncode = 0
        return 0

    def terminate(self):
        self.terminated = True
        self.returncode = -1


class _Case(unittest.TestCase):

    def setUp(self):
        sleep = mock.patch.object(skills_mixin.time, "sleep", lambda s: None)
        sleep.start()
        self.addCleanup(sleep.stop)

    def launch(self, params, child=None):
        child = child or _Child()
        with mock.patch("myagent.skills_mixin.subprocess.Popen", side_effect=child.bind) as popen:
            out = _Host().do_run_instruction(dict(params, name="Child"))
        return out, popen.call_args[0][0] if popen.call_args else None, child


class FlagTests(_Case):

    def test_prepend_text_travels_by_file_in_front_beside_extra_text(self):
        out, cmd, _ = self.launch({"prepend_text": "Pay the water bill.", "extra_text": "ctx"})
        self.assertIn("--prepend-file", cmd)
        self.assertIn("--extra-file", cmd)
        path = cmd[cmd.index("--prepend-file") + 1]
        try:
            with open(path, encoding="utf-8") as f:
                self.assertEqual(f.read(), "Pay the water bill.")
        finally:
            os.remove(path)
            os.remove(cmd[cmd.index("--extra-file") + 1])   # fire-and-forget leaves both for the child
        self.assertIn("with extra task context with a prompt in front of its instruction", out)

    def test_no_prepend_text_means_no_flag(self):
        _, cmd, _ = self.launch({})
        self.assertNotIn("--prepend-file", cmd)

    def test_show_window_follows_headless(self):
        _, cmd, _ = self.launch({"show_window": True})
        self.assertEqual(cmd[cmd.index("--headless") + 1], "--show-window")

    def test_show_window_is_dropped_without_headless_and_absent_by_default(self):
        _, cmd, _ = self.launch({"show_window": True, "headless": False})
        self.assertNotIn("--show-window", cmd)
        self.assertNotIn("--headless", cmd)
        _, cmd, _ = self.launch({})
        self.assertNotIn("--show-window", cmd)

    def test_no_answers_means_no_relay_folder(self):
        _, cmd, _ = self.launch({"wait": True})
        self.assertNotIn("--relay-dir", cmd)

    def test_a_non_list_is_refused_before_anything_is_launched(self):
        for bad in (5, {"a": 1}, ["ok", 7]):
            with self.subTest(answers=bad):
                out, cmd, _ = self.launch({"answers": bad})
                self.assertTrue(out.startswith("Error: 'answers' must be a list of strings"), out)
                self.assertIsNone(cmd)

    def test_the_waited_run_removes_every_temp_file_and_the_folder(self):
        out, cmd, child = self.launch({"wait": True, "answers": ["A"], "prepend_text": "p",
                                       "extra_text": "e"})
        self.assertIn("finished (status=completed", out)
        for flag in ("--result-file", "--extra-file", "--prepend-file", "--relay-dir"):
            self.assertFalse(os.path.exists(cmd[cmd.index(flag) + 1]), flag)


class AnswerTests(_Case):

    def test_each_question_gets_the_next_answer_then_the_empty_reply_stops_the_child(self):
        child = _Child(["Which account?", "Proceed with the payment?", "Anything else?"])
        out, cmd, child = self.launch({"wait": True, "answers": ["Savings", "Yes"]}, child)
        self.assertEqual(child.replies, ["Savings", "Yes", ""])
        self.assertIn("finished (status=stopped", out)       # the child's own account of the empty reply
        self.assertIn("\nAgent Requests: 3\n", out)
        self.assertIn('  #1 "Which account?" → answered "Savings"', out)
        self.assertIn('  #2 "Proceed with the payment?" → answered "Yes"', out)
        self.assertIn('  #3 "Anything else?" → no answer left: the empty reply stopped the run', out)
        self.assertIn("\nFinal report:\ndone", out)
        self.assertFalse(os.path.exists(cmd[cmd.index("--relay-dir") + 1]))

    def test_an_empty_list_stops_the_child_at_its_first_question(self):
        child = _Child(["Which account?"])
        out, _, child = self.launch({"wait": True, "answers": []}, child)
        self.assertEqual(child.replies, [""])
        self.assertIn('  #1 "Which account?" → no answer left: the empty reply stopped the run', out)

    def test_a_child_that_asks_nothing_reports_the_spare_answers(self):
        out, _, child = self.launch({"wait": True, "answers": ["A", "B"]})
        self.assertEqual(child.replies, [])
        self.assertIn("\nAgent Requests: none (2 answer(s) given, none needed).", out)

    def test_a_bare_string_is_one_answer(self):
        child = _Child(["Which account?", "Sure?"])
        out, _, child = self.launch({"wait": True, "answers": "Savings"}, child)
        self.assertEqual(child.replies, ["Savings", ""])

    def test_a_request_answered_at_the_pc_is_recorded_and_consumes_no_answer(self):
        child = _Child(["Which account?", "Proceed?"], pc={1})
        out, _, child = self.launch({"wait": True, "answers": ["Yes"]}, child)
        self.assertEqual(child.replies, [None, "Yes"])        # the PC took #1; #2 got the FIRST answer
        self.assertIn("  #1 answered at the PC", out)
        self.assertIn('  #2 "Proceed?" → answered "Yes"', out)

    def test_long_questions_and_answers_are_clipped_in_the_report(self):
        child = _Child(["Q " * 200])
        out, _, _ = self.launch({"wait": True, "answers": ["A " * 200]}, child)
        line = next(ln for ln in out.split("\n") if ln.startswith("  #1 "))
        self.assertLess(len(line), 300)
        self.assertEqual(line.count("…"), 2)

    def test_stop_pressed_while_waiting_still_removes_the_folder(self):
        host = _Host()
        host.stop_requested = True
        child = _Child(["Q?"])
        with mock.patch("myagent.skills_mixin.subprocess.Popen", side_effect=child.bind) as popen:
            out = host.do_run_instruction({"name": "Child", "wait": True, "answers": ["A"]})
        self.assertIn("cancelled: STOP pressed", out)
        self.assertTrue(child.terminated)
        cmd = popen.call_args[0][0]
        self.assertFalse(os.path.exists(cmd[cmd.index("--relay-dir") + 1]))

    def test_fire_and_forget_serves_on_a_thread_and_removes_the_folder_when_the_child_exits(self):
        child = _Child(["Which account?", "Proceed?"])
        started = []

        class _InlineThread:
            """threading.Thread run to completion at start(): the fake child
            progresses inside _relay_server's own poll loop."""
            def __init__(self, target=None, args=(), name=None, daemon=None):
                self.target, self.args, self.name, self.daemon = target, args, name, daemon

            def start(self):
                started.append(self)
                self.target(*self.args)

        with mock.patch.object(skills_mixin.threading, "Thread", _InlineThread):
            out, cmd, child = self.launch({"answers": ["Savings"]}, child)
        self.assertEqual(len(started), 1)
        self.assertTrue(started[0].daemon)
        self.assertEqual(started[0].name, "relay-42")
        self.assertEqual(child.replies, ["Savings", ""])
        self.assertIn("(PID 42); its Agent Requests are answered from the 1 answer(s) given, "
                      "the next one with an empty reply, which stops it.", out)
        self.assertFalse(os.path.exists(cmd[cmd.index("--relay-dir") + 1]))

    def test_fire_and_forget_without_answers_starts_no_thread(self):
        with mock.patch.object(skills_mixin.threading, "Thread") as thread:
            out, _, _ = self.launch({})
        thread.assert_not_called()
        self.assertEqual(out, "Launched instruction 'Child' in headless mode (PID 42).")


class ServeTests(unittest.TestCase):
    """_relay_serve on its own, over a real temp folder."""

    def setUp(self):
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def test_nothing_open_writes_nothing(self):
        served = []
        self.assertFalse(SkillsMixin._relay_serve(self.dir, ["A"], served))
        self.assertEqual(served, [])
        self.assertIsNone(relay.read_reply(self.dir, 1))

    def test_an_open_request_is_answered_once_until_the_child_closes_it(self):
        served = []
        relay.write_request(self.dir, 1, "Q1?")
        self.assertTrue(SkillsMixin._relay_serve(self.dir, ["A1", "A2"], served))
        self.assertEqual(relay.read_reply(self.dir, 1), "A1")
        self.assertFalse(SkillsMixin._relay_serve(self.dir, ["A1", "A2"], served))   # same seq: no rewrite
        self.assertEqual(len(served), 1)
        relay.clear(self.dir)
        relay.write_request(self.dir, 2, "Q2?")
        self.assertTrue(SkillsMixin._relay_serve(self.dir, ["A1", "A2"], served))
        self.assertEqual(relay.read_reply(self.dir, 2), "A2")
        relay.clear(self.dir)
        relay.write_request(self.dir, 3, "Q3?")
        SkillsMixin._relay_serve(self.dir, ["A1", "A2"], served)
        self.assertEqual(relay.read_reply(self.dir, 3), "")                          # the list spent
        self.assertEqual([r["outcome"] for r in served], ["answered", "answered", "stopped"])
        self.assertEqual([r["question"] for r in served], ["Q1?", "Q2?", "Q3?"])


class SchemaTests(unittest.TestCase):

    def test_the_schema_names_the_three_parameters(self):
        tool = next(t for t in constants.META_TOOLS if t["name"] == "run_instruction")
        props = tool["input_schema"]["properties"]
        self.assertEqual(props["prepend_text"]["type"], "string")
        self.assertEqual(props["show_window"]["type"], "boolean")
        self.assertEqual(props["answers"], {"type": "array", "items": {"type": "string"},
                                            "description": props["answers"]["description"]})
        self.assertIn("EMPTY reply", props["answers"]["description"])
        self.assertIn("[] = stop", props["answers"]["description"])
        self.assertIn("IN FRONT", props["prepend_text"]["description"])
        self.assertIn("headless=false", props["show_window"]["description"])
        self.assertIn("answers", tool["description"])

    def test_the_launcher_passes_the_flags_myagent_defines(self):
        main = _read("MyAgent.py")
        src = _read("myagent", "skills_mixin.py")
        for flag in ("--prepend-file", "--show-window", "--relay-dir"):
            self.assertIn(f'"{flag}"', main)
            self.assertIn(f'"{flag}"', src)
        self.assertIn("shutil.rmtree(relay_dir, ignore_errors=True)", src)
        self.assertEqual(src.count("shutil.rmtree(relay_dir, ignore_errors=True)"), 3)  # launch failure, waited, thread


if __name__ == "__main__":
    unittest.main()
