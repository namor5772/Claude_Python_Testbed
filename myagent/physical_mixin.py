"""Physical tools — sensing the room the computer sits in (OpenCV).

One tool today: camera_capture, a still photo from a webcam, handed to the
model the way a screenshot is (a text block + an image block in the
tool_result). It is its own tool set behind its own checkbox (Physical), not a
Desktop tool — see the PHYSICAL_TOOLS comment in constants.py.

Architecture notes:
- Stateless per call: the camera is opened, read and RELEASED inside one call,
  so its indicator light is on only while a photo is being taken, no handle
  outlives the call, and another app can have the camera back at once.
- A photo is not a click surface. Nothing here reads or writes the screenshot
  pipeline's state (_screenshot_scale / _offset / _dims, _display_states) — a
  camera_capture between a screenshot and a mouse_click must leave the click
  exactly where it would have landed.
- The first frames a webcam delivers are wrong: auto-exposure is still
  hunting. Measured on the development laptop (2026-09-21, 1080p integrated
  camera): mean brightness ran 79 -> 138 -> 64 over the first 20 frames and was
  still falling; from cold it took 1.9 s (DirectShow) to 2.6 s (Media
  Foundation) to level out. So the grab reads frames until the brightness has
  held still for CAMERA_SETTLE_WINDOW seconds, capped at CAMERA_SETTLE_CAP for
  a scene that never holds still (a TV, trees in the wind) — never a fixed
  frame count.
- The grab runs on its own short-lived thread: a wedged driver then costs a
  timeout instead of hanging the agent (a blocking C call cannot be
  interrupted by STOP), and a fresh thread carries no COM apartment for Media
  Foundation to collide with (the Excel tools initialise COM on the worker).
- delay_seconds is a self-timer, and the way a monitoring loop should pace
  itself: ONE tool call is then "wait, then photo", so a quiet cycle costs one
  API call and every result the model reads is a fresh photo. Pacing with
  run_command's Start-Sleep costs a second call per cycle and leaves the model
  a turn with nothing new in front of it — where claude-haiku-4-5 dropped out
  of the loop ("Monitoring continues. Waiting for STOP.") in the user's first
  run and again in a 20-photo control run (2026-09-21). The wait comes BEFORE
  the camera opens (the light is on for the photo, not the wait) and STOP ends
  it within 0.1 s, which nothing can do to a Start-Sleep.
- cv2 is imported at the first capture, not at startup (_HAS_CAMERA in
  constants is a find_spec probe), and all helpers are prefixed _camera_
  against flat-namespace MRO shadowing.
"""

import base64
import os
import sys
import threading
import time

from myagent.constants import IS_WINDOWS
from myagent.helpers import CAMERA_RESULT_MARKER, normalize_save_path


class _CameraStopped(Exception):
    """STOP was pressed while the camera was settling — not a camera fault,
    so it must not be answered with the troubleshooting hint."""


class PhysicalMixin:

    #: Asked of the driver, which answers with the nearest mode it has.
    CAMERA_REQUEST_SIZE = (1920, 1080)
    #: Exposure counts as settled once the mean brightness (0-255) of every
    #: frame in the trailing WINDOW seconds lies within SPAN of the others.
    CAMERA_SETTLE_WINDOW = 0.75
    CAMERA_SETTLE_SPAN = 1.0
    #: ...and after CAP seconds of frames the latest one is taken regardless.
    CAMERA_SETTLE_CAP = 4.0
    #: An opened camera that delivers no frame at all within this is given up.
    CAMERA_FIRST_FRAME_TIMEOUT = 5.0
    #: The whole grab (every backend tried) — beyond it the driver is wedged.
    CAMERA_TIMEOUT = 25.0
    #: The self-timer's ceiling (delay_seconds) — run_command's own maximum, so
    #: one tool call can never park the agent for longer than a command could.
    CAMERA_MAX_DELAY = 600.0
    #: Mean brightness under which the photo is reported as black.
    CAMERA_DARK_MEAN = 6.0
    CAMERA_JPEG_QUALITY = 85        # the copy the model sees
    CAMERA_SAVE_JPEG_QUALITY = 92   # the full-resolution file on disk
    CAMERA_SAVE_EXTENSIONS = (".jpg", ".jpeg", ".png")
    #: (long edge, pixels) per provider — the limits the screenshot pipeline
    #: resizes to, for the same reason: past them the API downscales anyway,
    #: and the extra bytes only slow the request down.
    CAMERA_IMAGE_CAPS = {"Google": (2048, 4_000_000), "OpenAI": (2048, 5_000_000)}
    CAMERA_IMAGE_CAP_DEFAULT = (1568, 1_150_000)

    # ── Pure helpers (unit-tested in tests/test_physical_mixin.py) ───────

    @staticmethod
    def _camera_fit(width, height, max_long_edge, max_pixels):
        """(width, height) scaled down — never up — to fit both limits,
        aspect ratio kept."""
        if width <= 0 or height <= 0:
            return width, height
        ratio = min(1.0,
                    max_long_edge / max(width, height),
                    (max_pixels / (width * height)) ** 0.5)
        if ratio >= 1.0:
            return width, height
        return max(1, round(width * ratio)), max(1, round(height * ratio))

    @staticmethod
    def _camera_settled(samples, window, span):
        """True once auto-exposure has stopped hunting. `samples` is the
        [(seconds, mean brightness), ...] of every frame so far; settled means
        they reach back at least `window` seconds and every one inside that
        trailing window lies within `span` of the others. The window must be
        FULL: a criterion that fires on the first few frames catches the
        turning point of the exposure ramp, which looks flat and is not."""
        if not samples:
            return False
        now = samples[-1][0]
        if now - samples[0][0] < window:
            return False
        recent = [mean for t, mean in samples if now - t <= window]
        return max(recent) - min(recent) <= span

    @classmethod
    def _camera_save_target(cls, save_path, home=None):
        """A save_path argument -> (absolute path, note, error). The path goes
        through normalize_save_path like the mail tools' save_to (a Windows
        path invented on the Mac is redirected, and `note` says where to);
        '.jpg' is added to a path without an extension; any other format, a
        folder, and an EXISTING file (a photo must never replace something the
        user has) are refused — all before the camera is switched on.
        `home` overrides ~ for tests."""
        path, note = normalize_save_path(str(save_path).strip(), home=home)
        path = os.path.abspath(path)
        if os.path.isdir(path):
            return None, "", f"save_path {path} is a folder — give a file name."
        root, ext = os.path.splitext(path)
        if not ext:
            path, ext = root + ".jpg", ".jpg"
        if ext.lower() not in cls.CAMERA_SAVE_EXTENSIONS:
            return None, "", f"save_path must end in .jpg or .png, not '{ext}'."
        if os.path.exists(path):
            return None, "", (f"{path} already exists — pick a new file name "
                              "(a photo never overwrites an existing file).")
        return path, note, None

    @staticmethod
    def _camera_backends(cv2):
        """[(label, OpenCV backend id)] to try, in order. Windows names both of
        its backends so one that fails falls through to the other: Media
        Foundation first (it opened faster and delivered 26 fps against
        DirectShow's 16 at 1080p on the development laptop), DirectShow as the
        fallback for a driver Media Foundation cannot open."""
        if IS_WINDOWS:
            return [("Media Foundation", cv2.CAP_MSMF), ("DirectShow", cv2.CAP_DSHOW)]
        if sys.platform == "darwin":
            return [("AVFoundation", cv2.CAP_AVFOUNDATION)]
        return [("default", cv2.CAP_ANY)]

    @staticmethod
    def _camera_failure_hint():
        """What to check when no camera answers, for the platform at hand."""
        if IS_WINDOWS:
            return ("Check that a camera is attached, that no other app is using it, "
                    "and Settings > Privacy & security > Camera ('Let desktop apps "
                    "access your camera').")
        if sys.platform == "darwin":
            return ("Check that a camera is attached, that no other app is using it, "
                    "and System Settings > Privacy & Security > Camera for the app "
                    "that launched MyAgent.")
        return "Check that a camera is attached and that no other app is using it."

    # ── OpenCV ───────────────────────────────────────────────────────────

    @staticmethod
    def _camera_cv2():
        """OpenCV, imported on first use. Raises RuntimeError with the install
        hint when it is missing — the single guard for the whole tool."""
        if sys.platform == "darwin":
            # OpenCV's own permission request spins the MAIN thread's run loop
            # and fails from any other thread; skipped, the system asks on its
            # own at the first open. Read when a capture opens, so setting it
            # here is early enough even with cv2 already imported.
            os.environ.setdefault("OPENCV_AVFOUNDATION_SKIP_AUTH", "1")
        try:
            import cv2
        except Exception as e:
            raise RuntimeError(
                "OpenCV is not installed. Install with: pip install opencv-python"
            ) from e
        try:
            # An index that will not open is an expected outcome here, reported
            # in the tool result — not a warning for the console.
            cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
        except Exception:
            pass
        return cv2

    def _camera_grab(self, cv2, index, cancel):
        """Open camera `index`, let the exposure settle, return the frame.
        -> (frame, info) where info = {"backend", "settled", "seconds"}.
        Raises RuntimeError when no backend yields a frame. Runs on the grab
        thread; `cancel` (threading.Event) ends the wait early, and the camera
        is released on every path out."""
        started = time.monotonic()
        failure = f"camera {index} did not open"
        for label, backend in self._camera_backends(cv2):
            if cancel.is_set():
                break
            cap = cv2.VideoCapture(index, backend)
            try:
                if not cap.isOpened():
                    failure = f"camera {index} did not open ({label})"
                    continue
                req_w, req_h = self.CAMERA_REQUEST_SIZE
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, req_w)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, req_h)
                frame, samples, settled, misses = None, [], False, 0
                opened = time.monotonic()
                while not cancel.is_set():
                    ok, shot = cap.read()
                    now = time.monotonic()
                    if ok and shot is not None and shot.size:
                        frame, misses = shot, 0
                        # Every 8th pixel each way: an exposure signal needs no
                        # more, and it costs 1/64 of a full-frame mean.
                        samples.append((now, float(shot[::8, ::8].mean())))
                        settled = self._camera_settled(
                            samples, self.CAMERA_SETTLE_WINDOW, self.CAMERA_SETTLE_SPAN)
                        if settled or now - samples[0][0] >= self.CAMERA_SETTLE_CAP:
                            break
                        continue
                    misses += 1
                    if frame is None:
                        if now - opened >= self.CAMERA_FIRST_FRAME_TIMEOUT:
                            break
                    elif misses >= 10:
                        break  # the stream died — keep the last good frame
                    time.sleep(0.02)
                if frame is not None:
                    return frame, {"backend": label, "settled": settled,
                                   "seconds": time.monotonic() - started}
                failure = (f"camera {index} opened ({label}) but delivered no "
                           "picture — another app may be using it")
            finally:
                cap.release()
        raise RuntimeError(failure)

    def _camera_grab_guarded(self, cv2, index):
        """_camera_grab on its own thread, bounded by CAMERA_TIMEOUT and ended
        early by STOP. -> (frame, info); raises RuntimeError on failure."""
        cancel = threading.Event()
        box = {}

        def work():
            try:
                box["result"] = self._camera_grab(cv2, index, cancel)
            except Exception as e:  # reported to the model, never raised on a daemon thread
                box["error"] = e

        worker = threading.Thread(target=work, name="camera-grab", daemon=True)
        worker.start()
        deadline = time.monotonic() + self.CAMERA_TIMEOUT
        while worker.is_alive():
            worker.join(0.1)
            if getattr(self, "stop_requested", False):
                cancel.set()
                worker.join(2.0)  # the grab sees `cancel` within a frame and releases
                raise _CameraStopped()
            if time.monotonic() >= deadline:
                # The thread is stuck inside the driver. It is a daemon, and it
                # releases the camera itself if the call ever returns.
                cancel.set()
                raise RuntimeError(
                    f"the camera did not respond within {self.CAMERA_TIMEOUT:.0f} s "
                    "(its driver is not answering — another app may be holding it)")
        if "error" in box:
            raise RuntimeError(str(box["error"]))
        return box["result"]

    def _camera_delay(self, value):
        """A delay_seconds argument -> (seconds, note, error). Absent / None / 0
        is no delay; more than CAMERA_MAX_DELAY is clamped, and `note` says so
        (a monitor that asked for an hour should learn it got ten minutes)."""
        if value is None or value == "":
            return 0.0, "", None
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            return 0.0, "", f"'delay_seconds' must be a number of seconds, got {value!r}."
        if seconds != seconds or seconds < 0:          # NaN or negative
            return 0.0, "", "'delay_seconds' must be 0 or greater."
        if seconds > self.CAMERA_MAX_DELAY:
            return self.CAMERA_MAX_DELAY, (f"delay_seconds was capped at "
                                           f"{self.CAMERA_MAX_DELAY:g}"), None
        return seconds, "", None

    def _camera_wait(self, seconds):
        """The self-timer: wait BEFORE the camera is opened, so its light is on
        for the photo and not for the wait, in 0.1 s steps so STOP ends it at
        once — the reason a monitoring loop should pace itself with this rather
        than run_command's Start-Sleep, which nothing can interrupt."""
        deadline = time.monotonic() + seconds
        while True:
            if getattr(self, "stop_requested", False):
                raise _CameraStopped()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.1, remaining))

    def _camera_save(self, cv2, frame, path):
        """Write the full-resolution frame to `path` -> the sentence reporting
        it. A failed save is a note, not an error: the photo was still taken,
        and the model still gets to see it."""
        height, width = frame.shape[:2]
        ext = os.path.splitext(path)[1].lower()
        params = ([] if ext == ".png"
                  else [cv2.IMWRITE_JPEG_QUALITY, self.CAMERA_SAVE_JPEG_QUALITY])
        try:
            ok, encoded = cv2.imencode(ext, frame, params)
            if not ok:
                return f"⚠ NOT saved: the photo could not be encoded as {ext}."
            # Written by Python, not cv2.imwrite: that goes through the ANSI code
            # page on Windows and fails — silently, returning False — on a path
            # with characters outside it.
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(encoded.tobytes())
        except OSError as e:
            return f"⚠ NOT saved to {path}: {e}"
        return f"Saved at full resolution ({width} by {height}) to {path}."

    # ── Tool ─────────────────────────────────────────────────────────────

    def do_camera_capture(self, inp):
        inp = inp or {}
        try:
            index = int(inp.get("camera") or 0)
        except (TypeError, ValueError):
            return f"camera_capture error: 'camera' must be a whole number, got {inp.get('camera')!r}."
        if index < 0:
            return "camera_capture error: 'camera' must be 0 or greater."
        delay, delay_note, problem = self._camera_delay(inp.get("delay_seconds"))
        if problem:
            return f"camera_capture error: {problem}"
        save_path, save_note = None, ""
        if str(inp.get("save_path") or "").strip():
            save_path, save_note, problem = self._camera_save_target(inp["save_path"])
            if problem:
                return f"camera_capture refused: {problem}"
        try:
            cv2 = self._camera_cv2()      # before the wait: a missing OpenCV fails at once
            if delay:
                self._camera_wait(delay)
            frame, info = self._camera_grab_guarded(cv2, index)
        except _CameraStopped:
            return "camera_capture stopped: STOP was pressed before the photo was taken."
        except RuntimeError as e:
            return f"camera_capture error: {e}. {self._camera_failure_hint()}"
        except Exception as e:
            return f"camera_capture error: {e}"
        try:
            full_h, full_w = frame.shape[:2]
            brightness = float(frame[::8, ::8].mean())
            notes = [f"({delay_note} seconds.)"] if delay_note else []
            if save_path:
                notes.append(self._camera_save(cv2, frame, save_path))
                if save_note:
                    notes.append(f"({save_note}.)")
            caps = self.CAMERA_IMAGE_CAPS.get(getattr(self, "provider", ""),
                                              self.CAMERA_IMAGE_CAP_DEFAULT)
            img_w, img_h = self._camera_fit(full_w, full_h, *caps)
            if (img_w, img_h) != (full_w, full_h):
                # INTER_AREA averages the source pixels under each target pixel
                # — the one interpolation that shrinks without aliasing.
                frame = cv2.resize(frame, (img_w, img_h), interpolation=cv2.INTER_AREA)
            ok, encoded = cv2.imencode(
                ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.CAMERA_JPEG_QUALITY])
            if not ok:
                return "camera_capture error: could not encode the photo."
            b64_data = base64.standard_b64encode(encoded.tobytes()).decode("utf-8")
        except Exception as e:
            return f"camera_capture error: {e}"
        if brightness < self.CAMERA_DARK_MEAN:
            notes.append(
                f"⚠ The photo is almost entirely black (mean brightness "
                f"{brightness:.0f} of 255): the camera's privacy shutter or lens "
                "cover may be closed, or the room is dark.")
        elif not info["settled"]:
            notes.append("The scene kept changing while the exposure settled, so "
                         "brightness may be slightly off.")
        # No "(WxH pixels)" in parentheses: that is the shape the translators'
        # screenshot-hint regex picks dimensions out of, and in a turn that
        # holds a screenshot AND a photo it must find the screenshot's.
        text = (f"{CAMERA_RESULT_MARKER} — {img_w}x{img_h} pixels, camera {index}, taken "
                f"{time.strftime('%Y-%m-%d %H:%M:%S')}. This is the physical scene in "
                "front of the camera, NOT the screen: its pixel coordinates mean "
                "nothing to mouse_click.")
        if notes:
            text += " " + " ".join(notes)
        return [
            {"type": "text", "text": text},
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": b64_data}},
        ]
