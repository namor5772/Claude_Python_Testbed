from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path


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
