"""Characterization test: retry_util backoff schedules (Phase 3).

Locks the exact 429 (cap 60s) and 5xx/529 (cap 90s) exponential schedules
shared by the Anthropic / OpenAI / Gemini streaming retry loops — both as
explicit golden values and as equivalence to the original inline min()
expressions the refactor replaced."""
import unittest

from myagent.retry_util import rate_limit_backoff, server_error_backoff

# Explicit golden schedules for attempt 0..7
RATE_LIMIT = [5, 10, 20, 40, 60, 60, 60, 60]
SERVER_ERROR = [10, 20, 40, 80, 90, 90, 90, 90]


class TestBackoff(unittest.TestCase):
    def test_rate_limit_golden(self):
        self.assertEqual([rate_limit_backoff(a) for a in range(8)], RATE_LIMIT)

    def test_server_error_golden(self):
        self.assertEqual([server_error_backoff(a) for a in range(8)], SERVER_ERROR)

    def test_matches_original_inline_formula(self):
        # The exact expressions these functions replaced in the provider mixins.
        for attempt in range(16):
            with self.subTest(attempt=attempt):
                self.assertEqual(rate_limit_backoff(attempt), min(2 ** attempt * 5, 60))
                self.assertEqual(server_error_backoff(attempt), min(2 ** attempt * 10, 90))


if __name__ == "__main__":
    unittest.main()
