"""Shared retry-backoff arithmetic for the API streaming providers.

Anthropic, OpenAI, and Gemini share the same exponential backoff schedule for
transient errors: a rate-limit (HTTP 429) wait that caps at 60s, and a
server-error (HTTP 5xx, including Anthropic's 529 overload) wait that caps at
90s. Ollama uses a different schedule and keeps its own inline math.
"""


def rate_limit_backoff(attempt):
    """Seconds to wait before retrying after a 429 rate-limit error
    (0-indexed attempt). Exponential from 5s, capped at 60s."""
    return min(2 ** attempt * 5, 60)


def server_error_backoff(attempt):
    """Seconds to wait before retrying after a 5xx / 529 server error
    (0-indexed attempt). Exponential from 10s, capped at 90s."""
    return min(2 ** attempt * 10, 90)
