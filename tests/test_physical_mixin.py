"""Characterization tests for the Physical tool set (camera_capture).

No camera, no OpenCV: a fake cv2 stands in for the driver, so the suite runs
anywhere. What is pinned:

* the pure helpers — the fit-to-API-limits arithmetic, the exposure-settle
  criterion (against the REAL brightness ramps measured on the development
  laptop, 2026-09-21), the save_path rules;
* do_camera_capture's contract — the result shape every provider translator
  handles, the camera released on EVERY path out, the backend fall-through,
  the refusals that never switch the camera on;
* the two separations that are the point of Physical being its own set: a
  photo never touches the screenshot pipeline's coordinate state, and the
  translators that hoist tool-result images never introduce a photo with the
  "use these coordinates for mouse_click" screenshot hint — while that hint
  itself stays byte-identical for screenshots;
* the wiring — gate, dispatch, toggle persistence.
"""
import base64
import os
import queue
import re
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from myagent import constants as C
from myagent import physical_mixin
from myagent.helpers import CAMERA_RESULT_MARKER, camera_aware_hint, is_camera_result
from myagent.kimi_mixin import KimiMixin
from myagent.ollama_mixin import OllamaMixin
from myagent.physical_mixin import PhysicalMixin
from myagent.streaming_mixin import StreamingMixin
from tests._util import stub
from tests.test_state_skill_modes import _Host as _StateHost

# The regex every hoisting translator picks screenshot dimensions out of.
DIMS_RE = re.compile(r"\((\d+)x(\d+)(?:\s+pixels)?\)")

# The screenshot hints as they were before the camera existed — byte for byte.
RESPONSES_HINT = (
    "Below is the screenshot image (800x600 pixels) returned by the "
    "screenshot tool above. COORDINATE SYSTEM: the top-left pixel "
    "is (0, 0), X increases rightward, Y increases downward. "
    "When calling mouse_click, use the pixel (x, y) coordinates "
    "as they appear in THIS image — they are automatically "
    "scaled to actual screen coordinates."
)

# (seconds since open, mean brightness) — every third frame of two cold starts
# of the integrated camera at 1080p. Media Foundation dips, turns (the false
# plateau around 0.95 s) and levels out at ~2.65 s; DirectShow levels at ~1.9 s.
MSMF_RAMP = [(0.65, 52.0), (0.76, 50.2), (0.85, 49.5), (0.95, 49.3), (1.04, 49.7),
             (1.16, 50.4), (1.25, 50.9), (1.35, 51.7), (1.46, 52.4), (1.56, 53.2),
             (1.66, 53.9), (1.74, 54.4), (1.85, 55.2), (1.94, 55.9), (2.05, 56.6),
             (2.17, 57.3), (2.23, 58.1), (2.34, 58.9), (2.44, 59.7), (2.54, 60.4),
             (2.65, 60.7), (2.74, 60.9), (2.84, 60.9), (2.95, 61.0), (3.05, 61.1),
             (3.14, 61.1), (3.26, 61.0), (3.35, 61.0), (3.45, 61.0), (3.54, 61.1)]
DSHOW_RAMP = [(1.09, 47.1), (1.19, 46.7), (1.32, 52.0), (1.47, 59.5), (1.63, 62.0),
              (1.77, 62.8), (1.92, 63.5), (2.08, 63.6), (2.22, 63.5), (2.38, 63.4),
              (2.53, 63.4), (2.67, 63.4), (2.83, 63.5), (2.97, 63.6), (3.13, 63.6)]


def settle_time(ramp, window=PhysicalMixin.CAMERA_SETTLE_WINDOW,
                span=PhysicalMixin.CAMERA_SETTLE_SPAN):
    for i in range(1, len(ramp) + 1):
        if PhysicalMixin._camera_settled(ramp[:i], window, span):
            return ramp[i - 1][0]
    return None


# ── a fake OpenCV ────────────────────────────────────────────────────────────

class _Frame:
    """What the mixin touches of a numpy frame: shape, size, [::8, ::8].mean()."""

    def __init__(self, width=1920, height=1080, brightness=60.0):
        self.shape = (height, width, 3)
        self.size = width * height * 3
        self.brightness = brightness

    def __getitem__(self, key):
        return self

    def mean(self):
        return self.brightness


class _Encoded:
    def __init__(self, payload):
        self.payload = payload

    def tobytes(self):
        return self.payload


class _Capture:
    def __init__(self, opened=True, frames=None):
        self.opened = opened
        self.frames = frames           # None -> an endless supply of good frames
        self.released = 0
        self.props = {}

    def isOpened(self):
        return self.opened

    def set(self, prop, value):
        self.props[prop] = value

    def read(self):
        if self.frames is None:
            return True, _Frame()
        if not self.frames:
            return False, None
        return True, self.frames.pop(0)

    def release(self):
        self.released += 1


class _FakeCv2:
    CAP_MSMF, CAP_DSHOW, CAP_AVFOUNDATION, CAP_ANY = 1400, 700, 1200, 0
    CAP_PROP_FRAME_WIDTH, CAP_PROP_FRAME_HEIGHT = 3, 4
    IMWRITE_JPEG_QUALITY, INTER_AREA = 1, 3

    def __init__(self, captures=None):
        self.captures = list(captures) if captures is not None else None
        self.opened_with = []          # (index, backend) per VideoCapture()
        self.made = []
        self.resized_to = None
        self.encoded = []              # (ext, params)

    def VideoCapture(self, index, backend):
        self.opened_with.append((index, backend))
        cap = self.captures.pop(0) if self.captures else _Capture()
        self.made.append(cap)
        return cap

    def resize(self, frame, size, interpolation=None):
        self.resized_to = (size, interpolation)
        return _Frame(size[0], size[1], frame.brightness)

    def imencode(self, ext, frame, params=None):
        self.encoded.append((ext, list(params or [])))
        return True, _Encoded(b"IMG" + ext.encode())


def camera_host(cv2=None, provider="Anthropic", **attrs):
    """A bare PhysicalMixin whose first frame counts as settled (no real
    waiting) over a fake cv2."""
    host = stub(PhysicalMixin, provider=provider, stop_requested=False,
                CAMERA_SETTLE_WINDOW=0.0, **attrs)
    host.cv2 = cv2 or _FakeCv2()
    host._camera_cv2 = lambda: host.cv2
    return host


# ── pure helpers ─────────────────────────────────────────────────────────────

class FitTests(unittest.TestCase):
    def test_never_scales_up(self):
        self.assertEqual(PhysicalMixin._camera_fit(640, 480, 1568, 1_150_000), (640, 480))

    def test_a_1080p_frame_under_the_default_limits(self):
        # The pixel limit bites before the long edge does: 1568x882 is 1.38 MP.
        w, h = PhysicalMixin._camera_fit(1920, 1080, *PhysicalMixin.CAMERA_IMAGE_CAP_DEFAULT)
        self.assertEqual((w, h), (1430, 804))
        self.assertLessEqual(w * h, 1_150_000)

    def test_long_edge_limit_alone(self):
        self.assertEqual(PhysicalMixin._camera_fit(4096, 2304, 2048, 5_000_000), (2048, 1152))

    def test_portrait_frames_are_limited_by_their_height(self):
        self.assertEqual(PhysicalMixin._camera_fit(1080, 1920, 1568, 99_000_000), (882, 1568))

    def test_aspect_ratio_is_kept(self):
        w, h = PhysicalMixin._camera_fit(1920, 1080, 1568, 1_150_000)
        self.assertAlmostEqual(w / h, 1920 / 1080, places=2)

    def test_a_1080p_frame_passes_the_openai_and_google_limits_whole(self):
        for provider in ("OpenAI", "Google"):
            with self.subTest(provider=provider):
                caps = PhysicalMixin.CAMERA_IMAGE_CAPS[provider]
                self.assertEqual(PhysicalMixin._camera_fit(1920, 1080, *caps), (1920, 1080))

    def test_degenerate_sizes_pass_through(self):
        self.assertEqual(PhysicalMixin._camera_fit(0, 0, 1568, 1_150_000), (0, 0))


class SettleTests(unittest.TestCase):
    def test_nothing_is_settled_before_the_window_is_full(self):
        flat = [(0.0, 60.0), (0.1, 60.0), (0.2, 60.0)]
        self.assertFalse(PhysicalMixin._camera_settled(flat, 0.75, 1.0))
        self.assertFalse(PhysicalMixin._camera_settled([], 0.75, 1.0))

    def test_a_flat_run_settles_once_the_window_is_full(self):
        flat = [(i / 10, 60.0 + (i % 2) * 0.3) for i in range(9)]   # 0.0 .. 0.8 s
        self.assertTrue(PhysicalMixin._camera_settled(flat, 0.75, 1.0))

    def test_media_foundation_ramp_settles_on_the_plateau_not_the_turn(self):
        # The exposure turns at ~0.95 s (49.3) and looks flat there for a few
        # frames; the real plateau starts at 2.65 s. A settle before ~3 s would
        # have taken a frame a fifth too dark.
        at = settle_time(MSMF_RAMP)
        self.assertIsNotNone(at)
        self.assertGreaterEqual(at, 3.0)
        self.assertLessEqual(at, 3.5)

    def test_directshow_ramp_settles_after_its_climb(self):
        at = settle_time(DSHOW_RAMP)
        self.assertIsNotNone(at)
        self.assertGreaterEqual(at, 2.5)
        self.assertLessEqual(at, 2.9)

    def test_a_short_window_would_have_been_fooled_by_the_turn(self):
        # Why the window is 0.75 s and not a handful of frames.
        self.assertLess(settle_time(MSMF_RAMP, window=0.25, span=1.5), 1.2)

    def test_a_scene_that_keeps_changing_never_settles(self):
        busy = [(i / 10, 60.0 + (i % 2) * 5) for i in range(40)]
        self.assertIsNone(settle_time(busy))

    def test_both_measured_settles_fall_inside_the_cap(self):
        for ramp in (MSMF_RAMP, DSHOW_RAMP):
            self.assertLess(settle_time(ramp) - ramp[0][0], PhysicalMixin.CAMERA_SETTLE_CAP)


class SaveTargetTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir, ignore_errors=True))

    def test_jpg_and_png_are_accepted_as_given(self):
        for name in ("a.jpg", "b.JPEG", "c.png"):
            with self.subTest(name=name):
                path, note, error = PhysicalMixin._camera_save_target(os.path.join(self.dir, name))
                self.assertIsNone(error)
                self.assertEqual(path, os.path.abspath(os.path.join(self.dir, name)))
                self.assertEqual(note, "")

    def test_a_path_without_an_extension_gets_jpg(self):
        path, _note, error = PhysicalMixin._camera_save_target(os.path.join(self.dir, "desk"))
        self.assertIsNone(error)
        self.assertTrue(path.endswith("desk.jpg"))

    def test_other_formats_are_refused(self):
        _path, _note, error = PhysicalMixin._camera_save_target(os.path.join(self.dir, "x.gif"))
        self.assertIn(".jpg or .png", error)

    def test_an_existing_file_is_never_overwritten(self):
        existing = os.path.join(self.dir, "keep.jpg")
        with open(existing, "wb") as f:
            f.write(b"the user's file")
        _path, _note, error = PhysicalMixin._camera_save_target(existing)
        self.assertIn("already exists", error)
        # ...including the one a missing extension would resolve to.
        _path, _note, error = PhysicalMixin._camera_save_target(os.path.join(self.dir, "keep"))
        self.assertIn("already exists", error)

    def test_a_folder_is_refused(self):
        _path, _note, error = PhysicalMixin._camera_save_target(self.dir)
        self.assertIn("folder", error)

    def test_relative_paths_come_back_absolute(self):
        path, _note, error = PhysicalMixin._camera_save_target("rel_photo_zz.png")
        self.assertIsNone(error)
        self.assertTrue(os.path.isabs(path))

    @unittest.skipIf(os.name == "nt", "a drive-letter path is valid on Windows")
    def test_a_windows_path_on_posix_is_redirected_with_a_note(self):
        path, note, error = PhysicalMixin._camera_save_target(
            r"C:\Users\x\photo.jpg", home=self.dir)
        self.assertIsNone(error)
        self.assertEqual(path, os.path.join(self.dir, "Temp", "photo.jpg"))
        self.assertIn("Windows path", note)


class BackendTests(unittest.TestCase):
    def test_windows_names_both_backends_media_foundation_first(self):
        with mock.patch.object(physical_mixin, "IS_WINDOWS", True):
            self.assertEqual(PhysicalMixin._camera_backends(_FakeCv2),
                             [("Media Foundation", 1400), ("DirectShow", 700)])

    def test_macos_uses_avfoundation(self):
        with mock.patch.object(physical_mixin, "IS_WINDOWS", False), \
                mock.patch.object(physical_mixin.sys, "platform", "darwin"):
            self.assertEqual(PhysicalMixin._camera_backends(_FakeCv2), [("AVFoundation", 1200)])

    def test_anything_else_lets_opencv_choose(self):
        with mock.patch.object(physical_mixin, "IS_WINDOWS", False), \
                mock.patch.object(physical_mixin.sys, "platform", "linux"):
            self.assertEqual(PhysicalMixin._camera_backends(_FakeCv2), [("default", 0)])


# ── do_camera_capture ────────────────────────────────────────────────────────

class CaptureTests(unittest.TestCase):
    def test_result_is_the_text_plus_image_block_pair_screenshots_use(self):
        host = camera_host()
        result = host.do_camera_capture({})
        self.assertEqual([b["type"] for b in result], ["text", "image"])
        source = result[1]["source"]
        self.assertEqual(source["type"], "base64")
        self.assertEqual(source["media_type"], "image/jpeg")   # a photo is no PNG
        self.assertEqual(base64.b64decode(source["data"]), b"IMG.jpg")
        self.assertTrue(result[0]["text"].startswith(CAMERA_RESULT_MARKER))
        self.assertTrue(is_camera_result(result))

    def test_the_text_says_it_is_not_the_screen_and_carries_no_screenshot_dims(self):
        text = camera_host().do_camera_capture({})[0]["text"]
        self.assertIn("NOT the screen", text)
        self.assertIn("1430x804 pixels", text)
        # In a turn holding a screenshot AND a photo, the translators' regex
        # must find the screenshot's dimensions, never the photo's.
        self.assertIsNone(DIMS_RE.search(text))

    def test_the_saved_note_does_not_look_like_screenshot_dims_either(self):
        with tempfile.TemporaryDirectory() as d:
            text = camera_host().do_camera_capture(
                {"save_path": os.path.join(d, "p.jpg")})[0]["text"]
        self.assertIn("1920 by 1080", text)
        self.assertIsNone(DIMS_RE.search(text))

    def test_frame_is_sized_to_the_providers_limits(self):
        anthropic = camera_host(provider="Anthropic")
        anthropic.do_camera_capture({})
        self.assertEqual(anthropic.cv2.resized_to, ((1430, 804), _FakeCv2.INTER_AREA))
        for provider in ("OpenAI", "Google"):
            with self.subTest(provider=provider):
                host = camera_host(provider=provider)
                text = host.do_camera_capture({})[0]["text"]
                self.assertIsNone(host.cv2.resized_to)
                self.assertIn("1920x1080 pixels", text)

    def test_a_full_hd_frame_is_asked_of_the_driver(self):
        host = camera_host()
        host.do_camera_capture({})
        self.assertEqual(host.cv2.made[0].props, {3: 1920, 4: 1080})

    def test_camera_is_released_after_a_photo(self):
        host = camera_host()
        host.do_camera_capture({})
        self.assertEqual([c.released for c in host.cv2.made], [1])

    def test_the_photo_never_touches_the_screenshot_coordinate_state(self):
        state = dict(_screenshot_scale=1.6327, _screenshot_offset=(2560, 0),
                     _screenshot_dims=(1568, 882), _display_states={1: "s"},
                     _display_full_states={1: "f"}, _display_images={1: b"i"},
                     _display_full_images={1: b"fi"}, _last_screenshot_bytes=b"shot")
        host = camera_host(**{k: (dict(v) if isinstance(v, dict) else v)
                              for k, v in state.items()})
        host.do_camera_capture({})
        for name, before in state.items():
            with self.subTest(attr=name):
                self.assertEqual(getattr(host, name), before)

    def test_default_camera_is_index_zero_and_the_index_is_passed_through(self):
        host = camera_host()
        host.do_camera_capture({})
        self.assertEqual(host.cv2.opened_with[0][0], 0)
        other = camera_host()
        other.do_camera_capture({"camera": 2})
        self.assertEqual(other.cv2.opened_with[0][0], 2)
        self.assertIn("camera 2", other.do_camera_capture({"camera": "2"})[0]["text"])

    def test_bad_camera_arguments_are_refused_before_the_camera_is_touched(self):
        for bad in ("abc", -1, [1]):
            with self.subTest(camera=bad):
                host = camera_host()
                result = host.do_camera_capture({"camera": bad})
                self.assertIsInstance(result, str)
                self.assertIn("camera_capture error", result)
                self.assertEqual(host.cv2.opened_with, [])

    def test_a_backend_that_will_not_open_falls_through_to_the_next(self):
        cv2 = _FakeCv2([_Capture(opened=False), _Capture()])
        host = camera_host(cv2)
        with mock.patch.object(physical_mixin, "IS_WINDOWS", True):
            result = host.do_camera_capture({})
        self.assertIsInstance(result, list)
        self.assertEqual([b for _i, b in cv2.opened_with], [1400, 700])
        self.assertEqual([c.released for c in cv2.made], [1, 1])

    def test_no_backend_opening_is_a_friendly_error_with_every_capture_released(self):
        cv2 = _FakeCv2([_Capture(opened=False), _Capture(opened=False)])
        host = camera_host(cv2)
        with mock.patch.object(physical_mixin, "IS_WINDOWS", True):
            result = host.do_camera_capture({"camera": 5})
        self.assertIn("camera 5 did not open", result)
        self.assertIn("Privacy", result)
        self.assertEqual([c.released for c in cv2.made], [1, 1])

    def test_an_open_camera_that_delivers_nothing_is_reported_as_in_use(self):
        cv2 = _FakeCv2([_Capture(frames=[]), _Capture(frames=[])])
        host = camera_host(cv2, CAMERA_FIRST_FRAME_TIMEOUT=0.05)
        with mock.patch.object(physical_mixin, "IS_WINDOWS", True):
            result = host.do_camera_capture({})
        self.assertIn("delivered no picture", result)
        self.assertIn("another app", result)
        self.assertEqual([c.released for c in cv2.made], [1, 1])

    def test_a_stream_that_dies_keeps_its_last_good_frame(self):
        cv2 = _FakeCv2([_Capture(frames=[_Frame(brightness=10), _Frame(brightness=90)])])
        # never settles (span < 0), so the loop runs the stream dry
        host = camera_host(cv2, CAMERA_SETTLE_SPAN=-1.0)
        result = host.do_camera_capture({})
        self.assertIsInstance(result, list)
        self.assertEqual(cv2.made[0].released, 1)

    def test_a_scene_that_never_settles_is_taken_at_the_cap_and_says_so(self):
        host = camera_host(CAMERA_SETTLE_SPAN=-1.0, CAMERA_SETTLE_CAP=0.05)
        text = host.do_camera_capture({})[0]["text"]
        self.assertIn("kept changing", text)

    def test_a_black_photo_is_returned_with_the_shutter_warning(self):
        cv2 = _FakeCv2([_Capture(frames=[_Frame(brightness=0.4)] * 3)])
        result = camera_host(cv2).do_camera_capture({})
        self.assertEqual(result[1]["type"], "image")
        self.assertIn("privacy shutter", result[0]["text"])

    def test_stop_ends_the_wait_without_the_troubleshooting_hint(self):
        host = camera_host(CAMERA_SETTLE_SPAN=-1.0, CAMERA_SETTLE_CAP=30.0)
        host.stop_requested = True
        result = host.do_camera_capture({})
        self.assertIn("STOP", result)
        self.assertNotIn("Privacy", result)
        self.assertEqual(host.cv2.made[0].released, 1)

    def test_a_wedged_driver_costs_a_timeout_not_the_run(self):
        release = __import__("threading").Event()

        class _Wedged(_FakeCv2):
            def VideoCapture(self, index, backend):
                release.wait(5)             # the driver call that never returns
                return super().VideoCapture(index, backend)

        host = camera_host(_Wedged(), CAMERA_TIMEOUT=0.2)
        try:
            result = host.do_camera_capture({})
        finally:
            release.set()
        self.assertIn("did not respond", result)

    def test_missing_opencv_is_the_install_hint(self):
        host = stub(PhysicalMixin, provider="Anthropic", stop_requested=False)
        with mock.patch.dict("sys.modules", {"cv2": None}):
            result = host.do_camera_capture({})
        self.assertIn("pip install opencv-python", result)


class SelfTimerTests(unittest.TestCase):
    """delay_seconds: one tool call = wait + photo, which is how a monitoring
    loop paces itself without a separate sleep command (and the empty turn
    after it, where claude-haiku-4-5 dropped out of the loop)."""

    def test_delay_argument_parsing(self):
        ok = stub(PhysicalMixin)._camera_delay
        self.assertEqual(ok(None), (0.0, "", None))
        self.assertEqual(ok(""), (0.0, "", None))
        self.assertEqual(ok(0), (0.0, "", None))
        self.assertEqual(ok(5), (5.0, "", None))
        self.assertEqual(ok("2.5"), (2.5, "", None))
        for bad in ("soon", [5], -1, float("nan")):
            with self.subTest(value=bad):
                self.assertIsNotNone(ok(bad)[2])

    def test_a_delay_beyond_the_ceiling_is_capped_and_says_so(self):
        seconds, note, error = stub(PhysicalMixin)._camera_delay(3600)
        self.assertEqual(seconds, PhysicalMixin.CAMERA_MAX_DELAY)
        self.assertIn("capped at 600", note)
        self.assertIsNone(error)
        # the ceiling is run_command's own maximum timeout
        self.assertEqual(PhysicalMixin.CAMERA_MAX_DELAY, 600.0)

    def test_the_cap_is_reported_in_the_result(self):
        host = camera_host(CAMERA_MAX_DELAY=0.05)
        text = host.do_camera_capture({"delay_seconds": 99})[0]["text"]
        self.assertIn("capped at", text)

    def test_the_wait_happens_before_the_camera_is_opened(self):
        # the indicator light is on for the photo, not for the wait
        stamps = {}

        class _Stamping(_FakeCv2):
            def VideoCapture(self, index, backend):
                stamps.setdefault("opened", time.monotonic())
                return super().VideoCapture(index, backend)

        host = camera_host(_Stamping())
        started = time.monotonic()
        result = host.do_camera_capture({"delay_seconds": 0.3})
        self.assertIsInstance(result, list)
        self.assertGreaterEqual(stamps["opened"] - started, 0.28)

    def test_no_delay_means_no_wait(self):
        host = camera_host()
        host._camera_wait = lambda seconds: self.fail("must not wait")
        for inp in ({}, {"delay_seconds": 0}, {"delay_seconds": None}):
            with self.subTest(inp=inp):
                self.assertIsInstance(host.do_camera_capture(inp), list)

    def test_stop_cuts_the_wait_short_and_the_camera_is_never_opened(self):
        host = camera_host()
        threading.Timer(0.15, lambda: setattr(host, "stop_requested", True)).start()
        started = time.monotonic()
        result = host.do_camera_capture({"delay_seconds": 30})
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertIn("STOP", result)
        self.assertEqual(host.cv2.opened_with, [])

    def test_a_bad_delay_is_refused_before_anything_happens(self):
        host = camera_host()
        result = host.do_camera_capture({"delay_seconds": "soon"})
        self.assertIn("delay_seconds", result)
        self.assertEqual(host.cv2.opened_with, [])

    def test_missing_opencv_fails_before_the_wait_not_after_it(self):
        host = stub(PhysicalMixin, provider="Anthropic", stop_requested=False)
        host._camera_wait = lambda seconds: self.fail("waited for a camera that cannot work")
        with mock.patch.dict("sys.modules", {"cv2": None}):
            self.assertIn("pip install opencv-python",
                          host.do_camera_capture({"delay_seconds": 30}))

    def test_the_activity_line_announces_the_wait(self):
        host = _ToolHost()
        host.do_camera_capture = lambda inp: "ok"
        with mock.patch("myagent.streaming_mixin._HAS_CAMERA", True):
            host._execute_tool(SimpleNamespace(name="camera_capture",
                                               input={"delay_seconds": 10}))
        self.assertIn("waiting 10 s, then taking a photo", host.queue.get_nowait()["content"])


class SaveTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir, ignore_errors=True))

    def test_the_full_resolution_frame_is_written_beside_the_model_copy(self):
        host = camera_host()
        target = os.path.join(self.dir, "new folder", "photo é.jpg")
        text = host.do_camera_capture({"save_path": target})[0]["text"]
        with open(target, "rb") as f:
            self.assertEqual(f.read(), b"IMG.jpg")
        self.assertIn(f"to {target}", text)
        # saved first at full size and high quality, THEN shrunk for the model
        self.assertEqual(host.cv2.encoded[0],
                         (".jpg", [_FakeCv2.IMWRITE_JPEG_QUALITY, 92]))
        self.assertEqual(host.cv2.encoded[1],
                         (".jpg", [_FakeCv2.IMWRITE_JPEG_QUALITY, 85]))

    def test_png_is_written_without_jpeg_parameters(self):
        host = camera_host()
        host.do_camera_capture({"save_path": os.path.join(self.dir, "p.png")})
        self.assertEqual(host.cv2.encoded[0], (".png", []))

    def test_an_existing_file_is_refused_with_the_camera_untouched(self):
        existing = os.path.join(self.dir, "keep.jpg")
        with open(existing, "wb") as f:
            f.write(b"mine")
        host = camera_host()
        result = host.do_camera_capture({"save_path": existing})
        self.assertIn("refused", result)
        self.assertEqual(host.cv2.opened_with, [])
        with open(existing, "rb") as f:
            self.assertEqual(f.read(), b"mine")

    def test_a_failed_save_is_a_note_and_the_photo_still_arrives(self):
        host = camera_host()
        with mock.patch("builtins.open", side_effect=PermissionError("denied")):
            result = host.do_camera_capture({"save_path": os.path.join(self.dir, "p.jpg")})
        self.assertEqual(result[1]["type"], "image")
        self.assertIn("NOT saved", result[0]["text"])

    def test_a_blank_save_path_saves_nothing(self):
        host = camera_host()
        host.do_camera_capture({"save_path": "   "})
        self.assertEqual(len(host.cv2.encoded), 1)


# ── the hoisted-image hint ───────────────────────────────────────────────────

def photo_result(call_id="call_cam"):
    return {"type": "tool_result", "tool_use_id": call_id, "content": [
        {"type": "text", "text": f"{CAMERA_RESULT_MARKER} — 1430x804 pixels, camera 0, taken now."},
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                     "data": "UEhPVE8="}}]}


def screenshot_result(call_id="call_shot"):
    return {"type": "tool_result", "tool_use_id": call_id, "content": [
        {"type": "text", "text": "Display 0 (800x600 pixels):"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                     "data": "U0hPVA=="}}]}


class HintHelperTests(unittest.TestCase):
    def test_is_camera_result(self):
        self.assertTrue(is_camera_result(photo_result()["content"]))
        self.assertFalse(is_camera_result(screenshot_result()["content"]))
        self.assertFalse(is_camera_result("Camera photo as a bare string"))
        self.assertFalse(is_camera_result([]))
        self.assertFalse(is_camera_result(None))

    def test_without_a_photo_the_screenshot_hint_is_returned_untouched(self):
        self.assertIs(camera_aware_hint(RESPONSES_HINT, [False, False]), RESPONSES_HINT)
        self.assertIs(camera_aware_hint(RESPONSES_HINT, []), RESPONSES_HINT)

    def test_photos_alone_replace_the_hint(self):
        hint = camera_aware_hint(RESPONSES_HINT, [True])
        self.assertIn("camera photo", hint)
        self.assertIn("NOT the screen", hint)
        self.assertNotIn("COORDINATE SYSTEM", hint)
        self.assertNotIn("When calling mouse_click, use", hint)
        self.assertIn("the 2 camera photos", camera_aware_hint(RESPONSES_HINT, [True, True]))

    def test_a_mixed_turn_keeps_the_screenshot_hint_and_names_the_photos(self):
        hint = camera_aware_hint(RESPONSES_HINT, [False, True])
        self.assertTrue(hint.startswith(RESPONSES_HINT))
        self.assertIn("of the 2 images below, image 2 is a camera photo", hint)
        many = camera_aware_hint(RESPONSES_HINT, [True, False, True])
        self.assertIn("images 1, 3 are camera photos", many)


class TranslatorHintTests(unittest.TestCase):
    """Each hoisting translator: screenshot hint byte-identical, photo hint its own."""

    def hints(self, results):
        msgs = [{"role": "user", "content": results}]
        responses = stub(StreamingMixin)._messages_to_responses(msgs)[-1]
        kimi = stub(KimiMixin)._messages_to_kimi(msgs)[-1]
        ollama = stub(OllamaMixin)._messages_to_ollama(msgs)[-1]
        return {"responses": (responses["content"][0]["text"], responses["content"][1:]),
                "kimi": (kimi["content"][0]["text"], kimi["content"][1:]),
                "ollama": (ollama["content"], ollama["images"])}

    def test_screenshot_hint_is_byte_identical_to_before(self):
        for name, (text, _images) in self.hints([screenshot_result()]).items():
            with self.subTest(translator=name):
                self.assertEqual(text, RESPONSES_HINT)

    def test_a_photo_is_never_introduced_as_a_click_surface(self):
        for name, (text, images) in self.hints([photo_result()]).items():
            with self.subTest(translator=name):
                self.assertIn("camera_capture", text)
                self.assertIn("NOT the screen", text)
                self.assertNotIn("screenshot tool", text)
                self.assertEqual(len(images), 1)

    def test_the_photo_keeps_its_jpeg_media_type_on_the_wire(self):
        hints = self.hints([photo_result()])
        self.assertEqual(hints["responses"][1][0]["image_url"], "data:image/jpeg;base64,UEhPVE8=")
        self.assertEqual(hints["kimi"][1][0]["image_url"]["url"], "data:image/jpeg;base64,UEhPVE8=")

    def test_a_mixed_turn_gets_the_screenshots_dimensions_and_names_the_photo(self):
        for name, (text, images) in self.hints([screenshot_result(), photo_result()]).items():
            with self.subTest(translator=name):
                self.assertTrue(text.startswith(RESPONSES_HINT))   # (800x600), not the photo's
                self.assertIn("image 2 is a camera photo", text)
                self.assertEqual(len(images), 2)

    def test_gemini_translator(self):
        try:
            from myagent.gemini_mixin import GeminiMixin
        except Exception as e:                                      # pragma: no cover
            self.skipTest(f"google-genai unavailable: {e}")
        def hint_for(results):
            msgs = [{"role": "assistant", "content": [
                        {"type": "tool_use", "id": r["tool_use_id"], "name": "t", "input": {}}
                        for r in results]},
                    {"role": "user", "content": results}]
            return stub(GeminiMixin)._messages_to_gemini(msgs)[-1].parts[0].text
        self.assertEqual(hint_for([screenshot_result()]), (
            "Below is the screenshot image (800x600 pixels) returned by "
            "the screenshot tool above. Use pixel (x, y) coordinates "
            "directly from this image when calling mouse_click — "
            "top-left is (0, 0), X increases rightward, Y increases "
            "downward. Coordinates are automatically mapped to the "
            "actual screen."))
        photo = hint_for([photo_result()])
        self.assertIn("NOT the screen", photo)
        self.assertNotIn("screenshot tool", photo)


# ── wiring ───────────────────────────────────────────────────────────────────

class _Var:
    def __init__(self, value=False):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _ToolHost(StreamingMixin, PhysicalMixin):
    def __init__(self, physical=True, blocked=()):
        self.queue = queue.Queue()
        self.provider = "Anthropic"
        self.skills = {}
        self._blocked_tools = set(blocked)
        self.desktop_enabled = _Var(False)
        self.browser_enabled = _Var(False)
        self.meta_enabled = _Var(False)
        self.physical_enabled = _Var(physical)

    def names(self):
        return [t["name"] for t in self._get_tools()]


class SurfaceTests(unittest.TestCase):
    def test_tool_list(self):
        self.assertEqual([t["name"] for t in C.PHYSICAL_TOOLS], ["camera_capture"])

    def test_the_camera_is_not_a_desktop_tool(self):
        self.assertNotIn("camera_capture", [t["name"] for t in C.DESKTOP_TOOLS])

    def test_one_camera_is_never_driven_in_parallel(self):
        self.assertNotIn("camera_capture", C.PARALLEL_SAFE_TOOLS)

    def test_no_argument_is_required(self):
        schema = C.PHYSICAL_TOOLS[0]["input_schema"]
        self.assertEqual(schema["required"], [])
        self.assertEqual(sorted(schema["properties"]),
                         ["camera", "delay_seconds", "save_path"])

    def test_manage_instructions_can_set_the_toggle(self):
        manage = next(t for t in C.META_TOOLS if t["name"] == "manage_instructions")
        self.assertEqual(manage["input_schema"]["properties"]["physical"]["type"], "boolean")


class GateTests(unittest.TestCase):
    def test_offered_only_while_the_checkbox_is_on(self):
        with mock.patch("myagent.streaming_mixin._HAS_CAMERA", True):
            self.assertIn("camera_capture", _ToolHost(physical=True).names())
            self.assertNotIn("camera_capture", _ToolHost(physical=False).names())

    def test_never_offered_without_opencv(self):
        with mock.patch("myagent.streaming_mixin._HAS_CAMERA", False):
            self.assertNotIn("camera_capture", _ToolHost(physical=True).names())

    def test_desktop_alone_does_not_bring_the_camera(self):
        host = _ToolHost(physical=False)
        host.desktop_enabled = _Var(True)
        with mock.patch("myagent.streaming_mixin._HAS_CAMERA", True), \
                mock.patch("myagent.streaming_mixin._HAS_DESKTOP", False):
            self.assertNotIn("camera_capture", host.names())

    def test_blocked_tools_covers_it_by_name(self):
        with mock.patch("myagent.streaming_mixin._HAS_CAMERA", True):
            self.assertNotIn("camera_capture",
                             _ToolHost(blocked={"camera_capture"}).names())

    def test_a_host_without_the_variable_offers_nothing(self):
        host = _ToolHost()
        del host.physical_enabled                  # SelfBot reuses StreamingMixin-era hosts
        with mock.patch("myagent.streaming_mixin._HAS_CAMERA", True):
            self.assertNotIn("camera_capture", host.names())


class DispatchTests(unittest.TestCase):
    BLOCK = SimpleNamespace(name="camera_capture", input={"camera": 1})

    def test_dispatch_reaches_the_mixin_and_posts_an_activity_line(self):
        host = _ToolHost()
        host.do_camera_capture = lambda inp: ("called", inp)
        with mock.patch("myagent.streaming_mixin._HAS_CAMERA", True):
            self.assertEqual(host._execute_tool(self.BLOCK), ("called", {"camera": 1}))
        line = host.queue.get_nowait()
        self.assertEqual(line["type"], "tool_info")
        self.assertIn("camera 1", line["content"])

    def test_checkbox_off_is_refused(self):
        host = _ToolHost(physical=False)
        host.do_camera_capture = lambda inp: self.fail("must not run")
        with mock.patch("myagent.streaming_mixin._HAS_CAMERA", True):
            self.assertIn("Enable the Physical checkbox", host._execute_tool(self.BLOCK))

    def test_missing_opencv_is_the_install_hint(self):
        with mock.patch("myagent.streaming_mixin._HAS_CAMERA", False):
            self.assertIn("pip install opencv-python", _ToolHost()._execute_tool(self.BLOCK))

    def test_blocked_is_refused_first(self):
        host = _ToolHost(blocked={"camera_capture"})
        self.assertIn("HARD-BLOCKED", host._execute_tool(self.BLOCK))


class BlindModelWarningTests(unittest.TestCase):
    def host(self, provider, sees, physical=True, desktop=False):
        host = _ToolHost(physical=physical)
        host.provider, host.model = provider, "some-model"
        host.desktop_enabled = _Var(desktop)
        host._is_xai_vision_model = host._is_kimi_vision_model = \
            host._is_ollama_vision_model = lambda: sees
        return host

    def test_text_only_models_are_warned_about(self):
        with mock.patch("myagent.streaming_mixin._HAS_CAMERA", True):
            for provider in ("xAI", "Moonshot", "Ollama"):
                with self.subTest(provider=provider):
                    self.assertIn("cannot see photos",
                                  self.host(provider, sees=False)._blind_camera_warning())
                    self.assertIsNone(self.host(provider, sees=True)._blind_camera_warning())

    def test_silent_when_physical_is_off_or_the_provider_always_sees(self):
        with mock.patch("myagent.streaming_mixin._HAS_CAMERA", True):
            self.assertIsNone(self.host("xAI", sees=False, physical=False)._blind_camera_warning())
            self.assertIsNone(self.host("Anthropic", sees=False)._blind_camera_warning())

    def test_left_to_the_desktop_warning_when_desktop_is_on(self):
        with mock.patch("myagent.streaming_mixin._HAS_CAMERA", True), \
                mock.patch("myagent.streaming_mixin._HAS_DESKTOP", True):
            self.assertIsNone(
                self.host("xAI", sees=False, desktop=True)._blind_camera_warning())


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        fd, self.state_file = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(self.state_file)
        self.addCleanup(lambda: os.path.exists(self.state_file) and os.remove(self.state_file))

    def test_the_toggle_survives_a_relaunch(self):
        first = _StateHost({}, self.state_file)
        first.physical_enabled.set(True)
        first._save_last_state()
        second = _StateHost({}, self.state_file)
        self.assertFalse(second.physical_enabled.get())
        second._load_last_state()
        self.assertTrue(second.physical_enabled.get())

    def test_an_instruction_saved_before_the_toggle_existed_loads_with_it_off(self):
        host = _StateHost({}, self.state_file)
        host.physical_enabled.set(True)
        host._apply_instruction_entry("Old", {"text": "t", "desktop": True})
        self.assertFalse(host.physical_enabled.get())

    def test_every_site_that_persists_excel_persists_physical(self):
        # The toggle rides through nine hand-written sites; one missed and a
        # saved instruction silently loses its camera (or keeps a stale one).
        here = os.path.dirname(os.path.abspath(__file__))
        for module in ("instructions_mixin.py", "state_mixin.py"):
            with open(os.path.join(here, "..", "myagent", module), encoding="utf-8") as f:
                source = f.read()
            for excel, physical in (('"excel"', '"physical"'),
                                    ("excel_enabled", "physical_enabled"),
                                    ("_editor_excel", "_editor_physical")):
                with self.subTest(module=module, name=physical):
                    self.assertEqual(source.count(physical), source.count(excel))


if __name__ == "__main__":
    unittest.main()
