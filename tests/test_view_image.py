"""view_image, the Desktop tool that reads an image FILE into the conversation
(2026-10-13).

A trail-camera review (JustChat_2026-10-13_180525, Opus 5.5) had no way to
look at its JPEGs but the screen: the agent built a full-screen Tk viewer,
paged it through contact sheets and screenshotted it 32 times, until the
32 MB request limit ended the run. view_image returns the file itself as
the [text, image] pair a screenshot returns, resized to the provider's API
caps — the ONE table `_api_image_caps` now holds for screenshots and files
alike — with EXIF orientation applied, PNG unless the encoding is large
(then JPEG), never touching the screenshot pipeline's coordinate state, and
its text opening with FILE_IMAGE_MARKER so the hoisting translators (every
provider but Anthropic) say "an image file, not the screen" instead of
"use these coordinates for mouse_click". In the Desktop group at the user's
request; PARALLEL_SAFE (a file read).

Pinned here: the schema and the group, the dispatch, the caps table, the
real do_view_image on temp files, and the hint helper's file wording with
the camera wording byte-identical to before.
"""

import inspect
import os
import tempfile
import unittest

from myagent import constants as C
from myagent.desktop_mixin import DesktopMixin
from myagent.helpers import (CAMERA_RESULT_MARKER, FILE_IMAGE_MARKER, camera_aware_hint,
                             is_camera_result)
from myagent.streaming_mixin import StreamingMixin

try:
    from PIL import Image
except ImportError:          # pragma: no cover - the venv has Pillow
    Image = None

HINT = "COORDINATE SYSTEM: use these pixel coordinates for mouse_click."
# The camera-only hints exactly as they read before view_image existed.
CAMERA_ALONE = ("Below is the camera photo returned by the camera_capture tool above: the "
                "physical scene in front of the camera, NOT the screen. Describe or "
                "reason about what it shows; never use coordinates read from a "
                "camera photo for mouse_click.")
CAMERA_MIXED = (f"{HINT} EXCEPTION: of the 2 images below, image 2 is a camera photo "
                "(camera_capture) — the physical scene in front of the camera, not the "
                "screen. Never use coordinates read from a camera photo for mouse_click.")


def _host(provider="Anthropic"):
    host = object.__new__(DesktopMixin)
    host.provider = provider
    return host


def _png(path, size, color=(30, 120, 200)):
    Image.new("RGB", size, color).save(path, format="PNG")


class SurfaceTests(unittest.TestCase):
    def test_in_the_desktop_group_and_parallel_safe(self):
        names = [t["name"] for t in C.DESKTOP_TOOLS]
        self.assertIn("view_image", names)
        self.assertEqual(names[-1], "view_image")
        tool = next(t for t in C.DESKTOP_TOOLS if t["name"] == "view_image")
        self.assertEqual(tool["input_schema"]["required"], ["path"])
        self.assertIn("NOT the screen", tool["description"])
        self.assertIn("view_image", C.PARALLEL_SAFE_TOOLS)
        for other in (C.TOOLS, C.FILE_TOOLS, C.PHYSICAL_TOOLS, C.BROWSER_TOOLS):
            self.assertNotIn("view_image", [t["name"] for t in other])

    def test_dispatch_is_gated_by_the_desktop_checkbox(self):
        src = inspect.getsource(StreamingMixin._execute_tool)
        gate = src.index('"find_image_on_screen", "mouse_drag", "find_element",\n'
                         '                             "view_image"')
        branch = src.index('if block.name == "view_image":')
        self.assertLess(gate, branch)
        self.assertIn("return self.do_view_image(path)", src)

    def test_the_caps_table_serves_screenshots_too(self):
        self.assertEqual(_host("Google")._api_image_caps(), (2048, 4_000_000))
        self.assertEqual(_host("OpenAI")._api_image_caps(), (2048, 5_000_000))
        for provider in ("Anthropic", "xAI", "Moonshot", "Ollama", ""):
            self.assertEqual(_host(provider)._api_image_caps(), (1568, 1_150_000), provider)
        self.assertIn("max_long_edge, max_megapixels = self._api_image_caps()",
                      inspect.getsource(DesktopMixin._capture_single_display))
        self.assertNotIn("2048, 4_000_000", inspect.getsource(DesktopMixin._capture_single_display))


@unittest.skipIf(Image is None, "Pillow not installed")
class ViewImageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _path(self, name):
        return os.path.join(self.dir, name)

    def _decode(self, result):
        import base64
        import io
        self.assertIsInstance(result, list)
        self.assertEqual([b["type"] for b in result], ["text", "image"])
        src = result[1]["source"]
        self.assertEqual(src["type"], "base64")
        im = Image.open(io.BytesIO(base64.standard_b64decode(src["data"])))
        im.load()
        return result[0]["text"], src["media_type"], im

    def test_a_small_png_comes_back_whole_as_png_with_the_marker(self):
        p = self._path("chart.png")
        _png(p, (640, 400))
        text, media, im = self._decode(_host().do_view_image(p))
        self.assertEqual(media, "image/png")
        self.assertEqual(im.size, (640, 400))
        self.assertTrue(text.startswith(FILE_IMAGE_MARKER))
        self.assertEqual(is_camera_result(_host().do_view_image(p)), "file")
        self.assertIn("chart.png", text)
        self.assertIn("PNG 640x400 pixels", text)
        self.assertNotIn("shown at", text)
        self.assertIn("NOT the screen", text)
        self.assertIn("mouse_click", text)
        self.assertNotRegex(text, r"\(\d+x\d+ pixels\)")      # the translators' regex trap

    def test_a_large_photo_is_resized_to_the_providers_cap_and_sent_as_jpeg(self):
        import random
        p = self._path("IMAG0001.jpg")
        rnd = random.Random(1)
        im = Image.new("RGB", (3200, 1800))
        im.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
                    for _ in range(3200 * 1800 // 16)] * 16)
        im.save(p, format="JPEG", quality=90)
        text, media, out = self._decode(_host("Anthropic").do_view_image(p))
        self.assertEqual(media, "image/jpeg")
        self.assertLessEqual(max(out.size), 1568)
        self.assertLessEqual(out.size[0] * out.size[1], 1_150_000)
        self.assertIn("JPEG 3200x1800 pixels, shown at", text)
        _text, _media, out = self._decode(_host("Google").do_view_image(p))
        self.assertLessEqual(max(out.size), 2048)
        self.assertGreater(max(out.size), 1568)

    def test_exif_orientation_is_applied(self):
        p = self._path("phone.jpg")
        im = Image.new("RGB", (300, 100), (200, 50, 50))
        exif = Image.Exif()
        exif[0x0112] = 6                       # rotate 90° clockwise to view
        im.save(p, format="JPEG", exif=exif.tobytes())
        text, _media, out = self._decode(_host().do_view_image(p))
        self.assertEqual(out.size, (100, 300))

    def test_other_formats_and_a_tilde_path(self):
        for name, fmt in (("a.gif", "GIF"), ("b.bmp", "BMP"), ("c.webp", "WEBP")):
            p = self._path(name)
            try:
                Image.new("RGB", (50, 40), (1, 2, 3)).save(p, format=fmt)
            except (OSError, KeyError):          # a Pillow without that encoder
                continue
            text, _media, out = self._decode(_host().do_view_image(p))
            self.assertEqual(out.size, (50, 40), name)
            self.assertIn(f"{fmt} 50x40", text)
        home_file = os.path.join(os.path.expanduser("~"), "_view_image_probe_.png")
        _png(home_file, (8, 8))
        try:
            _text, _media, out = self._decode(_host().do_view_image("~/_view_image_probe_.png"))
            self.assertEqual(out.size, (8, 8))
        finally:
            os.remove(home_file)

    def test_the_errors_are_sentences_not_exceptions(self):
        host = _host()
        self.assertIn("no path", host.do_view_image(""))
        self.assertIn("file not found", host.do_view_image(self._path("missing.png")))
        self.assertIn("is a directory", host.do_view_image(self.dir))
        p = self._path("notes.txt")
        with open(p, "w") as f:
            f.write("not pixels")
        self.assertIn("could not read", host.do_view_image(p))
        big = self._path("big.png")
        _png(big, (64, 64))
        host.VIEW_IMAGE_MAX_BYTES = 10
        self.assertIn("too large", host.do_view_image(big))

    def test_the_screenshot_pipelines_state_is_never_touched(self):
        host = _host()
        state = dict(_screenshot_scale=0.5, _screenshot_offset=(7, 9), _screenshot_dims=(640, 480),
                     _display_states={0: "s"}, _display_images={0: "i"},
                     _display_full_states={0: "fs"}, _display_full_images={0: "fi"})
        for k, v in state.items():
            setattr(host, k, v)
        p = self._path("x.png")
        _png(p, (2000, 2000))
        host.do_view_image(p)
        for k, v in state.items():
            self.assertEqual(getattr(host, k), v, k)
        self.assertFalse(hasattr(host, "_screenshot_cached_image"))


class HintTests(unittest.TestCase):
    def test_is_camera_result_reports_the_kind(self):
        photo = [{"type": "text", "text": f"{CAMERA_RESULT_MARKER} — 10x10 pixels"}]
        file_ = [{"type": "text", "text": f"{FILE_IMAGE_MARKER} — a.png, PNG 10x10 pixels"}]
        shot = [{"type": "text", "text": "Display 0 (10x10 pixels):"}]
        self.assertEqual(is_camera_result(photo), "camera")
        self.assertEqual(is_camera_result(file_), "file")
        self.assertIs(is_camera_result(shot), False)
        self.assertTrue(is_camera_result(photo))
        self.assertTrue(is_camera_result(file_))

    def test_the_camera_wording_is_byte_identical_to_before(self):
        self.assertEqual(camera_aware_hint(HINT, [True]), CAMERA_ALONE)
        self.assertEqual(camera_aware_hint(HINT, ["camera"]), CAMERA_ALONE)
        self.assertEqual(camera_aware_hint(HINT, [False, True]), CAMERA_MIXED)
        self.assertEqual(camera_aware_hint(HINT, [False, "camera"]), CAMERA_MIXED)
        self.assertIs(camera_aware_hint(HINT, [False, False]), HINT)

    def test_files_alone_get_their_own_wording(self):
        hint = camera_aware_hint(HINT, ["file"])
        self.assertIn("the image file returned by the view_image tool", hint)
        self.assertIn("NOT the screen", hint)
        self.assertNotIn("camera", hint)
        self.assertNotIn("COORDINATE SYSTEM", hint)
        self.assertIn("the 3 image files", camera_aware_hint(HINT, ["file"] * 3))

    def test_a_mixed_turn_names_the_files(self):
        hint = camera_aware_hint(HINT, [False, "file"])
        self.assertTrue(hint.startswith(HINT))
        self.assertIn("of the 2 images below, image 2 is an image file (view_image)", hint)
        both = camera_aware_hint(HINT, ["camera", False, "file", "file"])
        self.assertIn("image 1 is a camera photo (camera_capture) and images 3, 4 are "
                      "image files (view_image)", both)
        neither_screen = camera_aware_hint(HINT, ["camera", "file"])
        self.assertIn("camera photos and image files, NOT the screen", neither_screen)
        self.assertNotIn("COORDINATE SYSTEM", neither_screen)


if __name__ == "__main__":
    unittest.main()
