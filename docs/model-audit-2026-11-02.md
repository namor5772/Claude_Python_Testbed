# Model audit — 2026-11-02

Every LLM model MyAgent can run, checked for two things: that the parameters
it exposes and sends are the ones the provider's API takes for that model, and
that every priced bucket matches the provider's current rate card. Five
providers (Anthropic, OpenAI, Google, xAI, Moonshot; Ollama is free and local),
done from the laptop with the real keys, read-only except where noted.

## Method

1. **Every provider's live listing through MyAgent's own fetchers** on a bare
   host (`_fetch_available_models`, `_fetch_openai_models`,
   `_fetch_gemini_models`, `_fetch_xai_models`, `_fetch_kimi_models`): what the
   picker offers, what is served but hidden, each model's thinking kind and
   level ladder (`_upgrade_level_values`, the editor's own rung helpers on a
   probe), its price lookup (`_get_pricing`) and the per-provider request
   facts (always-on / on-by-default / temperature rejection / cache-write
   billing / vision / the listing's own capability records).
2. **The rate cards** — platform.claude.com pricing, developers.openai.com
   pricing, ai.google.dev pricing (the three fetched that day), the xAI
   listing's own price fields, Moonshot's client-rendered table (the React
   payload grepped) — row by row against `constants.py`.
3. **The deprecation pages** of Anthropic, OpenAI and Google.
4. **Live probes, tiny** (≈ US$0.02 in all): the Anthropic Models API
   capability tree (zero tokens) for every served id's thinking types; the
   three floating Gemini aliases and the three Grok aliases resolved with one
   call each; gpt-6.1-terra's reasoning contract (none / minimal / max /
   temperature) earlier the same day.
5. **The instruction store**: every model the user's saved instructions and
   upgrade targets pin, against the picker, the served set and the price
   table.

## Findings

### Prices: correct on every picker model

Anthropic (all 13 picker ids, the Haiku 5.5 second card, the three fast-mode
rows), OpenAI (every served 5.2 / 5.4 / 5.5 / 5.6 / 6 / 6.1 row, cache writes
included), Gemini (Argon's launch rate and its 2026-12-01 flip, the 3.6 / 3.8
Flash promo and its 2027-01-01 flip, 3.5 Flash-Lite, 3.1 Pro, 3 Flash), xAI
(the listing's price fields match `XAI_PRICING` line for line, and the API's
`cost_in_usd_ticks` is authoritative anyway) and Moonshot (the K3 line's
input / output / cache-hit / cache-write rates) all agree with the tables.
The floating aliases price by the right row: gemini-flash-latest →
gemini-3.8-flash, gemini-flash-lite-latest → 3.5-flash-lite, gemini-pro-latest
→ 3.1-pro-preview, grok-latest → grok-4.7, grok-build-latest → grok-4.5,
grok-4.20 → grok-4.20-0309-reasoning (all verified live).

### Fixed: OpenAI's deprecations since the July audit

The deprecations page names ids the picker still offered: the whole gpt-4.1
family (4.1 and 4.1-mini retire 2027-01-14; 4.1-nano shut down 2026-10-23 and
is still listed), gpt-5-pro (announced 2026-06-11, shutdown 2026-12-11 — the
July audit had it as unannounced), the whole gpt-5.1 family (the base id
announced 2026-10-01 → 2027-04-01; -chat / -codex / -codex-max / -codex-mini
shut down 2026-07-23), gpt-5.2-chat-latest (2026-08-10), gpt-5.2-codex
(2026-07-23), gpt-5.3-codex (→ 2027-04-01) and gpt-5.3-chat-latest
(2026-08-10), gpt-5.4-nano (→ 2027-04-01), codex-mini-latest. Under the
retirement policy they left the picker and the price table at once
(`OPENAI_DEPRECATED_MODEL_PREFIXES`, `OPENAI_PRICING`); their param wiring
stays, so a pinned instruction still runs one with the "hidden from the model
picker" and "no pricing row" warnings. The picker went from 40 ids to 10
(plus dated snapshots). None of the user's instructions pins a removed id.

A table subtlety found on the way: a retiring sub-tier whose family row
survives (gpt-5.4-nano under gpt-5.4, the 5.2 -chat / -codex ids under
gpt-5.2) cannot simply be deleted — the longest-prefix rule would price it
by the family's row, nano at twelve times its rate. Such an id keeps an
explicit `None` row, which `_get_pricing` reads as unpriced and
`_unpriced_model_warning` now tests by the entry rather than the prefix.

### Fixed: Sonnet 5.5's off switch

The Models API tree says `claude-sonnet-5-5` rejects `thinking: disabled` and
takes `between_tools` — the only served model with that pair. MyAgent sent it
the disable under the Sonnet ≥ 5 rule (SelfBot had the contract since
2026-10-14), so an instruction pinned to it with Thinking Off would have
failed on call 1; the four pinned that day all run with thinking on.
`_is_anthropic_sonnet_55` (ui_mixin) now makes Off a bare `between_tools` in
the live call and the Debug dump, and gives the model the Fable surface
through `_anthropic_fable_features` (the binding beside adaptive thinking,
`fallbacks: "default"` beside either shape). `tests/test_sonnet55_wiring.py`.

### Fixed: the long-context rate cards

OpenAI bills the 5.4 / 5.5 / 5.6 / 6 tiers at 2x input and cache and 1.5x
output above 272K input tokens, and Gemini 3.1 Pro at $4 / $18 (cached
$0.40) above 200K; MyAgent priced every call on the base card, so a long run
on a 1M-context tier was logged low past the line. `OPENAI_LONG_CONTEXT_PRICING`
and `GEMINI_LONG_CONTEXT_PRICING` join Haiku 5.5's card in the one
`LONG_CONTEXT_PRICING` map `_get_pricing` reads, selected per call by the
prompt size (input plus both cache buckets, what each API counts) with the
existing one-⚠-per-run at the crossing. A card applies only by the same
prefix as the base row, so gpt-5.4-mini, which the page gives no long-context
rate, keeps its row at any size. Not modelled, deliberately: OpenAI's 12-hour
cache-write tier (MyAgent sends no retention option), Fast / Flex / Batch
multipliers, Gemini cache storage.

### Parameters: no other drift

Every Anthropic ladder `_anthropic_mode_values` builds and every thinking
kind agrees with the capability tree (Opus 5.5 and the Fable class always-on,
Haiku 5.5 / Opus 5 / Sonnet 5 with the accepted disable, the 4.6 tiers
without Xhigh, the 4.5 ids on the manual budget). OpenAI: the always-reasoning
list (astra, 6.1-sol) and the None rung on the rest match the probes;
gpt-6.1-terra joined the table earlier the same day. xAI: every picker model's
knob and vision come from the listing. Moonshot: the K3 line's ladder from the
listing, None the measured off switch. Nothing changed in the request
builders beyond Sonnet 5.5.

### Left as is, for the user

- **AgentRun_Test3** pins `claude-sonnet-4-5-20250929`: deprecated, retires
  2026-11-30 → Sonnet 5.5; runs unlogged today.
- **Test1** pins `kimi-k2.7-code`: the K2 line retires at the end of December
  2026; runs unlogged today.
- `gemini-3.5-flash` (hidden, pinned by nothing) keeps its old $1.50 / $9 row
  although Google now auto-routes it to 3.6-flash; what the auto-routed call
  bills is not published.
- `grok-latest` no longer appears among the listing's aliases (it still
  resolves, to grok-4.7); a pinned instruction would drift to the default
  with a warning. Nothing pins it.

## Re-running

`tests/check_kimi_models_live.py` and `tests/check_ollama_models_live.py` are
the hand-run harnesses for their providers; the listing script of this audit
is not kept — it is the five fetchers on a bare host plus
`_upgrade_level_values` / `_get_pricing` per id, and the memory note
`model-audit-probe-technique` has the recipe and the URLs that work.
