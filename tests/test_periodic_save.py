"""The periodic save's tick is one named constant, PERIODIC_SAVE_MS (2026-10-08).

_periodic_save (state_mixin) writes the state file every tick and, when the
message count changed, the run's chat files, then reschedules itself;
MyAgent.py __init__ schedules the first tick. Until 2026-10-08 both sites
carried a bare 5000, the only link between them; now both read
constants.PERIODIC_SAVE_MS, 10 s — the user's request, the chats living in
the OneDrive share since the same day, where every rewrite of a growing
transcript during a run is a re-upload. The interval has no bearing on a
run's speed: the work is one file write on the Tk thread, skipped while
nothing changed.
"""

import unittest
from pathlib import Path

from myagent import constants
import myagent.state_mixin as stm
from tests._util import stub

REPO = Path(__file__).resolve().parents[1]


class _Root:
    def __init__(self):
        self.after_calls = []

    def after(self, ms, fn):
        self.after_calls.append((ms, fn))


class TickTests(unittest.TestCase):

    def test_the_tick_is_ten_seconds(self):
        self.assertEqual(constants.PERIODIC_SAVE_MS, 10_000)

    def host(self, messages=()):
        saved = []
        host = stub(stm.StateMixin, root=_Root(), messages=list(messages),
                    _save_last_state=lambda: saved.append("state"),
                    _auto_save_on_close=lambda: saved.append("chat"))
        return host, saved

    def test_each_tick_reschedules_at_the_constant(self):
        host, saved = self.host()
        host._periodic_save()
        self.assertEqual(host.root.after_calls,
                         [(constants.PERIODIC_SAVE_MS, host._periodic_save)])
        self.assertEqual(saved, ["state"])  # no messages → no chat save

    def test_the_chat_is_saved_only_when_the_message_count_changed(self):
        host, saved = self.host(messages=[{"role": "user", "content": "go"}])
        host._periodic_save()
        host._periodic_save()  # nothing new → the state alone
        host.messages.append({"role": "assistant", "content": "done"})
        host._periodic_save()
        self.assertEqual(saved, ["state", "chat", "state", "state", "chat"])
        self.assertEqual([ms for ms, _ in host.root.after_calls],
                         [constants.PERIODIC_SAVE_MS] * 3)


class WiringTests(unittest.TestCase):

    def test_both_schedules_read_the_constant_and_no_literal_remains(self):
        app = (REPO / "MyAgent.py").read_text(encoding="utf-8")
        state = (REPO / "myagent" / "state_mixin.py").read_text(encoding="utf-8")
        self.assertIn("self.root.after(PERIODIC_SAVE_MS, self._periodic_save)", app)
        self.assertIn("self.root.after(PERIODIC_SAVE_MS, self._periodic_save)", state)
        for src in (app, state):
            self.assertNotRegex(src, r"after\(\s*5000\s*,")


if __name__ == "__main__":
    unittest.main()
