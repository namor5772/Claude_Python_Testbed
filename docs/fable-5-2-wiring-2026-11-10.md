# Claude Fable 5.2 wired into MyAgent and SelfBot — 2026-11-10

The user asked, the morning after the launch: *confirm/implement that Fable 5.2 is
fully and correctly wired into MyAgent and SelfBot, including every setting the GUI
needs to expose — and can I use fast mode? Should that be a parameter / checkbox?*

## The facts, read live that morning

| Source | What it said |
|---|---|
| Models API (`models.list()` through MyAgent's venv client, anthropic 0.84.0) | `claude-fable-5-2`, "Claude Fable 5.2", created 2026-11-09 18:30 UTC. `max_input_tokens` 1,000,000, `max_tokens` 128,000. Capabilities: thinking `adaptive` only (`enabled` / `disabled` / `between_tools` unsupported), effort low / medium / high / xhigh / max, image and PDF input, web search + code execution server tools, structured outputs, context management. **Identical to Fable 5.1's tree.** |
| Models overview | $8 / MTok input, $40 / MTok output; default effort `high`; 1M / 128K; retirement not sooner than 2027-11-09. Fable 5.1 and 5 moved to the "legacy, still available" list — no deprecation announced. |
| Pricing page | 5-minute cache writes $10, 1-hour $16, cache reads $0.20 (0.025x, as on 5.1). Batch $4 / $20. |
| Fast mode page | Supported: `claude-fable-5-2`, `claude-mythos-5-2`, `claude-opus-5-5`, `claude-opus-5`, `claude-opus-4-8`. **No beta header on Fable 5.2 or Opus 5.5**; the `fast-mode-2026-02-01` header still required on Opus 5 and 4.8. Fast pricing $16 / $80 on Fable 5.2 (2x). A default fast rate limit from the Start usage tier up. Opus 4.8's fast mode deprecated 2026-10-30, removed 2026-11-30. |
| Release notes, 2026-11-09 | Launch of Fable 5.2 / Mythos 5.2; the prefix check (preserved thinking) enforced for every account; thinking blocks from other models are kept when a conversation moves ONTO 5.2 and 5.2's own are dropped (unbilled) when it moves off; computer use only through `computer_toolset_20260801` (MyAgent uses its own desktop tools — not affected); the advisor tool accepts only 5.2 as a 5.2 executor's advisor (not used); 30-day data retention required, as on 5.1. |

## The live probe (three calls, about US$0.10)

Each the exact shape MyAgent builds — `thinking: {adaptive, display: summarized,
block_binding: {prefix_mismatch_behavior: drop_block}}`, `output_config.effort: low`,
`fallbacks: "default"` in the body, the `thinking-binding-controls-2026-08-01` and
`server-side-fallback-2026-07-01` betas, `max_tokens` 64, "Reply with the single
word OK." — on `claude-fable-5-2`:

| Call | Extra | Result |
|---|---|---|
| 1 | the 20260209 server-tool triple, standard speed | 200, `usage.speed` **standard**, 6,554 in / 4 out, "OK" |
| 2 | `speed: "fast"`, no fast beta header | 200, `usage.speed` **fast**, 18 in / 4 out |
| 3 | `speed: "fast"` WITH the stale `fast-mode-2026-02-01` header | 200, `usage.speed` **fast** |

So: the whole Fable 5.1 request surface is accepted on 5.2; this API key has fast
mode on 5.2 (the Build tier's default limit); and the old header is tolerated, which
lets the caller keep one request shape for all four fast-capable tiers instead of
splitting the header by model.

## What was already right (nothing to do)

The always-on class is prefix-matched (`ALWAYS_ON_THINKING_PREFIXES` = `claude-fable-`,
`claude-mythos-`) in both apps, so for thinking (adaptive only, no Off rung,
`display: summarized` + the binding), temperature (never sent), effort (Xhigh and Max
offered), the Fable surface (`_anthropic_fable_features` / SelfBot's `_fable_features`:
binding + `fallbacks: "default"`), the output ceiling (the listing's 128,000, else the
`claude-fable-5` family row) and the picker (the live listing, filtered only by the
deprecated prefixes) Fable 5.2 was served correctly the moment it appeared — the
same way 5.1 was on 2026-08-28.

## What was wrong, and is fixed

1. **Pricing.** `ANTHROPIC_PRICING` had no 5.2 row, so the longest-prefix match found
   `claude-fable-5` — $10 / $50 with $1.00 cache reads: 25% over on every token and
   5x on cache reads, in silence (the Opus 5.5 / Sonnet 5.5 bug class). Rows added
   for `claude-fable-5-2` and `claude-mythos-5-2`: `(8.00, 40.00, 10.00, 0.20)`.
2. **Fast mode.** `_anthropic_supports_fast_mode` (ui_mixin) was Opus >= (4, 8) only,
   so the editor's Fast checkbox never showed for 5.2 and a ticked instruction would
   have run standard. It now admits Fable / Mythos >= (5, 2) too — 5.1 and 5 stay out,
   they have no fast mode. `ANTHROPIC_FAST_PRICING` gained `(16.00, 80.00, 20.00, 0.40)`
   for both ids (exactly 2x, the caching multipliers on top of the fast rate, as the
   pricing page says). The header stays on every fast request (see the probe).
3. **The Model upgrade's Anthropic default** moved from `claude-fable-5-1` Max to
   `claude-fable-5-2` Max — the same tier, cheaper. An instruction with its own
   upgrade model is untouched.
4. **The lists and texts**: `FALLBACK_MODELS` / `ADAPTIVE_THINKING_MODELS` in both apps
   name the new ids (the fallback list is what a failed startup listing serves); the
   `manage_instructions` schema's `fast_mode` / `upgrade_target` / `thinking_mode`
   descriptions name 5.2; the context-window-exceeded advice names the current 1M
   tiers; the two fast-mode warnings no longer call every refusal "outside the
   research preview".

SelfBot: the two lists and the comments. It prices through the shared table, so the
new rows reach it at once. It has **no fast mode** — the Fast checkbox is a MyAgent
run setting (see the question below).

## Can I use fast mode, and should it be a checkbox?

**Yes, on Fable 5.2 and Opus 5.5, with this key, today** — probed. And it already IS a
checkbox: **Fast**, at the right end of the Instruction Editor's model-parameter row,
shown only for a fast-capable model, persisted per instruction as `fast_mode`
(2026-09-24). Until this change it appeared for the Opus tiers alone; it now appears
for Fable 5.2 as well. What it does: every call of the run sends `speed: "fast"`, the
title and the cost log's PARAMETERS say `speed=fast`, and each call is priced by the
speed the API REPORTS (`usage.speed`), so a call that fell back to standard bills
standard. The trade: up to 2.5x the output tokens per second, time to first token
unchanged, at **2x every bucket** — on Fable 5.2 that is $16 / $80 and $0.40 cache
reads. Fast and standard requests do not share a prompt cache, so a mid-run switch
rebuilds it once.

A per-instruction box is the right shape rather than a global switch: the runs where
speed is worth double money — an interactive session at an Agent Request, a short
run someone is waiting on — are a property of the instruction, not of the machine.
Not added to SelfBot: a chat window streams one reply at a time and the user is
reading as it arrives; the gain is small against the doubled price, and SelfBot's
rule is to take MyAgent's run features only when they fit a chat. Easy to add later
(one toolbar box, the `speed` kwarg, the two reactive rungs, pricing by `usage.speed`)
if a duo ever wants it.

## Tests and docs

- `tests/test_fable52_wiring.py` (new): the pricing rows (standard and fast, the fast
  row exactly 2x, 5.1 kept out of the fast table), the gate (5.2 and later admitted,
  5.1 / 5 / the other families not; the Opus gate unchanged), the REAL
  `_stream_anthropic_call` over the capturing fake client (fast: the full surface plus
  `speed` and its header, effort Max, 128K, no temperature; unticked: neither; Adaptive:
  no effort), the upgrade default, both apps' lists, the schema texts, SelfBot's
  detectors and Fable surface on the id and its pricing lookup, and a scan that SelfBot
  sends no `speed`.
- Updated: `tests/test_fast_mode.py` (the gate matrix, a 5.2 fast lookup),
  `tests/test_anthropic_detect.py`, `tests/test_pricing.py`, `tests/test_fable51_harness.py`,
  `tests/test_model_upgrade.py` (the default).
- `MyAgent_Pricing.txt`, `CLAUDE.md`, `README.md`, `.claude/rules/CLAUDE_MYAGENT.md`,
  `.claude/rules/CLAUDE_SELFBOT.md`.

## Not done, on purpose

- No instruction was re-pointed from 5.1 to 5.2 — the store is the user's; 5.1 stays
  served and priced.
- `claude_code_mcp/server.py`'s headless host still names `claude-fable-5-1` as its
  model field: the MCP server never calls a model, the field only labels the host.
- Per-message effort, `clear_at` system messages and `display: "updates"` — not
  adopted, as on 5.1.
- Re-check on 2026-11-30: Opus 4.8's fast mode is removed; the 400 rung will learn
  it off per session, and the gate can then drop 4.8 from the fast set.
