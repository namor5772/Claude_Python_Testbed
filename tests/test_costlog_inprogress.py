"""The in-progress cost record (2026-10-08).

A run's cost lives in stream_worker's locals until the nested _end_run writes
the cost-log line at loop end — so a run killed from OUTSIDE (a reboot, a
taskkill, a power cut) left no line at all. The case that prompted it: a
US$7.77 gpt-6-astra run finished its task, parked at the Convo-mode Agent
Request dialog, and Windows Update restarted the laptop while it waited; its
chat was complete, the log had nothing.

Now stream_worker rewrites agent_run_<N>.json (AGENT_RUN_PREFIX + the
instance number) after every call's cost accounting with exactly what
_log_run would hand _log_api_cost, plus the live provider / model / parameter
summary and the time; _end_run removes it after the real line on BOTH tails;
and the next launch that owns slot N (MyAgent.py __init__) folds a leftover
record into the log through _log_api_cost's new overrides — the record's own
provider / model / params / timestamp, with `unfinished@call<N>` appended to
PARAMETERS.

Two things these tests pin above all: a run that ends normally writes the
SAME line it wrote before (the record is invisible to it), and a bare host
without an instance number writes no record at all, so the other modules'
loop harnesses — and a test run beside a live MyAgent — never touch one.

The loop is the real stream_worker over tests/test_costlog_run_fields.py's
harness (the provider call stubbed at the dispatch seam)."""

import inspect
import json
import os
import pathlib
import queue
import re
import tempfile
import unittest
from unittest import mock

import myagent.streaming_mixin as sm
from tests.test_costlog_run_fields import _CacheHost

ROOT = pathlib.Path(__file__).resolve().parents[1]
STAMP = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


class _RecordHost(_CacheHost):
    """_CacheHost with an instance number — so the real loop keeps
    agent_run_7.json — and a chat name, so the line has its 14 fields. Each
    stubbed call first reads the record as it stands when the call BEGINS:
    None before call 1, call 1's totals before call 2."""

    def __init__(self, second_call="end", instruction="Balance Westpac"):
        super().__init__(second_call, instruction)
        self._instance_num = 7
        self._run_chat_name = "Balance Westpac_2026-10-08_092704"
        self.seen = []

    def _stream_anthropic_call(self, messages, max_retries, label_emitted):
        path = self._run_progress_path()
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                self.seen.append(json.load(f))
        else:
            self.seen.append(None)
        return super()._stream_anthropic_call(messages, max_retries, label_emitted)


class _TempFiles(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = pathlib.Path(tmp.name)
        self.log = self.dir / "APICostLog_test.txt"
        for name, value in (("APICOST_LOG_FILE", str(self.log)),
                            ("AGENT_RUN_PREFIX", str(self.dir / "agent_run_"))):
            patcher = mock.patch.object(sm, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def lines(self):
        if not self.log.exists():
            return []
        return self.log.read_text(encoding="utf-8").splitlines()

    def records(self):
        return sorted(p.name for p in self.dir.glob("agent_run_*"))

    def warnings(self, host):
        out = []
        while True:
            try:
                msg = host.queue.get_nowait()
            except queue.Empty:
                return out
            if msg.get("type") == "warning":
                out.append(msg["content"])


class RealLoopTests(_TempFiles):
    def test_record_written_after_each_call_and_removed_at_the_end(self):
        host = _RecordHost(second_call="end")
        host.stream_worker([{"role": "user", "content": "go"}])
        # Before call 1 there was nothing; before call 2, call 1's totals.
        self.assertIsNone(host.seen[0])
        rec = host.seen[1]
        self.assertEqual(rec["version"], 1)
        self.assertRegex(rec["written"], STAMP)
        self.assertEqual(rec["provider"], "Anthropic")
        self.assertEqual(rec["model"], "claude-sonnet-5")
        self.assertEqual(rec["params"], "mode=Adaptive")
        self.assertTrue(rec["had_usage"])
        self.assertGreater(rec["total_cost"], 0)
        self.assertGreaterEqual(rec["duration_secs"], 0)
        self.assertEqual(rec["instruction"], "Balance Westpac")
        self.assertEqual(rec["calls"], 1)
        self.assertEqual(rec["tokens"], [1000, 100, 40, 7])
        self.assertEqual(list(rec["split"]), ["claude-sonnet-5"])
        self.assertEqual(rec["split"]["claude-sonnet-5"][1:], [1000, 100, 40, 7])
        self.assertAlmostEqual(rec["split"]["claude-sonnet-5"][0], rec["total_cost"])
        self.assertEqual(rec["chat"], "Balance Westpac_2026-10-08_092704")
        # The run ended normally: the real line is there, the record is gone.
        self.assertEqual(self.records(), [])
        f = self.lines()[0].split(";")
        self.assertEqual(len(f), 14)
        self.assertEqual(f[7], "2")
        self.assertEqual(f[8:12], ["2000", "200", "80", "14"])
        self.assertEqual(f[13], "Balance Westpac_2026-10-08_092704")
        self.assertAlmostEqual(float(f[3]), 2 * rec["total_cost"], places=4)

    def test_normal_end_writes_the_same_line_as_a_host_without_a_record(self):
        # The user's question: everything as before when a run ends normally.
        with_record = _RecordHost(second_call="end")
        with_record.stream_worker([{"role": "user", "content": "go"}])
        line_with = self.lines()[0]
        self.log.unlink()
        without = _CacheHost(second_call="end")
        without._run_chat_name = "Balance Westpac_2026-10-08_092704"
        without.stream_worker([{"role": "user", "content": "go"}])
        line_without = self.lines()[0]
        # Everything but the timestamp field is byte-identical.
        self.assertEqual(line_with.split(";")[1:], line_without.split(";")[1:])
        self.assertEqual(self.records(), [])

    def test_exception_tail_logs_the_line_and_removes_the_record(self):
        host = _RecordHost(second_call="raise")
        host.stream_worker([{"role": "user", "content": "go"}])
        self.assertEqual(self.records(), [])
        f = self.lines()[0].split(";")
        self.assertEqual(f[7], "2")   # the failing call, as before
        self.assertNotIn("unfinished", f[4])

    def test_bare_host_without_an_instance_number_writes_no_record(self):
        host = _CacheHost(second_call="end")
        self.assertIsNone(host._run_progress_path())
        host.stream_worker([{"role": "user", "content": "go"}])
        self.assertEqual(self.records(), [])
        self.assertEqual(len(self.lines()), 1)

    def test_a_killed_run_leaves_its_record_behind(self):
        # What a reboot leaves: the loop never reaches _end_run. Modelled by
        # a clear that does nothing — the record is then what the last call
        # wrote.
        host = _RecordHost(second_call="end")
        with mock.patch.object(_RecordHost, "_run_progress_clear", lambda self: None):
            host.stream_worker([{"role": "user", "content": "go"}])
        self.assertEqual(self.records(), ["agent_run_7.json"])
        rec = json.loads((self.dir / "agent_run_7.json").read_text(encoding="utf-8"))
        self.assertEqual(rec["calls"], 2)
        self.assertEqual(rec["tokens"], [2000, 200, 80, 14])

    def test_write_failure_warns_once_and_never_interrupts_the_run(self):
        host = _RecordHost(second_call="end")
        with mock.patch.object(sm.tempfile, "mkstemp", side_effect=OSError("disk full")):
            host.stream_worker([{"role": "user", "content": "go"}])
        warns = [w for w in self.warnings(host) if "in-progress cost record" in w]
        self.assertEqual(len(warns), 1)      # two calls, one warning
        self.assertEqual(len(self.lines()), 1)   # the real line still written
        self.assertEqual(host.tool_calls, ["read_file"])


class FoldInTests(_TempFiles):
    def _leftover(self, **over):
        """A record as a dead instance 7 leaves it, with overrides."""
        rec = {
            "version": 1, "written": "2026-10-08 10:08:04",
            "provider": "OpenAI", "model": "gpt-6-astra",
            "params": "reasoning=Max verbosity=medium",
            "total_cost": 7.7744525, "had_usage": True, "duration_secs": 1234.6,
            "instruction": "Process_Dockets_PROGRAM", "calls": 53,
            "tokens": [18103, 48399, 108567, 3816385],
            "split": {"gpt-6-astra": [7.7744525, 18103, 48399, 108567, 3816385]},
            "chat": "Process_Dockets_PROGRAM_2026-10-08_092704",
        }
        rec.update(over)
        (self.dir / "agent_run_7.json").write_text(json.dumps(rec), encoding="utf-8")
        return rec

    def _launching_host(self):
        # The process that claims slot 7 next: a different provider / model /
        # params than the dead run's, so the overrides are what the line shows.
        host = _CacheHost()
        host._instance_num = 7
        host.provider = "Anthropic"
        host.model = "claude-haiku-4-5"
        return host

    def test_fold_in_writes_the_line_from_the_record(self):
        self._leftover()
        host = self._launching_host()
        self.assertTrue(host._run_progress_fold_in())
        f = self.lines()[0].split(";")
        self.assertEqual(len(f), 14)
        self.assertEqual(f[0], "2026-10-08 10:08:04")   # the record's time
        self.assertEqual(f[1:3], ["OpenAI", "gpt-6-astra"])
        self.assertEqual(f[3], "7.7745")
        self.assertEqual(f[4], "reasoning=Max, verbosity=medium, unfinished@call53")
        self.assertEqual(f[5], "1235")
        self.assertEqual(f[6], "Process_Dockets_PROGRAM")
        self.assertEqual(f[7], "53")
        self.assertEqual(f[8:12], ["18103", "48399", "108567", "3816385"])
        self.assertEqual(f[12], "")   # one model, logged under it: no split
        self.assertEqual(f[13], "Process_Dockets_PROGRAM_2026-10-08_092704")
        self.assertEqual(self.records(), [])
        warns = self.warnings(host)
        self.assertEqual(len(warns), 1)
        self.assertIn("unfinished@call53", warns[0])
        self.assertIn("Process_Dockets_PROGRAM_2026-10-08_092704", warns[0])

    def test_no_record_means_nothing_happens(self):
        host = self._launching_host()
        self.assertFalse(host._run_progress_fold_in())
        self.assertEqual(self.lines(), [])
        self.assertEqual(self.warnings(host), [])

    def test_host_without_an_instance_number_folds_nothing(self):
        self._leftover()
        host = _CacheHost()
        self.assertFalse(host._run_progress_fold_in())
        self.assertEqual(self.lines(), [])
        self.assertEqual(self.records(), ["agent_run_7.json"])   # not its record

    def test_unreadable_record_is_removed_with_a_warning_and_no_line(self):
        (self.dir / "agent_run_7.json").write_text("{not json", encoding="utf-8")
        host = self._launching_host()
        self.assertFalse(host._run_progress_fold_in())
        self.assertEqual(self.lines(), [])
        self.assertEqual(self.records(), [])
        warns = self.warnings(host)
        self.assertEqual(len(warns), 1)
        self.assertIn("Could not fold in", warns[0])

    def test_unpriced_record_is_gated_like_a_live_run(self):
        # A paid provider's run that priced nothing: no line, as at run end.
        self._leftover(total_cost=0.0, split={})
        host = self._launching_host()
        self.assertFalse(host._run_progress_fold_in())
        self.assertEqual(self.lines(), [])
        self.assertEqual(self.records(), [])
        warns = self.warnings(host)
        self.assertEqual(len(warns), 1)
        self.assertIn("NOT written", warns[0])
        self.assertIn("gpt-6-astra", warns[0])       # the record's model...
        self.assertIn("OpenAI pricing table", warns[0])   # ...and provider

    def test_free_ollama_record_logs_at_zero(self):
        self._leftover(provider="Ollama", model="qwen3:32b", total_cost=0.0,
                       params="thinking=on", split={})
        host = self._launching_host()
        self.assertTrue(host._run_progress_fold_in())
        f = self.lines()[0].split(";")
        self.assertEqual(f[1:4], ["Ollama", "qwen3:32b", "0.0000"])
        self.assertEqual(f[4], "thinking=on, unfinished@call53")

    def test_split_record_renders_the_13th_field(self):
        self._leftover(model="gpt-6-astra",
                       params="reasoning=Max verbosity=low upgraded-from=gpt-6-luna@call71",
                       split={"gpt-6-luna": [0.077055, 4828, 53699, 96311, 3768425],
                              "gpt-6-astra": [7.635504, 1564, 44544, 177423, 3174876]})
        host = self._launching_host()
        self.assertTrue(host._run_progress_fold_in())
        f = self.lines()[0].split(";")
        self.assertEqual(
            f[12],
            "gpt-6-luna,0.077055,4828,53699,96311,3768425"
            "|gpt-6-astra,7.635504,1564,44544,177423,3174876")
        self.assertEqual(f[4], "reasoning=Max, verbosity=low, "
                               "upgraded-from=gpt-6-luna@call71, unfinished@call53")

    def test_record_without_calls_or_tokens_still_folds(self):
        self._leftover(calls=None, tokens=None, split={})
        host = self._launching_host()
        self.assertTrue(host._run_progress_fold_in())
        f = self.lines()[0].split(";")
        self.assertEqual(f[4], "reasoning=Max, verbosity=medium, unfinished")
        self.assertEqual(f[7], "")
        self.assertEqual(f[8:12], ["", "", "", ""])


class OverrideTests(_TempFiles):
    def test_overrides_equal_to_the_live_fields_write_the_same_line(self):
        host = _CacheHost()
        self.assertTrue(host._log_api_cost(
            0.5, had_usage=True, duration_secs=12, instruction="X", calls=3,
            tokens=(1, 2, 3, 4), chat="X_2026-10-08_101010"))
        plain = self.lines()[0]
        self.log.unlink()
        self.assertTrue(host._log_api_cost(
            0.5, had_usage=True, duration_secs=12, instruction="X", calls=3,
            tokens=(1, 2, 3, 4), chat="X_2026-10-08_101010",
            provider="Anthropic", model="claude-sonnet-5", params="mode=Adaptive",
            timestamp=plain.split(";")[0]))
        self.assertEqual(self.lines()[0], plain)

    def test_returns_false_when_the_gate_skips_or_the_write_fails(self):
        host = _CacheHost()
        self.assertFalse(host._log_api_cost(0.0, had_usage=True))   # unpriced, paid
        self.assertEqual(self.lines(), [])
        with mock.patch.object(sm, "APICOST_LOG_FILE", str(self.dir / "no" / "dir" / "x.txt")):
            self.assertFalse(host._log_api_cost(0.5, had_usage=True))
        warns = self.warnings(host)
        self.assertTrue(any("Could not write the API cost log" in w for w in warns))


class WiringTests(unittest.TestCase):
    def test_stream_worker_writes_after_accounting_and_clears_after_the_line(self):
        src = inspect.getsource(sm.StreamingMixin.stream_worker)
        self.assertIn("self._run_progress_write(", src)
        # Written only for a call that reported usage — inside the block that
        # accumulates the totals, after both cost_update branches.
        self.assertLess(src.index('"total_output_tokens": total_output_tokens'),
                        src.index("self._run_progress_write("))
        self.assertLess(src.index("self._run_progress_write("),
                        src.index("# Post-process LaTeX"))
        # Cleared right after the line, inside _end_run — the one place both
        # tails go through.
        self.assertRegex(src, r"_log_run\(\)\n\s*(#[^\n]*\n\s*)*self\._run_progress_clear\(\)")
        self.assertEqual(src.count("self._run_progress_clear()"), 1)
        self.assertEqual(src.count("_end_run()"), 3)   # the def + both tails

    def test_app_folds_in_once_the_queue_exists_and_before_any_run(self):
        src = (ROOT / "MyAgent.py").read_text(encoding="utf-8")
        fold = src.index("self._run_progress_fold_in()")
        self.assertLess(src.index("self.queue = queue.Queue()"), fold)
        self.assertLess(src.index("self._instance_num = self._claim_instance_number()"), fold)
        self.assertLess(fold, src.index("self.root.after(50, self.check_queue)"))
        self.assertLess(fold, src.index("self.root.after(100, self._auto_launch)"))

    def test_record_files_are_gitignored(self):
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("agent_run_*.json", ignore)
        self.assertTrue(sm.AGENT_RUN_PREFIX.endswith("agent_run_"))


if __name__ == "__main__":
    unittest.main()
