"""HTTP 413 request_too_large on the Anthropic caller (2026-10-13).

The API caps a request BODY at 32 MB. A screenshot of a 2560x1440 display,
resized to the Anthropic cap, is ~1 MB of base64, and the agentic loop keeps
every one in the history — the chat JustChat_2026-10-13_180525 (Opus 5.5,
a trail-camera contact-sheet review) died on call #33 with 32 screenshots
behind it: "Error code: 413 - request_too_large". Bytes, not tokens, so the
context-overflow rung (a 400 "prompt is too long") never saw it, and a 413
is a plain APIStatusError that the ladder re-raised.

Now `helpers.strip_old_images` replaces the oldest half of the images with
a short note (the text and tool history stays whole; the first user
message's reference images are never touched) and the caller retries on the
same list; with no image left it falls back to the overflow rung's round
trim, and with nothing to trim it raises. Pinned here: the pure helper, and
the REAL _stream_anthropic_call over the fake client of
tests/test_fast_mode.py.
"""

import inspect
import unittest

import anthropic

from myagent.anthropic_mixin import AnthropicMixin
from myagent.helpers import IMAGE_REMOVED_NOTE, strip_old_images
from tests.test_fast_mode import _WireHost
from tests.test_review_core import _drain


def _img(tag):
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                        "data": "A" * 100 + tag}}


def _shot(n):
    """One screenshot round: tool_use, then the tool_result carrying the image."""
    return [
        {"role": "assistant", "content": [{"type": "tool_use", "id": f"t{n}",
                                           "name": "screenshot", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{n}",
                                      "content": [{"type": "text", "text": f"Display 0 #{n}"},
                                                  _img(f"shot{n}")]}]},
    ]


def _history(shots, first_images=0):
    first = {"role": "user", "content": "Review the photos."}
    if first_images:
        first["content"] = [{"type": "text", "text": "Review the photos."}] + [
            _img(f"ref{i}") for i in range(first_images)]
    msgs = [first]
    for n in range(shots):
        msgs += _shot(n)
    return msgs


def _images(messages):
    """Every image tag left in `messages`, oldest first."""
    tags = []
    for m in messages:
        c = m.get("content")
        if not isinstance(c, list):
            continue
        for b in c:
            if b.get("type") == "image":
                tags.append(b["source"]["data"][100:])
            elif b.get("type") == "tool_result" and isinstance(b.get("content"), list):
                tags += [x["source"]["data"][100:] for x in b["content"]
                         if x.get("type") == "image"]
    return tags


def _too_large():
    e = anthropic.APIStatusError.__new__(anthropic.APIStatusError)
    e.message = "Error code: 413 - {'error': {'type': 'request_too_large'}}"
    e.status_code = 413
    return e


class HelperTests(unittest.TestCase):
    def test_the_oldest_half_goes_and_the_newest_half_stays(self):
        msgs = _history(8)
        self.assertEqual(strip_old_images(msgs), 4)
        self.assertEqual(_images(msgs), ["shot4", "shot5", "shot6", "shot7"])

    def test_the_removed_image_becomes_a_note_and_the_text_beside_it_stays(self):
        msgs = _history(2)
        strip_old_images(msgs)
        result = msgs[2]["content"][0]
        self.assertEqual(result["type"], "tool_result")
        self.assertEqual(result["content"],
                         [{"type": "text", "text": "Display 0 #0"},
                          {"type": "text", "text": IMAGE_REMOVED_NOTE}])
        self.assertIn("screenshot", IMAGE_REMOVED_NOTE)

    def test_an_odd_count_keeps_the_newest_half_rounded_down(self):
        msgs = _history(5)
        self.assertEqual(strip_old_images(msgs), 3)
        self.assertEqual(_images(msgs), ["shot3", "shot4"])

    def test_one_image_is_still_removed(self):
        msgs = _history(1)
        self.assertEqual(strip_old_images(msgs), 1)
        self.assertEqual(_images(msgs), [])

    def test_keep_is_honoured(self):
        msgs = _history(6)
        self.assertEqual(strip_old_images(msgs, keep=5), 1)
        self.assertEqual(_images(msgs), ["shot1", "shot2", "shot3", "shot4", "shot5"])
        msgs = _history(3)
        self.assertEqual(strip_old_images(msgs, keep=0), 3)
        self.assertEqual(_images(msgs), [])

    def test_the_first_user_messages_reference_images_are_never_touched(self):
        msgs = _history(2, first_images=2)
        self.assertEqual(strip_old_images(msgs), 1)
        self.assertEqual(_images(msgs), ["ref0", "ref1", "shot1"])
        msgs = _history(0, first_images=3)
        self.assertEqual(strip_old_images(msgs), 0)
        self.assertEqual(_images(msgs), ["ref0", "ref1", "ref2"])

    def test_a_replys_attached_images_count_too(self):
        msgs = _history(1) + [
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": [{"type": "text", "text": "and this"}, _img("reply")]},
        ]
        self.assertEqual(strip_old_images(msgs), 1)
        self.assertEqual(_images(msgs), ["reply"])

    def test_no_images_means_nothing_touched(self):
        msgs = [{"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t",
                                              "content": "plain text"}]}]
        before = [dict(m) for m in msgs]
        self.assertEqual(strip_old_images(msgs), 0)
        self.assertEqual(msgs, before)

    def test_sdk_object_blocks_in_assistant_turns_are_skipped(self):
        class _Block:
            type = "image"
        msgs = _history(2)
        msgs.insert(2, {"role": "assistant", "content": [_Block()]})
        self.assertEqual(strip_old_images(msgs), 1)


class WireTests(unittest.TestCase):
    def _call(self, host, msgs):
        return host._stream_anthropic_call(msgs, 5, False)

    def test_a_413_drops_the_oldest_images_and_retries_the_same_list(self):
        host = _WireHost(fast=False, fail_first=_too_large())
        msgs = _history(6)
        stop, *_ = self._call(host, msgs)
        self.assertEqual(stop, "end_turn")
        self.assertEqual(len(host.calls), 2)
        self.assertEqual(_images(msgs), ["shot3", "shot4", "shot5"])      # in place
        self.assertEqual(_images(host.calls[1][1]["messages"]), ["shot3", "shot4", "shot5"])
        self.assertEqual(len(host.calls[1][1]["messages"]), len(msgs))     # no round dropped
        warnings = [m["content"] for m in _drain(host.queue) if m["type"] == "warning"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("HTTP 413", warnings[0])
        self.assertIn("removed the 3 oldest", warnings[0])

    def test_with_no_image_left_the_round_trim_runs(self):
        host = _WireHost(fast=False, fail_first=_too_large())
        msgs = []
        for n in range(4):
            msgs += [{"role": "user", "content": f"turn {n} " * 50},
                     {"role": "assistant", "content": f"reply {n}"}]
        before = len(msgs)
        stop, *_ = self._call(host, msgs)
        self.assertEqual(stop, "end_turn")
        self.assertEqual(len(host.calls), 2)
        self.assertLess(len(msgs), before)
        self.assertEqual(msgs[0]["role"], "user")
        warnings = [m["content"] for m in _drain(host.queue) if m["type"] == "warning"]
        self.assertIn("no images to drop", warnings[0])

    def test_nothing_to_remove_raises(self):
        host = _WireHost(fast=False, fail_first=_too_large())
        with self.assertRaises(RuntimeError) as cm:
            self._call(host, [{"role": "user", "content": "hi"}])
        self.assertIn("413", str(cm.exception))
        self.assertEqual(len(host.calls), 1)

    def test_a_529_keeps_its_own_rung(self):
        e = anthropic.APIStatusError.__new__(anthropic.APIStatusError)
        e.message, e.status_code = "overloaded", 529
        host = _WireHost(fast=False, fail_first=e)
        msgs = _history(4)
        from unittest import mock
        with mock.patch("myagent.anthropic_mixin.time.sleep"):
            self._call(host, msgs)
        self.assertEqual(len(host.calls), 2)
        self.assertEqual(len(_images(msgs)), 4)                           # untouched

    def test_wiring(self):
        src = inspect.getsource(AnthropicMixin._stream_anthropic_call)
        self.assertIn("if e.status_code == 413:", src)
        self.assertIn("removed = strip_old_images(messages)", src)
        self.assertLess(src.index("if e.status_code == 413:"),
                        src.index("if e.status_code == 529"))


if __name__ == "__main__":
    unittest.main()
