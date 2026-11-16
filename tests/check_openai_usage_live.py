"""Hand-run LIVE probe of OpenAI's usage report — NOT part of the suite.

Deliberately outside the ``test*.py`` discovery pattern (like
check_kimi_models_live.py and check_voice_models_live.py): it needs
OPENAI_API_KEY, talks to api.openai.com and is BILLED — about US$0.001 for the
four calls on gpt-6-luna at 2026-11-17 rates.

It answers one question: of a prompt's tokens, how many does OpenAI report as
neither read from the cache nor written to it — the bucket MyAgent prices at
the full input rate and the cost line names ``uncached``? Four calls, each
printed as the RAW usage object beside what MyAgent's own normalizer
(``OpenAIMixin._openai_usage_dict``, write-billing decided by the model's
pricing row as in a real run) makes of it:

    A  a cold prompt over the 1,024-token cache floor   -> all written but a few
    B  the identical request again                      -> all read but a few
    C  the conversation grown by one exchange           -> read + written + a few
    D  a prompt under the cache floor                   -> nothing cached at all

    python tests/check_openai_usage_live.py [MODEL]     (default gpt-6-luna)

On 2026-11-17 the "few" was 3 on every call over the floor (3,130 input tokens
with 3,127 written; 3,130 with 3,127 read; 3,509 = 3,127 read + 379 written
+ 3) and the whole 86 under it — the totals move by a few tokens from run to
run with the nonce, the 3 does not — which is why every cost line of an OpenAI
run read ``in:3`` until that day (docs/openai-cost-line-in3-2026-11-17.md). The
cost logs show the same constant was 68 through 2026-10-08, with no change to
the request MyAgent sends: the provider moves it. Re-run this when a run's
``uncached`` stops reading 3, and when a new OpenAI tier is priced — the raw
object also shows any usage field the normalizer does not read yet
(``cache_write_tokens_details`` splits a write into ``ttl_30m_tokens`` — the
default, the rate in OPENAI_PRICING — and ``ttl_12h_tokens``, billed at twice
that, which MyAgent never asks for).

Exits 1 if the normalizer's buckets do not add up to the provider's
input_tokens on any call.
"""
import json
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import openai  # noqa: E402

from myagent.openai_mixin import OpenAIMixin  # noqa: E402

MODEL = sys.argv[1] if len(sys.argv) > 1 else "gpt-6-luna"

# A fresh nonce at the very start of the prompt: this probe's cache is cold
# whatever was probed before.
NONCE = uuid.uuid4().hex
FILLER = " ".join(f"Rule {i}: item number {i} of the reference list is stored in bin {i * 7 % 97}."
                  for i in range(1, 161))
LONG = f"Session {NONCE}. You are a terse assistant. Reference list follows. {FILLER}"
SHORT = f"Session {NONCE}. You are a terse assistant."
TOOLS = [{"type": "function", "name": "lookup_bin",
          "description": "Look up the bin an item of the reference list is stored in.",
          "parameters": {"type": "object", "properties": {"item": {"type": "integer"}},
                         "required": ["item"], "additionalProperties": False}}]
ASK = [{"role": "user", "content": "Reply with the single word OK."}]
GROWN = ASK + [{"role": "assistant", "content": "OK"},
               {"role": "user", "content": "Now reply with the single word YES. "
                + " ".join(f"Note {i}: nothing to do here." for i in range(1, 41))}]


def main():
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY is not set.")
    client = openai.OpenAI(timeout=120)
    host = OpenAIMixin.__new__(OpenAIMixin)      # no Tk, no store: only .model is read
    host.model = MODEL
    billed = host._openai_bills_cache_writes()
    print(f"model {MODEL} — cache writes billed by its pricing row: {billed}\n")
    ok = True
    for label, instructions, convo in (
            ("A  cold prompt over the cache floor", LONG, ASK),
            ("B  the identical request again", LONG, ASK),
            ("C  the conversation grown by one exchange", LONG, GROWN),
            ("D  a prompt under the cache floor", SHORT, ASK)):
        resp = client.responses.create(
            model=MODEL, instructions=instructions, input=convo, tools=TOOLS, store=False,
            reasoning={"effort": "low"}, text={"verbosity": "low"}, max_output_tokens=200)
        raw = resp.usage.model_dump()
        norm = OpenAIMixin._openai_usage_dict(resp.usage, cache_write_billed=billed)
        gross = raw["input_tokens"]
        buckets = (norm["input_tokens"] + norm["cache_read_input_tokens"]
                   + norm.get("cache_creation_input_tokens", 0))
        print(f"--- {label}  [served by {resp.model}]")
        print("  raw usage :", json.dumps(raw, sort_keys=True))
        print(f"  normalized: uncached {norm['input_tokens']:,}  "
              f"cache_write {norm.get('cache_creation_input_tokens', 0):,}  "
              f"cache_read {norm['cache_read_input_tokens']:,}  "
              f"-> {buckets:,} of {gross:,} input tokens")
        if buckets != gross:
            ok = False
            print("  *** the buckets do not add up to input_tokens ***")
        time.sleep(3)                            # let the write become readable
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
