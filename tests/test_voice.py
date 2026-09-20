"""Characterization tests for voice input (myagent/voice_mixin.py, 2026-09-19).

The Agent Request dialog's Mike button records the microphone, a speech-to-text
model transcribes the recording, and the text lands in the reply box. This
module pins, without a microphone, a key or the network:

* the pure helpers: settings sanitising and the atomic save, WAV framing, the
  peak / level meter, the per-model request parameters (the live-probed
  2026-09-19 matrix: gpt-transcribe takes `languages` + `keywords`, the other
  OpenAI models one `language` + a `prompt`, and an unsupported parameter is a
  400 that names nothing), the model-id filter, the per-minute cost estimate,
  the spacing of inserted text, microphone lookup by NAME within one host API,
  and the raw-body Gemini parser (the dedicated transcribe model answers in a
  part field the installed SDK cannot parse);
* the API edge against fake clients: parameters forwarded, the one bare retry,
  Gemini's three request shapes stepping down on each 400;
* the recorder against a fake sounddevice: the 16 kHz → native-rate fallback,
  the upload budget, stop keeps / abort discards;
* the toggle's state machine on real widgets (a withdrawn Tk root; skipped
  where no display exists): the recording look and the exact resting look
  restored, too-short and silent recordings never sent, a late transcript
  dropped once the window has closed, and the app-wide open-stream counter
  that gates the PortAudio rescan;
* the wiring (a static scan): the dialog builds the row, owns the mnemonics,
  refuses to send mid-dictation, releases the microphone on close — and
  sounddevice is never imported at module level (~0.5 s, paid at first use).
"""

import io
import json
import os
import pathlib
import re
import tempfile
import threading
import time
import tkinter as tk
import unittest
import wave
from unittest import mock

import httpx
import openai
from google.genai import errors as genai_errors

from tests._util import stub
from myagent import voice_mixin
from myagent.constants import (
    VOICE_DEFAULT_MODELS, VOICE_FALLBACK_MODELS, VOICE_MODEL_NOTES,
    VOICE_PROVIDERS, VOICE_RECORDING_BG, VOICE_UNAUDITED_NOTE, VOICE_UNSUITABLE_MODELS,
)
from myagent.ui_mixin import UIMixin
from myagent.voice_mixin import VoiceMixin, _VoiceDictation, _VoiceRecorder

REPO = pathlib.Path(__file__).resolve().parents[1]

# OpenAI's models.list() speech / audio ids as served on 2026-09-19.
LIVE_OPENAI_AUDIO_IDS = [
    "gpt-4o-mini-transcribe", "gpt-4o-mini-transcribe-2025-03-20",
    "gpt-4o-mini-transcribe-2025-12-15", "gpt-4o-mini-tts", "gpt-4o-transcribe",
    "gpt-4o-transcribe-diarize", "gpt-audio", "gpt-audio-mini", "gpt-live-transcribe",
    "gpt-realtime", "gpt-realtime-2.1-mini", "gpt-realtime-translate",
    "gpt-realtime-whisper", "gpt-transcribe", "tts-1", "tts-1-hd", "whisper-1",
]


class ConfigTests(unittest.TestCase):

    def test_anything_sanitises_to_a_complete_settings_dict(self):
        defaults = {"provider": "OpenAI", "models": dict(VOICE_DEFAULT_MODELS),
                    "language": "", "hint": "", "device": ""}
        for junk in (None, [], "text", 7, {}, {"provider": "Nobody", "models": "x",
                                                "language": 5, "hint": None}):
            self.assertEqual(VoiceMixin._voice_sanitize_config(junk), defaults, junk)

    def test_one_model_is_kept_per_provider(self):
        cfg = VoiceMixin._voice_sanitize_config({
            "provider": "Google", "language": "  en, pl ", "hint": " Westpac ",
            "device": " USB Headset ",
            "models": {"OpenAI": " whisper-1 ", "Google": "", "Other": "ignored"}})
        self.assertEqual(cfg["provider"], "Google")
        self.assertEqual(cfg["models"], {"OpenAI": "whisper-1",
                                         "xAI": VOICE_DEFAULT_MODELS["xAI"],
                                         "Google": VOICE_DEFAULT_MODELS["Google"]})
        self.assertEqual((cfg["language"], cfg["hint"], cfg["device"]),
                         ("en, pl", "Westpac", "USB Headset"))

    def test_the_defaults_are_the_first_curated_model_of_each_provider(self):
        self.assertEqual(tuple(VOICE_FALLBACK_MODELS), VOICE_PROVIDERS)
        for provider in VOICE_PROVIDERS:
            self.assertEqual(VOICE_DEFAULT_MODELS[provider], VOICE_FALLBACK_MODELS[provider][0])
        self.assertEqual(VOICE_DEFAULT_MODELS, {"OpenAI": "gpt-transcribe",
                                                "xAI": "grok-voice-transcribe-2.0",
                                                "Google": "gemini-3.5-transcribe"})
        # No Anthropic: no Claude model takes audio (probed live 2026-09-20).
        self.assertNotIn("Anthropic", VOICE_PROVIDERS)


class AuditTests(unittest.TestCase):
    """The 2026-09-20 suitability audit decides what the picker offers."""

    def test_what_passed_is_listed_with_its_caveat_and_what_failed_is_not(self):
        listed = [m for models in VOICE_FALLBACK_MODELS.values() for m in models]
        self.assertEqual(listed, ["gpt-transcribe", "gpt-4o-mini-transcribe", "whisper-1",
                                  "grok-voice-transcribe-2.0", "gemini-3.5-transcribe"])
        self.assertEqual(set(listed) & set(VOICE_UNSUITABLE_MODELS), set())
        for model in listed:
            note = VoiceMixin._voice_model_note(model)
            self.assertNotEqual(note, VOICE_UNAUDITED_NOTE, model)
            self.assertEqual(note, VOICE_MODEL_NOTES[model])
        self.assertEqual(sorted(VOICE_UNSUITABLE_MODELS),
                         ["gemini-3.5-flash-lite", "gemini-3.8-flash", "gpt-4o-transcribe",
                          "grok-voice-transcribe-1.0"])

    def test_the_note_follows_the_family_and_owns_up_to_the_unknown(self):
        note = VoiceMixin._voice_model_note
        self.assertEqual(note("gpt-4o-mini-transcribe-2025-12-15"),
                         VOICE_MODEL_NOTES["gpt-4o-mini-transcribe"])     # a dated snapshot
        self.assertEqual(note("gpt-next-transcribe"), VOICE_UNAUDITED_NOTE)   # a newcomer
        # gpt-4o-transcribe does not read as a gpt-transcribe: it was audited out.
        self.assertTrue(note("gpt-4o-transcribe").startswith("Audited out: drops the speech"))
        self.assertIn("OBEY a spoken command", note("gemini-3.5-transcribe"))

    def test_an_audited_out_id_cannot_return_through_the_live_list(self):
        self.assertFalse(VoiceMixin._voice_is_stt_model_id("gpt-4o-transcribe"))
        self.assertTrue(VoiceMixin._voice_is_stt_model_id("gpt-4o-mini-transcribe"))

    def test_save_then_load_round_trips_and_leaves_no_temp_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "nested", "config.json")   # the folder is created
            cfg = VoiceMixin._voice_sanitize_config(
                {"provider": "Google", "language": "en", "device": "USB Headset"})
            VoiceMixin._voice_save_config(cfg, path)
            self.assertEqual(VoiceMixin._voice_load_config(path), cfg)
            self.assertEqual(os.listdir(os.path.dirname(path)), ["config.json"])

    def test_a_missing_or_torn_file_loads_as_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            defaults = VoiceMixin._voice_sanitize_config(None)
            self.assertEqual(VoiceMixin._voice_load_config(path), defaults)
            with open(path, "w", encoding="utf-8") as f:
                f.write('{"provider": "Goo')
            self.assertEqual(VoiceMixin._voice_load_config(path), defaults)


class AudioTests(unittest.TestCase):

    def test_wav_bytes_frame_the_pcm(self):
        pcm = b"\x01\x00\xff\x7f" * 100
        with wave.open(io.BytesIO(VoiceMixin._voice_wav_bytes(pcm, 16000)), "rb") as w:
            self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate()),
                             (1, 2, 16000))
            self.assertEqual(w.readframes(w.getnframes()), pcm)

    def test_peak_is_the_loudest_absolute_sample(self):
        self.assertEqual(VoiceMixin._voice_peak(b""), 0)
        self.assertEqual(VoiceMixin._voice_peak(b"\x00\x00\x10\x00\xf0\xff"), 16)   # 0, 16, -16
        self.assertEqual(VoiceMixin._voice_peak(b"\x00\x80"), 32768)                # -32768
        self.assertEqual(VoiceMixin._voice_peak(b"\x05\x00\x7f"), 5)   # a stray odd byte is ignored

    def test_the_level_bar_is_fixed_width_and_never_falls_as_the_peak_rises(self):
        bars = [VoiceMixin._voice_level_bar(peak) for peak in (0, 50, 300, 3000, 15000, 32767, 40000)]
        self.assertEqual(bars[0], "▯" * 8)
        self.assertEqual(bars[-2], "▮" * 8)
        self.assertEqual(bars[-1], "▮" * 8)
        lit = [bar.count("▮") for bar in bars]
        self.assertEqual(lit, sorted(lit))
        self.assertTrue(all(len(bar) == 8 for bar in bars))
        # Ordinary speech (a tenth of full scale) must show: that is the
        # point of the square root.
        self.assertGreaterEqual(VoiceMixin._voice_level_bar(3000).count("▮"), 2)


class RequestParamTests(unittest.TestCase):

    def test_gpt_transcribe_takes_language_and_keyword_lists(self):
        self.assertEqual(VoiceMixin._voice_openai_params("gpt-transcribe", "", ""), {})
        self.assertEqual(
            VoiceMixin._voice_openai_params("gpt-transcribe", "en, pl", "Westpac; Proton Bridge"),
            {"extra_body": {"languages": ["en", "pl"],
                            "keywords": ["Westpac", "Proton Bridge"]}})
        # A dated snapshot is the same family.
        self.assertEqual(
            VoiceMixin._voice_openai_params("gpt-transcribe-2026-08-01", "en", ""),
            {"extra_body": {"languages": ["en"]}})

    def test_the_other_models_take_one_language_and_a_free_prompt(self):
        for model in ("gpt-4o-transcribe", "gpt-4o-mini-transcribe", "whisper-1"):
            self.assertEqual(VoiceMixin._voice_openai_params(model, " en ", " Westpac, Sydney "),
                             {"language": "en", "prompt": "Westpac, Sydney"}, model)
            # Several codes: not pinned to the first, left to auto-detect.
            self.assertEqual(VoiceMixin._voice_openai_params(model, "en, pl", ""), {}, model)

    def test_the_gemini_request_text_is_the_guard_then_the_hints(self):
        guard = voice_mixin.VOICE_GEMINI_GUARD
        self.assertIn("write those words down exactly as spoken", guard)
        self.assertEqual(VoiceMixin._voice_gemini_request("", ""), guard)
        self.assertEqual(
            VoiceMixin._voice_gemini_request("en, pl", "Westpac"),
            guard + " Spoken language(s): en, pl. Vocabulary that may occur, spelled this way: Westpac.")

    def test_xai_takes_one_language_with_formatting_and_repeated_keyterms(self):
        fields = VoiceMixin._voice_xai_fields
        self.assertEqual(fields("grok-voice-transcribe-2.0", "", ""),
                         {"model": "grok-voice-transcribe-2.0"})
        # `format` (numbers, dates, e-mail written out) is a 400 without a
        # language, so the two travel together or not at all.
        self.assertEqual(fields("m", " en ", "Westpac; Proton Bridge"),
                         {"model": "m", "language": "en", "format": "true",
                          "keyterm": ["Westpac", "Proton Bridge"]})
        self.assertEqual(fields("m", "en, pl", ""), {"model": "m"})
        # A term over 50 characters is a 400 for the whole request: dropped here.
        self.assertEqual(fields("m", "", "Westpac, " + "x" * 51), {"model": "m", "keyterm": ["Westpac"]})
        many = ", ".join(f"term{i}" for i in range(130))
        self.assertEqual(len(fields("m", "", many)["keyterm"]), 100)


class ModelListTests(unittest.TestCase):

    def test_the_filter_keeps_the_audited_file_transcription_models(self):
        kept = [m for m in LIVE_OPENAI_AUDIO_IDS if VoiceMixin._voice_is_stt_model_id(m)]
        self.assertEqual(sorted(kept), sorted(VOICE_FALLBACK_MODELS["OpenAI"]))
        self.assertIn("gpt-4o-transcribe", LIVE_OPENAI_AUDIO_IDS)       # served, but audited out

    def test_curated_ids_come_first_and_newcomers_follow_alphabetically(self):
        self.assertEqual(
            VoiceMixin._voice_order_models(
                "OpenAI", ["whisper-1", "zeta-transcribe", "gpt-transcribe", "alpha-transcribe"]),
            ["gpt-transcribe", "whisper-1", "alpha-transcribe", "zeta-transcribe"])

    def test_a_failed_or_keyless_fetch_serves_the_curated_list(self):
        class Boom:
            class models:
                @staticmethod
                def list():
                    raise RuntimeError("offline")
        for provider in VOICE_PROVIDERS:     # xAI included: it has no listing endpoint at all
            for app in (stub(VoiceMixin), stub(VoiceMixin, openai_client=None, gemini_client=None),
                        stub(VoiceMixin, openai_client=Boom, gemini_client=Boom, xai_client=Boom)):
                self.assertEqual(app._voice_fetch_models(provider),
                                 VOICE_FALLBACK_MODELS[provider])

    def test_the_live_lists_are_filtered_and_ordered(self):
        class Model:
            def __init__(self, name, actions=("generateContent",)):
                self.id = self.name = name
                self.supported_actions = list(actions)

        class Client:
            def __init__(self, models):
                self.models = self
                self._models = models

            def list(self):
                return self._models

        app = stub(
            VoiceMixin,
            openai_client=Client([Model(m) for m in LIVE_OPENAI_AUDIO_IDS + ["gpt-next-transcribe"]]),
            gemini_client=Client([Model("models/gemini-3.8-flash"),
                                  Model("models/gemini-4-transcribe"),
                                  Model("models/gemini-3.5-transcribe"),
                                  Model("models/gemini-3.5-transcribe-live",
                                        ("bidiGenerateContent",))]))
        self.assertEqual(app._voice_fetch_models("OpenAI"),
                         VOICE_FALLBACK_MODELS["OpenAI"] + ["gpt-next-transcribe"])
        # Dedicated ids only, from the live list: the bidi-only -live one is
        # dropped, and so are the chat tiers the audit removed.
        self.assertEqual(app._voice_fetch_models("Google"),
                         ["gemini-3.5-transcribe", "gemini-4-transcribe"])


class CostTests(unittest.TestCase):

    def test_cost_is_per_minute_by_longest_prefix(self):
        self.assertAlmostEqual(VoiceMixin._voice_estimate_cost("gpt-transcribe", 60), 0.0045)
        self.assertAlmostEqual(VoiceMixin._voice_estimate_cost("gpt-4o-transcribe", 30), 0.003)
        self.assertAlmostEqual(
            VoiceMixin._voice_estimate_cost("gpt-4o-mini-transcribe-2025-12-15", 120), 0.006)
        self.assertAlmostEqual(VoiceMixin._voice_estimate_cost("whisper-1", 6), 0.0006)
        # xAI: "$0.10 per hour", for either model id.
        self.assertAlmostEqual(VoiceMixin._voice_estimate_cost("grok-voice-transcribe-2.0", 3600), 0.10)

    def test_an_unpriced_model_shows_no_cost_rather_than_a_wrong_one(self):
        for model in ("gemini-3.5-transcribe", "gemini-3.5-flash-lite", "some-future-stt"):
            self.assertIsNone(VoiceMixin._voice_estimate_cost(model, 60), model)
        for provider in ("OpenAI", "xAI"):      # every listed per-minute model is priced
            for model in VOICE_FALLBACK_MODELS[provider]:
                self.assertIsNotNone(VoiceMixin._voice_estimate_cost(model, 60), model)

    def test_the_done_line_names_words_audio_model_cost_and_note(self):
        info = {"seconds": 5.6, "model": "gpt-transcribe", "elapsed": 1.24, "note": ""}
        self.assertEqual(VoiceMixin._voice_done_text("one two three", info),
                         "3 words  ·  5.6 s of audio  ·  gpt-transcribe, 1.2 s  ·  ≈ $0.0004")
        info = {"seconds": 2.0, "model": "gemini-3.5-transcribe", "elapsed": 2.5, "note": "sent bare"}
        self.assertEqual(VoiceMixin._voice_done_text("word", info),
                         "1 word  ·  2.0 s of audio  ·  gemini-3.5-transcribe, 2.5 s  ·  sent bare")


class SpacingTests(unittest.TestCase):

    def test_the_transcript_is_set_off_from_words_but_not_from_brackets_or_punctuation(self):
        spaced = VoiceMixin._voice_spaced
        self.assertEqual(spaced("", " Hello there. ", "\n"), "Hello there.")       # empty box
        self.assertEqual(spaced(".", "Hello", "\n"), " Hello")                     # after a sentence
        self.assertEqual(spaced(" ", "Hello", "\n"), "Hello")                      # after a space
        self.assertEqual(spaced("(", "Hello", ")"), "Hello")                       # inside brackets
        self.assertEqual(spaced("a", "Hello", "b"), " Hello ")                     # mid-word gap
        self.assertEqual(spaced("a", "Hello", ","), " Hello")                      # before a comma
        self.assertEqual(spaced("a", "   ", "b"), "")                              # nothing heard


class DeviceTests(unittest.TestCase):

    DEVICES = [
        {"name": "Sound Mapper - Input", "hostapi": 0, "max_input_channels": 2},
        {"name": "Microphone Array", "hostapi": 0, "max_input_channels": 2},
        {"name": "Speakers", "hostapi": 0, "max_input_channels": 0},
        {"name": "Microphone Array", "hostapi": 1, "max_input_channels": 2},
        {"name": "USB Headset", "hostapi": 1, "max_input_channels": 1},
        {"name": "Microphone Array", "hostapi": 0, "max_input_channels": 2},   # a duplicate name
    ]

    def test_the_picker_lists_one_host_apis_inputs_once_each(self):
        self.assertEqual(VoiceMixin._voice_input_devices(self.DEVICES, 0),
                         ["Sound Mapper - Input", "Microphone Array"])
        self.assertEqual(VoiceMixin._voice_input_devices(self.DEVICES, 1),
                         ["Microphone Array", "USB Headset"])

    def test_a_saved_name_resolves_within_the_host_api_else_to_the_default(self):
        resolve = VoiceMixin._voice_resolve_device
        self.assertIsNone(resolve("", self.DEVICES, 0))
        self.assertEqual(resolve("Microphone Array", self.DEVICES, 0), 1)
        self.assertEqual(resolve("Microphone Array", self.DEVICES, 1), 3)
        self.assertIsNone(resolve("USB Headset", self.DEVICES, 0))    # unplugged / other API
        self.assertIsNone(resolve("Speakers", self.DEVICES, 0))       # not an input


class GeminiBodyTests(unittest.TestCase):

    def test_the_dedicated_model_answers_in_an_audio_transcription_part(self):
        body = json.dumps({"candidates": [{"content": {"role": "model", "parts": [
            {"audioTranscription": {"text": "What is the weather in Sydney today?"}}]}}]})
        self.assertEqual(VoiceMixin._voice_gemini_text(body),
                         "What is the weather in Sydney today?")

    def test_a_chat_tier_answers_in_text_parts_and_thoughts_are_skipped(self):
        body = {"candidates": [
            {"content": {"parts": [{"text": "planning…", "thought": True},
                                   {"text": "Hello "}, {"text": "there. "}]}},
            {"content": {"parts": [{"text": "a second candidate is ignored"}]}}]}
        self.assertEqual(VoiceMixin._voice_gemini_text(body), "Hello there.")

    def test_garbage_is_an_empty_transcript(self):
        for body in ("", "not json", "[]", "{}", b'{"candidates": []}', None,
                     '{"candidates": [{"finishReason": "SAFETY"}]}'):
            self.assertEqual(VoiceMixin._voice_gemini_text(body), "", body)


def _bad_request(message="Invalid request."):
    return openai.BadRequestError(
        message, body=None,
        response=httpx.Response(400, request=httpx.Request("POST", "https://api.openai.com/v1")))


class _FakeOpenAI:
    """client.audio.transcriptions.create, recording its calls."""

    def __init__(self, fail_when=lambda kwargs: False, text="  Hello there.  "):
        self.audio = self.transcriptions = self
        self.calls, self._fail_when, self._text = [], fail_when, text

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._fail_when(kwargs):
            raise _bad_request()
        return type("Transcription", (), {"text": self._text})()


class OpenAITranscribeTests(unittest.TestCase):

    def cfg(self, model, language="", hint=""):
        return VoiceMixin._voice_sanitize_config(
            {"provider": "OpenAI", "models": {"OpenAI": model}, "language": language, "hint": hint})

    def test_the_parameters_and_a_wav_upload_reach_the_endpoint(self):
        client = _FakeOpenAI()
        text, info = stub(VoiceMixin, openai_client=client)._voice_transcribe(
            self.cfg("whisper-1", "en", "Westpac"), b"RIFFwav")
        self.assertEqual(text, "Hello there.")
        self.assertEqual((info["model"], info["note"]), ("whisper-1", ""))
        self.assertGreaterEqual(info["elapsed"], 0.0)
        (call,) = client.calls
        name, stream, mime = call["file"]
        self.assertEqual((name, stream.read(), mime), ("speech.wav", b"RIFFwav", "audio/wav"))
        self.assertEqual((call["model"], call["language"], call["prompt"]),
                         ("whisper-1", "en", "Westpac"))
        self.assertEqual(call["timeout"], voice_mixin.VOICE_API_TIMEOUT)

    def test_a_400_is_retried_once_with_the_audio_alone(self):
        client = _FakeOpenAI(fail_when=lambda kw: "language" in kw)
        text, info = stub(VoiceMixin, openai_client=client)._voice_transcribe(
            self.cfg("whisper-1", "not-a-language"), b"wav")
        self.assertEqual(text, "Hello there.")
        self.assertIn("sent without", info["note"])
        self.assertEqual([sorted(c) for c in client.calls],
                         [["file", "language", "model", "timeout"], ["file", "model", "timeout"]])

    def test_a_400_with_nothing_to_drop_is_the_callers_error(self):
        client = _FakeOpenAI(fail_when=lambda kw: True)
        with self.assertRaises(openai.BadRequestError):
            stub(VoiceMixin, openai_client=client)._voice_transcribe(self.cfg("whisper-1"), b"wav")
        self.assertEqual(len(client.calls), 1)

    def test_no_key_is_a_readable_error(self):
        for provider, name in (("OpenAI", "OPENAI_API_KEY"), ("Google", "GEMINI_API_KEY"),
                               ("xAI", "XAI_API_KEY")):
            keyless = stub(VoiceMixin, openai_client=None, gemini_client=None, xai_client=None)
            cfg = VoiceMixin._voice_sanitize_config({"provider": provider})
            with self.assertRaises(RuntimeError) as caught:
                keyless._voice_transcribe(cfg, b"wav")
            message = VoiceMixin._voice_error_text(caught.exception)
            self.assertIn(name, message)
            self.assertIn("Voice Setup", message)
            self.assertEqual(keyless._voice_key_status(provider), (mock.ANY, True))
            self.assertIn(name, keyless._voice_key_status(provider)[0])
        keyed = stub(VoiceMixin, xai_client=object())
        self.assertEqual(keyed._voice_key_status("xAI"), ("XAI_API_KEY is set.", False))


class _FakeXaiPost:
    """httpx.post as voice_mixin calls it, answering from a list of (status, json)."""

    def __init__(self, *answers):
        self.calls, self._answers = [], list(answers)

    def __call__(self, url, headers, data, files, timeout):
        self.calls.append({"url": url, "headers": headers, "data": data, "files": files,
                           "timeout": timeout})
        status, payload = self._answers.pop(0)
        return httpx.Response(status, json=payload, request=httpx.Request("POST", url))


class XaiTranscribeTests(unittest.TestCase):
    """Grok Speech to Text: a multipart POST to <base>/stt (no OpenAI-style
    /audio/transcriptions exists there), with the chat client's key and base."""

    CLIENT = type("Client", (), {"api_key": "xai-key", "base_url": "https://api.x.ai/v1/"})()

    def run_with(self, post, language="", hint=""):
        cfg = VoiceMixin._voice_sanitize_config(
            {"provider": "xAI", "language": language, "hint": hint})
        with mock.patch.object(voice_mixin.httpx, "post", post):
            return stub(VoiceMixin, xai_client=self.CLIENT)._voice_transcribe(cfg, b"RIFFwav")

    def test_the_form_reaches_the_stt_endpoint_with_the_chat_clients_key(self):
        post = _FakeXaiPost((200, {"text": " Hello there. ", "language": "en", "duration": 1.5}))
        text, info = self.run_with(post, "en", "Westpac")
        self.assertEqual((text, info["model"], info["note"]),
                         ("Hello there.", "grok-voice-transcribe-2.0", ""))
        (call,) = post.calls
        self.assertEqual(call["url"], "https://api.x.ai/v1/stt")
        self.assertEqual(call["headers"], {"Authorization": "Bearer xai-key"})
        self.assertEqual(call["data"], {"model": "grok-voice-transcribe-2.0", "language": "en",
                                        "format": "true", "keyterm": ["Westpac"]})
        # `files` is encoded after `data`: xAI wants the file field last.
        self.assertEqual(call["files"], {"file": ("speech.wav", b"RIFFwav", "audio/wav")})
        self.assertEqual(call["timeout"], voice_mixin.VOICE_API_TIMEOUT)

    def test_a_400_is_retried_once_with_the_audio_and_the_model_alone(self):
        post = _FakeXaiPost((400, {"error": "Keyterm too long"}), (200, {"text": "Hello there."}))
        text, info = self.run_with(post, hint="Westpac")
        self.assertEqual(text, "Hello there.")
        self.assertIn("sent without", info["note"])
        self.assertEqual([c["data"] for c in post.calls],
                         [{"model": "grok-voice-transcribe-2.0", "keyterm": ["Westpac"]},
                          {"model": "grok-voice-transcribe-2.0"}])

    def test_failures_are_readable_and_a_bare_request_is_not_retried(self):
        post = _FakeXaiPost((400, {"error": "bad audio"}))
        with self.assertRaises(RuntimeError) as caught:
            self.run_with(post)
        self.assertEqual(len(post.calls), 1)
        self.assertIn("HTTP 400", str(caught.exception))
        self.assertIn("bad audio", str(caught.exception))
        with self.assertRaises(RuntimeError) as caught:
            self.run_with(_FakeXaiPost((401, {"error": "nope"})))
        self.assertEqual(str(caught.exception), "xAI rejected the API key: check XAI_API_KEY.")
        self.assertIn("Could not reach xAI",
                      VoiceMixin._voice_error_text(httpx.ConnectError("no route")))


class _FakeGemini:
    """client.models.generate_content, recording each call's request shape."""

    def __init__(self, reject=lambda shape: False, body=None, status=400):
        self.models = self
        self.shapes, self.calls = [], []
        self._reject, self._status = reject, status
        self._body = body or json.dumps(
            {"candidates": [{"content": {"parts": [{"text": "Hello there."}]}}]})

    def generate_content(self, model, contents, config):
        shape = ("tuned" if config.thinking_config is not None
                 else "untuned" if config.system_instruction else "plain")
        self.shapes.append(shape)
        self.calls.append((model, contents, config))
        if self._reject(shape):
            raise genai_errors.ClientError(self._status, {"error": {"message": "no"}})
        http = type("Http", (), {"body": self._body})()
        return type("Response", (), {"sdk_http_response": http, "text": None})()


class GeminiTranscribeTests(unittest.TestCase):

    def cfg(self, model, language="", hint=""):
        return VoiceMixin._voice_sanitize_config(
            {"provider": "Google", "models": {"Google": model}, "language": language, "hint": hint})

    def test_a_chat_tier_is_instructed_and_tuned_and_read_from_the_raw_body(self):
        client = _FakeGemini()
        text, _info = stub(VoiceMixin, gemini_client=client)._voice_transcribe(
            self.cfg("gemini-3.5-flash-lite", "en"), b"wav")
        self.assertEqual(text, "Hello there.")
        self.assertEqual(client.shapes, ["tuned"])
        _model, contents, config = client.calls[0]
        self.assertEqual(config.system_instruction, voice_mixin.VOICE_GEMINI_SYSTEM)
        self.assertTrue(config.should_return_http_response)
        # The guard text goes BEFORE the audio: that order wrote down all 7
        # spoken commands in the 2026-09-20 audit, the other one 6.
        self.assertEqual(contents[0], voice_mixin.VOICE_GEMINI_GUARD + " Spoken language(s): en.")
        self.assertEqual(contents[1].inline_data.mime_type, "audio/wav")

    def test_each_400_steps_down_one_request_shape(self):
        client = _FakeGemini(reject=lambda shape: shape != "plain")
        text, _info = stub(VoiceMixin, gemini_client=client)._voice_transcribe(
            self.cfg("gemini-3.8-flash"), b"wav")
        self.assertEqual(text, "Hello there.")
        self.assertEqual(client.shapes, ["tuned", "untuned", "plain"])
        # With no system instruction left, its text rides in the user turn.
        self.assertTrue(client.calls[-1][1][0].startswith(voice_mixin.VOICE_GEMINI_SYSTEM))

    def test_the_last_rung_and_any_other_status_raise(self):
        client = _FakeGemini(reject=lambda shape: True)
        with self.assertRaises(genai_errors.ClientError):
            stub(VoiceMixin, gemini_client=client)._voice_transcribe(self.cfg("gemini-3.8-flash"), b"wav")
        self.assertEqual(client.shapes, ["tuned", "untuned", "plain"])
        client = _FakeGemini(reject=lambda shape: True, status=403)
        with self.assertRaises(genai_errors.ClientError):
            stub(VoiceMixin, gemini_client=client)._voice_transcribe(self.cfg("gemini-3.8-flash"), b"wav")
        self.assertEqual(client.shapes, ["tuned"])

    def test_the_dedicated_model_gets_the_audio_and_nothing_else(self):
        body = json.dumps({"candidates": [{"content": {"parts": [
            {"audioTranscription": {"text": "Hello there."}}]}}]})
        client = _FakeGemini(body=body)
        app = stub(VoiceMixin, gemini_client=client)
        self.assertEqual(app._voice_transcribe(self.cfg("gemini-3.5-transcribe"), b"wav")[0],
                         "Hello there.")
        # Hints configured or not: it reads no text part at all (its output was
        # byte-identical with and without one), so none is sent.
        app._voice_transcribe(self.cfg("gemini-3.5-transcribe", "en", "Westpac"), b"wav")
        self.assertEqual(client.shapes, ["plain", "plain"])
        for _model, contents, config in client.calls:
            self.assertEqual(len(contents), 1)
            self.assertEqual(contents[0].inline_data.mime_type, "audio/wav")
            self.assertIsNone(config.system_instruction)


class _FakeStream:
    def __init__(self, log, fail_start=False):
        self._log, self._fail_start = log, fail_start

    def start(self):
        if self._fail_start:
            raise RuntimeError("Invalid sample rate")
        self._log.append("start")

    def stop(self):
        self._log.append("stop")

    def abort(self):
        self._log.append("abort")

    def close(self):
        self._log.append("close")


class _FakeSoundDevice:
    """RawInputStream / query_devices; `refuse` lists the rates that fail."""

    def __init__(self, refuse=(), native=48000):
        self.log, self.opened, self._refuse, self._native = [], [], set(refuse), native

    def query_devices(self, device=None, kind=None):
        return {"default_samplerate": float(self._native)}

    def RawInputStream(self, samplerate, channels, dtype, device, callback):
        self.opened.append((samplerate, channels, dtype, device))
        self.callback = callback
        return _FakeStream(self.log, fail_start=samplerate in self._refuse)


class RecorderTests(unittest.TestCase):

    def test_it_opens_16k_mono_16bit_and_keeps_every_block(self):
        sd = _FakeSoundDevice()
        recorder = _VoiceRecorder(sd)
        recorder.start(device=3)
        self.assertEqual(sd.opened, [(16000, 1, "int16", 3)])
        sd.callback(b"\x10\x00" * 4, 4, None, None)
        sd.callback(b"\x00\x01" * 4, 4, None, None)     # 256: the loudest so far
        sd.callback(b"\x20\x00" * 4, 4, None, None)
        self.assertEqual((recorder.level, recorder.peak, recorder.nbytes), (32, 256, 24))
        pcm, rate = recorder.stop()
        self.assertEqual((len(pcm), rate), (24, 16000))
        self.assertEqual(sd.log, ["start", "stop", "close"])

    def test_a_host_api_that_will_not_resample_records_at_the_native_rate(self):
        sd = _FakeSoundDevice(refuse=(16000,))
        recorder = _VoiceRecorder(sd)
        recorder.start()
        self.assertEqual([rate for rate, *_ in sd.opened], [16000, 48000])
        self.assertEqual(recorder.samplerate, 48000)
        self.assertEqual(sd.log, ["close", "start"])    # the refused stream was closed
        sd = _FakeSoundDevice(refuse=(16000, 48000))
        with self.assertRaises(RuntimeError):
            _VoiceRecorder(sd).start()

    def test_the_upload_budget_stops_the_intake(self):
        sd = _FakeSoundDevice()
        recorder = _VoiceRecorder(sd)
        recorder.start()
        with mock.patch.object(voice_mixin, "VOICE_MAX_PCM_BYTES", 10):
            sd.callback(b"\x01\x00" * 3, 3, None, None)
            self.assertFalse(recorder.full)
            sd.callback(b"\x01\x00" * 3, 3, None, None)
            self.assertTrue(recorder.full)
            sd.callback(b"\x01\x00" * 3, 3, None, None)     # ignored once full
        self.assertEqual(recorder.nbytes, 12)

    def test_abort_discards_the_recording(self):
        sd = _FakeSoundDevice()
        recorder = _VoiceRecorder(sd)
        recorder.start()
        sd.callback(b"\x01\x00", 1, None, None)
        recorder.abort()
        self.assertEqual(sd.log, ["start", "abort", "close"])
        self.assertEqual(recorder.stop(), (b"", 16000))     # nothing left, nothing to close


class _FakeRecorder:
    """What the toggle needs of a recorder; `seconds` of speech-loud audio."""

    def __init__(self, seconds=2.0, peak=9000, fail=None):
        self.samplerate, self.level, self.peak, self.full = 16000, peak, peak, False
        self._pcm = b"\x00\x10" * int(16000 * seconds)
        self._fail, self.log = fail, []

    def start(self, device=None):
        if self._fail:
            raise self._fail
        self.log.append(("start", device))

    def stop(self):
        self.log.append("stop")
        return self._pcm, self.samplerate

    def abort(self):
        self.log.append("abort")


class _FakeSD:
    class default:
        hostapi = 0

    @staticmethod
    def query_devices():
        return DeviceTests.DEVICES


class _Host:
    """The slice of the app a _VoiceDictation talks to."""

    def __init__(self, recorder, transcribe=None):
        self.recorder, self.rescans, self.transcribed = recorder, [], []
        self._transcribe = transcribe or (lambda cfg, wav: ("Hello there.", {
            "model": cfg["models"][cfg["provider"]], "note": "", "elapsed": 0.1}))

    def _voice_sd(self, rescan=False):
        self.rescans.append(rescan)
        return _FakeSD

    def _voice_new_recorder(self, sd):
        return self.recorder

    def _voice_transcribe(self, cfg, wav):
        self.transcribed.append((cfg, wav))
        return self._transcribe(cfg, wav)


class DictationTests(unittest.TestCase):

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:  # headless box, no display
            self.skipTest(f"Tk unavailable: {exc}")
        self.root.withdraw()
        self.button = tk.Button(self.root, text="Mike", width=15)
        self.status = tk.Label(self.root, fg="#555555")
        self.resting = {opt: self.button.cget(opt) for opt in (
            "text", "bg", "fg", "activebackground", "activeforeground", "highlightbackground")}
        self.heard = []
        self.cfg = VoiceMixin._voice_sanitize_config({"device": "Microphone Array"})
        _VoiceDictation.live = 0

    def tearDown(self):
        _VoiceDictation.live = 0
        self.root.destroy()

    def dictation(self, host):
        d = _VoiceDictation(host, self.root, self.button, self.status,
                            lambda: self.cfg, self.heard.append, idle_hint="hint")
        self.button.config(command=d.toggle)
        return d

    def settle(self, d, timeout=5.0):
        end = time.monotonic() + timeout
        while d.state != "idle" and time.monotonic() < end:
            self.root.update()
            time.sleep(0.01)
        self.root.update()
        self.assertEqual(d.state, "idle")

    def look(self):
        return {opt: self.button.cget(opt) for opt in self.resting}

    def test_a_full_dictation_records_transcribes_and_restores_the_button(self):
        host = _Host(_FakeRecorder())
        d = self.dictation(host)
        self.assertEqual(self.status.cget("text"), "hint")

        self.button.invoke()
        self.assertEqual(d.state, "recording")
        self.assertEqual((self.button.cget("text"), self.button.cget("bg"), self.button.cget("fg")),
                         ("■ Stop", VOICE_RECORDING_BG, "white"))
        self.assertIn("● Recording 0:00", self.status.cget("text"))
        self.assertEqual(host.rescans, [True])                  # PortAudio rescanned per press
        self.assertEqual(host.recorder.log, [("start", 1)])     # the saved NAME → its index
        self.assertEqual(_VoiceDictation.live, 1)

        self.button.invoke()
        self.assertEqual(d.state, "transcribing")
        self.assertEqual((self.button.cget("text"), str(self.button.cget("state"))),
                         ("Transcribing…", "disabled"))
        self.assertEqual(_VoiceDictation.live, 0)
        self.settle(d)
        self.assertEqual(self.heard, ["Hello there."])
        self.assertEqual(self.look(), self.resting)             # exactly as created
        self.assertEqual(str(self.button.cget("state")), "normal")
        self.assertIn("2 words", self.status.cget("text"))
        (_cfg, wav), = host.transcribed
        with wave.open(io.BytesIO(wav), "rb") as w:
            self.assertEqual((w.getframerate(), w.getnframes()), (16000, 32000))

    def test_a_slip_and_a_silent_recording_are_never_sent(self):
        for recorder, expected in ((_FakeRecorder(seconds=0.2), "Too short"),
                                   (_FakeRecorder(peak=voice_mixin.VOICE_SILENCE_PEAK - 1),
                                    "Only silence")):
            host = _Host(recorder)
            d = self.dictation(host)
            self.button.invoke()
            self.button.invoke()
            self.assertEqual((d.state, host.transcribed, self.heard), ("idle", [], []))
            self.assertIn(expected, self.status.cget("text"))
            self.assertEqual(self.look(), self.resting)
            self.assertEqual(_VoiceDictation.live, 0)

    def test_a_microphone_that_will_not_open_leaves_the_button_idle(self):
        host = _Host(_FakeRecorder(fail=RuntimeError("Voice input needs the 'sounddevice' package")))
        d = self.dictation(host)
        self.button.invoke()
        self.assertEqual((d.state, _VoiceDictation.live), ("idle", 0))
        self.assertIn("sounddevice", self.status.cget("text"))
        self.assertEqual(self.status.cget("fg"), voice_mixin.VOICE_ERROR_FG)
        self.assertEqual(self.look(), self.resting)

    def test_an_unplugged_microphone_falls_back_to_the_default_and_says_so(self):
        self.cfg = VoiceMixin._voice_sanitize_config({"device": "USB Headset"})   # not on API 0
        host = _Host(_FakeRecorder())
        self.dictation(host)
        self.button.invoke()
        self.assertEqual(host.recorder.log, [("start", None)])
        self.assertIn("'USB Headset' not found", self.status.cget("text"))

    def test_an_api_error_and_an_empty_transcript_are_reported_not_inserted(self):
        def boom(cfg, wav):
            raise RuntimeError("OPENAI_API_KEY is not set")
        for transcribe, expected in ((boom, "OPENAI_API_KEY"),
                                     (lambda cfg, wav: ("", {"model": "m", "note": "", "elapsed": 0}),
                                      "No speech recognised")):
            d = self.dictation(_Host(_FakeRecorder(), transcribe))
            self.button.invoke()
            self.button.invoke()
            self.settle(d)
            self.assertEqual(self.heard, [])
            self.assertIn(expected, self.status.cget("text"))
            self.assertEqual(str(self.button.cget("state")), "normal")

    def test_the_upload_budget_ends_the_recording_by_itself(self):
        host = _Host(_FakeRecorder())
        d = self.dictation(host)
        self.button.invoke()
        host.recorder.full = True
        self.settle(d)
        self.assertEqual(self.heard, ["Hello there."])

    def test_closing_mid_recording_releases_the_microphone(self):
        host = _Host(_FakeRecorder())
        d = self.dictation(host)
        self.button.invoke()
        d.shutdown()
        d.shutdown()                                    # idempotent
        self.assertEqual(host.recorder.log, [("start", 1), "abort"])
        self.assertEqual((_VoiceDictation.live, host.transcribed), (0, []))
        d.toggle()                                      # a closed dictation ignores presses
        self.assertEqual(host.recorder.log, [("start", 1), "abort"])

    def test_a_transcript_that_arrives_after_the_close_is_dropped(self):
        release = threading.Event()

        def slow(cfg, wav):
            release.wait(5.0)       # the API call is still out when the window closes
            return "Too late.", {"model": "m", "note": "", "elapsed": 0}

        d = self.dictation(_Host(_FakeRecorder(), slow))
        self.button.invoke()
        self.button.invoke()
        self.assertEqual(d.state, "transcribing")
        d.shutdown()
        release.set()
        end = time.monotonic() + 0.5
        while time.monotonic() < end:
            self.root.update()
            time.sleep(0.01)
        self.assertEqual(self.heard, [])

    def test_the_rescan_waits_while_any_stream_is_open(self):
        calls = []
        fake_sd = type("SD", (), {"_terminate": staticmethod(lambda: calls.append("terminate")),
                                  "_initialize": staticmethod(lambda: calls.append("initialize"))})
        with mock.patch.dict("sys.modules", {"sounddevice": fake_sd}):
            app = stub(VoiceMixin)
            self.assertIs(app._voice_sd(), fake_sd)
            self.assertEqual(calls, [])                         # no rescan unless asked
            app._voice_sd(rescan=True)
            self.assertEqual(calls, ["terminate", "initialize"])
            _VoiceDictation.live = 1                            # a stream is open somewhere
            app._voice_sd(rescan=True)
            self.assertEqual(calls, ["terminate", "initialize"])

    def test_a_missing_sounddevice_is_an_install_hint(self):
        with mock.patch.dict("sys.modules", {"sounddevice": None}):
            with self.assertRaises(RuntimeError) as caught:
                stub(VoiceMixin)._voice_sd()
        self.assertIn("pip install sounddevice", VoiceMixin._voice_error_text(caught.exception))

    def test_the_transcript_lands_at_the_cursor_and_replaces_a_selection(self):
        box = tk.Text(self.root)
        box.insert("1.0", "Check the account today")
        box.mark_set("insert", "1.17")                  # after "account"
        VoiceMixin._voice_insert_into(box, " at Westpac ")
        self.assertEqual(box.get("1.0", "end-1c"), "Check the account at Westpac today")
        # Like typing (tk::TextInsert): a selection holding the cursor is
        # replaced, one elsewhere is left alone.
        box.tag_add("sel", "1.0", "1.5")                # "Check", cursor far away
        VoiceMixin._voice_insert_into(box, "now")
        self.assertEqual(box.get("1.0", "end-1c"), "Check the account at Westpac now today")
        box.mark_set("insert", "1.5")                   # cursor at the selection's end
        VoiceMixin._voice_insert_into(box, "Verify")
        self.assertEqual(box.get("1.0", "end-1c"), "Verify the account at Westpac now today")
        empty = tk.Text(self.root)
        VoiceMixin._voice_insert_into(empty, "Hello there.")
        self.assertEqual(empty.get("1.0", "end-1c"), "Hello there.")


class MainWindowButtonTests(unittest.TestCase):
    """Voice Setup sits at the main window's bottom left, left of the Debug
    checkbox (2026-09-20). Built by the REAL setup_ui on a stub, on a mapped
    but fully transparent root (a withdrawn one is never laid out). Every
    assertion is relative to the widgets' own requested sizes, so it holds at
    any DPI and on either OS."""

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:  # headless box, no display
            self.skipTest(f"Tk unavailable: {exc}")
        self.root.attributes("-alpha", 0.0)
        self.opened = []
        app = stub(UIMixin, root=self.root, provider="OpenAI", model="m",
                   available_models=["m"], temperature=1.0)
        for name in ("debug_enabled", "tool_calls_enabled", "show_activity",
                     "show_thinking", "save_thinking", "diag_enabled"):
            setattr(app, name, tk.BooleanVar(master=self.root, value=False))
        app._update_title = lambda: None
        app._get_display_name = lambda model_id: model_id
        app.open_instruction_editor = app._start_agent = app._stop_agent = lambda: None
        app._voice_setup_from_main = lambda: self.opened.append("setup")
        app.setup_ui()
        self.app = app
        self.button, self.debug, self.diag = (app.voice_setup_button, app.debug_toggle,
                                              app.diag_toggle)
        self.root.update()      # a Frame only knows its requested width after an idle pass
        self.need = self.button.winfo_reqwidth() + 10 + self.debug.master.winfo_reqwidth()

    def tearDown(self):
        self.root.destroy()

    def lay_out(self, width):
        self.root.geometry(f"{width}x400+0+0")
        end = time.monotonic() + 3.0    # the window manager applies a resize in its own time
        while self.root.winfo_width() != width and time.monotonic() < end:
            self.root.update()
            time.sleep(0.01)
        for _ in range(3):              # then the re-grid on <Configure> needs a pass of its own
            self.root.update()
        if self.root.winfo_width() != width:
            self.skipTest(f"the display would not size the window to {width} px")
        left = self.root.winfo_rootx()
        return {"button": self.button.winfo_rootx() - left,
                "debug": self.debug.winfo_rootx() - left,
                "diag_end": self.diag.winfo_rootx() - left + self.diag.winfo_width(),
                "below": self.debug.winfo_rooty() >= self.button.winfo_rooty()
                + self.button.winfo_height()}

    def test_with_room_it_sits_left_of_debug_and_the_checkboxes_stay_centred(self):
        width = self.need + 400
        at = self.lay_out(width)
        self.assertFalse(at["below"])
        self.assertEqual(at["button"], 10)                          # the bottom-left corner
        self.assertLess(at["button"] + self.button.winfo_width(), at["debug"])
        # Centred on the WINDOW, as before the button existed (the mirror column).
        self.assertAlmostEqual((at["debug"] + at["diag_end"]) / 2, width / 2, delta=6)

    def test_a_narrow_window_puts_the_checkboxes_on_the_line_below(self):
        at = self.lay_out(self.need - 40)
        self.assertTrue(at["below"])
        self.assertEqual(at["button"], 10)
        self.assertGreaterEqual(at["debug"], 0)
        # ... and widening it again brings them back beside the button.
        self.assertFalse(self.lay_out(self.need + 400)["below"])

    def test_the_threshold_is_the_pair_side_by_side(self):
        stacked = UIMixin._bottom_row_stacked
        self.assertFalse(stacked(798, 121, 677))
        self.assertTrue(stacked(797, 121, 677))
        self.assertFalse(stacked(1, 121, 677))      # not laid out yet, not narrow

    def test_tab_reaches_it_before_the_checkboxes_and_pressing_it_opens_setup(self):
        self.lay_out(self.need + 400)
        after_pane = self.app.chat_display.tk_focusNext()
        self.assertIs(after_pane, self.button)
        self.assertIs(after_pane.tk_focusNext(), self.debug)
        self.button.invoke()
        self.assertEqual(self.opened, ["setup"])


class IndependenceTests(unittest.TestCase):
    """The settings belong to the user and the machine: one file, read at every
    Mike press, and nothing in an instruction or an instance's state file."""

    def test_no_instruction_or_state_code_mentions_voice(self):
        for name in ("instructions_mixin.py", "instruction_layout.py", "state_mixin.py",
                     "skills_mixin.py", "streaming_mixin.py", "datapaths.py"):
            src = (REPO / "myagent" / name).read_text(encoding="utf-8").lower()
            self.assertNotIn("voice", src, name)
        self.assertNotIn("voice", (REPO / "Heartbeat.py").read_text(encoding="utf-8").lower())

    def test_every_press_reads_the_one_per_user_file(self):
        self.assertEqual(os.path.normpath(voice_mixin.VOICE_CONFIG_FILE),
                         os.path.normpath(os.path.expanduser("~/.config/myagent-voice/config.json")))
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            with mock.patch.object(voice_mixin, "VOICE_CONFIG_FILE", path):
                # Two "instances" (or one, before and after applying another
                # instruction): what one saves, the other's next press loads.
                first, second = stub(VoiceMixin), stub(VoiceMixin)
                first._voice_save_config({"provider": "Google", "device": "USB Headset"})
                loaded = second._voice_load_config()
        self.assertEqual((loaded["provider"], loaded["device"]), ("Google", "USB Headset"))


class WiringTests(unittest.TestCase):
    """The dialog and the app are wired to the mixin (a static scan, no Tk)."""

    def test_the_agent_request_dialog_embeds_the_voice_row(self):
        src = (REPO / "myagent" / "safety_mixin.py").read_text(encoding="utf-8")
        self.assertIn("self._voice_build_row(", src)
        # One bind_mnemonics call per window: the dialog owns Mike's letter.
        # Voice Setup is NOT in this dialog (it moved to the main window
        # 2026-09-20, where it can be reached before a run asks anything).
        self.assertIn('bind_mnemonics(dlg, {"i": attach_btn, "r": remove_btn, "m": mike_btn})', src)
        self.assertNotIn("voice_setup_btn", src)
        # Enter never sends mid-dictation, and every close path frees the microphone.
        inject = src[src.index("def on_inject("):src.index("def on_close(")]
        self.assertLess(inject.index('dictation.state == "recording"'),
                        inject.index("resp_text.get("))
        self.assertIn('dictation.state == "transcribing"', inject)
        close = src[src.index("def _capture_and_close("):src.index("def on_inject(")]
        self.assertIn("dictation.shutdown()", close)
        # The voice row is built before the image row: Tab order is creation order.
        self.assertLess(src.index("self._voice_build_row("), src.index("attach_btn = tk.Button("))

    def test_the_main_window_carries_the_voice_setup_button(self):
        src = (REPO / "myagent" / "ui_mixin.py").read_text(encoding="utf-8")
        self.assertIn("command=self._voice_setup_from_main", src)
        self.assertIn('"v": self.voice_setup_button,', src)
        # Left of the checkboxes and created before them: Tab order is creation order.
        self.assertLess(src.index("self.voice_setup_button = tk.Button("),
                        src.index("self.debug_toggle = tk.Checkbutton("))

    def test_the_app_inherits_the_mixin(self):
        src = (REPO / "MyAgent.py").read_text(encoding="utf-8")
        self.assertIn("from myagent.voice_mixin import VoiceMixin", src)
        bases = src[src.index("class App("):src.index("def __init__")]
        self.assertIn("VoiceMixin", bases)

    def test_sounddevice_is_imported_at_first_use_only(self):
        src = (REPO / "myagent" / "voice_mixin.py").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"^(import|from) sounddevice", src, re.MULTILINE))
        self.assertEqual(src.count("import sounddevice"), 1)    # the one in _voice_sd


if __name__ == "__main__":
    unittest.main()
