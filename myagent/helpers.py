import json
import os
import re
import time
from contextlib import contextmanager
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


def _block_type(block):
    """`type` of a content block that may be a dict (tool_result / translated
    providers) or an Anthropic SDK block object (assistant turns, appended
    verbatim from final_message.content)."""
    return block.get("type") if isinstance(block, dict) else getattr(block, "type", None)


def _block_field(block, name):
    return block.get(name) if isinstance(block, dict) else getattr(block, name, None)


def strip_thinking_blocks(messages):
    """Remove every thinking / redacted_thinking block from the assistant turns
    of `messages` IN PLACE (their text and tool_use blocks stay). Returns the
    number of blocks removed.

    The no-beta recovery for Claude Fable 5.1's preserved-thinking check: a 5.1
    thinking block's signature is bound to the conversation prefix that produced
    it, so replaying one after that prefix changed is a 400 ("Invalid `signature`
    in `thinking` block ... bound to a different conversation") on enforced
    accounts. Stripping the blocks and retrying once lets the turn proceed
    without the reasoning they carried — a one-time recovery, not a steady
    state (MyAgent's steady state is `block_binding: drop_block` under the
    thinking-binding-controls beta, which has the API drop them instead). An
    assistant turn that would end up EMPTY is left alone: an empty content list
    is itself a 400, and the retry loop's one-shot guard then surfaces the
    original error rather than looping."""
    removed = 0
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        kept = [b for b in content
                if _block_type(b) not in ("thinking", "redacted_thinking")]
        if kept and len(kept) != len(content):
            removed += len(content) - len(kept)
            msg["content"] = kept
    return removed


def strip_pre_fallback_blocks(blocks):
    """Content blocks to echo back from a response that switched models via a
    server-side refusal fallback (a `fallback` block marks each switch point).

    Everything the declined model produced BEFORE the last fallback block is
    not part of the served response: its thinking / redacted_thinking and
    tool_use blocks must not be echoed (the fallback model never saw them and
    their ids would have no tool_result — the API's own echo rule), so only
    text blocks and PAIRED server-tool blocks (a server_tool_use with its
    *_tool_result before the boundary) survive from that partial; everything
    after the boundary is the serving model's own turn and echoes normally. The
    fallback markers are dropped too — the API treats them as ignorable audit
    markers, and anthropic 0.84.0 parses the unknown block type into a
    text-block shell it cannot re-serialize. Because the pre-boundary tool_use
    blocks are gone, stream_worker's tool dispatch naturally executes only the
    serving model's calls. A response with no fallback block is returned
    unchanged (same list object)."""
    last = -1
    for i, block in enumerate(blocks):
        if _block_type(block) == "fallback":
            last = i
    if last < 0:
        return blocks
    pre = blocks[:last]
    result_ids = {_block_field(b, "tool_use_id") for b in pre
                  if (_block_type(b) or "").endswith("_tool_result")}
    kept, kept_use_ids = [], set()
    for block in pre:
        btype = _block_type(block) or ""
        if btype == "text":
            kept.append(block)
        elif btype == "server_tool_use":
            bid = _block_field(block, "id")
            if bid in result_ids:
                kept.append(block)
                kept_use_ids.add(bid)
        elif btype.endswith("_tool_result"):
            if _block_field(block, "tool_use_id") in kept_use_ids:
                kept.append(block)
    kept.extend(b for b in blocks[last + 1:] if _block_type(b) != "fallback")
    return kept


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
        # newline="\n": the cost logs sync cross-platform via OneDrive, and a
        # Windows CRLF marker line would show as ^M in the macOS viewer.
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} log restarted "
                    f"(previous log archived to {path.name}.old)\n")
    except OSError:
        return False
    return True


@contextmanager
def input_wait_timer(app):
    """Accumulate seconds spent blocked on user input into app._input_wait_secs.

    Wraps every dialog wait that parks the streaming thread on the user —
    do_user_prompt, _request_confirmation, and the mail confirms
    (mail_common.confirm_action) — so the cost log's TIME(sec) field measures
    the agent working, not the user's response latency: stream_worker zeroes
    the accumulator at run start and subtracts it from the wall clock at log
    time. The finally path accumulates even when the wrapped wait raises, and
    getattr-defaulting keeps the dialogs callable outside a run (nothing to
    bill the wait against)."""
    started = time.monotonic()
    try:
        yield
    finally:
        app._input_wait_secs = (getattr(app, "_input_wait_secs", 0.0)
                                + (time.monotonic() - started))


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


_DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:[\\/]")


def normalize_save_path(path, home=None):
    """Make a model-supplied save path usable on this machine.

    Models sometimes hallucinate a path from the wrong OS — e.g. grok wrote
    an email attachment to C:\\Users\\...\\Temp\\INV.pdf on macOS (2026-07-23),
    which created a literal ``C:/`` directory tree inside the repo. Expands
    ``~``; on POSIX, a drive-letter path is redirected to ``~/Temp/<basename>``.

    Returns ``(usable_path, note)`` — ``note`` is "" when nothing was
    redirected, else a sentence for the tool result so the model learns where
    the file actually went. ``home`` overrides ``~`` for tests.
    """
    home = home or os.path.expanduser("~")
    expanded = os.path.expanduser(path)
    if os.name != "nt" and _DRIVE_LETTER_RE.match(expanded):
        rest = _DRIVE_LETTER_RE.sub("", expanded).replace("\\", "/").rstrip("/")
        basename = rest.rsplit("/", 1)[-1]
        redirected = os.path.join(home, "Temp", basename or "attachment.bin")
        return redirected, (
            f"note: save_to {path!r} is a Windows path, invalid on this "
            f"machine — saved to {redirected} instead"
        )
    return expanded, ""
