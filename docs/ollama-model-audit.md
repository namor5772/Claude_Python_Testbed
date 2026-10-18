# Ollama model audit — 2026-10-18

The models MyAgent can run through the local Ollama server on the M4 Mac Mini,
each one driven live through MyAgent's own request path, with what it can do,
how MyAgent exposes it, what was retired and what is worth adding. Re-run it
with `python tests/check_ollama_models_live.py run --prefill` (hand-run, free,
20–60 minutes; `report` prints the saved results).

## The machine and the server

| | |
|---|---|
| Machine | Mac Mini, Apple M4 (base), 32 GB unified memory, 713 GB free |
| Ollama | 0.32.9 (Homebrew service, `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0`); 0.40.6 is current (2026-10-16) |
| Python SDK | `ollama` 0.6.1 — `chat(think=...)` typed as `bool | "low" | "medium" | "high"` |
| MyAgent | `OLLAMA_NUM_CTX_CAP` 32768, `OLLAMA_KEEP_ALIVE=24h` from `~/.zshenv`, read timeout 300 s before this audit, 900 s after |
| Model store | 75 GB before the audit: six models, two of them grafts sharing a blob with their base |

## Method

`tests/check_ollama_models_live.py` builds a bare `OllamaMixin` host (the test
suite's `stub`, no Tk) around the real `ollama.Client` wrapped in a recorder,
and runs the REAL `_stream_ollama_call` — the same request builder, streaming
parser and `<message>` stripping a MyAgent run uses — so every number below is
what MyAgent would see. Per model:

1. **caps** — `/api/show`: capabilities, context length, baked parameters.
2. **tool** — one client-side tool (`lookup_order`) declared; call 1 must come
   back as a parsed `tool_use` with the right argument, call 2 (the result fed
   back) must answer from it. The one thing an agent loop cannot do without.
3. **think_off** — Thinking unticked must put `think: false` on the wire and
   get no thinking deltas back (omitting the flag leaves Qwen3 thinking).
4. **vision** — a 64×64 solid red square in a user message, and the same
   square hoisted out of a `screenshot` tool_result the way the desktop tools
   deliver one. One word back: "red".
5. **prefill** — a MyAgent-shaped call: ~10.5K tokens (a filler system prompt
   plus the real core + file tool catalogue, 11 tools), Thinking off, "reply
   READY" — cold, then the identical prompt again. The final chunk's
   `prompt_eval_duration` gives the prompt-processing rate, the first-chunk
   latency says whether the app's read timeout would have fired, the warm
   repeat shows what the server's KV prefix cache saves.

Every request carried the capped `num_ctx` and the environment's keep_alive
exactly as a run does, so no model was warmed small (the small-ctx trap).

## Results

Temperature 0.2 for every probe. "Call 1" is the first tool call's wall time
including the thinking shown. Prefill and generation rates are the server's
own counts (`prompt_eval_count` / `eval_count` over their durations).

| Model | On disk | `/api/show` capabilities · context | Tool round trip | Thinking off | Vision: user message · screenshot path | Call 1 | Cold prefill (first chunk) | Generation | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| `muse-glimmer:30b-mlx` | 21 GB, 32.3B, nvfp4 (MLX) | completion, vision, tools, thinking · 131072 | yes (384 chars of thinking) | yes (14.3 s) | red · red | 33.6 s | 54.4 tok/s (197.7 s) | 5.9–9.2 tok/s | keep — the default |
| `gemma3-tools:27b` (repo graft) | 17 GB, 27.4B Q4_K_M | completion, vision, tools · 131072 | yes | n/a (no thinking) | red · red | 20.6 s | 54.5 tok/s (196.7 s) | 7.7 tok/s | keep |
| `qwen3:32b-q4_K_M` | 20 GB, 32.8B Q4_K_M | completion, tools, thinking · 40960 | yes (456 chars of thinking) | yes (5.4 s) | text-only | 37.8 s | 34.8 tok/s (302.6 s) | 7.7 tok/s | keep — text tasks, slowest |
| `gemma3:27b` (stock) | same blob as the graft | completion, vision · 131072 | **no tools** — MyAgent sends none, it can only talk | n/a | red · — | 5.8 s | — | — | hidden from the picker |
| `qwen2.5vl:32b` (stock) | 21 GB, 33.5B Q4_K_M | completion, vision · 128000 | **no tools** | n/a | red · — | 13.6 s | — | — | hidden from the picker |
| `qwen2.5vl-tools:32b` (repo graft) | same blob as the stock model | completion, vision, tools · 128000 | yes | n/a | red · **"black"** | 25.7 s | 35.4 tok/s (297.2 s) | 7.1 tok/s | **retired** — hidden by name |
| `gemma4:26b-a4b-it-q4_K_M` (pulled today) | 17 GB, 26B MoE, 4B active | completion, vision, tools, thinking · 262144 | yes (226 chars of thinking) | yes (1.5 s) | red · red | 19.4 s | **285.3 tok/s (38.0 s)** | **50.0 tok/s** | **add** — verified |
| `gpt-oss:20b` (pulled today) | 14 GB, 20.9B MoE, 3.6B active, MXFP4 | completion, tools, thinking · 131072 | yes (130 chars of thinking) | **no** — `think: false` still returned 165 chars | text-only | **9.3 s** | **305.0 tok/s (32.1 s)** | 23.5 tok/s | **add** — with the level wiring below |

The warm repeat of the 10.5K-token prompt took 0.18–0.28 s on every model: an
unchanged prefix is served from the KV cache, which is what `OLLAMA_KEEP_ALIVE`
preserves between runs and why a run's later calls cost only their new tokens.

## What each model can do, and how MyAgent treats it

MyAgent sends every Ollama model the same request skeleton: the system prompt,
the history in Ollama's shape, `options.temperature` (the editor's Temp box is
always shown and always sent — Ollama has no model that refuses it),
`options.num_ctx` = min(the model's context, `OLLAMA_NUM_CTX_CAP`), and
`keep_alive` when the environment sets it. What differs per model comes from
`/api/show`'s capability list since this audit:

| Model | Thinking checkbox | `think` on the wire | Tools declared | Desktop / Browser / Physical | Notes |
|---|---|---|---|---|---|
| `muse-glimmer:30b-mlx` | shown | `true` / `false` | yes | works — reads the hoisted screenshot | Meta's "always-on local agent" model; the fastest tool parse of the dense-class models, fewer tokens per screenshot than qwen2.5vl (2026-08-14) |
| `gemma4:26b-a4b-it-q4_K_M` | shown | `true` / `false` | yes | works | 256K context (capped to 32K); thinking off answers in 1.5 s; the only model whose cold MyAgent-sized call takes under a minute |
| `gemma3-tools:27b` | hidden (no `thinking` capability) | not sent | yes | works | Gemma 3 27B behind the repo's `Gemma3-tools.Modelfile`; answers "Red." with trailing blank lines, harmless |
| `gpt-oss:20b` | shown, **with the Low / Medium / High strength combo** | `"low"` / `"medium"` / `"high"`; unticked → `"low"` plus a once-per-session notice | yes | text-only warning at run start | cannot stop thinking; 128K context (capped to 32K); the quickest tool call and prompt processing of every model here |
| `qwen3:32b-q4_K_M` | shown | `true` / `false` | yes | text-only warning at run start | 40,960 context; Qwen's model card says greedy decoding in thinking mode degrades and repeats and recommends 0.6 — the `Weather` instruction runs it thinking at 0.0 |
| `gemma3:27b`, `qwen2.5vl:32b` | hidden | not sent | **none** (no `tools` capability, a one-time warning) | sees images, cannot click | hidden from the picker; an instruction pinned to one still runs it (`GeneralChatAgent_MacOS_Ollama_gemma` pins `gemma3:27b`) with the hidden-model note |
| `qwen2.5vl-tools:32b` | hidden | not sent | yes | **misreads the screenshot path** | `OLLAMA_UNSUITABLE_PREFIXES`; bakes `temperature=0.0001` which MyAgent's option overrides anyway |

Thinking is boolean on every model here except gpt-oss, whose `think` field
takes a level: Ollama's docs say a model publishes its levels in `/api/show`
as `thinking.values` on the 0.40 line, but this 0.32.9 server sends no such
block for any model, so MyAgent reads the block where a server sends it and
falls back to `OLLAMA_THINK_LEVEL_PREFIXES` (`gpt-oss`). The thinking text is
shown in the Show Thinking pane and not replayed into the history (Ollama
models regenerate it per turn; Qwen3's template strips earlier thinking
itself).

## This morning's run, explained by the numbers

`Weather_2026-10-18_080848` (qwen3:32b, Thinking on, temp 0): 3 calls, 1,231
working seconds, 27,075 input tokens. The server log shows the calls as
`500 5m0s`, `200 4m26s`, `200 3m41s`, `500 5m0s`, `200 2m18s`, and the
transcript two lines *Ollama stream timed out — retrying in 2s*. The client's
read timeout was 300 s, and before the first chunk the whole prompt is being
processed: at qwen3's 34.8 tokens/s a ~10K-token prompt takes 290–300 s, so
two of the three calls died at exactly five minutes during prefill and were
sent again (the second attempt reusing the part of the prefix the server had
already processed). Nothing was wrong with the model or the server — the
timeout was sized for a cold load, not for a dense 32B model's prefill on a
base M4. `OLLAMA_READ_TIMEOUT` is 900 s now (env-overridable), which covers a
prompt filling the whole 32K window at that rate while a request stuck behind
a wedged instance still raises.

The practical reading: on this machine a dense 32B model costs 3–5 minutes per
call whose prefix is new (the first call of a run; any call after a history
edit), and ~8 tokens/s thereafter. Gemma 4 26B-A4B does the same call in 38 s
and generates at 50 tokens/s. muse-glimmer sits between (200 s, 6–9 tok/s).

## What changed in MyAgent

- **Capability-first detection.** `_is_ollama_thinking_model` /
  `_is_ollama_vision_model` read the cached `/api/show` capability list first
  (`_ollama_known_capabilities`; the model fetch warms the cache for every
  listed model, each stream call for its own) and fall back to the
  `OLLAMA_THINKING_PREFIXES` / `OLLAMA_VISION_PREFIXES` names only for a
  model the server has not described. The tables, written in April, knew
  nothing of Gemma 4, the Qwen 3.x vision tiers or Nemotron: a pulled Gemma 4
  showed no Thinking checkbox and got the text-only warning. The editor's
  `_model_supports_thinking`, the request builder's think gate and the Model
  upgrade probe all go through the helper now.
- **The picker offers only models that can run the loop.** `_fetch_ollama_models`
  records every listed id as served and drops a model whose capabilities lack
  `tools` or whose name `OLLAMA_UNSUITABLE_PREFIXES` lists; a failed query
  keeps its model, nothing passing offers the whole list. A pinned hidden
  model still runs (the retirement rule the cloud providers follow).
- **One request builder.** `_ollama_request_kwargs` is behind the live call
  and the Debug dump, which until now showed Ollama an Anthropic-shaped
  payload with `max_tokens` and `thinking` keys it never sees.
- **`OLLAMA_READ_TIMEOUT`** (900 s) replaces the 300 s literal in `MyAgent.py`.
- **A `length` stop posts a cut-off ⚠** — no `num_predict` is sent, so it
  means the context window filled while the model was still writing.
- **Fallback list:** `muse-glimmer:30b-mlx`, `gemma4:26b-a4b-it-q4_K_M`,
  `qwen3:32b-q4_K_M` (served only when the model fetch fails).
- **Retired from the repo:** `Qwen25VL-tools.Modelfile` (the graft above) and
  `Llama32Vision-tools.Modelfile` (mllama, which no Ollama from 0.32 on loads;
  kept as a recipe since 2026-08-14, it had no use). `Gemma3-tools.Modelfile`
  stays. Pinned in `tests/test_ollama_caps.py` (26 tests).

## Retire these from the model store (your call — nothing was deleted)

```bash
ollama rm qwen2.5vl-tools:32b qwen2.5vl:32b   # frees their shared 21 GB; both are out of the picker already
```

`gemma3:27b` shares its blob with `gemma3-tools:27b`, so removing it frees
nothing; it is out of the picker and `GeneralChatAgent_MacOS_Ollama_gemma`
still pins it — re-point that instruction at `gemma3-tools:27b` (same weights,
with tools) or `gemma4:26b-a4b-it-q4_K_M` first if you remove it.

## Candidates

The Ollama library's current tool-capable families that fit 32 GB beside a
32K context, from the 2026-10-18 catalogue (Popular sort, 255 tool-capable
models in all):

| Model | Tags | Size | Why / why not |
|---|---|---|---|
| **`gemma4:26b-a4b-it-q4_K_M`** | vision, tools, thinking, 256K | 17 GB | **Pulled and verified above** — the recommendation. The bare `gemma4:26b-a4b` tag resolves to an MLX nvfp4 build on Ollama 0.40, which should be faster still; the GGUF tag is what a 0.32.9 server runs |
| **`gpt-oss:20b`** | tools, thinking (levels), 128K, text-only | 14 GB | **Pulled and verified above** — the pick for text-only work (mail, files, coding): the quickest tool call and prompt processing of every model here |
| `qwen3.6:35b-a3b` | vision, tools, thinking, 256K | 23–24 GB | MoE, 3B active — likely as quick as Gemma 4, but 24 GB of weights plus a 32K q8 KV cache is tight on 32 GB; not pulled |
| `qwen3.8:27b` | vision, tools, thinking, 256K | 18 GB | Dense 27B — expect qwen3:32b-class prefill (minutes per cold call) unless the `-mlx` / `-nvfp4` tags on a newer Ollama change that; not pulled |
| `nemotron-3.5-lightning:30b-a3b` | tools, thinking, 1M | 19–25 GB | MoE, 3B active, text-only, built "for agents that run continuously"; its newest tags are a day old and may want a newer Ollama; not pulled |
| `devstral-small-2:24b` | vision, tools | ~14 GB | Dense coding model, no thinking; not pulled |
| `bonsai-2:27b`, `ornith:35b` | tools, thinking (+vision for bonsai) | — | Newer entries with far fewer pulls; unproven here |

The MLX point matters on this hardware: `muse-glimmer:30b-mlx` is already an
MLX nvfp4 build, and Ollama 0.40 runs every supported architecture on MLX by
default. Upgrading is the user's decision — 0.40 converts earlier downloads in
the background on first run (backups kept, downgrading needs a re-pull), and
the last engine upgrade (0.20 → 0.32, 2026-08-12) silently broke two models —
so after any upgrade, run this audit again before trusting the picker.

### gpt-oss:20b

OpenAI's 21B mixture-of-experts (3.6B active, MXFP4, 14 GB, text-only, 128K
context). On this 0.32.9 server: the tool round trip passed in 9.3 s — the
quickest of every model audited — with prompt processing at 305 tokens/s
(a 9.7K-token MyAgent-shaped prompt in 32 s) and generation at 23 tokens/s.
Two facts decided how MyAgent treats it:

- **It cannot stop thinking.** Thinking unticked sent `think: false` and 165
  characters of reasoning came back anyway.
- **It takes a level.** `think: "low"` gave 59 characters of reasoning and 43
  output tokens on a small arithmetic question; `"high"` gave 1,070 and 289 —
  same answer. The SDK types `think` as `bool | "low" | "medium" | "high"`.

So gpt-oss gets the Gemini rule for a tier that cannot stop: the editor shows
the Low / Medium / High strength combo beside the Thinking checkbox (the only
Ollama model with one), the request carries the chosen rung — a saved effort
the model lacks maps to the nearest rung (a Claude Max → high, minimal → low,
adaptive → medium) — and an unticked box sends `low`, the quietest rung, with
one Activity-log notice per model per session; the title and the cost log
keep saying `thinking=off`, the tokens tell the rest. Model Setup offers Off,
Low, Medium, High for it. On a 0.40 server the levels would come from
`/api/show`'s `thinking.values` instead of the name table.

## Open items

- Ollama upgrade 0.32.9 → 0.40.x: user's call (above).
- `Weather` on qwen3 at `temp=0` with Thinking on: Qwen's card recommends 0.6
  in thinking mode; consider 0.6, or Gemma 4 / muse-glimmer for the task.
- `GeneralChatAgent_MacOS_Ollama_gemma` pins the now-hidden `gemma3:27b`.
