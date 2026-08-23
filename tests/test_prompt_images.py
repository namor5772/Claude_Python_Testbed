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


class HtmlClipboardFallbackTest(unittest.TestCase):
    """CF_HTML fallback: web apps (OneDrive Photos etc.) copy an <img>
    reference instead of a bitmap; _html_to_image_tuples turns it into an
    attachment. Only the network-free paths are tested here."""

    PNG = b"\x89PNG\r\n\x1a\nfakepixels"

    def setUp(self):
        self.host = ChatMixin()

    def test_data_uri_decodes_locally(self):
        b64 = base64.b64encode(self.PNG).decode()
        html = f'<html><body><img alt="x" src="data:image/png;base64,{b64}"></body></html>'
        result = self.host._html_to_image_tuples(html)
        self.assertEqual(len(result), 1)
        data, media_type, filename = result[0]
        self.assertEqual(media_type, "image/png")
        self.assertTrue(filename.endswith(".png"))
        self.assertEqual(base64.b64decode(data), self.PNG)

    def test_http_url_uses_fetch(self):
        fetched = {}

        class Host(ChatMixin):
            @staticmethod
            def _fetch_image_url(url, timeout=10, max_bytes=30_000_000):
                fetched["url"] = url
                return HtmlClipboardFallbackTest.PNG

        html = '<img src="https://example.com/photo123.png">'
        result = Host()._html_to_image_tuples(html)
        self.assertEqual(fetched["url"], "https://example.com/photo123.png")
        self.assertEqual(result[0][1], "image/png")

    def test_failed_fetch_gives_empty(self):
        class Host(ChatMixin):
            @staticmethod
            def _fetch_image_url(url, timeout=10, max_bytes=30_000_000):
                return None

        self.assertEqual(
            Host()._html_to_image_tuples('<img src="https://x.example/a.png">'), []
        )

    def test_no_img_tag_or_no_html_gives_empty(self):
        self.assertEqual(self.host._html_to_image_tuples(None), [])
        self.assertEqual(self.host._html_to_image_tuples("<p>just a link</p>"), [])

    def test_non_image_bytes_rejected_by_sniff(self):
        class Host(ChatMixin):
            @staticmethod
            def _fetch_image_url(url, timeout=10, max_bytes=30_000_000):
                return b"<html>login page, not an image</html>"

        self.assertEqual(
            Host()._html_to_image_tuples('<img src="https://x.example/a.png">'), []
        )

    def test_sniff_magic_numbers(self):
        sniff = ChatMixin._sniff_image_media_type
        self.assertEqual(sniff(b"\x89PNG\r\n\x1a\nxx"), "image/png")
        self.assertEqual(sniff(b"\xff\xd8\xffxx"), "image/jpeg")
        self.assertEqual(sniff(b"GIF89a"), "image/gif")
        self.assertEqual(sniff(b"RIFF\x00\x00\x00\x00WEBPVP8 "), "image/webp")
        self.assertIsNone(sniff(b"RIFF\x00\x00\x00\x00WAVEdata"))  # a .wav, not webp
        self.assertIsNone(sniff(b"plain text"))


if __name__ == "__main__":
    unittest.main()
