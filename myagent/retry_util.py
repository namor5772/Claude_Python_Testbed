"""Shared retry-backoff arithmetic for the API streaming providers.

Anthropic, OpenAI, and Gemini share the same exponential backoff schedule for
transient errors: a rate-limit (HTTP 429) wait that caps at 60s, and a
server-error (HTTP 5xx, including Anthropic's 529 overload) wait that caps at
90s. A connection error (no HTTP status at all — the request never reached
the API, or the stream broke under it; the SDK's own two quick retries have
already failed by the time the caller sees one) waits on the 429 schedule.
Ollama uses a different schedule and keeps its own inline math.

The OpenAI, xAI and Kimi callers also share the "Waiting for model response"
ticker that runs while an attempt has produced no visible output yet
(`start_waiting_ticker`; the numbers are constants.FIRST_CONTENT_TIMEOUT /
WAITING_MSG_INTERVAL).
"""
import threading
import time

from myagent.constants import WAITING_MSG_INTERVAL


def rate_limit_backoff(attempt):
    """Seconds to wait before retrying after a 429 rate-limit error
    (0-indexed attempt). Exponential from 5s, capped at 60s."""
    return min(2 ** attempt * 5, 60)


def server_error_backoff(attempt):
    """Seconds to wait before retrying after a 5xx / 529 server error
    (0-indexed attempt). Exponential from 10s, capped at 90s."""
    return min(2 ** attempt * 10, 90)


def connection_error_backoff(attempt):
    """Seconds to wait before retrying after a connection error — a DNS /
    TCP / TLS failure, a dropped stream, a read timeout (0-indexed attempt).
    Exponential from 5s, capped at 60s: a network blip clears in seconds
    and an outage is no better served by a longer wait."""
    return min(2 ** attempt * 5, 60)


def start_waiting_ticker(first_content, started, post):
    """Post "Waiting for model response... (Ns elapsed)" through `post` every
    WAITING_MSG_INTERVAL seconds until the attempt's first-content Event is
    set — by the first visible output, or on every exit of the call. Both
    `first_content` and `started` are zero-argument callables, looked up at
    each tick as the callers' original closures did: a call re-armed after a
    first-content timeout binds a NEW Event and start time, and a ticker
    already running follows them. The thread is a daemon; this returns at
    once."""
    def _tick():
        while not first_content().wait(timeout=WAITING_MSG_INTERVAL):
            post(f"Waiting for model response... ({int(time.time() - started())}s elapsed)\n")
    threading.Thread(target=_tick, daemon=True).start()
