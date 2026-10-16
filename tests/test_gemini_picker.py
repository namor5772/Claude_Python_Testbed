"""Characterization test: _fetch_gemini_models — the Gemini model picker.

Pinned after the 2026-10-17 audit, which found Gemini 4 Argon served (the
picker offered it, but with no thinking control — see GEMINI_THINKING_PREFIXES)
and the 3.1 Flash-Lite / 3.5 Flash / 3.7 Flash tiers deprecated on Google's
deprecations page. The filter runs on the live listing's SHAPE (name +
supported_actions): it drops the non-agentic families, the shut-down and
legacy generations and the deprecated tiers (GEMINI_DEPRECATED_MODEL_IDS /
_PREFIXES), keeps every served id in `_served_model_ids` so a pinned
instruction still runs on a hidden tier, and answers a failed or empty fetch
with the fallback list."""
import unittest

from tests._util import stub
from myagent.constants import (
    GEMINI_DEFAULT_MODEL,
    GEMINI_DEPRECATED_MODEL_IDS,
    GEMINI_DEPRECATED_MODEL_PREFIXES,
    GEMINI_FALLBACK_MODELS,
    GEMINI_PRICING,
    GEMINI_QUIET_STYLE_PREFIXES,
    GEMINI_THINKING_PREFIXES,
)
from myagent.gemini_mixin import GeminiMixin
from myagent.ui_mixin import UIMixin

GEN = ["generateContent", "countTokens"]
BIDI = ["bidiGenerateContent"]

# A cut of the live 2026-10-17 listing (63 ids): one id per branch of the
# filter, every text tier, with the actions the API reported for it.
LISTING = [
    ("gemini-4-argon", GEN + ["batchGenerateContent"]),            # NEW — kept
    ("gemini-3.8-flash", GEN + ["createCachedContent"]),           # kept (the default)
    ("gemini-3.7-flash", GEN),                                     # deprecated → 3.8 (prefix)
    ("gemini-3.6-flash", GEN),                                     # kept
    ("gemini-3.5-flash", GEN),                                     # deprecated → 3.6 (EXACT id)
    ("gemini-3.5-flash-lite", GEN),                                # kept — must survive the 3.5-flash drop
    ("gemini-3.1-pro-preview", GEN),                               # kept
    ("gemini-3.1-pro-preview-customtools", GEN),                   # kept
    ("gemini-3.1-flash-lite", GEN),                                # deprecated, shutdown 2027-05-07
    ("gemini-3.1-flash-lite-preview", GEN),                        # its preview, shut down 2026-05-25
    ("gemini-3-flash-preview", GEN),                               # kept — a current preview
    ("gemini-3-pro-preview", GEN),                                 # shut down 2026-03-09
    ("gemini-flash-latest", GEN), ("gemini-flash-lite-latest", GEN), ("gemini-pro-latest", GEN),
    ("gemini-2.5-pro", GEN), ("gemini-2.5-flash", GEN),            # legacy generation
    ("gemini-2.5-computer-use-preview-10-2025", GEN),              # own tool protocol
    ("gemini-2.0-flash", GEN), ("gemini-1.5-pro", GEN),            # shut-down generations
    ("gemini-3.8-live", BIDI), ("gemini-3.8-flash-tts", GEN),      # wrong modality
    ("gemini-3.1-flash-image", GEN), ("gemini-nano-banana-2.1", GEN),
    ("gemini-3.5-transcribe", GEN), ("gemini-omni-1.1-flash", GEN),
    ("gemini-embedding-2", ["embedContent"]), ("veo-3.1-generate-preview", ["predictLongRunning"]),
    ("lyria-3.5", GEN), ("gemma-4-31b-it", GEN), ("gemini-robotics-er-2-preview", GEN),
    ("deep-research-preview-04-2026", GEN), ("antigravity-preview-latest", GEN), ("aqa", ["generateAnswer"]),
]

PICKER = [
    "gemini-3-flash-preview",
    "gemini-3.1-pro-preview",
    "gemini-3.1-pro-preview-customtools",
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
    "gemini-3.8-flash",
    "gemini-4-argon",
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
    "gemini-pro-latest",
]


class _Model:
    def __init__(self, name, actions):
        self.name = "models/" + name
        self.supported_actions = actions


class _Client:
    def __init__(self, listing=LISTING, exc=None):
        self._listing, self._exc = listing, exc
        self.models = self

    def list(self):
        if self._exc:
            raise self._exc
        return [_Model(n, a) for n, a in self._listing]


def _host(client):
    return stub(GeminiMixin, gemini_client=client)


class PickerFilter(unittest.TestCase):

    def test_picker_keeps_the_agentic_current_tiers_sorted(self):
        host = _host(_Client())
        self.assertEqual(host._fetch_gemini_models(), PICKER)
        self.assertEqual(host._gemini_model_display_names, {m: m for m in PICKER})

    def test_deprecated_tiers_leave_the_picker_but_stay_served(self):
        host = _host(_Client())
        picker = host._fetch_gemini_models()
        served = host._served_model_ids["Google"]
        for hidden in ("gemini-3.7-flash", "gemini-3.5-flash", "gemini-3.1-flash-lite",
                       "gemini-3.1-flash-lite-preview", "gemini-3-pro-preview",
                       "gemini-2.5-pro", "gemini-2.5-flash"):
            with self.subTest(model=hidden):
                self.assertNotIn(hidden, picker)
                self.assertIn(hidden, served)        # a pinned instruction still runs on it
        # ...while the shut-down generations are not even served
        self.assertNotIn("gemini-2.0-flash", served)
        self.assertNotIn("gemini-1.5-pro", served)

    def test_the_exact_id_rule_spares_flash_lite(self):
        # 3.5-flash is deprecated, 3.5-flash-lite is current: an exact-id drop,
        # because a prefix would take both
        self.assertIn("gemini-3.5-flash", GEMINI_DEPRECATED_MODEL_IDS)
        self.assertFalse(any("gemini-3.5-flash".startswith(p) for p in GEMINI_DEPRECATED_MODEL_PREFIXES))
        self.assertIn("gemini-3.5-flash-lite", _host(_Client())._fetch_gemini_models())

    def test_fallback_on_failure_empty_or_no_client(self):
        for client in (_Client(exc=RuntimeError("down")),
                       _Client(listing=[("gemini-3.8-live", BIDI)]),
                       None):
            with self.subTest(client=type(client).__name__):
                host = _host(client)
                got = host._fetch_gemini_models()
                self.assertEqual(got, list(GEMINI_FALLBACK_MODELS))
                self.assertIsNot(got, GEMINI_FALLBACK_MODELS)   # callers mutate it
        host = _host(None)
        host._fetch_gemini_models()
        self.assertIsNone(host._served_model_ids["Google"])   # nothing is known without a listing


class FallbackList(unittest.TestCase):
    """The curated list a failed fetch serves: current tiers only, the
    default first, every entry priced by its own row, thinking-capable and
    seeded on the quiet ladder."""

    def test_default_leads_and_argon_is_second(self):
        self.assertEqual(GEMINI_FALLBACK_MODELS[0], GEMINI_DEFAULT_MODEL)
        self.assertEqual(GEMINI_DEFAULT_MODEL, "gemini-3.8-flash")
        self.assertEqual(GEMINI_FALLBACK_MODELS[1], "gemini-4-argon")

    def test_no_deprecated_tier_in_the_fallback(self):
        for mid in GEMINI_FALLBACK_MODELS:
            with self.subTest(model=mid):
                self.assertNotIn(mid, GEMINI_DEPRECATED_MODEL_IDS)
                self.assertFalse(mid.startswith(GEMINI_DEPRECATED_MODEL_PREFIXES))
                self.assertFalse(mid.startswith("gemini-2."))

    def test_every_fallback_and_picker_id_is_wired(self):
        for mid in set(GEMINI_FALLBACK_MODELS) | set(PICKER):
            with self.subTest(model=mid):
                # thinking-capable per the prefix table...
                self.assertTrue(stub(UIMixin, model=mid)._is_gemini_thinking_model(), mid)
                # ...on the thinking_level style...
                self.assertTrue(stub(UIMixin, model=mid)._gemini_uses_thinking_level(), mid)
                # ...and priced by a row that is not the bare "gemini-3" catch-all
                row = max((k for k in GEMINI_PRICING if mid.startswith(k)), key=len, default=None)
                self.assertIsNotNone(row, mid)
                self.assertNotEqual(row, "gemini-3", mid)


class Argon(unittest.TestCase):
    """Gemini 4 Argon's wiring, from the 2026-10-17 live probe."""

    def test_thinking_prefix_covers_the_fourth_generation(self):
        self.assertIn("gemini-4", GEMINI_THINKING_PREFIXES)
        self.assertTrue(stub(UIMixin, model="gemini-4-argon")._is_gemini_thinking_model())
        self.assertTrue(stub(UIMixin, model="gemini-4-argon")._gemini_uses_thinking_level())

    def test_quiet_ladder_seed(self):
        # thinking_level=minimal is a 400 on Argon; thinking_budget=0 is
        # accepted and yields 0 thoughts — so it starts one rung down
        self.assertEqual(GEMINI_QUIET_STYLE_PREFIXES["gemini-4-argon"], "budget0")
        self.assertEqual(stub(GeminiMixin, model="gemini-4-argon")._gemini_quiet_style(), "budget0")

    def test_levels_the_wire_carries(self):
        for effort, level in (("low", "low"), ("medium", "medium"), ("high", "high"),
                              ("max", "high"), ("minimal", "low")):   # the two it refuses coerce
            with self.subTest(effort=effort):
                cfg = stub(GeminiMixin, model="gemini-4-argon", thinking_effort=effort,
                           _gemini_uses_thinking_level=lambda: True)._gemini_thinking_config()
                self.assertTrue(str(cfg.thinking_level).lower().endswith(level))
                self.assertTrue(cfg.include_thoughts)

    def test_priced_by_its_own_dated_row_not_a_catch_all(self):
        self.assertIn("gemini-4-argon", GEMINI_PRICING)
        self.assertFalse(any(k == "gemini-4" for k in GEMINI_PRICING))   # no family catch-all


if __name__ == "__main__":
    unittest.main()
