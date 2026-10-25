"""Hand-run LIVE audit of the Moonshot (Kimi) catalog — NOT part of the suite.

Deliberately outside the ``test*.py`` discovery pattern (like
check_ollama_models_live.py and check_voice_models_live.py): it needs
MOONSHOT_API_KEY, talks to api.moonshot.ai and is BILLED — about US$0.02
for the whole ``all`` run at 2026-10-26 rates (a few hundred tokens per
call). It drives MyAgent's REAL ``_stream_kimi_call`` on a bare host (no Tk,
no store, no cost log) and prints, per request, the exact wire params and
message shapes the mixin sent and the raw usage the API returned, which is
what the 2026-10-26 audit (docs/kimi-model-audit.md) was decided on.

    python tests/check_kimi_models_live.py list
        the /v1/models catalog with every capability field the listing
        carries, and what MyAgent makes of it (picker / served / knob /
        vision / caps)
    python tests/check_kimi_models_live.py roundtrip MODEL [EFFORT] [on|off]
        call 1 with one tool declared -> the tool call -> call 2 with the
        result: the reasoning_content round trip on the wire, and the cache
        write / hit buckets of call 2
    python tests/check_kimi_models_live.py effort MODEL EFFORT [EFFORT ...]
        one short question per rung, no tools: reasoning tokens per effort
        ("none" is the measured off switch the docs do not list)
    python tests/check_kimi_models_live.py temp MODEL
        a plain request carrying temperature=0.3: HTTP 400 "only 1 is
        allowed" on every current model
    python tests/check_kimi_models_live.py all
        list, a round trip on every picker model at low, the none / low
        rungs on the K3 line, and the temperature probe on the default

Re-run it when Moonshot announces a model, a retirement or a price (the
price table lives in platform.kimi.ai/docs/pricing/chat — rendered
client-side, so read the page's React payload, not its .md).
"""
import json
import os
import queue
import sys
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import openai  # noqa: E402

from myagent.constants import KIMI_DEFAULT_BASE_URL, KIMI_DEFAULT_MODEL  # noqa: E402
from myagent.kimi_mixin import KimiMixin  # noqa: E402
from myagent.streaming_mixin import StreamingMixin  # noqa: E402

KEY = os.environ.get("MOONSHOT_API_KEY") or os.environ.get("KIMI_API_KEY")

SYSTEM = ("You are a terse assistant. When asked about the weather you MUST call "
          "the get_weather tool, then answer in one short sentence.")
TOOLS = [{"name": "get_weather", "description": "Current weather for a city.",
          "input_schema": {"type": "object",
                           "properties": {"city": {"type": "string"}},
                           "required": ["city"]}}]


class Recorder:
    """Wraps the real client's chat.completions.create: records the kwargs of
    every request and the raw usage / served model / finish_reason of the
    stream, while the mixin consumes the chunks unchanged."""

    def __init__(self, real):
        self.real = real
        self.calls = []
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self.create))
        self.models = real.models

    def create(self, **kwargs):
        rec = {"kwargs": kwargs, "usage": None, "chunks": 0, "model": None,
               "finish": None, "started": time.time(), "first": None, "ended": None}
        self.calls.append(rec)
        stream = self.real.chat.completions.create(**kwargs)

        def gen():
            try:
                for chunk in stream:
                    rec["chunks"] += 1
                    if rec["first"] is None:
                        rec["first"] = time.time()
                    if getattr(chunk, "model", None):
                        rec["model"] = chunk.model
                    u = getattr(chunk, "usage", None)
                    if u:
                        rec["usage"] = (u.model_dump(exclude_none=True)
                                        if hasattr(u, "model_dump") else str(u))
                    for c in (getattr(chunk, "choices", None) or []):
                        if getattr(c, "finish_reason", None):
                            rec["finish"] = c.finish_reason
                    yield chunk
            finally:
                rec["ended"] = time.time()
                try:
                    stream.close()
                except Exception:
                    pass
        return gen()


class Host(StreamingMixin, KimiMixin):
    """_stream_kimi_call (KimiMixin) over the static _get_pricing
    (StreamingMixin), as the App composes them."""


def make_host(model, effort="high", enabled=True, caps=None):
    h = Host.__new__(Host)
    h.provider = "Moonshot"
    h.model = model
    h.thinking_enabled = enabled
    h.thinking_effort = effort
    h.thinking_mode = effort
    h.temperature = 1.0
    h.text_verbosity = "medium"
    h.stop_requested = False
    h.queue = queue.Queue()
    h._kimi_caps = caps or {}
    h.kimi_client = Recorder(openai.OpenAI(api_key=KEY, base_url=KIMI_DEFAULT_BASE_URL,
                                           timeout=600))
    h._build_system_prompt = lambda: SYSTEM
    h._get_tools = lambda: TOOLS
    return h


def notices(h):
    out = []
    while True:
        try:
            m = h.queue.get_nowait()
        except queue.Empty:
            return out
        if m.get("type") in ("tool_info", "warning"):
            out.append(m.get("content", "").strip())


def describe_request(rec):
    kw = rec["kwargs"]
    wire = {k: v for k, v in kw.items() if k not in ("messages", "tools")}
    shapes = []
    for m in kw["messages"]:
        s = m["role"]
        if m["role"] == "assistant":
            rc = m.get("reasoning_content")
            s += (f"(reasoning_content={len(rc) if rc else 0} chars, "
                  f"tool_calls={len(m.get('tool_calls') or [])}, "
                  f"content={len(m.get('content') or '')})")
        elif m["role"] == "tool":
            s += f"(tool_call_id={m.get('tool_call_id')})"
        shapes.append(s)
    return wire, shapes


def report_call(label, h, rec, stop, blocks, text, had_thinking, usage):
    wire, shapes = describe_request(rec)
    print(f"--- {label} ---")
    print("  request params :", json.dumps(wire, sort_keys=True))
    print("  request msgs   :", " | ".join(shapes))
    print("  served model   :", rec["model"], " finish:", rec["finish"],
          " chunks:", rec["chunks"],
          " ttfb: %.1fs" % ((rec["first"] or rec["ended"]) - rec["started"]),
          " total: %.1fs" % (rec["ended"] - rec["started"]))
    print("  raw usage      :", json.dumps(rec["usage"], sort_keys=True))
    print("  usage dict     :", json.dumps(usage, sort_keys=True))
    th = [b for b in blocks if b.get("type") == "thinking"]
    tu = [b for b in blocks if b.get("type") == "tool_use"]
    print("  stop:", stop, " had_thinking:", had_thinking,
          " thinking chars:", sum(len(b["thinking"]) for b in th),
          " tool calls:", [(b["name"], b["id"], b["input"]) for b in tu])
    print("  text           :", repr((text or "")[:200]))
    n = notices(h)
    if n:
        print("  NOTICES        :", n)


def list_models():
    """The raw catalog and MyAgent's reading of it."""
    import httpx
    r = httpx.get(KIMI_DEFAULT_BASE_URL + "/models",
                  headers={"Authorization": "Bearer " + KEY}, timeout=60)
    print("HTTP", r.status_code)
    for m in sorted(r.json().get("data", []), key=lambda d: d.get("id", "")):
        keep = {k: v for k, v in m.items()
                if k not in ("permission", "object", "owned_by", "parent", "root", "created")}
        print(json.dumps(keep, sort_keys=True))
    h = make_host(KIMI_DEFAULT_MODEL)
    h.kimi_client = openai.OpenAI(api_key=KEY, base_url=KIMI_DEFAULT_BASE_URL, timeout=60)
    picker = h._fetch_kimi_models()
    served = sorted(h._served_model_ids.get("Moonshot") or ())
    print("\nMyAgent picker :", picker, " default:", KIMI_DEFAULT_MODEL)
    print("MyAgent served :", served, " (hidden:", sorted(set(served) - set(picker)), ")")
    for mid in served:
        print(f"  {mid:28s} knob={h._kimi_reasoning_values(mid)} "
              f"toggle={h._kimi_thinking_toggleable(mid)} vision={h._is_kimi_vision_model(mid)} "
              f"caps={h._kimi_caps.get(mid)}")
    return picker, h._kimi_caps


def roundtrip(model, effort="low", enabled=True, caps=None):
    h = make_host(model, effort, enabled, caps)
    msgs = [{"role": "user", "content": "What is the weather in Sydney right now? Use the tool."}]
    stop, blocks, text, had, _label, usage = h._stream_kimi_call(msgs, 3, False)
    report_call(f"{model} effort={effort} enabled={enabled} CALL 1", h,
                h.kimi_client.calls[-1], stop, blocks, text, had, usage)
    if stop != "tool_use":
        print("  !! no tool call on call 1 — round trip not exercised")
        return
    tu = [b for b in blocks if b.get("type") == "tool_use"]
    msgs.append({"role": "assistant", "content": blocks})
    msgs.append({"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": b["id"],
         "content": f"Sunny, 22C in {b['input'].get('city', '?')}"} for b in tu]})
    stop2, blocks2, text2, had2, _l2, usage2 = h._stream_kimi_call(msgs, 3, False)
    report_call(f"{model} CALL 2 (tool result + round trip)", h,
                h.kimi_client.calls[-1], stop2, blocks2, text2, had2, usage2)


def effort_probe(model, efforts, caps=None):
    for e in efforts:
        h = make_host(model, e, e != "none", caps)
        msgs = [{"role": "user", "content": "In one word: what colour is a clear daytime sky?"}]
        try:
            stop, blocks, text, had, _label, usage = h._stream_kimi_call(msgs, 2, False)
            report_call(f"{model} effort={e}", h, h.kimi_client.calls[-1],
                        stop, blocks, text, had, usage)
        except Exception as ex:
            print(f"--- {model} effort={e} --- ERROR: {type(ex).__name__}: {str(ex)[:300]}")


def temp_probe(model):
    client = openai.OpenAI(api_key=KEY, base_url=KIMI_DEFAULT_BASE_URL, timeout=120)
    try:
        r = client.chat.completions.create(
            model=model, temperature=0.3, max_completion_tokens=64,
            messages=[{"role": "user", "content": "Say OK."}])
        print(f"--- {model} temperature=0.3: HTTP 200 (!), served {r.model}")
    except Exception as ex:
        print(f"--- {model} temperature=0.3: {type(ex).__name__}: {str(ex)[:300]}")


def main(argv):
    if not KEY:
        print("MOONSHOT_API_KEY (or KIMI_API_KEY) is not set")
        return 2
    cmd = argv[1] if len(argv) > 1 else "list"
    if cmd == "list":
        list_models()
    elif cmd == "roundtrip":
        model = argv[2]
        effort = argv[3] if len(argv) > 3 else "low"
        enabled = (argv[4] != "off") if len(argv) > 4 else True
        roundtrip(model, effort, enabled)
    elif cmd == "effort":
        effort_probe(argv[2], argv[3:])
    elif cmd == "temp":
        temp_probe(argv[2])
    elif cmd == "all":
        picker, caps = list_models()
        for mid in picker:
            print(f"\n########## roundtrip {mid}")
            roundtrip(mid, "low", True, caps)
        for mid in picker:
            if h_knob(mid, caps):
                print(f"\n########## none / low rungs on {mid}")
                effort_probe(mid, ["none", "low"], caps)
        print(f"\n########## temperature on {KIMI_DEFAULT_MODEL}")
        temp_probe(KIMI_DEFAULT_MODEL)
    else:
        print(__doc__)
        return 2
    return 0


def h_knob(mid, caps):
    return bool(make_host(mid, caps=caps)._kimi_reasoning_values(mid))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
