# Moonshot (Kimi) model audit — 2026-10-26

What MyAgent exposes of Moonshot's Kimi catalogue, checked model by model
against the live API and Moonshot's documentation on 2026-10-26, and what
changed because of it. The previous audits were 2026-07-25 (the wiring),
2026-08-25 (pricing) and 2026-09-23 (the `none` rung, the vision flags).

Method: the raw `/v1/models` listing; the docs (models page, parameter
reference, reasoning-effort and thinking guides, context-caching guide, the
Chat Completions reference, the changelog, and the pricing page — whose table
is rendered client-side, so its `.md` copy has an empty `<table>` and the
numbers were read from the page's React payload); and MyAgent's REAL
`_stream_kimi_call` on a bare host (no Tk, no store) against the live API,
recording every request's wire shape and every response's raw usage. The
harness is `tests/check_kimi_models_live.py` (hand-run, about US$0.02 for
the whole run).

## The catalogue

Six ids served, two of them new since the September audit:

| Model | Context | Input | Reasoning knob (listing) | Status |
|---|---|---|---|---|
| `kimi-k3.1-pro` | 1,048,576 | text, image, video | `reasoning_effort` low / high / max, default max; `supports_thinking_type: only` | **new** — "the first model of the new K3.1 series and Kimi's most capable flagship to date" |
| `kimi-k3.1-pro-highspeed` | 1,048,576 | text, image, video | same | **new** — the same model at 2–3x the speed |
| `kimi-k3` | 1,048,576 | text, image, video | same | the previous flagship |
| `kimi-k2.6` | 262,144 | text, image, video | none listed (`thinking` toggle) | **retiring** |
| `kimi-k2.7-code` | 262,144 | text, image, video | none listed (always thinks) | **retiring** |
| `kimi-k2.7-code-highspeed` | 262,144 | text, image, video | none listed | **retiring** |

The models page (and the pricing page) carry a new notice: "starting
October 31, 2026, K2 series models will no longer be available to newly
registered users; existing users are not affected. We plan to retire all K2
series models by the end of December 2026." `kimi-k2.5` and the
`moonshot-v1` series were retired on 2026-08-31 (a 404 now); the dash-named
`kimi-k2-*` family on 2026-05-25.

The listing now publishes the K3 line's effort ladder in two shapes
(`reasoning_efforts.valid_efforts` and `reasoning.effort.valid`) and a
`supports_thinking_type` of `"only"`; the K2 entries carry neither.

## Prices (USD per million tokens, from Moonshot's table)

| Model | Input (miss) | Output | Cached input | Cache write 5m | Cache write 1h |
|---|---|---|---|---|---|
| `kimi-k3.1-pro` | 2.00 | 10.00 | 0.20 | 2.00 | 4.00 |
| `kimi-k3.1-pro-highspeed` | 4.00 | 20.00 | 0.20 | 4.00 | 8.00 |
| `kimi-k3` | 3.00 | 15.00 | 0.30 | 3.00 | 6.00 |
| `kimi-k2.7-code` | 0.95 | 4.00 | 0.19 | — | — |
| `kimi-k2.7-code-highspeed` | 1.90 | 8.00 | 0.38 | — | — |
| `kimi-k2.6` | 0.95 | 4.00 | 0.16 | — | — |

One flat rate at every context length. Cache writes are a billable item on
the K3 line only, "billed separately by TTL (5min / 1h)"; a 5-minute write
costs the same as a miss on every K3 model, a 1-hour write twice that.
MyAgent sends no `prompt_cache_options`, so its writes are at the 5-minute
tier. The K3.1 Pro flagship is cheaper than K3 on every column.

## Parameters, per the docs and the probes

- **The K3 line** (`kimi-k3.1-pro`, `-highspeed`, `kimi-k3`) shares one
  parameter contract (the parameter reference says the `kimi-k3` column
  "applies to both" K3.1 ids): always reasons with Preserved Thinking on,
  a top-level `reasoning_effort` of `low` / `high` / `max` (default `max`),
  no `thinking` parameter ("you do not need to (and should not) pass" it),
  `tool_choice: required` accepted, `max_completion_tokens` "defaults to
  131072 and can be set up to 1048576", `temperature` fixed at 1.0.
- **`reasoning_effort: "none"`** is not in the docs ("How do I turn off
  Kimi K3.1 Pro's chain-of-thought? You can't") nor in the listing's
  `valid_efforts`, yet it is a real off switch on all three K3 ids, measured
  through MyAgent's caller: no `reasoning_content`, no
  `completion_tokens_details.reasoning_tokens`, and a prompt 68 tokens
  shorter (146 against 214 at `low` — the reasoning preamble is gone). The
  ladder at `low` / `high` / `max` on `kimi-k3.1-pro` produced 33 / 57 / 84
  reasoning tokens for a one-word question. MyAgent keeps its None rung on
  the whole line, as it has on `kimi-k3` since 2026-09-23.
- **`temperature`** — `0.3` is HTTP 400 `invalid temperature: only 1 is
  allowed for this model` on `kimi-k3.1-pro` and `kimi-k2.6` (the parameter
  reference: fixed at 1.0 on every model, 0.6 on k2.6 without thinking).
  MyAgent never sends it.
- **The K2 line** is unchanged: `kimi-k2.6` takes `thinking` enabled /
  disabled with `keep: "all"`; `kimi-k2.7-code` accepts only
  `{"type": "enabled", "keep": "all"}` (or nothing); `max_tokens >= 16000`
  for tool loops.
- **Preserved Thinking**: "For K3.1 Pro and K3, this is required in
  multi-turn conversations and tool-call loops" — the whole assistant
  message, `reasoning_content` and `tool_calls` included, goes back as-is.

## What the live round trips showed

Every served model, through the real caller, with one tool declared: call 1
returned a `get_weather` tool call with its reasoning streamed first; call 2
carried that reasoning back as `reasoning_content` on the assistant message
beside the `tool` result and answered from it. Served model ids echoed the
request (no aliasing). Latencies 0.7–2.7 s to the first token.

| Model | Call 1 (prompt / completion / reasoning) | Call 2 | Notes |
|---|---|---|---|
| `kimi-k3.1-pro` @ low | 215 / 84 / 31 | 335 / 61 / 34, `cache_write_tokens: 256` | the write field appears on the K3 line |
| `kimi-k3` @ low | 215 / 79 / 25 | 330 / 54 / 26, `cache_write_tokens: 256` | |
| `kimi-k2.6` thinking on, keep all | 91 / 66 / 48 | 184 / 38 / 23, no write field | |
| `kimi-k2.7-code-highspeed` | 91 / 51 / 32 | 168 / 37 / 23, no write field | no knobs sent |

`prompt_tokens_details.cache_write_tokens`, `cached_tokens` and the uncached
rest "are mutually exclusive and sum to `prompt_tokens`" (the Chat
Completions reference), which is the contract the cost code now relies on.

## What changed in MyAgent

1. **The K2 line left the picker and the price tables** under the model
   retirement policy (announced retirements leave at once; parameter wiring
   stays): `KIMI_DEPRECATED_MODEL_PREFIXES = ("kimi-k2.",)`, applied by
   `_fetch_kimi_models` AFTER every kept id is recorded as served
   (`helpers.note_served_models`), so an instruction pinned to `kimi-k2.6` or
   `kimi-k2.7-code` still runs it behind the "hidden from the model picker"
   warning until Moonshot's shutdown — unlogged, with the unpriced warning,
   as the policy says. One saved instruction (`Test1`, `kimi-k2.7-code`) is
   affected; the fifteen `kimi-k3` pins and the three `kimi-k3` upgrade
   targets are not.
2. **The default is `kimi-k3.1-pro`**: the cheapest K3 tier, the flagship,
   and the migration target Moonshot's own notice points to.
   `KIMI_FALLBACK_MODELS` mirrors the picker.
3. **Pricing rows** for `kimi-k3.1-pro` and `-highspeed` (input / output,
   cache hit, cache write). Until then the `kimi-k3` prefix priced the new
   tier at K3's rates — 1.5x over on every token.
4. **Cache writes are their own bucket**: `_kimi_usage_dict` reads
   `cache_write_tokens`, emits it as `cache_creation_input_tokens`, takes it
   out of the uncached count and bills it at the 5-minute write rate
   (`KIMI_CACHE_WRITE_PRICING`). The dollars were already right (the write
   rate equals the miss rate), the cost log's buckets were not.
5. **The output cap on the K3 line is the API's own 131,072 default**
   (`KIMI_MAX_COMPLETION_TOKENS`), thinking on or off — the K2 rule's
   32,768 had lowered it, and a long max-effort turn would have been cut
   short with a `finish_reason=length`. The K2 line keeps 32,768 / 8,192.
6. **The listing's ladder is read**: `_kimi_caps` keeps each model's
   `valid_efforts` and `supports_thinking_type`; a tier the static tables
   do not name gets its Reasoning combobox from the listing the day it
   appears, and is treated as always-thinking when the listing says
   `"only"`. The hand-verified table still answers first for the K3 line,
   because its `none` rung is measured, not published.

Pinned in `tests/test_kimi_detect.py`, `tests/test_pricing.py`,
`tests/test_model_upgrade.py` and `tests/test_thinking_mode_temp.py`.

## Re-check

- The K2 shutdown (end of December 2026): once the ids 404, drop the
  `kimi-k2.` prefix from `KIMI_DEPRECATED_MODEL_PREFIXES` and the K2 param
  wiring can go with it.
- The pricing page, whenever Moonshot announces a model or a rate — it is
  the one table at `platform.kimi.ai/docs/pricing/chat`, client-rendered.
- `reasoning_effort: "none"` on a future K3 tier: it is undocumented, so
  measure it (the `effort` command of the live check) before offering it.
