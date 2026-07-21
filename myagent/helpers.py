import json
import re
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path


def estimate_content_tokens(content):
    """Rough token estimate for one message's content: ~chars/4, images ~1600 flat.

    Used only to size the history cut on a context-overflow 400 — never for
    billing — so a coarse heuristic is fine (and it deliberately errs toward
    over-counting base64 images, the safe direction for trimming). Ported from
    SelfBot's `_estimate_content_tokens` (same semantics)."""
    if isinstance(content, str):
        return len(content) // 4
    if not isinstance(content, list):
        return len(str(content)) // 4
    total = 0
    for block in content:
        if isinstance(block, dict):
            if block.get("type") == "image":
                total += 1600  # ~1MP image ≈ 1.15–1.6K tokens regardless of base64 length
            else:
                try:
                    total += len(json.dumps(block, default=str)) // 4
                except (TypeError, ValueError):
                    total += len(str(block)) // 4
        else:
            # Anthropic SDK block objects (assistant tool_use/text) — repr is
            # roughly proportional to content size.
            total += len(str(block)) // 4
    return total


def parse_overflow_counts(msg):
    """Extract (reported_tokens, reported_max) from a context-overflow 400
    message like 'prompt is too long: 1,597,842 tokens > 1,000,000 maximum'.
    Returns (None, None) when the counts don't parse."""
    mt = re.search(r"(\d[\d,]*)\s*tokens\s*>\s*(\d[\d,]*)", msg or "")
    if not mt:
        return None, None
    return (int(mt.group(1).replace(",", "")),
            int(mt.group(2).replace(",", "")))


def trim_history_for_context(messages, reported_tokens=None, reported_max=None):
    """Drop the oldest conversation rounds IN PLACE so `messages` fits the
    context window after a 400 'prompt is too long'.

    Cuts ONLY at genuine user-turn boundaries — a role=='user' message counts
    as a turn start unless its content carries a tool_result — so a
    tool_use/tool_result pair is never orphaned (itself a 400) and the first
    kept message is always a real user turn. Sizes the cut proportionally when
    the reported <T>/<M> counts are known (target 0.75*M, leaving headroom for
    system prompt, tools, and output), else drops the oldest half. Always keeps
    at least the last two rounds. Returns the number of messages removed
    (0 = nothing safe to drop — even the recent context alone is over budget).
    Ported from SelfBot's `_trim_history_for_context` (same semantics)."""
    def is_turn_start(m):
        if m.get("role") != "user":
            return False
        c = m.get("content")
        if isinstance(c, str):
            return True
        if isinstance(c, list):
            for b in c:
                btype = b.get("type") if isinstance(b, dict) else getattr(b, "type", None)
                return btype != "tool_result"
            return True  # empty list — treat as a turn start
        return True

    starts = [i for i, m in enumerate(messages) if is_turn_start(m)]
    if len(starts) <= 2:
        return 0  # nothing safe to drop — keep the current exchange intact
    max_cut = starts[-2]  # never cut past this: preserve the last two rounds
    if reported_tokens and reported_max and reported_tokens > reported_max:
        target = int(reported_max * 0.75)
        need_remove = reported_tokens - target
        cut_at, removed = 0, 0
        for s in starts[1:]:
            if s > max_cut:
                break
            removed += sum(estimate_content_tokens(messages[i].get("content"))
                           for i in range(cut_at, s))
            cut_at = s
            if removed >= need_remove:
                break
    else:
        cut_at = min(starts[len(starts) // 2], max_cut)  # unknown size — drop the oldest half
    if cut_at <= 0:
        cut_at = min(starts[1], max_cut)  # guarantee at least one round is dropped
    del messages[:cut_at]
    return cut_at


def rotate_log_if_needed(log_path, max_bytes):
    """One-slot size-cap rotation for the append-only runtime logs
    (heartbeat.log, APICostLog.txt): past max_bytes the log is atomically
    renamed to <name>.old — replacing the previous archive — and restarts
    with a timestamped marker line. Best-effort: any OSError (no log yet,
    or the archive locked open) skips rotation until the next call, so
    housekeeping can never break the caller. Returns True on rotation."""
    path = Path(log_path)
    try:
        if path.stat().st_size <= max_bytes:
            return False
        path.replace(path.with_name(path.name + ".old"))
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} log restarted "
                    f"(previous log archived to {path.name}.old)\n")
    except OSError:
        return False
    return True


class HTMLTextExtractor(HTMLParser):
    """Strip HTML tags and return plain text."""

    def __init__(self):
        super().__init__()
        self._text = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self._skip = False
        if tag in ("p", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr"):
            self._text.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self._text.append(data)

    def get_text(self):
        return "".join(self._text).strip()


def extract_text_from_html(html):
    extractor = HTMLTextExtractor()
    extractor.feed(html)
    return extractor.get_text()


class _ToolBlock:
    """Thin wrapper so OpenAI/Gemini dict-based tool blocks expose the same
    .name, .id, .input attribute interface as Anthropic's Pydantic objects."""

    def __init__(self, name, id, input):
        self.name = name
        self.id = id
        self.input = input
        self.type = "tool_use"
