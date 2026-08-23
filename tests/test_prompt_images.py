"""Characterization tests for Agent Request dialog image attachments.

The dialog (safety_mixin.do_user_prompt) stashes attached images on
self._prompt_attached_images; callers pop them with _take_prompt_images()
and wire them as Anthropic-style image blocks via _prompt_image_blocks().
"""
import unittest

from myagent.safety_mixin import SafetyMixin


class PromptImageBlocksTest(unittest.TestCase):
    def test_blocks_shape_matches_screenshot_convention(self):
        blocks = SafetyMixin._prompt_image_blocks(
            [("QUJD", "image/png", "a.png"), ("REVG", "image/jpeg", "b.jpg")]
        )
        self.assertEqual(blocks, [
            {"type": "image",
             "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}},
            {"type": "image",
             "source": {"type": "base64", "media_type": "image/jpeg", "data": "REVG"}},
        ])

    def test_empty_input_gives_empty_list(self):
        self.assertEqual(SafetyMixin._prompt_image_blocks([]), [])


class TakePromptImagesTest(unittest.TestCase):
    def test_pop_returns_then_clears(self):
        host = SafetyMixin()
        host._prompt_attached_images = [("d", "image/png", "f.png")]
        self.assertEqual(host._take_prompt_images(), [("d", "image/png", "f.png")])
        self.assertEqual(host._take_prompt_images(), [])

    def test_unset_attribute_is_empty(self):
        host = SafetyMixin()
        self.assertEqual(host._take_prompt_images(), [])


if __name__ == "__main__":
    unittest.main()
