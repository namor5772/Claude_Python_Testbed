"""Hand-run LIVE audit of the Ollama models MyAgent can use on THIS machine.
NOT a unit test: it loads every installed model in turn (each load evicts the
previous one on a 32 GB Mac), takes 20-60 minutes, and the optional `--pull`
stage downloads candidate models — which is why its name keeps it out of the
`test*.py` discovery pattern, like check_voice_models_live.py.

    python tests/check_ollama_models_live.py run                 # every installed model
    python tests/check_ollama_models_live.py run --prefill       # + the MyAgent-shaped prompt timing
    python tests/check_ollama_models_live.py run --models qwen3:32b-q4_K_M
    python tests/check_ollama_models_live.py run --pull gemma4:26b-a4b gpt-oss:20b
    python tests/check_ollama_models_live.py report              # print the saved results (free)

Results go to <tempdir>/myagent_ollama_audit/results.json (override with
--dir), saved after every model, so a crash loses nothing. The first run of
this audit, 2026-10-18 on the M4 Mac Mini, is written up in
docs/ollama-model-audit.md.

What it measures, and why each stage exists:
* `caps` — what /api/show says (capabilities, context length, baked
  parameters): the facts MyAgent's capability detection reads.
* `tool` — a client-side tool round trip through the REAL
  OllamaMixin._stream_ollama_call on a bare host: call 1 must come back as a
  parsed tool_use with the right arguments, call 2 (the tool's result fed
  back) must answer from it. This is the one thing an agent loop cannot do
  without; a model that fails it is not an agent model whatever its tags say.
* `think_off` — on a thinking-capable model, Thinking OFF must send
  `think: false` and get NO thinking deltas back (omitting the flag leaves
  Qwen3 thinking at its training default).
* `vision` — a 64x64 solid red square in a user message; `vision_tool` the
  same square hoisted out of a screenshot tool_result the way the desktop
  tools deliver it. One word back: "red".
* `prefill` (--prefill) — a MyAgent-shaped call: an ~8K-token system prompt
  plus the real core + file tool catalogue, Thinking off, "reply READY". The
  final chunk's prompt_eval_duration gives the prompt-processing rate, the
  first-chunk latency says whether the app's read timeout would have fired,
  and a second identical call shows what the server's KV prefix cache saves.
* `think_levels` (gpt-oss only) — the `think` flag as "low" / "high" straight
  on the client: does this server accept string levels, and do they change
  the amount of reasoning?

Every request goes through the app's own path, so num_ctx is the capped
value the app sends (the small-ctx warm-trap rule) and keep_alive follows
OLLAMA_KEEP_ALIVE exactly as it does for a real run.
"""

import argparse
import base64
import io
import json
import os
import random
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from myagent.ollama_mixin import OllamaMixin  # noqa: E402
from myagent.constants import TOOLS, FILE_TOOLS, DESKTOP_TOOLS  # noqa: E402
from tests._util import stub  # noqa: E402

LOOKUP_TOOL = {
    "name": "lookup_order",
    "description": "Look up a customer order by its id and return its status.",
    "input_schema": {
        "type": "object",
        "properties": {"order_id": {"type": "string", "description": "The order id."}},
        "required": ["order_id"],
    },
}
LOOKUP_RESULT = '{"order_id": "4471", "status": "shipped", "eta": "Tuesday"}'
TOOL_QUESTION = ("Use the lookup_order tool to look up order 4471, then tell me "
                 "its status and when it arrives in one short sentence.")
SYSTEM = ("You are MyAgent, an autonomous assistant. Use the tools you are "
          "given when they apply; otherwise answer briefly.")
VISION_QUESTION = "What colour is this square? Reply with one word."

SCREENSHOT_TOOL = next(t for t in DESKTOP_TOOLS if t["name"] == "screenshot")


class Q:
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)


class Recorder:
    """Wraps ollama.Client: every chat() is streamed through and its request
    shape, first-chunk latency and final-chunk timings are kept."""

    def __init__(self, client):
        self._c = client
        self.calls = []

    def chat(self, **kw):
        t0 = time.monotonic()
        first = None
        last = None
        for chunk in self._c.chat(**kw):
            if first is None:
                first = time.monotonic() - t0
            last = chunk
            yield chunk
        rec = {
            "think": kw.get("think", "absent"),
            "tools": len(kw.get("tools") or []),
            "num_ctx": (kw.get("options") or {}).get("num_ctx"),
            "temperature": (kw.get("options") or {}).get("temperature"),
            "keep_alive": kw.get("keep_alive", "absent"),
            "images": sum(len(m.get("images") or []) for m in kw.get("messages", [])),
            "first_chunk_s": round(first, 2) if first is not None else None,
            "total_s": round(time.monotonic() - t0, 2),
        }
        for f in ("prompt_eval_count", "prompt_eval_duration", "eval_count",
                  "eval_duration", "load_duration", "done_reason"):
            v = getattr(last, f, None) if last is not None else None
            if v is None and isinstance(last, dict):
                v = last.get(f)
            rec[f] = v
        for f in ("prompt_eval_duration", "eval_duration", "load_duration"):
            if rec.get(f):
                rec[f] = round(rec[f] / 1e9, 2)   # ns -> s
        if rec.get("prompt_eval_count") and rec.get("prompt_eval_duration"):
            rec["prefill_tps"] = round(rec["prompt_eval_count"] / rec["prompt_eval_duration"], 1)
        if rec.get("eval_count") and rec.get("eval_duration"):
            rec["gen_tps"] = round(rec["eval_count"] / rec["eval_duration"], 1)
        self.calls.append(rec)

    def __getattr__(self, name):
        return getattr(self._c, name)


def make_client():
    import httpx
    import ollama
    host = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    # 900 s read timeout: a dense 30B model on a base M4 takes minutes to
    # process a 9K-token prompt, and the point is to MEASURE that, not to
    # trip over the app's 300 s (see the prefill stage).
    return Recorder(ollama.Client(host=host, timeout=httpx.Timeout(900.0, connect=10.0)))


def make_host(model, client, tools, thinking, system=SYSTEM, temperature=0.2):
    h = stub(OllamaMixin, model=model, ollama_client=client, queue=Q(),
             temperature=temperature, thinking_enabled=thinking,
             stop_requested=False)
    h._build_system_prompt = lambda: system
    h._get_tools = lambda: list(tools)
    return h


def red_square_b64(size=64):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (size, size), (220, 20, 20)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def image_block(b64):
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}}


def queue_summary(q):
    kinds = {}
    for it in q.items:
        kinds[it.get("type")] = kinds.get(it.get("type"), 0) + 1
    warnings = [it.get("content", "").strip() for it in q.items
                if it.get("type") in ("warning", "tool_info")]
    thinking = "".join(it.get("content", "") for it in q.items if it.get("type") == "thinking_delta")
    return kinds, warnings, thinking


def call(host, messages, label):
    """One _stream_ollama_call; returns a dict describing what came back."""
    host.queue.items.clear()
    t0 = time.monotonic()
    err = None
    stop = blocks = text = None
    try:
        stop, blocks, text, had_thinking, _, usage = host._stream_ollama_call(messages, 3, False)
    except Exception as e:  # recorded, never fatal to the audit
        err = f"{type(e).__name__}: {e}"
        had_thinking = False
        usage = None
    kinds, warnings, thinking = queue_summary(host.queue)
    rec = {
        "label": label,
        "error": err,
        "stop_reason": stop,
        "text": (text or "")[:400],
        "tool_calls": [{"name": b["name"], "input": b["input"]} for b in (blocks or [])
                       if b.get("type") == "tool_use"],
        "had_thinking": bool(had_thinking),
        "thinking_chars": len(thinking),
        "usage": usage,
        "warnings": warnings,
        "wall_s": round(time.monotonic() - t0, 1),
        "wire": host.ollama_client.calls[-1] if host.ollama_client.calls else None,
    }
    return rec, blocks


def stage_tool(model, client, thinking):
    h = make_host(model, client, [LOOKUP_TOOL], thinking)
    messages = [{"role": "user", "content": TOOL_QUESTION}]
    r1, blocks = call(h, messages, "tool call 1")
    out = {"call1": r1}
    tc = [b for b in (blocks or []) if b.get("type") == "tool_use"]
    out["tool_called"] = bool(tc and tc[0]["name"] == "lookup_order")
    out["args_ok"] = bool(tc and "4471" in json.dumps(tc[0].get("input", {})))
    if tc:
        messages = messages + [
            {"role": "assistant", "content": blocks},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tc[0]["id"],
                                          "content": LOOKUP_RESULT}]},
        ]
        r2, _ = call(h, messages, "tool call 2 (result fed back)")
        out["call2"] = r2
        low = (r2.get("text") or "").lower()
        out["answered_from_result"] = ("shipped" in low or "tuesday" in low) and not r2["error"]
    else:
        out["answered_from_result"] = False
    return out


def stage_vision(model, client, b64, via_tool):
    if via_tool:
        h = make_host(model, client, [SCREENSHOT_TOOL], False)
        messages = [
            {"role": "user", "content": "Take a screenshot, then tell me what colour the screen is in one word."},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "ollama_screenshot_0",
                                               "name": "screenshot", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "ollama_screenshot_0",
                                          "content": [{"type": "text", "text": "Screenshot captured (64x64 pixels)."},
                                                      image_block(b64)]}]},
        ]
        label = "vision via screenshot tool_result"
    else:
        h = make_host(model, client, [], False)
        messages = [{"role": "user", "content": [{"type": "text", "text": VISION_QUESTION},
                                                 image_block(b64)]}]
        label = "vision in a user message"
    r, _ = call(h, messages, label)
    r["saw_red"] = ("red" in (r.get("text") or "").lower()) and not r["error"]
    return r


def filler_prompt(target_words=6000):
    rnd = random.Random()
    nonce = rnd.randrange(10**9)
    para = ("Operating notes for this workstation (reference {n}): every file the "
            "agent writes goes under the project folder, never the home directory; "
            "commands run in a fresh shell, so a working-directory change does not "
            "persist from one command to the next; prefer the file tools over shell "
            "edits; read a file before editing it; when a command fails, quote the "
            "error and try one alternative before asking; keep replies short and "
            "state what was done. ")
    words = []
    i = 0
    while len(words) < target_words:
        words.extend(para.format(n=f"{nonce}-{i}").split())
        i += 1
    return SYSTEM + "\n\n" + " ".join(words)


def stage_prefill(model, client):
    system = filler_prompt()
    tools = list(TOOLS) + list(FILE_TOOLS)
    h = make_host(model, client, tools, False, system=system)
    messages = [{"role": "user", "content": "Reply with the single word READY."}]
    cold, _ = call(h, messages, "prefill cold (unique ~8K prompt + 11 tools)")
    warm, _ = call(h, messages, "prefill warm (same prompt again)")
    return {"cold": cold, "warm": warm}


def stage_think_levels(model, client):
    """gpt-oss: `think` as a level string, straight on the client."""
    out = {}
    for level in ("low", "high"):
        t0 = time.monotonic()
        try:
            resp = client._c.chat(model=model, think=level, stream=False,
                                  options={"num_ctx": 32768},
                                  messages=[{"role": "user", "content":
                                             "How many prime numbers are there between 10 and 50? Answer with the number."}])
            msg = resp.message if hasattr(resp, "message") else resp["message"]
            thinking = getattr(msg, "thinking", None) or (msg.get("thinking") if isinstance(msg, dict) else "")
            content = getattr(msg, "content", None) or (msg.get("content") if isinstance(msg, dict) else "")
            out[level] = {"accepted": True, "thinking_chars": len(thinking or ""),
                          "answer": (content or "")[:120],
                          "eval_count": getattr(resp, "eval_count", None),
                          "wall_s": round(time.monotonic() - t0, 1)}
        except Exception as e:
            out[level] = {"accepted": False, "error": f"{type(e).__name__}: {e}"[:300]}
    return out


def audit_model(model, client, b64, prefill):
    print(f"\n=== {model} ===", flush=True)
    rec = {"model": model, "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    h = make_host(model, client, [], False)
    caps = h._get_ollama_model_caps()
    rec["caps"] = {"capabilities": sorted(caps["capabilities"]), "context_length": caps["context_length"]}
    try:
        show = client._c.show(model)
        rec["caps"]["parameters"] = getattr(show, "parameters", None)
        details = getattr(show, "details", None)
        rec["caps"]["details"] = {k: getattr(details, k, None) for k in
                                  ("family", "parameter_size", "quantization_level", "format")} if details else None
    except Exception as e:
        rec["caps"]["show_error"] = str(e)[:200]
    print("  caps:", rec["caps"]["capabilities"], "ctx", rec["caps"]["context_length"], flush=True)

    has_tools = "tools" in caps["capabilities"]
    has_think = "thinking" in caps["capabilities"] and h._is_ollama_thinking_model()
    has_vision = "vision" in caps["capabilities"]

    rec["tool"] = stage_tool(model, client, thinking=has_think)
    t = rec["tool"]
    print(f"  tool round trip: called={t['tool_called']} args_ok={t['args_ok']} "
          f"answered={t['answered_from_result']} "
          f"(call1 {t['call1']['wall_s']}s, thinking {t['call1']['thinking_chars']} chars)", flush=True)
    if t["call1"]["error"]:
        print("    error:", t["call1"]["error"][:200], flush=True)
    if not has_tools:
        print("    (no tools capability — the app sends none; warnings:", t["call1"]["warnings"][:1], ")", flush=True)

    if has_think:
        h2 = make_host(model, client, [LOOKUP_TOOL], False)
        r, _ = call(h2, [{"role": "user", "content": TOOL_QUESTION}], "thinking off")
        rec["think_off"] = r
        rec["think_off"]["clean"] = (r["thinking_chars"] == 0 and r["wire"]
                                     and r["wire"]["think"] is False and not r["error"])
        print(f"  thinking off: think={r['wire'] and r['wire']['think']} deltas={r['thinking_chars']} "
              f"clean={rec['think_off']['clean']} ({r['wall_s']}s)", flush=True)

    if has_vision:
        rec["vision"] = stage_vision(model, client, b64, via_tool=False)
        print(f"  vision (user message): saw_red={rec['vision']['saw_red']} text={rec['vision']['text'][:60]!r}", flush=True)
        if has_tools:
            rec["vision_tool"] = stage_vision(model, client, b64, via_tool=True)
            print(f"  vision (screenshot tool_result): saw_red={rec['vision_tool']['saw_red']} "
                  f"text={rec['vision_tool']['text'][:60]!r}", flush=True)

    if prefill and has_tools:
        rec["prefill"] = stage_prefill(model, client)
        for k in ("cold", "warm"):
            w = rec["prefill"][k]["wire"] or {}
            print(f"  prefill {k}: prompt={w.get('prompt_eval_count')} tok in {w.get('prompt_eval_duration')}s "
                  f"({w.get('prefill_tps')} tok/s), first chunk {w.get('first_chunk_s')}s, "
                  f"gen {w.get('gen_tps')} tok/s", flush=True)

    if model.startswith("gpt-oss"):
        rec["think_levels"] = stage_think_levels(model, client)
        print("  think levels:", {k: (v.get("thinking_chars"), v.get("accepted")) for k, v in rec["think_levels"].items()}, flush=True)

    rec["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return rec


def installed_models(client):
    resp = client._c.list()
    models = getattr(resp, "models", None) or resp.get("models", [])
    names = []
    for m in models:
        names.append(getattr(m, "model", None) or (m.get("model") if isinstance(m, dict) else None))
    return [n for n in names if n]


def cmd_run(args):
    client = make_client()
    out_dir = args.dir
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "results.json")
    results = {}
    if os.path.exists(path) and not args.fresh:
        results = json.load(open(path, encoding="utf-8"))
    for name in args.pull or []:
        print(f"pulling {name} ...", flush=True)
        t0 = time.monotonic()
        try:
            for prog in client._c.pull(name, stream=True):
                status = getattr(prog, "status", None) or prog.get("status")
                if status and status.startswith(("success", "verifying", "writing")):
                    print("  ", status, flush=True)
            print(f"  pulled in {time.monotonic() - t0:.0f}s", flush=True)
        except Exception as e:
            print(f"  PULL FAILED: {e}", flush=True)
            results[name] = {"model": name, "pull_error": str(e)[:300]}
    models = args.models or installed_models(client)
    if args.pull:
        models = list(models) + [n for n in args.pull if n not in models and n not in results]
    b64 = red_square_b64()
    for model in models:
        if model in results and not args.fresh and results[model].get("finished"):
            print(f"skipping {model} (done; --fresh to redo)")
            continue
        try:
            results[model] = audit_model(model, client, b64, args.prefill)
        except Exception as e:
            results[model] = {"model": model, "fatal": f"{type(e).__name__}: {e}"[:400]}
            print("  FATAL:", results[model]["fatal"], flush=True)
        json.dump(results, open(path, "w", encoding="utf-8"), indent=1, default=str)
    print(f"\nsaved {path}")
    cmd_report(args)


def yn(v):
    return "-" if v is None else ("yes" if v else "NO")


def cmd_report(args):
    path = os.path.join(args.dir, "results.json")
    results = json.load(open(path, encoding="utf-8"))
    print("\nMODEL                      CAPS                      TOOL-RT  ARGS  ANSWER  THINK-OFF  VISION  VIS-TOOL  CALL1s  PREFILL tok/s (cold first-chunk s)")
    for model, r in results.items():
        if r.get("fatal") or r.get("pull_error"):
            print(f"{model:26} FAILED: {r.get('fatal') or r.get('pull_error')}")
            continue
        caps = ",".join(c[0].upper() + c[1:4] for c in r["caps"]["capabilities"])
        t = r.get("tool", {})
        to = r.get("think_off", {}).get("clean") if "think_off" in r else None
        vi = r.get("vision", {}).get("saw_red") if "vision" in r else None
        vt = r.get("vision_tool", {}).get("saw_red") if "vision_tool" in r else None
        pf = r.get("prefill", {}).get("cold", {}).get("wire") or {}
        pfs = f"{pf.get('prefill_tps')} ({pf.get('first_chunk_s')})" if pf else "-"
        print(f"{model:26} {caps:25} {yn(t.get('tool_called')):8} {yn(t.get('args_ok')):5} "
              f"{yn(t.get('answered_from_result')):7} {yn(to):10} {yn(vi):7} {yn(vt):9} "
              f"{str(t.get('call1', {}).get('wall_s', '-')):7} {pfs}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["run", "report"])
    ap.add_argument("--dir", default=os.path.join(tempfile.gettempdir(), "myagent_ollama_audit"))
    ap.add_argument("--models", nargs="*", help="audit these ids only (default: every installed model)")
    ap.add_argument("--pull", nargs="*", help="pull these ids first, then audit them too")
    ap.add_argument("--prefill", action="store_true", help="also time the MyAgent-shaped ~8K prompt")
    ap.add_argument("--fresh", action="store_true", help="redo models already in results.json")
    args = ap.parse_args()
    if args.command == "run":
        cmd_run(args)
    else:
        cmd_report(args)


if __name__ == "__main__":
    main()
