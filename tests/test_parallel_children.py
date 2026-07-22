"""Characterization tests for the parallel waited-children capability:
run_instruction is PARALLEL_SAFE (several calls in one assistant turn run
concurrently on stream_worker's executor), concurrent do_run_instruction
calls keep their per-spawn result channels separate, and
_claim_instance_number's O_EXCL claim gives simultaneously-launching
children distinct instance slots."""

import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from myagent import state_mixin
from myagent.constants import PARALLEL_SAFE_TOOLS
from myagent.skills_mixin import SkillsMixin
from myagent.state_mixin import StateMixin


class ParallelSafeMembership(unittest.TestCase):
    def test_run_instruction_is_parallel_safe(self):
        self.assertIn("run_instruction", PARALLEL_SAFE_TOOLS)

    def test_other_meta_tools_stay_sequential(self):
        self.assertNotIn("manage_instructions", PARALLEL_SAFE_TOOLS)
        self.assertNotIn("manage_skills", PARALLEL_SAFE_TOOLS)


class _Host(SkillsMixin):
    def __init__(self):
        self.stop_requested = False

    def _load_saved_instructions(self):
        return {"Child": {"text": "t"}}


class _FakeChild:
    """Stands in for a spawned child process: 'runs' for lifetime seconds and
    writes its result file up front (the real child finishes the write before
    exiting — process exit is the synchronization point)."""

    def __init__(self, cmd, lifetime, report):
        self.pid = 4242
        self.returncode = 0
        self.deadline = time.monotonic() + lifetime
        path = cmd[cmd.index("--result-file") + 1]
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"instruction": "Child", "status": "completed",
                       "error": "", "final_text": report}, f)

    def poll(self):
        return 0 if time.monotonic() >= self.deadline else None

    def terminate(self):
        self.returncode = -1


class ConcurrentWaitedChildren(unittest.TestCase):
    def test_waited_children_overlap_and_keep_results_separate(self):
        host = _Host()
        spawned = []  # [(spawn_time, _FakeChild), ...]

        def fake_popen(cmd, **kwargs):
            child = _FakeChild(cmd, lifetime=1.2,
                               report=f"REPORT_{len(spawned) + 1}")
            spawned.append((time.monotonic(), child))
            return child

        with mock.patch("myagent.skills_mixin.subprocess.Popen",
                        side_effect=fake_popen):
            t0 = time.monotonic()
            with ThreadPoolExecutor(max_workers=2) as ex:
                futures = [
                    ex.submit(host.do_run_instruction,
                              {"name": "Child", "wait": True})
                    for _ in range(2)
                ]
                r1, r2 = (f.result() for f in futures)
            elapsed = time.monotonic() - t0

        # Both children were alive at the same time: the second spawned
        # before the first's exit deadline had passed.
        self.assertEqual(len(spawned), 2)
        self.assertLess(spawned[1][0], spawned[0][1].deadline)
        # Each call returned its OWN child's report — no cross-talk between
        # the per-spawn result files.
        reports = {r1.rsplit("Final report:\n", 1)[1],
                   r2.rsplit("Final report:\n", 1)[1]}
        self.assertEqual(reports, {"REPORT_1", "REPORT_2"})
        # Wall clock is one child's lifetime plus poll granularity, not two
        # children back to back (~4s+ if the waits had serialized).
        self.assertLess(elapsed, 3.5)


class AtomicInstanceClaim(unittest.TestCase):
    """Models production liveness: a lock holding OUR pid is a live claimant
    (real children are live MyAgent.py processes); any other pid is dead."""

    def _race(self, n, prefix):
        hosts = [StateMixin.__new__(StateMixin) for _ in range(n)]
        nums = [None] * n
        gate = threading.Barrier(n)

        def claim(i):
            gate.wait()  # maximize the race on the lowest slot
            nums[i] = hosts[i]._claim_instance_number()

        with mock.patch.object(state_mixin, "AGENT_LOCK_PREFIX", prefix), \
                mock.patch.object(StateMixin, "_is_pid_alive",
                                  lambda self, pid: pid == os.getpid()):
            threads = [threading.Thread(target=claim, args=(i,))
                       for i in range(n)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        return nums

    def _tmp_prefix(self):
        tmp = tempfile.mkdtemp(prefix="myagent_locktest_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        return os.path.join(tmp, "agent_lock_")

    def _assert_distinct_and_owned(self, nums, prefix):
        self.assertEqual(len(set(nums)), len(nums),
                         f"duplicate instance slots claimed: {nums}")
        # Every claimed lock file exists and holds this process's PID.
        for num in nums:
            with open(f"{prefix}{num}.lock") as f:
                self.assertEqual(int(f.read().strip()), os.getpid())

    def test_simultaneous_claims_get_distinct_slots(self):
        prefix = self._tmp_prefix()
        nums = self._race(8, prefix)
        self._assert_distinct_and_owned(nums, prefix)

    def test_stale_lock_reclaim_race_stays_single_owner(self):
        # A genuinely stale lock (dead pid, old mtime) on slot 1: the storm of
        # simultaneous reclaimers must produce exactly one owner per slot —
        # the conditional remove + verify-after-write close the TOCTOU where
        # a reclaimer deletes a sibling's fresh replacement lock.
        prefix = self._tmp_prefix()
        stale = f"{prefix}1.lock"
        with open(stale, "w") as f:
            f.write("99999")
        old = time.time() - 3600
        os.utime(stale, (old, old))
        nums = self._race(8, prefix)
        self._assert_distinct_and_owned(nums, prefix)


if __name__ == "__main__":
    unittest.main()
