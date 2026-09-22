"""Characterization tests: XAIMixin pure/detection helpers.

Locks the two-source reasoning-effort lookup — the live /v1/language-models
capabilities first, the static XAI_REASONING_EFFORT table (longest prefix)
behind it — the vision detection, the listing parser, the model fetch, the
nearest-rung coercion and the UIMixin thinking-support plumbing that hangs
off them for provider "xAI". The static matrix mirrors the LIVE catalog
(verified 2026-07-17, grok-4.6 added 2026-08-18, grok-4.7 + grok-4.3's
xhigh rung 2026-09-23): grok-4.3 takes none..xhigh, grok-4.5 / 4.6 / 4.7
take low..xhigh (always-reasoning — "none" is HTTP 400 on all three),
grok-4.20-multi-agent takes low..xhigh (the knob is agent collaboration
count; the listing carries no capabilities block for it, so the table is
what serves it), and everything else — the pinned -reasoning/-non-reasoning
variants, grok-build, and the aliases (bare grok-4.20, grok-latest — which
floats: grok-4.3 until 2026-08, grok-4.6 then, grok-4.7 since 2026-09) —
has no client-side knob. LISTING is a trimmed copy of the real 2026-09-23
/v1/language-models body plus two entries the filter must drop."""
import copy
import unittest

from myagent.constants import XAI_FALLBACK_MODELS, XAI_REASONING_EFFORT
from myagent.ui_mixin import UIMixin
from myagent.xai_mixin import XAIMixin
from tests._util import stub


def _entry(mid, aliases=(), modalities=("text", "image"), ladder=None):
    e = {"id": mid, "object": "model", "owned_by": "xai",
         "input_modalities": list(modalities), "aliases": list(aliases)}
    if ladder is not None:
        e["capabilities"] = {"reasoning_effort": list(ladder),
                             "default_reasoning_effort": ladder[-2]}
    return e


LOW_XHIGH = ["low", "medium", "high", "xhigh"]
LISTING = {"models": [
    _entry("grok-4.20-0309-non-reasoning", ["grok-4.20-non-reasoning"]),
    _entry("grok-4.20-0309-reasoning", ["grok-4.20", "grok-4.20-reasoning"]),
    _entry("grok-4.20-multi-agent-0309", ["grok-4.20-multi-agent"]),
    _entry("grok-4.3", ["grok-4.3-latest"], ladder=["none", "low", "medium", "high", "xhigh"]),
    _entry("grok-4.5", ["grok-4.5-latest", "grok-build-latest"], ladder=LOW_XHIGH),
    _entry("grok-4.6", [], ladder=LOW_XHIGH),
    _entry("grok-4.7", [], ladder=LOW_XHIGH),
    _entry("grok-build-0.1", ["grok-code-fast-1", "grok-code-fast"]),
    # Not served today — the two shapes the filter must drop:
    _entry("grok-tts-1", modalities=("text",)),
    {"id": "imagine-image-3", "input_modalities": ["text"], "aliases": []},
]}
LIVE_IDS = ["grok-4.20-0309-non-reasoning", "grok-4.20-0309-reasoning",
            "grok-4.20-multi-agent-0309", "grok-4.3", "grok-4.5", "grok-4.6",
            "grok-4.7", "grok-build-0.1"]


def _live(**attrs):
    """A mixin stub carrying the parsed LISTING, as after a successful fetch."""
    _ids, caps = XAIMixin._parse_xai_language_models(LISTING)
    return stub(XAIMixin, _xai_caps=caps, **attrs)


class _UIStub(UIMixin, XAIMixin):
    """_model_supports_thinking (UIMixin) consults _xai_reasoning_values
    (XAIMixin) for provider xAI — the stub needs both in its MRO, like App."""


class TestParseLanguageModels(unittest.TestCase):

    def test_ids_are_the_served_language_models_sorted(self):
        ids, _caps = XAIMixin._parse_xai_language_models(LISTING)
        self.assertEqual(ids, LIVE_IDS)
        # ...which the offline fallback list must mirror 1:1 (as a set — the
        # fallback is curated, default first, the fetched list sorted).
        self.assertEqual(set(ids), set(XAI_FALLBACK_MODELS))

    def test_records_carry_ladder_and_vision(self):
        _ids, caps = XAIMixin._parse_xai_language_models(LISTING)
        self.assertEqual(caps["grok-4.7"], {"reasoning_effort": LOW_XHIGH, "vision": True})
        self.assertEqual(caps["grok-4.3"]["reasoning_effort"],
                         ["none", "low", "medium", "high", "xhigh"])
        # Listed WITHOUT a capabilities block: None, never [] — the static
        # table decides for it (see TestXaiReasoningValuesLive)
        self.assertEqual(caps["grok-4.20-multi-agent-0309"],
                         {"reasoning_effort": None, "vision": True})
        self.assertIsNone(caps["grok-build-0.1"]["reasoning_effort"])

    def test_aliases_share_their_targets_record(self):
        _ids, caps = XAIMixin._parse_xai_language_models(LISTING)
        self.assertIs(caps["grok-build-latest"], caps["grok-4.5"])
        self.assertIs(caps["grok-4.20"], caps["grok-4.20-0309-reasoning"])
        self.assertIs(caps["grok-code-fast"], caps["grok-build-0.1"])
        self.assertNotIn("grok-latest", caps)   # named under no model

    def test_dropped_entries(self):
        _ids, caps = XAIMixin._parse_xai_language_models(LISTING)
        self.assertNotIn("grok-tts-1", caps)        # XAI_NON_AGENTIC_SUBSTRINGS
        self.assertNotIn("imagine-image-3", caps)   # not a grok* id

    def test_ladder_is_ordered_quietest_first(self):
        listing = {"models": [_entry("grok-9", ladder=["XHIGH", "low", "high", "none", "medium"])]}
        _ids, caps = XAIMixin._parse_xai_language_models(listing)
        self.assertEqual(caps["grok-9"]["reasoning_effort"],
                         ["none", "low", "medium", "high", "xhigh"])
        # An unknown rung the ladder does not name sorts last, kept
        listing = {"models": [_entry("grok-9", ladder=["ultra", "low"])]}
        _ids, caps = XAIMixin._parse_xai_language_models(listing)
        self.assertEqual(caps["grok-9"]["reasoning_effort"], ["low", "ultra"])

    def test_degenerate_payloads(self):
        for payload in (None, {}, {"models": None}, {"models": []}, {"data": [{"id": "grok-4.7"}]}):
            with self.subTest(payload=payload):
                self.assertEqual(XAIMixin._parse_xai_language_models(payload), ([], {}))
        # An entry with no modalities / aliases / capabilities keys at all
        _ids, caps = XAIMixin._parse_xai_language_models({"models": [{"id": "grok-x"}]})
        self.assertEqual(caps, {"grok-x": {"reasoning_effort": None, "vision": False}})


class TestXaiReasoningValues(unittest.TestCase):
    """The static table alone — no caps on the stub (a failed / skipped fetch)."""
    CASES = {
        "grok-4.3": ["none", "low", "medium", "high", "xhigh"],
        # Alias/dated ids inherit their family's knob by prefix
        "grok-4.3-latest": ["none", "low", "medium", "high", "xhigh"],
        # grok-4.5 is always-reasoning — no "none" (HTTP 400, live-verified)
        "grok-4.5": LOW_XHIGH,
        "grok-4.5-latest": LOW_XHIGH,
        # grok-4.6 (2026-08 flagship) — same always-reasoning ladder; "none"
        # is HTTP 400 "This model does not support `reasoning_effort` value
        # `none`" (live-verified 2026-08-18)
        "grok-4.6": LOW_XHIGH,
        # grok-4.7 (2026-09) — the same ladder and the same 400 on "none"
        # (live-verified 2026-09-23)
        "grok-4.7": LOW_XHIGH,
        # Longest prefix wins — multi-agent must NOT fall through to a
        # shorter family entry
        "grok-4.20-multi-agent-0309": LOW_XHIGH,
        "grok-4.20-multi-agent-latest": LOW_XHIGH,
        # Pinned variants bake reasoning into the id — no knob (HTTP 400
        # "does not support parameter reasoningEffort"). The bare
        # "grok-4.20" alias resolves server-side to the pinned reasoning
        # variant, so it correctly gets no knob either.
        "grok-4.20-0309-reasoning": None,
        "grok-4.20-0309-non-reasoning": None,
        "grok-4.20": None,
        # Floating alias (grok-4.7 since 2026-09): deliberately knobless so a
        # server-side re-point can never strand a saved effort value
        "grok-latest": None,
        "grok-build-0.1": None,
    }

    def test_values(self):
        for mid, expected in self.CASES.items():
            with self.subTest(model=mid):
                obj = stub(XAIMixin, model=mid)
                self.assertEqual(obj._xai_reasoning_values(), expected)

    def test_explicit_model_id_overrides_self_model(self):
        obj = stub(XAIMixin, model="grok-build-0.1")
        self.assertEqual(obj._xai_reasoning_values("grok-4.3"),
                         ["none", "low", "medium", "high", "xhigh"])

    def test_table_ladders_are_ordered_and_lower_case(self):
        ladder = ("none", "low", "medium", "high", "xhigh")
        for prefix, values in XAI_REASONING_EFFORT.items():
            with self.subTest(prefix=prefix):
                self.assertEqual(values, sorted(values, key=ladder.index))


class TestXaiReasoningValuesLive(unittest.TestCase):
    """The listing's capabilities answer first; the table only where the
    listing said nothing."""

    def test_listed_ladder_wins(self):
        self.assertEqual(_live(model="grok-4.7")._xai_reasoning_values(), LOW_XHIGH)
        # A live ladder that differs from the table is what the model gets —
        # the day grok-4.3 gained xhigh no code change was needed
        caps = {"grok-4.3": {"reasoning_effort": ["none", "low"], "vision": True}}
        self.assertEqual(stub(XAIMixin, model="grok-4.3", _xai_caps=caps)._xai_reasoning_values(),
                         ["none", "low"])
        # ...and a copy, never the record's own list
        obj = _live(model="grok-4.7")
        obj._xai_reasoning_values().append("max")
        self.assertEqual(obj._xai_reasoning_values(), LOW_XHIGH)

    def test_a_new_tier_needs_no_table_entry(self):
        # The 2026-09-23 finding: grok-4.7 shipped 2026-09-01 and had no
        # Reasoning combobox for three weeks because the table did not name
        # it. A hypothetical grok-4.8 in the listing gets its knob at once.
        listing = {"models": [_entry("grok-4.8", ladder=["low", "high"])]}
        _ids, caps = XAIMixin._parse_xai_language_models(listing)
        obj = stub(XAIMixin, model="grok-4.8", _xai_caps=caps)
        self.assertNotIn("grok-4.8", XAI_REASONING_EFFORT)
        self.assertEqual(obj._xai_reasoning_values(), ["low", "high"])

    def test_alias_resolves_to_its_target(self):
        # Statically knobless (no "grok-build-latest" prefix in the table);
        # the listing says it is grok-4.5, which has one
        self.assertEqual(_live(model="grok-build-latest")._xai_reasoning_values(), LOW_XHIGH)
        self.assertEqual(_live(model="grok-4.5-latest")._xai_reasoning_values(), LOW_XHIGH)

    def test_listed_without_capabilities_falls_back_to_the_table(self):
        # multi-agent: the knob is real (xhigh billed 6x low's tokens live)
        # but unadvertised
        self.assertEqual(_live(model="grok-4.20-multi-agent-0309")._xai_reasoning_values(),
                         LOW_XHIGH)
        # the pinned variants and grok-build: unadvertised AND untabled
        for mid in ("grok-4.20-0309-reasoning", "grok-4.20-0309-non-reasoning",
                    "grok-4.20", "grok-build-0.1", "grok-code-fast"):
            with self.subTest(model=mid):
                self.assertIsNone(_live(model=mid)._xai_reasoning_values())

    def test_unlisted_ids_use_the_table(self):
        self.assertIsNone(_live(model="grok-latest")._xai_reasoning_values())
        self.assertEqual(_live(model="grok-4.6-something")._xai_reasoning_values(), LOW_XHIGH)

    def test_no_caps_attribute_means_the_table(self):
        obj = stub(XAIMixin, model="grok-4.7", _xai_caps=None)
        self.assertEqual(obj._xai_reasoning_values(), LOW_XHIGH)
        self.assertEqual(obj._xai_model_caps(), {})


class TestXaiNearestEffort(unittest.TestCase):

    def test_grok_4_3_has_a_none_floor(self):
        obj = stub(XAIMixin, model="grok-4.3")
        for requested, expected in (("low", "low"), ("xhigh", "xhigh"), ("off", "none"),
                                    ("adaptive", "none"), ("", "none"), ("max", "xhigh"),
                                    ("minimal", "low"), ("banana", "none"), (None, "none")):
            with self.subTest(requested=requested):
                self.assertEqual(obj._xai_nearest_effort(requested), expected)

    def test_always_reasoning_tiers_floor_at_low(self):
        for mid in ("grok-4.5", "grok-4.6", "grok-4.7"):
            obj = stub(XAIMixin, model=mid)
            for requested, expected in (("none", "low"), ("off", "low"), ("adaptive", "low"),
                                        ("minimal", "low"), ("max", "xhigh"), ("Medium", "medium"),
                                        ("high", "high"), ("garbage", "low")):
                with self.subTest(model=mid, requested=requested):
                    self.assertEqual(obj._xai_nearest_effort(requested), expected)

    def test_knobless_is_none(self):
        for mid in ("grok-4.20-0309-reasoning", "grok-build-0.1", "grok-latest"):
            with self.subTest(model=mid):
                self.assertIsNone(stub(XAIMixin, model=mid)._xai_nearest_effort("high"))

    def test_effective_effort_reads_thinking_effort(self):
        self.assertEqual(stub(XAIMixin, model="grok-4.7", thinking_effort="max")._xai_effective_effort(),
                         "xhigh")
        self.assertEqual(stub(XAIMixin, model="grok-4.7", thinking_effort="low")._xai_effective_effort(),
                         "low")
        self.assertEqual(stub(XAIMixin, model="grok-4.3", thinking_effort="off")._xai_effective_effort(),
                         "none")
        # No thinking_effort attribute at all (a bare host) → the floor
        self.assertEqual(stub(XAIMixin, model="grok-4.7")._xai_effective_effort(), "low")
        self.assertIsNone(stub(XAIMixin, model="grok-build-0.1", thinking_effort="high")
                          ._xai_effective_effort())
        # The explicit model id wins over self.model
        self.assertEqual(stub(XAIMixin, model="grok-build-0.1", thinking_effort="max")
                         ._xai_effective_effort("grok-4.5"), "xhigh")


class TestXaiModelParams(unittest.TestCase):

    def test_knob_model(self):
        obj = stub(XAIMixin, model="grok-4.7", thinking_effort="medium", temperature=0.3)
        self.assertEqual(obj._xai_model_params(),
                         {"temperature": 0.3, "reasoning": {"effort": "medium", "summary": "auto"}})
        # effort before summary — the order the Debug dump shows
        self.assertEqual(list(obj._xai_model_params()["reasoning"]), ["effort", "summary"])

    def test_stale_effort_is_coerced(self):
        obj = stub(XAIMixin, model="grok-4.7", thinking_effort="none", temperature=1.0)
        self.assertEqual(obj._xai_model_params()["reasoning"], {"effort": "low", "summary": "auto"})
        obj = stub(XAIMixin, model="grok-4.3", thinking_effort="max", temperature=1.0)
        self.assertEqual(obj._xai_model_params()["reasoning"], {"effort": "xhigh", "summary": "auto"})

    def test_knobless_model_still_asks_for_the_summary(self):
        # The pinned reasoning variant and grok-build reason and stream
        # their summaries only when asked (probed 2026-09-23); the
        # non-reasoning variant accepts the field and streams none
        for mid in ("grok-4.20-0309-reasoning", "grok-4.20-0309-non-reasoning",
                    "grok-build-0.1", "grok-latest"):
            with self.subTest(model=mid):
                obj = stub(XAIMixin, model=mid, thinking_effort="high", temperature=0.5)
                self.assertEqual(obj._xai_model_params(),
                                 {"temperature": 0.5, "reasoning": {"summary": "auto"}})

    def test_live_alias_gets_its_targets_knob(self):
        obj = _live(model="grok-build-latest", thinking_effort="high", temperature=1.0)
        self.assertEqual(obj._xai_model_params()["reasoning"], {"effort": "high", "summary": "auto"})


class TestXaiVisionModel(unittest.TestCase):
    STATIC_CASES = {
        "grok-4.3": True,
        "grok-4.5": True,
        "grok-4.6": True,
        "grok-4.7": True,
        "grok-4.20-0309-non-reasoning": True,
        "grok-latest": True,
        "grok-build-latest": True,
        # Vision since the 2026-09-23 audit (the listing says text + image
        # and it read a red test square live) — the table is empty now
        "grok-build-0.1": True,
        "grok-code-fast-1": True,
    }

    def test_static(self):
        for mid, expected in self.STATIC_CASES.items():
            with self.subTest(model=mid):
                self.assertEqual(stub(XAIMixin, model=mid)._is_xai_vision_model(), expected)

    def test_live_modalities_decide(self):
        listing = copy.deepcopy(LISTING)
        listing["models"].append(_entry("grok-4.9-text", ["grok-text-latest"], modalities=("text",)))
        _ids, caps = XAIMixin._parse_xai_language_models(listing)
        for mid, expected in (("grok-4.9-text", False), ("grok-text-latest", False),
                              ("grok-4.7", True), ("grok-build-0.1", True),
                              ("grok-code-fast", True), ("grok-latest", True)):
            with self.subTest(model=mid):
                self.assertEqual(stub(XAIMixin, model=mid, _xai_caps=caps)._is_xai_vision_model(),
                                 expected)


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _Client:
    """The one SDK call _fetch_xai_models makes: client.get(path, cast_to=)."""
    def __init__(self, payload=None, exc=None):
        self._payload, self._exc, self.calls = payload, exc, []

    def get(self, path, **kw):
        self.calls.append((path, kw))
        if self._exc:
            raise self._exc
        return _Response(self._payload)


class TestFetchXaiModels(unittest.TestCase):

    def test_success_lists_ids_and_keeps_caps(self):
        client = _Client(LISTING)
        obj = stub(XAIMixin, xai_client=client)
        self.assertEqual(obj._fetch_xai_models(), LIVE_IDS)
        (path, kw), = client.calls
        self.assertEqual(path, "/language-models")
        self.assertIn("cast_to", kw)
        self.assertEqual(obj._xai_model_display_names, {mid: mid for mid in LIVE_IDS})
        self.assertEqual(obj._xai_caps["grok-4.7"]["reasoning_effort"], LOW_XHIGH)
        self.assertIs(obj._xai_caps["grok-build-latest"], obj._xai_caps["grok-4.5"])
        self.assertEqual(obj._xai_reasoning_values("grok-4.7"), LOW_XHIGH)

    def test_failure_serves_the_fallback_and_clears_stale_caps(self):
        stale = {"grok-4.3": {"reasoning_effort": ["none"], "vision": False}}
        obj = stub(XAIMixin, xai_client=_Client(exc=RuntimeError("down")), _xai_caps=stale,
                   _xai_model_display_names={"grok-4.3": "grok-4.3"})
        self.assertEqual(obj._fetch_xai_models(), list(XAI_FALLBACK_MODELS))
        self.assertEqual(obj._xai_caps, {})
        self.assertEqual(obj._xai_model_display_names, {})
        # ...so the static tables answer again
        self.assertEqual(obj._xai_reasoning_values("grok-4.3"), XAI_REASONING_EFFORT["grok-4.3"])
        self.assertTrue(obj._is_xai_vision_model("grok-4.3"))

    def test_empty_listing_serves_the_fallback(self):
        obj = stub(XAIMixin, xai_client=_Client({"models": [{"id": "imagine-only"}]}))
        self.assertEqual(obj._fetch_xai_models(), list(XAI_FALLBACK_MODELS))
        self.assertEqual(obj._xai_caps, {})

    def test_no_client_serves_the_fallback_without_a_call(self):
        obj = stub(XAIMixin, xai_client=None)
        self.assertEqual(obj._fetch_xai_models(), list(XAI_FALLBACK_MODELS))
        self.assertEqual(obj._xai_caps, {})
        # A fresh list each time — callers mutate it
        self.assertIsNot(obj._fetch_xai_models(), XAI_FALLBACK_MODELS)


class TestModelSupportsThinkingXai(unittest.TestCase):
    def test_extended_for_knob_families(self):
        for mid in ("grok-4.3", "grok-4.5", "grok-4.6", "grok-4.7",
                    "grok-4.20-multi-agent-0309"):
            with self.subTest(model=mid):
                obj = stub(_UIStub, provider="xAI", model=mid)
                self.assertEqual(obj._model_supports_thinking(), "extended")

    def test_none_for_knobless_families(self):
        for mid in ("grok-4.20-0309-reasoning", "grok-4.20-0309-non-reasoning",
                    "grok-latest", "grok-build-0.1"):
            with self.subTest(model=mid):
                obj = stub(_UIStub, provider="xAI", model=mid)
                self.assertIsNone(obj._model_supports_thinking())

    def test_live_listing_drives_the_combobox(self):
        # A tier the table has never heard of gets the Reasoning combobox
        # from the listing alone; a live alias of a knob model too
        listing = {"models": [_entry("grok-4.8", ["grok-4.8-latest"], ladder=LOW_XHIGH)]}
        _ids, caps = XAIMixin._parse_xai_language_models(listing)
        for mid in ("grok-4.8", "grok-4.8-latest"):
            with self.subTest(model=mid):
                obj = stub(_UIStub, provider="xAI", model=mid, _xai_caps=caps)
                self.assertEqual(obj._model_supports_thinking(), "extended")
        obj = stub(_UIStub, provider="xAI", model="grok-build-latest", _xai_caps=caps)
        self.assertIsNone(obj._model_supports_thinking())   # unlisted here → the table


if __name__ == "__main__":
    unittest.main()
