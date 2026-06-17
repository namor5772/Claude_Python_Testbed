"""Characterization test: mail confirm-action BYPASS path (Phase 2 target).

Phase 2 extracts the char-identical askyesno+bypass scaffold from
_confirm_gmail_action / _confirm_proton_action / _confirm_outlook_action into a
shared helper. The only legitimate per-provider difference is the bypass-warning
string. This test pins those three exact strings (the bullet glyph, the wording,
the trailing newline), the return value True, and that EXACTLY ONE warning event
is queued. The interactive askyesno path needs a real Tk display + user click,
so it is not unit-testable; Phase 2 preserves it by delegation and the surface
guard proves the methods still exist.

Values are ACTUAL captured outputs."""
import queue
import unittest

from tests._util import stub
from myagent.gmail_mixin import GmailMixin
from myagent.protonmail_mixin import ProtonMailMixin
from myagent.outlook_mixin import OutlookMixin

CASES = [
    (GmailMixin, "_confirm_gmail_action", "⚠ Gmail confirm bypassed for thetool\n"),
    (ProtonMailMixin, "_confirm_proton_action", "⚠ Proton confirm bypassed for thetool\n"),
    (OutlookMixin, "_confirm_outlook_action", "⚠ Outlook confirm bypassed for thetool\n"),
]


class TestConfirmBypass(unittest.TestCase):
    def test_bypass(self):
        for cls, method, expected_content in CASES:
            with self.subTest(cls=cls.__name__):
                q = queue.Queue()
                obj = stub(cls, queue=q, _disabled_confirm_patterns={"thetool"})
                ret = getattr(obj, method)("thetool", "Title", "Summary", "Detail")
                self.assertIs(ret, True)
                evt = q.get_nowait()
                self.assertEqual(
                    evt, {"type": "warning", "content": expected_content})
                self.assertTrue(q.empty(), "exactly one warning event expected")


if __name__ == "__main__":
    unittest.main()
