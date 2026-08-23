"""Characterization tests for Agent Request dialog image attachments.

The dialog (safety_mixin.do_user_prompt) stashes attached images on
self._prompt_attached_images; callers pop them with _take_prompt_images()
and wire them as Anthropic-style image blocks via _prompt_image_blocks().
"""
import base64
import os
import tempfile
import unittest

from myagent.chat_mixin import ChatMixin
from myagent.safety_mixin import SafetyMixin

try:
    from PIL import Image
except ImportError:
    Image = None


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


class ClipboardContentToImagesTest(unittest.TestCase):
    """_clipboard_content_to_images handles ImageGrab.grabclipboard()'s
    three result shapes: None, a PIL bitmap, and a list of copied files."""

    def setUp(self):
        self.host = ChatMixin()

    def test_none_means_no_image(self):
        self.assertEqual(self.host._clipboard_content_to_images(None), [])

    @unittest.skipIf(Image is None, "PIL not installed")
    def test_bitmap_becomes_png_attachment(self):
        img = Image.new("RGB", (4, 4), color=(255, 0, 0))
        result = self.host._clipboard_content_to_images(img)
        self.assertEqual(len(result), 1)
        data, media_type, filename = result[0]
        self.assertEqual(media_type, "image/png")
        self.assertTrue(filename.startswith("pasted_"))
        self.assertTrue(filename.endswith(".png"))
        self.assertEqual(base64.b64decode(data)[:8], b"\x89PNG\r\n\x1a\n")

    def test_file_list_keeps_images_skips_others(self):
        with tempfile.TemporaryDirectory() as td:
            png_path = os.path.join(td, "shot.png")
            with open(png_path, "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\nfakedata")
            txt_path = os.path.join(td, "notes.txt")
            with open(txt_path, "w") as f:
                f.write("not an image")
            result = self.host._clipboard_content_to_images([png_path, txt_path])
        self.assertEqual(len(result), 1)
        data, media_type, filename = result[0]
        self.assertEqual((media_type, filename), ("image/png", "shot.png"))
        self.assertEqual(base64.b64decode(data), b"\x89PNG\r\n\x1a\nfakedata")

    def test_missing_file_skipped(self):
        result = self.host._clipboard_content_to_images(
            [os.path.join(tempfile.gettempdir(), "does_not_exist_xyz.png")]
        )
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
