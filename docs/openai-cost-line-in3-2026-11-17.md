# `in:3` on every OpenAI cost line — what it was, and the fix (2026-11-17)

## The report

The user read the last run in the API cost log — `Reconcile_Banking_Transactions_2026-11-17_094024`
on the laptop: gpt-6.1-terra, upgraded to gpt-6-astra at call 131, 153 calls, US$7.59 — and every
one of its 153 blue cost lines said `in:3`:

```
  $0.0227 this call  |  $0.02 total  (in:3  out:117  cache_write:17,668)
  $0.0209 this call  |  $0.04 total  (in:3  out:114  cache_write:15,534  cache_read:17,668)
  …
  $0.3371 this call  |  $7.59 total  (in:3  out:517  cache_write:11,931  cache_read:162,119)
```

"That seems wrong."

## The short answer

Nothing was miscounted and no dollar figure was wrong. The line printed the four **disjoint**
buckets the pricing tables rate — uncached input, output, cache write, cache read — and labelled the
first one `in:`. On the OpenAI tiers that bill cache writes (GPT-5.6 on), that bucket really is 3
tokens on an ordinary call, whatever the size of the prompt. The number was true; the label was the
fault, because `in:` reads as "tokens sent in", and the prompts behind those lines ran from 17,671
to 174,053 tokens.

Since this day `in:` **is** the whole prompt, and its split by billing rate follows in brackets:

```
  $0.0227 this call  |  $0.02 total  (in:17,671 [uncached:3  cache_write:17,668]  out:117)
  $0.3371 this call  |  $7.59 total  (in:174,053 [uncached:3  cache_write:11,931  cache_read:162,119]  out:517)
```

## Why the uncached bucket is 3

OpenAI caches automatically (no client opt-in). On GPT-5.6 and later it places a cache breakpoint
at the end of the newest message of the request, and bills what lies before it in one of two ways:
a **cache read** for the prefix an earlier call already stored, a **cache write** for the part
stored now. Only what follows the breakpoint is billed as plain input. In an ordinary call that is
3 tokens — the same 3 on a 3,130-token prompt and a 174,053-token one. (The caching guide does not
say what they are; presumably the tokens that open the model's reply.)

MyAgent's normalizer (`helpers.responses_usage_dict`) subtracts both cache counts from the
provider's gross `input_tokens`, as OpenAI's own usage example does, so the bucket it hands
`stream_worker` as `input_tokens` is exactly that remainder.

## The evidence

**1. The cost logs** (all three machines, the 365 OpenAI lines that carry token fields). The
uncached tokens per call are a constant — but not always 3:

| Period | Runs | Calls | Uncached per call |
|---|---|---|---|
| through 2026-10-08 | 279 | 6,296 | exactly **68** on 219 runs (25 more on a smaller multiple of 68 — a call that reported no usage) |
| from 2026-10-09 | 86 | 2,294 | exactly **3** on 75 runs (5 more on a smaller multiple of 3, likewise) |

The switch falls between the Mac's 22:27 run on 8 October and the laptop's 17:11 run on 9 October,
on every tier and every machine. No commit exists between 2026-10-08 22:16 and 2026-10-11 07:11,
and that one (`45965a1`) is Anthropic-only: the request MyAgent sends did not change — the provider
moved the breakpoint. A bare probe read 3 even in the 68 era (2,711 of 2,714 cached on 2026-07-31,
2,420 of 2,423 written on 2026-09-06) while MyAgent's real request, with the server tools declared,
read 68 (the 2026-09-23 note in `docs/claude/myagent/providers.md`: "68 in + 5 out + 5,530
cache-written"). What the other 65 tokens were is not known.

**2. The run's transcript.** All 153 cost lines read `in:3`, and the cache chains from call to
call: each line's `cache_read` equals the previous line's `cache_read + cache_write` on 151 of the
152 consecutive pairs. The one break is call 131, the model upgrade — a cache belongs to one model,
so gpt-6-astra started cold. The per-call figures sum to the log line's fields exactly
(459 / 45,658 / 265,446 / 11,817,353).

**3. The raw usage object**, probed live that day on gpt-6-luna (`tests/check_openai_usage_live.py`,
about US$0.001):

| Call | `input_tokens` | `cached_tokens` | `cache_write_tokens` | Neither |
|---|---|---|---|---|
| A — a cold prompt over the cache floor | 3,130 | 0 | 3,127 | **3** |
| B — the identical request again | 3,130 | 3,127 | 0 | **3** |
| C — the conversation grown by one exchange | 3,509 | 3,127 | 379 | **3** |
| D — a prompt under the 1,024-token floor | 86 | 0 | 0 | **86** |

So the 3 comes from the provider, not from MyAgent's arithmetic, and it is a measurement, not a
constant: under the cache floor nothing is cached and the whole prompt is plain input.

**4. OpenAI's pricing page and caching guide** (read 2026-11-17). The caching guide: in implicit
mode "OpenAI places a breakpoint at the end of the latest eligible message"; `cached_tokens`
"excludes tokens charged as writes"; and its worked example computes ordinary input as
`12000 - 4000 - 7000` — MyAgent's subtraction. The pricing page lists, per 1M tokens (Standard
tier, ≤272K input):

| Model | Input | Cached input | 30m write | 12h write | Output |
|---|---|---|---|---|---|
| gpt-6-astra | $10.00 | $1.00 | $12.50 | $25.00 | $50.00 |
| gpt-6-sol | $2.00 | $0.20 | $2.50 | $5.00 | $10.00 |
| gpt-6.1-sol | $2.00 | $0.10 | $2.50 | $5.00 | $10.00 |
| gpt-6.1-terra | $1.00 | $0.05 | $1.25 | $2.50 | $5.00 |
| gpt-6-luna | $0.10 | $0.01 | $0.125 | $0.25 | $0.50 |
| gpt-5.6-terra | $2.00 | $0.20 | $2.50 | $5.00 | $12.00 |

Every figure `OPENAI_PRICING` carries matches (its 4th element is the 30m write).

**5. The dollars.** The log line's per-model split, priced by that table:

- gpt-6.1-terra: 390 × $1 + 10,253 × $5 + 91,396 × $1.25 + 8,781,613 × $0.05 per M = **$0.604981**
- gpt-6-astra: 69 × $10 + 35,405 × $50 + 174,050 × $12.50 + 3,035,740 × $1 per M = **$6.982305**

Together $7.587286 — the logged `7.5873`.

## When `uncached` is not 3

- **A server-side web search.** What the search feeds the model mid-call is neither read nor
  written: 116 and 4,388 on two calls of `Act_on_unread_emails_2026-11-12_154336`, 4,207 on one of
  `Act_on_unread_emails_2026-11-06_140117`, each straight after "Searching the web...". Six runs
  since 9 October are off the 3-per-call line: these two, three of `Weather_Agent_Skill_based` on
  2026-10-18 and one upgraded `JustChat` on the desktop (2026-11-07) — the last four not opened.
- **A prompt under the cache floor** (1,024 tokens on GPT-5.6 and later): all of it.
- **A call that reported no usage.** `WBC_Payoff_Mastercard_2026-11-17_093025` logged 117 over 40
  calls: its transcript ends inside call 40, which reported nothing, so 39 calls × 3.
- **A family that does not bill writes** (before GPT-5.6): written tokens stay in the uncached
  bucket, where they are billed.

## What changed

- `myagent/event_loop_mixin.py` — `EventLoopMixin._cost_tokens(msg)` builds the token part of the
  line: `in:` = uncached + cache write + cache read; when a cache bucket is non-zero the split
  follows in brackets (`uncached` always, a cache bucket only when non-zero) and so always sums to
  `in:`; a call with no cache bucket keeps the short `in:N  out:M`. `_cost_line` wraps it for the
  priced, *unpriced* and *no charge* forms. Pure.
- `SelfBot.py` — a byte-identical copy of `_cost_tokens` on `App`, used by its `check_queue`.
  SelfBot's Anthropic lines had the same look (`in:2` or so on every call: Anthropic's breakpoint
  sits at the end of the conversation too), and one blue line with two meanings of `in:` across the
  two apps would have been a trap.
- `tests/test_cost_line_tokens.py` (15 tests) — the string on the run's own figures, the split
  summing to `in:`, the short form, the three line forms, the REAL `check_queue` drawing it in the
  real output pane (and still gated by Activity), SelfBot's copy pinned and its real `check_queue`
  drawing it. `tests/test_costlog_run_fields.py`'s two cache expectations follow the new form.
- `tests/check_openai_usage_live.py` — the probe above, hand-run and billed (outside discovery).

## Verified

- The suite: 123 modules, 2,393 tests, OK (11 skipped; none of the new module's when it is run
  alone); `ruff check MyAgent.py myagent/` at its baseline of 42 E402.
- A real headless run on gpt-6-luna (two calls, US$0.0011, against a scratch share so the user's
  store, log and chats were untouched):

  ```
    Call #1
    $0.0010 this call  |  $0.0010 total  (in:7,755 [uncached:3  cache_write:7,752]  out:23)
    Call #2
    $0.0001 this call  |  $0.0011 total  (in:7,795 [uncached:3  cache_write:40  cache_read:7,752]  out:10)
  ```

  and its log line still carried the disjoint buckets: `…;2;6;33;7792;7752;;…` — `{in}` 6, the two
  calls' `uncached`.

## What did not change

- **The `cost_update` message, the cost log and both viewers** keep the four disjoint buckets.
  They are what is priced, the log's 1,245 lines on the laptop alone are in that form, and the
  per-model split and the viewers' CACHE% are computed from them. The log's `{in}` field (the
  viewers' TOK-IN) is the line's `uncached`, **not** its `in:` — a run rebuilt by hand from the pane
  takes field 9 from `uncached:`.
- **No price and no arithmetic.** Every logged cost stands.

## For the next audit

- **Two cache-write classes.** The pricing page splits "cache writes" into a 30-minute write
  (1.25x input, the default) and a 12-hour write (2.5x). The usage object splits them the same way
  (`input_tokens_details.cache_write_tokens_details.ttl_30m_tokens` / `ttl_12h_tokens`). MyAgent
  sends no retention parameter and the probe put every written token under `ttl_30m_tokens`, so
  the one write rate in `OPENAI_PRICING` is right. If a 12-hour retention is ever requested,
  `responses_usage_dict` needs a second write bucket — as it stands it would price those writes at
  half their rate.
- **A model upgrade starts the new model's cache cold.** Call 131 of the run wrote the whole
  conversation again, 91,000 tokens at gpt-6-astra's $12.50/M: $1.14 of that one call's $1.17. The
  23 astra calls were $6.98 of the run's $7.59; the 130 terra calls before them, $0.60.
- **The constant moves without notice** (68 → 3 on 2026-10-09). Re-run the probe when a run's
  `uncached` stops reading 3.
