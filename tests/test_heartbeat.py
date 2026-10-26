"""Characterization tests for Heartbeat.py: the one-slot log rotation and,
since 2026-10-26, the Telegram transport — the trigger parser, the reply
chunking, the config file, the pass over a fake Bot API (a stranger's message
consumed and ignored, a trigger confirmed BEFORE its launch and started with
--result-file, poison and deferred triggers, a launch that fails after the
confirm telling the user to resend, one trigger per pass), the outcome
delivery (the result file, a dead process, the 24-hour rule), main() over the
one transport (no setup is an error, idle is one line), the resident listener
(every update of a long-poll batch handled, a deferred trigger pausing the
poll, errors backed off and logged once, the one-instance lock, runs from an
earlier life watched, the per-run watcher posting the outcome the moment the
child exits), the launcher's command
line still matching the watchdog pattern, the pairing command, and the rule
that the bot token never reaches a log line or an error text.

rotate_log_if_needed reads the module-global LOG_FILE (not self), so these
tests repoint Heartbeat.LOG_FILE at a temp directory instead of using
_util.stub. log() resolves the same global at call time, so the marker line
lands in the temp log too. The Telegram tests repoint the config / pending /
results folders the same way — the suite must never touch, or poll, a real
bot.
"""

import io
import json
import os
import re
import subprocess
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import Heartbeat
from myagent import relay


class RotateLogTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log = Path(tmp.name) / "heartbeat.log"
        self.old = self.log.with_name("heartbeat.log.old")
        orig = Heartbeat.LOG_FILE
        Heartbeat.LOG_FILE = self.log
        self.addCleanup(setattr, Heartbeat, "LOG_FILE", orig)

    def test_missing_log_is_a_noop(self):
        Heartbeat.rotate_log_if_needed()
        self.assertFalse(self.log.exists())
        self.assertFalse(self.old.exists())

    def test_log_at_cap_is_untouched(self):
        content = "x" * Heartbeat.LOG_MAX_BYTES
        self.log.write_text(content, encoding="utf-8")
        Heartbeat.rotate_log_if_needed()
        self.assertEqual(self.log.read_text(encoding="utf-8"), content)
        self.assertFalse(self.old.exists())

    def test_over_cap_archives_and_seeds_marker(self):
        content = "x" * (Heartbeat.LOG_MAX_BYTES + 1)
        self.log.write_text(content, encoding="utf-8")
        Heartbeat.rotate_log_if_needed()
        self.assertEqual(self.old.read_text(encoding="utf-8"), content)
        fresh = self.log.read_text(encoding="utf-8")
        self.assertIn("log restarted", fresh)
        self.assertIn("heartbeat.log.old", fresh)
        self.assertEqual(len(fresh.splitlines()), 1)

    def test_rotation_replaces_previous_archive(self):
        self.old.write_text("ancient history", encoding="utf-8")
        content = "y" * (Heartbeat.LOG_MAX_BYTES + 1)
        self.log.write_text(content, encoding="utf-8")
        Heartbeat.rotate_log_if_needed()
        self.assertEqual(self.old.read_text(encoding="utf-8"), content)


# ── Telegram transport ────────────────────────────────────────────────────

def _patch(test, target, name, value):
    p = mock.patch.object(target, name, value)
    p.start()
    test.addCleanup(p.stop)


class _FakeTelegram:
    """A Bot API stand-in: queued message updates, the confirm offset, every
    sendMessage, and the order of confirms against the launches."""

    def __init__(self, events, updates=()):
        self.events = events
        self.updates = list(updates)
        self.offset = 0
        self.sent = []
        self.calls = []

    def __call__(self, token, method, **params):
        self.calls.append((method, params))
        assert token == "TOKEN", token
        if method == "getUpdates":
            if "offset" in params:
                self.offset = max(self.offset, params["offset"])
                self.events.append(f"confirm:{params['offset'] - 1}")
            return [u for u in self.updates if u["update_id"] >= self.offset]
        if method == "sendMessage":
            self.sent.append((params["chat_id"], params["text"]))
            self.events.append(f"send:{params['chat_id']}")
            return {"message_id": len(self.sent)}
        if method == "getMe":
            return {"username": "myagent_desk_bot"}
        raise AssertionError(method)


def _update(uid, text, chat_id=777, **extra):
    msg = {"chat": {"id": chat_id, "first_name": "Roman"}}
    if text is not None:
        msg["text"] = text
    msg.update(extra)
    return {"update_id": uid, "message": msg}


class _TelegramBase(unittest.TestCase):
    """Temp log + temp Telegram folders, the store and the process table
    faked, the launcher recorded."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.log = self.root / "heartbeat.log"
        _patch(self, Heartbeat, "LOG_FILE", self.log)
        _patch(self, Heartbeat, "TELEGRAM_CONFIG_DIR", self.root / "tg")
        _patch(self, Heartbeat, "TELEGRAM_CONFIG_FILE", self.root / "tg" / "config.json")
        _patch(self, Heartbeat, "TELEGRAM_PENDING_DIR", self.root / "tg" / "pending")
        _patch(self, Heartbeat, "TELEGRAM_RESULTS_DIR", self.root / "tg" / "results")
        self.events = []
        self.rewrites = []
        self.launches = []
        self.relays = []
        self.running = []
        self.launch_error = None
        _patch(self, Heartbeat, "rewrite_instruction",
               lambda name, core: self.rewrites.append((name, core)))
        _patch(self, Heartbeat, "running_instances", lambda name: list(self.running))

        def launch(name, result_file=None, relay_dir=None):
            self.events.append(f"launch:{name}")
            self.launches.append((name, result_file))
            self.relays.append(relay_dir)
            if self.launch_error:
                raise self.launch_error
            return 4242
        _patch(self, Heartbeat, "launch_instruction", launch)
        self.cfg = {"bot_token": "TOKEN", "allowed_chat_ids": [777]}

    def fake(self, *updates):
        self.api = _FakeTelegram(self.events, updates)
        _patch(self, Heartbeat, "telegram_api", self.api)
        return self.api

    def log_text(self):
        return self.log.read_text(encoding="utf-8") if self.log.exists() else ""

    def pending_files(self):
        d = self.root / "tg" / "pending"
        return sorted(d.glob("*.json")) if d.exists() else []


class TelegramParseTests(unittest.TestCase):
    def test_name_then_core_with_the_email_layout(self):
        self.assertEqual(Heartbeat.parse_telegram_trigger("ZZ Probe\n\nDo the thing\nline 2"),
                         ("ZZ Probe", "Do the thing\nline 2"))

    def test_the_blank_second_line_is_optional(self):
        self.assertEqual(Heartbeat.parse_telegram_trigger("ZZ Probe\nDo the thing"),
                         ("ZZ Probe", "Do the thing"))
        self.assertEqual(Heartbeat.parse_telegram_trigger("ZZ Probe\n\n\n\nDo it"),
                         ("ZZ Probe", "Do it"))

    def test_a_name_alone_has_an_empty_core(self):
        self.assertEqual(Heartbeat.parse_telegram_trigger("  ZZ Probe  "), ("ZZ Probe", ""))
        self.assertEqual(Heartbeat.parse_telegram_trigger(""), ("", ""))
        self.assertEqual(Heartbeat.parse_telegram_trigger(None), ("", ""))

    def test_a_core_keeps_its_own_blank_lines_and_markers(self):
        name, core = Heartbeat.parse_telegram_trigger("N\n\na\n\nb\n*****\nc")
        self.assertEqual((name, core), ("N", "a\n\nb\n*****\nc"))


class TelegramChunkTests(unittest.TestCase):
    def test_short_text_is_one_chunk(self):
        self.assertEqual(Heartbeat.telegram_chunks("hello"), ["hello"])
        self.assertEqual(Heartbeat.telegram_chunks("  hello \n"), ["hello"])

    def test_empty_text_is_sent_as_a_placeholder(self):
        self.assertEqual(Heartbeat.telegram_chunks(""), ["(empty)"])
        self.assertEqual(Heartbeat.telegram_chunks(None), ["(empty)"])

    def test_long_text_is_cut_at_a_line_break_past_the_midpoint(self):
        text = "a" * 60 + "\n" + "b" * 60 + "\n" + "c" * 30
        chunks = Heartbeat.telegram_chunks(text, limit=100)
        self.assertEqual(chunks, ["a" * 60, "b" * 60 + "\n" + "c" * 30])
        self.assertTrue(all(len(c) <= 100 for c in chunks))

    def test_a_line_longer_than_the_limit_is_cut_hard(self):
        chunks = Heartbeat.telegram_chunks("x" * 250, limit=100)
        self.assertEqual(chunks, ["x" * 100, "x" * 100, "x" * 50])

    def test_every_chunk_respects_the_telegram_limit(self):
        text = "\n".join("line %d %s" % (i, "y" * 90) for i in range(200))
        for chunk in Heartbeat.telegram_chunks(text):
            self.assertLessEqual(len(chunk), Heartbeat.TELEGRAM_CHUNK_CHARS)
            self.assertTrue(chunk)
        self.assertLess(Heartbeat.TELEGRAM_CHUNK_CHARS, 4096)


class TelegramReplyFormatTests(unittest.TestCase):
    def test_completed_run_with_text(self):
        text = Heartbeat.format_result_reply(
            "ZZ", {"status": "completed", "error": "", "final_text": "All done.\nBye"})
        self.assertEqual(text, f"✅ ZZ completed on {Heartbeat.HOST}\n\nAll done.\nBye")

    def test_error_run_names_the_error(self):
        text = Heartbeat.format_result_reply(
            "ZZ", {"status": "error", "error": "boom", "final_text": ""})
        self.assertEqual(text, f"❌ ZZ error on {Heartbeat.HOST}\nboom")

    def test_stopped_and_unknown_statuses(self):
        self.assertTrue(Heartbeat.format_result_reply("ZZ", {"status": "stopped"})
                        .startswith("⏹ ZZ stopped on "))
        self.assertTrue(Heartbeat.format_result_reply("ZZ", {}).startswith("ℹ ZZ unknown on "))


class TelegramConfigTests(_TelegramBase):
    def write(self, data):
        (self.root / "tg").mkdir(parents=True, exist_ok=True)
        (self.root / "tg" / "config.json").write_text(json.dumps(data), encoding="utf-8")

    def test_no_file_means_no_telegram_on_this_machine(self):
        self.assertIsNone(Heartbeat.telegram_config())

    def test_a_good_file_is_read(self):
        self.write({"bot_token": " TOKEN ", "allowed_chat_ids": [777, 888]})
        self.assertEqual(Heartbeat.telegram_config(),
                         {"bot_token": "TOKEN", "allowed_chat_ids": [777, 888]})

    def test_a_file_without_a_token_raises(self):
        self.write({"allowed_chat_ids": [777]})
        with self.assertRaises(RuntimeError):
            Heartbeat.telegram_config()

    def test_chat_ids_must_be_integers(self):
        for bad in ("777", ["777"], [True], {"a": 1}):
            self.write({"bot_token": "TOKEN", "allowed_chat_ids": bad})
            with self.assertRaises(RuntimeError):
                Heartbeat.telegram_config()

    def test_write_round_trips(self):
        Heartbeat.telegram_write_config({"bot_token": "TOKEN", "allowed_chat_ids": [1]})
        self.assertEqual(Heartbeat.telegram_config(),
                         {"bot_token": "TOKEN", "allowed_chat_ids": [1]})


class TelegramProcessTests(_TelegramBase):
    def test_no_updates_is_idle(self):
        self.fake()
        self.assertFalse(Heartbeat.process_telegram(self.cfg))
        self.assertEqual(self.events, [])
        self.assertEqual(self.log_text(), "")

    def test_a_trigger_is_confirmed_before_the_launch_and_answered(self):
        self.fake(_update(10, "ZZ Probe\n\nDo the thing"))
        self.assertTrue(Heartbeat.process_telegram(self.cfg))
        self.assertEqual(self.events, ["confirm:10", "launch:ZZ Probe", "send:777"])
        self.assertEqual(self.rewrites, [("ZZ Probe", "Do the thing")])
        name, result_file = self.launches[0]
        self.assertEqual(Path(result_file), self.root / "tg" / "results" / "10.json")
        self.assertTrue((self.root / "tg" / "results").is_dir())
        self.assertIn("Started ZZ Probe on", self.api.sent[0][1])
        pending = self.pending_files()
        self.assertEqual([p.name for p in pending], ["10.json"])
        record = json.loads(pending[0].read_text(encoding="utf-8"))
        self.assertEqual((record["update_id"], record["chat_id"], record["instruction"],
                          record["pid"], Path(record["result_file"])),
                         (10, 777, "ZZ Probe", 4242, self.root / "tg" / "results" / "10.json"))
        self.assertIn("launched 'ZZ Probe' headless (PID 4242) from telegram update 10",
                      self.log_text())

    def test_a_stranger_is_consumed_logged_and_never_answered(self):
        self.fake(_update(10, "ZZ Probe\n\nDo", chat_id=999))
        self.assertTrue(Heartbeat.process_telegram(self.cfg))
        self.assertEqual(self.events, ["confirm:10"])
        self.assertEqual(self.api.sent, [])
        self.assertEqual(self.launches, [])
        self.assertIn("ignored update 10 from chat 999", self.log_text())

    def test_a_stranger_ahead_of_the_owner_does_not_block_the_owner(self):
        self.fake(_update(10, "spam", chat_id=999), _update(11, "ZZ Probe\nDo"))
        self.assertTrue(Heartbeat.process_telegram(self.cfg))
        self.assertEqual(self.events, ["confirm:10", "confirm:11", "launch:ZZ Probe", "send:777"])

    def test_an_empty_first_line_is_poison(self):
        self.fake(_update(10, "\n\nDo the thing"))
        self.assertTrue(Heartbeat.process_telegram(self.cfg))
        self.assertEqual(self.events, ["confirm:10", "send:777"])
        self.assertEqual(self.launches, [])
        self.assertIn("Not run on", self.api.sent[0][1])
        self.assertIn("POISON telegram update 10: empty first line", self.log_text())
        self.assertEqual(self.pending_files(), [])

    def test_an_unknown_instruction_is_poison_with_the_reason(self):
        def missing(name, core):
            raise LookupError(f"instruction {name!r} not found")
        _patch(self, Heartbeat, "rewrite_instruction", missing)
        self.fake(_update(10, "Nope\n\nDo"))
        self.assertTrue(Heartbeat.process_telegram(self.cfg))
        self.assertEqual(self.events, ["confirm:10", "send:777"])
        self.assertIn("instruction 'Nope' not found", self.api.sent[0][1])
        self.assertEqual(self.launches, [])

    def test_a_poison_trigger_does_not_use_up_the_pass(self):
        def rewrite(name, core):
            if name == "Nope":
                raise LookupError(f"instruction {name!r} not found")
        _patch(self, Heartbeat, "rewrite_instruction", rewrite)
        self.fake(_update(10, "Nope\nDo"), _update(11, "ZZ Probe\nDo"))
        Heartbeat.process_telegram(self.cfg)
        self.assertEqual(self.events, ["confirm:10", "send:777", "confirm:11",
                                       "launch:ZZ Probe", "send:777"])

    def test_start_and_help_are_answered_with_the_usage_and_skipped(self):
        self.fake(_update(10, "/start"), _update(11, " /HELP "), _update(12, "ZZ Probe\nDo"))
        self.assertTrue(Heartbeat.process_telegram(self.cfg))
        self.assertEqual(self.events, ["confirm:10", "send:777", "confirm:11", "send:777",
                                       "confirm:12", "launch:ZZ Probe", "send:777"])
        self.assertIn("line 1 the name of a saved MyAgent instruction", self.api.sent[0][1])
        self.assertIn(Heartbeat.HOST, self.api.sent[0][1])
        self.assertEqual(self.rewrites, [("ZZ Probe", "Do")])
        self.assertIn("/start in update 10 — answered with the usage", self.log_text())

    def test_a_live_run_defers_the_trigger_unconsumed_and_unanswered(self):
        self.running = [(5151, 60)]
        self.fake(_update(10, "ZZ Probe\n\nDo"))
        self.assertTrue(Heartbeat.process_telegram(self.cfg))
        self.assertEqual(self.events, [])
        self.assertEqual(self.rewrites, [("ZZ Probe", "Do")])    # the rewrite still lands
        self.assertEqual(self.api.sent, [])
        self.assertEqual(self.pending_files(), [])
        self.assertIn("spawn deferred for telegram update 10", self.log_text())

    def test_a_hung_run_is_killed_and_the_trigger_launched(self):
        self.running = [(5151, Heartbeat.WATCHDOG_SECONDS + 1)]
        killed = []
        _patch(self, Heartbeat.os, "kill", lambda pid, sig: killed.append((pid, sig)))
        self.fake(_update(10, "ZZ Probe\n\nDo"))
        Heartbeat.process_telegram(self.cfg)
        self.assertEqual(killed, [(5151, 15)])
        self.assertEqual(self.events, ["confirm:10", "launch:ZZ Probe", "send:777"])

    def test_a_failed_launch_after_the_confirm_tells_the_user_to_resend(self):
        self.launch_error = OSError("python missing")
        self.fake(_update(10, "ZZ Probe\n\nDo"))
        with self.assertRaises(OSError):
            Heartbeat.process_telegram(self.cfg)
        self.assertEqual(self.events, ["confirm:10", "launch:ZZ Probe", "send:777"])
        self.assertIn("Could not start ZZ Probe", self.api.sent[0][1])
        self.assertIn("Resend", self.api.sent[0][1])
        self.assertEqual(self.pending_files(), [])

    def test_a_failure_before_the_confirm_leaves_the_trigger_pending_silently(self):
        def stalled(name):
            raise subprocess.TimeoutExpired("powershell", Heartbeat.WATCHDOG_QUERY_TIMEOUT)
        _patch(self, Heartbeat, "running_instances", stalled)
        self.fake(_update(10, "ZZ Probe\n\nDo"))
        with self.assertRaises(subprocess.TimeoutExpired):
            Heartbeat.process_telegram(self.cfg)
        self.assertEqual(self.events, [])         # not confirmed: the next tick retries
        self.assertEqual(self.api.sent, [])

    def test_a_non_text_message_is_consumed_with_a_hint_naming_its_kind(self):
        self.fake(_update(10, None, voice={"file_id": "x", "duration": 3}))
        self.assertTrue(Heartbeat.process_telegram(self.cfg))
        self.assertEqual(self.events, ["confirm:10", "send:777"])
        self.assertIn("I received a voice message", self.api.sent[0][1])
        self.assertIn("Send text", self.api.sent[0][1])
        self.assertEqual(self.launches, [])
        self.assertIn("a voice message in update 10 — consumed", self.log_text())

    def test_message_kinds(self):
        self.assertEqual(Heartbeat.telegram_message_kind({"photo": [{"file_id": "x"}]}), "a photo")
        self.assertEqual(Heartbeat.telegram_message_kind({"sticker": {}}), "a message without text")
        self.assertEqual(Heartbeat.telegram_message_kind({"sticker": {"file_id": "s"}}), "a sticker")
        self.assertEqual(Heartbeat.telegram_message_kind({"sticker": {"file_id": "s", "emoji": "👍"}}),
                         "a sticker (👍)")
        self.assertEqual(Heartbeat.telegram_message_kind({}), "a message without text")

    def test_a_sticker_does_not_use_up_the_pass(self):
        self.fake(_update(10, None, sticker={"file_id": "s", "emoji": "👍"}),
                  _update(11, "ZZ Probe\nDo"))
        self.assertTrue(Heartbeat.process_telegram(self.cfg))
        self.assertEqual(self.events, ["confirm:10", "send:777", "confirm:11",
                                       "launch:ZZ Probe", "send:777"])
        self.assertIn("a sticker (👍)", self.api.sent[0][1])
        self.assertIn("a sticker (👍) in update 10", self.log_text())

    def test_a_lone_sticker_is_still_activity(self):
        self.fake(_update(10, None, sticker={"file_id": "s"}))
        self.assertTrue(Heartbeat.process_telegram(self.cfg))
        self.assertEqual(self.events, ["confirm:10", "send:777"])

    def test_a_photo_caption_is_read_as_the_trigger(self):
        self.fake(_update(10, None, photo=[{"file_id": "x"}], caption="ZZ Probe\nDo"))
        Heartbeat.process_telegram(self.cfg)
        self.assertEqual(self.events, ["confirm:10", "launch:ZZ Probe", "send:777"])
        self.assertEqual(self.rewrites, [("ZZ Probe", "Do")])

    def test_one_trigger_per_pass_oldest_first(self):
        self.fake(_update(11, "Second\n\nb"), _update(10, "First\n\na"))
        Heartbeat.process_telegram(self.cfg)
        self.assertEqual([n for n, _ in self.launches], ["First"])
        self.assertEqual(self.events, ["confirm:10", "launch:First", "send:777"])
        self.assertEqual(self.api.offset, 11)      # update 11 is still pending

    def test_outcomes_are_delivered_before_the_poll(self):
        self.fake()
        Heartbeat.telegram_pending_write(3, 777, "ZZ", 1, self.root / "tg" / "results" / "3.json")
        (self.root / "tg" / "results").mkdir(parents=True)
        (self.root / "tg" / "results" / "3.json").write_text(
            json.dumps({"status": "completed", "error": "", "final_text": "Done."}),
            encoding="utf-8")
        self.assertTrue(Heartbeat.process_telegram(self.cfg))
        self.assertEqual(self.events, ["send:777"])
        self.assertIn("ZZ completed", self.api.sent[0][1])


class TelegramDeliverTests(_TelegramBase):
    def pend(self, uid=3, pid=1, started=None):
        result = self.root / "tg" / "results" / f"{uid}.json"
        Heartbeat.telegram_pending_write(uid, 777, "ZZ", pid, result)
        if started is not None:
            p = self.root / "tg" / "pending" / f"{uid}.json"
            rec = json.loads(p.read_text(encoding="utf-8"))
            rec["started"] = started
            p.write_text(json.dumps(rec), encoding="utf-8")
        return result

    def test_nothing_pending_sends_nothing(self):
        self.fake()
        self.assertEqual(Heartbeat.telegram_deliver_results(self.cfg), 0)

    def test_a_result_file_is_posted_and_both_files_removed(self):
        self.fake()
        result = self.pend()
        result.parent.mkdir(parents=True)
        result.write_text(json.dumps({"status": "completed", "error": "",
                                      "final_text": "Report:\n- a\n- b"}), encoding="utf-8")
        _patch(self, Heartbeat, "pid_alive", lambda pid: False)
        self.assertEqual(Heartbeat.telegram_deliver_results(self.cfg), 1)
        self.assertEqual(self.api.sent,
                         [(777, f"✅ ZZ completed on {Heartbeat.HOST}\n\nReport:\n- a\n- b")])
        self.assertEqual(self.pending_files(), [])
        self.assertFalse(result.exists())
        self.assertIn("sent result (completed) of 'ZZ' to chat 777", self.log_text())

    def test_a_dead_run_without_a_result_gets_the_no_result_note(self):
        self.fake()
        self.pend(pid=1)
        _patch(self, Heartbeat, "pid_alive", lambda pid: False)
        self.assertEqual(Heartbeat.telegram_deliver_results(self.cfg), 1)
        self.assertIn("ended without a result", self.api.sent[0][1])
        self.assertEqual(self.pending_files(), [])

    def test_a_live_young_run_waits(self):
        self.fake()
        self.pend(pid=1)
        _patch(self, Heartbeat, "pid_alive", lambda pid: True)
        self.assertEqual(Heartbeat.telegram_deliver_results(self.cfg), 0)
        self.assertEqual(self.api.sent, [])
        self.assertEqual(len(self.pending_files()), 1)

    def test_a_live_run_past_a_day_gets_the_timeout_note(self):
        self.fake()
        self.pend(pid=1, started=time.time() - Heartbeat.TELEGRAM_RESULT_WAIT_SECONDS - 5)
        _patch(self, Heartbeat, "pid_alive", lambda pid: True)
        self.assertEqual(Heartbeat.telegram_deliver_results(self.cfg), 1)
        self.assertIn("over 24 hours", self.api.sent[0][1])
        self.assertEqual(self.pending_files(), [])

    def test_a_half_written_result_of_a_live_run_waits(self):
        self.fake()
        result = self.pend(pid=1)
        result.parent.mkdir(parents=True)
        result.write_text('{"status": "comp', encoding="utf-8")
        _patch(self, Heartbeat, "pid_alive", lambda pid: True)
        self.assertEqual(Heartbeat.telegram_deliver_results(self.cfg), 0)
        _patch(self, Heartbeat, "pid_alive", lambda pid: False)
        self.assertEqual(Heartbeat.telegram_deliver_results(self.cfg), 1)
        self.assertIn("result file unreadable", self.api.sent[0][1])

    def test_an_unreadable_pending_record_is_dropped(self):
        self.fake()
        (self.root / "tg" / "pending").mkdir(parents=True)
        (self.root / "tg" / "pending" / "9.json").write_text("{", encoding="utf-8")
        self.assertEqual(Heartbeat.telegram_deliver_results(self.cfg), 0)
        self.assertEqual(self.pending_files(), [])
        self.assertIn("unreadable pending record 9.json", self.log_text())

    def test_pid_alive_on_this_process_and_a_dead_pid(self):
        self.assertTrue(Heartbeat.pid_alive(os.getpid()))
        self.assertFalse(Heartbeat.pid_alive(0))
        self.assertFalse(Heartbeat.pid_alive(None))
        self.assertFalse(Heartbeat.pid_alive("x"))


class HeartbeatMainTests(_TelegramBase):
    """main(): no Telegram setup is an error (nothing can reach the machine),
    an idle pass writes the one 'nothing found' line, a failure propagates
    to the entry block, which logs it."""

    def test_idle_writes_one_nothing_found_line(self):
        _patch(self, Heartbeat, "telegram_config", lambda: self.cfg)
        self.fake()
        Heartbeat.main()
        self.assertEqual(self.log_text().strip().split(" ", 2)[2], "checked — nothing found")

    def test_no_telegram_setup_is_an_error_and_polls_nothing(self):
        api = self.fake()
        with self.assertRaises(RuntimeError) as cm:
            Heartbeat.main()
        self.assertIn("--telegram-pair", str(cm.exception))
        self.assertEqual(api.calls, [])

    def test_a_trigger_runs_through_main(self):
        _patch(self, Heartbeat, "telegram_config", lambda: self.cfg)
        self.fake(_update(10, "ZZ Probe\n\nDo"))
        Heartbeat.main()
        self.assertEqual(self.events, ["confirm:10", "launch:ZZ Probe", "send:777"])
        self.assertNotIn("nothing found", self.log_text())

    def test_a_telegram_failure_propagates(self):
        _patch(self, Heartbeat, "telegram_config", lambda: self.cfg)

        def down(token, method, **params):
            raise RuntimeError("Telegram getUpdates: HTTP 502")
        _patch(self, Heartbeat, "telegram_api", down)
        with self.assertRaises(RuntimeError):
            Heartbeat.main()

    def test_a_bad_config_file_is_an_error(self):
        (self.root / "tg").mkdir()
        (self.root / "tg" / "config.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            Heartbeat.main()

    def test_the_module_needs_no_google_library(self):
        src = Path(Heartbeat.__file__).read_text(encoding="utf-8")
        for name in ("googleapiclient", "google.oauth2", "google.auth", "gmail_service",
                     "extract_body", "GMAIL_SCOPES"):
            self.assertNotIn(name, src)


class LaunchCommandTests(unittest.TestCase):
    def _command(self, **kwargs):
        seen = {}

        class _Proc:
            pid = 99

        def popen(cmd, **popen_kwargs):
            seen["cmd"] = cmd
            return _Proc()
        with mock.patch.object(Heartbeat.subprocess, "Popen", popen):
            self.assertEqual(Heartbeat.launch_instruction("Balance Westpac", **kwargs), 99)
        return seen["cmd"]

    def test_the_result_file_follows_headless_and_the_watchdog_still_matches(self):
        cmd = self._command(result_file=Path("C:/x/results/10.json"))
        self.assertEqual(cmd[1:], ["MyAgent.py", "-l", "Balance Westpac", "--headless",
                                   "--result-file", str(Path("C:/x/results/10.json"))])
        line = subprocess.list2cmdline(cmd)
        self.assertRegex(line, Heartbeat.watchdog_pattern("Balance Westpac"))

    def test_the_relay_folder_follows_too(self):
        cmd = self._command(result_file=Path("C:/x/results/10.json"),
                            relay_dir=Path("C:/x/relay/10"))
        self.assertEqual(cmd[-4:], ["--result-file", str(Path("C:/x/results/10.json")),
                                    "--relay-dir", str(Path("C:/x/relay/10"))])
        self.assertRegex(subprocess.list2cmdline(cmd), Heartbeat.watchdog_pattern("Balance Westpac"))

    def test_without_a_result_file_the_command_is_unchanged(self):
        cmd = self._command()
        self.assertEqual(cmd[1:], ["MyAgent.py", "-l", "Balance Westpac", "--headless"])

    def test_the_launched_popen_is_kept_for_the_listener_watcher(self):
        self._command()
        self.assertIn(99, Heartbeat.LAUNCHED)
        self.assertEqual(Heartbeat.LAUNCHED.pop(99).pid, 99)


class TelegramPairTests(_TelegramBase):
    def test_pairing_saves_the_token_records_the_first_chat_and_consumes_it(self):
        api = self.fake(_update(10, "hello", chat_id=777))
        with mock.patch("builtins.print"):
            self.assertEqual(Heartbeat.telegram_pair("TOKEN"), 0)
        self.assertEqual(Heartbeat.telegram_config(),
                         {"bot_token": "TOKEN", "allowed_chat_ids": [777]})
        self.assertEqual([m for m, _ in api.calls][:2], ["getMe", "getUpdates"])
        self.assertEqual(api.offset, 11)
        self.assertEqual(api.sent[0][0], 777)
        self.assertIn(f"Paired with {Heartbeat.HOST}", api.sent[0][1])
        self.assertIn("paired with chat 777 on bot @myagent_desk_bot", self.log_text())

    def test_pairing_keeps_an_earlier_chat_and_adds_the_new_one(self):
        Heartbeat.telegram_write_config({"bot_token": "OLD", "allowed_chat_ids": [1]})
        self.fake(_update(10, "hello", chat_id=777))
        with mock.patch("builtins.print"):
            Heartbeat.telegram_pair("TOKEN")
        self.assertEqual(Heartbeat.telegram_config(),
                         {"bot_token": "TOKEN", "allowed_chat_ids": [1, 777]})

    def test_nothing_arriving_gives_up_with_exit_1(self):
        _patch(self, Heartbeat, "TELEGRAM_PAIR_WAIT_SECONDS", 0)
        self.fake()
        with mock.patch("builtins.print"):
            self.assertEqual(Heartbeat.telegram_pair("TOKEN"), 1)
        self.assertEqual(Heartbeat.telegram_config()["allowed_chat_ids"], [])

    def test_test_command_messages_every_allowed_chat(self):
        Heartbeat.telegram_write_config({"bot_token": "TOKEN", "allowed_chat_ids": [777, 888]})
        api = self.fake()
        with mock.patch("builtins.print"):
            self.assertEqual(Heartbeat.telegram_test(), 0)
        self.assertEqual([c for c, _ in api.sent], [777, 888])
        self.assertIn("can reach you", api.sent[0][1])

    def test_test_command_without_setup_exits_1(self):
        with mock.patch("builtins.print"):
            self.assertEqual(Heartbeat.telegram_test(), 1)


class _FakeListenerApi(_FakeTelegram):
    """The fake over a long poll: each getUpdates with a timeout hands out
    the next scripted batch (a list, or an exception to raise); when the
    script runs out it raises KeyboardInterrupt, which stops the listener."""

    def __init__(self, events, batches):
        super().__init__(events, ())
        self.batches = list(batches)
        self.polls = 0

    def __call__(self, token, method, **params):
        if method == "getUpdates" and params.get("timeout"):
            self.calls.append((method, params))
            if not self.batches:
                raise KeyboardInterrupt
            self.polls += 1
            batch = self.batches.pop(0)
            if isinstance(batch, Exception):
                raise batch
            return [u for u in batch if u["update_id"] >= self.offset]
        return super().__call__(token, method, **params)


class ListenerTests(_TelegramBase):
    def setUp(self):
        super().setUp()
        _patch(self, Heartbeat, "TELEGRAM_LOCK_FILE", self.root / "tg" / "listener.lock")
        _patch(self, Heartbeat, "telegram_config", lambda: self.cfg)
        self.sleeps = []
        _patch(self, Heartbeat.time, "sleep", lambda secs: self.sleeps.append(secs))
        self.watched = []
        _patch(self, Heartbeat, "start_watcher",
               lambda cfg, pid, pending: self.watched.append((pid, pending.name)))

    def listen(self, *batches):
        self.api = _FakeListenerApi(self.events, batches)
        _patch(self, Heartbeat, "telegram_api", self.api)
        return Heartbeat.listen()

    def test_long_polls_with_the_timeout_and_stops_cleanly(self):
        self.assertEqual(self.listen([]), 0)
        method, params = self.api.calls[0]
        self.assertEqual((method, params["timeout"], params["allowed_updates"]),
                         ("getUpdates", Heartbeat.LONG_POLL_SECONDS, ["message"]))
        self.assertNotIn("offset", params)
        log = self.log_text()
        self.assertIn("listener started (PID", log)
        self.assertIn("listener stopped", log)
        self.assertFalse((self.root / "tg" / "listener.lock").exists())

    def test_every_trigger_of_a_batch_is_launched_and_watched(self):
        self.listen([_update(10, "First\na"), _update(11, "Second\nb")])
        self.assertEqual(self.events, ["confirm:10", "launch:First", "send:777",
                                       "confirm:11", "launch:Second", "send:777"])
        self.assertEqual(self.watched, [(4242, "10.json"), (4242, "11.json")])
        self.assertEqual(self.sleeps, [])

    def test_a_deferred_trigger_pauses_the_poll_and_holds_the_rest(self):
        self.running = [(5151, 60)]
        self.listen([_update(10, "ZZ Probe\na"), _update(11, "Other\nb")])
        self.assertEqual(self.events, [])                 # nothing confirmed, nothing launched
        self.assertEqual(self.sleeps, [Heartbeat.LISTEN_DEFER_RETRY_SECONDS])
        self.assertEqual(self.rewrites, [("ZZ Probe", "a")])

    def test_a_failing_poll_backs_off_and_the_next_one_goes_on(self):
        self.listen(RuntimeError("Telegram getUpdates: HTTP 502"), [_update(10, "ZZ Probe\na")])
        self.assertEqual(self.sleeps, [5])
        self.assertIn("ERROR listener RuntimeError: Telegram getUpdates: HTTP 502 — retrying in 5 s",
                      self.log_text())
        self.assertEqual(self.events, ["confirm:10", "launch:ZZ Probe", "send:777"])

    def test_repeats_of_one_error_grow_the_backoff_and_are_logged_once(self):
        errs = [RuntimeError("down") for _ in range(5)]
        self.listen(*errs)
        self.assertEqual(self.sleeps, [5, 10, 20, 40, 60])
        self.assertEqual(self.log_text().count("ERROR listener"), 1)

    def test_a_different_error_is_logged(self):
        self.listen(RuntimeError("down"), OSError("no route"))
        self.assertEqual(self.log_text().count("ERROR listener"), 2)

    def test_a_live_listener_keeps_a_second_one_out(self):
        (self.root / "tg").mkdir(parents=True)
        (self.root / "tg" / "listener.lock").write_text("424242", encoding="utf-8")
        _patch(self, Heartbeat, "pid_alive", lambda pid: pid == 424242)
        self.assertEqual(self.listen([]), 1)
        self.assertEqual(self.api.polls, 0)
        self.assertIn("listener already running", self.log_text())
        self.assertEqual((self.root / "tg" / "listener.lock").read_text(encoding="utf-8"), "424242")

    def test_a_stale_lock_is_taken_over(self):
        (self.root / "tg").mkdir(parents=True)
        (self.root / "tg" / "listener.lock").write_text("424242", encoding="utf-8")
        _patch(self, Heartbeat, "pid_alive", lambda pid: False)
        self.assertEqual(self.listen([]), 0)
        self.assertEqual(self.api.polls, 1)

    def test_runs_from_an_earlier_life_are_watched_at_start(self):
        Heartbeat.telegram_pending_write(3, 777, "ZZ", 31337, self.root / "tg" / "results" / "3.json")
        self.listen([])
        self.assertEqual(self.watched, [(31337, "3.json")])

    def test_no_setup_is_an_error(self):
        _patch(self, Heartbeat, "telegram_config", lambda: None)
        with self.assertRaises(RuntimeError):
            Heartbeat.listen()

    def test_the_alive_line(self):
        _patch(self, Heartbeat, "LISTEN_ALIVE_LINE_SECONDS", 0)
        self.listen([], [])
        self.assertEqual(self.log_text().count("listener alive"), 2)


class _FakeProc:
    def __init__(self, pid, hang=False):
        self.pid, self.hang, self.waited = pid, hang, []

    def wait(self, timeout=None):
        self.waited.append(timeout)
        if self.hang:
            raise subprocess.TimeoutExpired("MyAgent.py", timeout)


class WatchRunTests(_TelegramBase):
    def pend(self, pid, started=None):
        result = self.root / "tg" / "results" / "7.json"
        pending = Heartbeat.telegram_pending_write(7, 777, "ZZ", pid, result)
        if started is not None:
            rec = json.loads(pending.read_text(encoding="utf-8"))
            rec["started"] = started
            pending.write_text(json.dumps(rec), encoding="utf-8")
        return pending, result

    def test_the_outcome_is_posted_as_soon_as_the_child_exits(self):
        self.fake()
        pending, result = self.pend(5151)
        proc = _FakeProc(5151)
        Heartbeat.LAUNCHED[5151] = proc
        result.parent.mkdir(parents=True)
        result.write_text(json.dumps({"status": "completed", "error": "", "final_text": "Done."}),
                          encoding="utf-8")
        _patch(self, Heartbeat, "pid_alive", lambda pid: False)
        Heartbeat.watch_run(self.cfg, 5151, pending)
        self.assertEqual(len(proc.waited), 1)
        self.assertNotIn(5151, Heartbeat.LAUNCHED)
        self.assertIn("ZZ completed", self.api.sent[0][1])
        self.assertFalse(pending.exists())
        self.assertFalse(result.exists())

    def test_a_child_that_dies_without_a_result_gets_the_no_result_note(self):
        self.fake()
        pending, _result = self.pend(5151)
        Heartbeat.LAUNCHED[5151] = _FakeProc(5151)
        _patch(self, Heartbeat, "pid_alive", lambda pid: False)
        Heartbeat.watch_run(self.cfg, 5151, pending)
        self.assertIn("ended without a result", self.api.sent[0][1])

    def test_a_run_from_an_earlier_life_is_polled_by_pid(self):
        self.fake()
        pending, _result = self.pend(31337)
        alive = [True, True, False]
        _patch(self, Heartbeat, "pid_alive", lambda pid: alive.pop(0) if alive else False)
        sleeps = []
        _patch(self, Heartbeat.time, "sleep", lambda secs: sleeps.append(secs))
        Heartbeat.watch_run(self.cfg, 31337, pending)
        self.assertEqual(sleeps, [Heartbeat.WATCH_POLL_SECONDS] * 2)
        self.assertIn("ended without a result", self.api.sent[0][1])

    def test_a_run_parked_past_the_deadline_gets_the_timeout_note(self):
        self.fake()
        pending, _result = self.pend(5151, started=time.time() - Heartbeat.TELEGRAM_RESULT_WAIT_SECONDS - 5)
        Heartbeat.LAUNCHED[5151] = _FakeProc(5151, hang=True)
        _patch(self, Heartbeat, "pid_alive", lambda pid: True)
        Heartbeat.watch_run(self.cfg, 5151, pending)
        self.assertIn("over 24 hours", self.api.sent[0][1])
        self.assertFalse(pending.exists())

    def test_a_record_already_delivered_is_left_alone(self):
        self.fake()
        pending, _result = self.pend(5151)
        pending.unlink()
        Heartbeat.LAUNCHED[5151] = _FakeProc(5151)
        Heartbeat.watch_run(self.cfg, 5151, pending)
        self.assertEqual(self.api.sent, [])
        self.assertEqual(self.log_text(), "")


class RelayHandleTests(_TelegramBase):
    """telegram_handle_update while a run of the chat's is waiting on an
    Agent Request: the message is the reply, not a trigger."""

    def setUp(self):
        super().setUp()
        _patch(self, Heartbeat, "AWAITING", {})
        _patch(self, Heartbeat, "TELEGRAM_RELAY_DIR", self.root / "tg" / "relay")
        self.relay_dir = self.root / "tg" / "relay" / "10"
        self.relay_dir.mkdir(parents=True)

    def test_a_launch_gets_a_relay_folder_on_the_command_line_and_in_the_record(self):
        self.fake(_update(10, "ZZ Probe\nDo"))
        Heartbeat.process_telegram(self.cfg)
        self.assertEqual(self.relays, [self.root / "tg" / "relay" / "10"])
        record = json.loads(self.pending_files()[0].read_text(encoding="utf-8"))
        self.assertEqual(Path(record["relay_dir"]), self.root / "tg" / "relay" / "10")

    def test_the_next_message_answers_the_open_request(self):
        relay.write_request(self.relay_dir, 1, "Which month?")
        Heartbeat.AWAITING[777] = [(str(self.relay_dir), 1, "ZZ Probe")]
        self.fake(_update(20, "October please"))
        status, _ = Heartbeat.telegram_handle_update(self.cfg, _update(20, "October please"))
        self.assertEqual(status, "skipped")
        self.assertEqual(self.events, ["confirm:20", "send:777"])
        self.assertEqual(relay.read_reply(self.relay_dir, 1), "October please")
        self.assertIn("Sent to ZZ Probe", self.api.sent[0][1])
        self.assertEqual(self.launches, [])
        self.assertEqual(Heartbeat.AWAITING, {})
        self.assertIn("reply to 'ZZ Probe' request #1 from update 20", self.log_text())

    def test_a_reply_after_the_pc_answered_is_not_used(self):
        Heartbeat.AWAITING[777] = [(str(self.relay_dir), 1, "ZZ Probe")]
        self.fake()
        status, _ = Heartbeat.telegram_handle_update(self.cfg, _update(20, "too late"))
        self.assertEqual(status, "skipped")
        self.assertIn("already been answered at the PC", self.api.sent[0][1])
        self.assertIsNone(relay.read_reply(self.relay_dir, 1))

    def test_a_reply_to_an_old_request_is_not_used(self):
        relay.write_request(self.relay_dir, 2, "second question")
        Heartbeat.AWAITING[777] = [(str(self.relay_dir), 1, "ZZ Probe")]
        self.fake()
        Heartbeat.telegram_handle_update(self.cfg, _update(20, "stale"))
        self.assertIsNone(relay.read_reply(self.relay_dir, 2))
        self.assertIn("already been answered", self.api.sent[0][1])

    def test_the_oldest_request_is_answered_first_and_the_rest_wait(self):
        other = self.root / "tg" / "relay" / "11"
        other.mkdir()
        relay.write_request(self.relay_dir, 1, "q1")
        relay.write_request(other, 1, "q2")
        Heartbeat.AWAITING[777] = [(str(self.relay_dir), 1, "First"), (str(other), 1, "Second")]
        self.fake()
        Heartbeat.telegram_handle_update(self.cfg, _update(20, "a1"))
        self.assertEqual(relay.read_reply(self.relay_dir, 1), "a1")
        self.assertIsNone(relay.read_reply(other, 1))
        self.assertEqual(Heartbeat.AWAITING, {777: [(str(other), 1, "Second")]})

    def test_commands_and_media_are_not_taken_as_the_reply(self):
        Heartbeat.AWAITING[777] = [(str(self.relay_dir), 1, "ZZ Probe")]
        relay.write_request(self.relay_dir, 1, "q")
        self.fake()
        Heartbeat.telegram_handle_update(self.cfg, _update(20, "/help"))
        Heartbeat.telegram_handle_update(self.cfg, _update(21, None, sticker={"file_id": "s"}))
        self.assertIsNone(relay.read_reply(self.relay_dir, 1))
        self.assertEqual(len(Heartbeat.AWAITING[777]), 1)

    def test_another_chat_is_not_awaited(self):
        Heartbeat.AWAITING[888] = [(str(self.relay_dir), 1, "ZZ Probe")]
        self.cfg["allowed_chat_ids"] = [777, 888]
        self.fake()
        status, _ = Heartbeat.telegram_handle_update(self.cfg, _update(20, "ZZ Probe\nDo"))
        self.assertEqual(status, "launched")
        self.assertIsNone(relay.read_reply(self.relay_dir, 1))


class RelayTickTests(_TelegramBase):
    def setUp(self):
        super().setUp()
        _patch(self, Heartbeat, "AWAITING", {})
        self.relay_dir = self.root / "tg" / "relay" / "10"
        self.relay_dir.mkdir(parents=True)
        self.record = {"chat_id": 777, "instruction": "ZZ Probe"}
        self.fake()

    def test_an_open_request_is_posted_once_and_awaited(self):
        relay.write_request(self.relay_dir, 1, "Which month?")
        seq = Heartbeat.relay_tick(self.cfg, self.record, self.relay_dir, 0)
        self.assertEqual(seq, 1)
        self.assertEqual(self.api.sent, [(777, "❓ ZZ Probe asks:\nWhich month?\n\nReply here and it goes to the run.")])
        self.assertEqual(Heartbeat.AWAITING, {777: [(str(self.relay_dir), 1, "ZZ Probe")]})
        self.assertEqual(Heartbeat.relay_tick(self.cfg, self.record, self.relay_dir, seq), 1)
        self.assertEqual(len(self.api.sent), 1)                  # not posted again
        self.assertIn("relayed 'ZZ Probe' request #1 to chat 777", self.log_text())

    def test_a_later_request_is_posted_in_turn(self):
        relay.write_request(self.relay_dir, 1, "q1")
        seq = Heartbeat.relay_tick(self.cfg, self.record, self.relay_dir, 0)
        relay.clear(self.relay_dir)
        relay.write_request(self.relay_dir, 2, "q2")
        seq = Heartbeat.relay_tick(self.cfg, self.record, self.relay_dir, seq)
        self.assertEqual(seq, 2)
        self.assertEqual([t for _, t in self.api.sent][1], "❓ ZZ Probe asks:\nq2\n\nReply here and it goes to the run.")

    def test_a_request_answered_at_the_pc_is_withdrawn(self):
        relay.write_request(self.relay_dir, 1, "q1")
        Heartbeat.relay_tick(self.cfg, self.record, self.relay_dir, 0)
        relay.clear(self.relay_dir)
        relay.mark_answered(self.relay_dir, 1)
        Heartbeat.relay_tick(self.cfg, self.record, self.relay_dir, 1)
        self.assertIn("answered at the PC", self.api.sent[1][1])
        self.assertEqual(Heartbeat.AWAITING, {})
        self.assertFalse((self.relay_dir / relay.ANSWERED).exists())

    def test_a_pc_answer_after_the_phone_replied_withdraws_nothing(self):
        relay.mark_answered(self.relay_dir, 1)      # nothing awaited any more
        Heartbeat.relay_tick(self.cfg, self.record, self.relay_dir, 1)
        self.assertEqual(self.api.sent, [])

    def test_forget_drops_the_run_and_keeps_the_others(self):
        other = self.root / "tg" / "relay" / "11"
        Heartbeat.AWAITING[777] = [(str(self.relay_dir), 1, "A"), (str(other), 1, "B")]
        Heartbeat.relay_forget(777, self.relay_dir)
        self.assertEqual(Heartbeat.AWAITING, {777: [(str(other), 1, "B")]})
        Heartbeat.relay_forget(777, other)
        self.assertEqual(Heartbeat.AWAITING, {})


class WatchRunRelayTests(_TelegramBase):
    """watch_run with a relay folder: the request posted while the run lives,
    the awaiting entry dropped and the folder removed when it ends."""

    def setUp(self):
        super().setUp()
        _patch(self, Heartbeat, "AWAITING", {})
        self.relay_dir = self.root / "tg" / "relay" / "7"
        self.relay_dir.mkdir(parents=True)
        self.fake()

    def test_a_request_during_the_run_is_posted_and_the_folder_cleaned_at_the_end(self):
        result = self.root / "tg" / "results" / "7.json"
        pending = Heartbeat.telegram_pending_write(7, 777, "ZZ", 5151, result, self.relay_dir)
        ticks = []

        class _Proc:
            pid = 5151

            def wait(self_, timeout=None):
                ticks.append(timeout)
                if len(ticks) == 1:
                    relay.write_request(self.relay_dir, 1, "Which month?")
                    raise subprocess.TimeoutExpired("MyAgent.py", timeout)
                if len(ticks) == 2:
                    raise subprocess.TimeoutExpired("MyAgent.py", timeout)
                result.parent.mkdir(parents=True, exist_ok=True)
                result.write_text(json.dumps({"status": "completed", "error": "",
                                              "final_text": "Done"}), encoding="utf-8")
        Heartbeat.LAUNCHED[5151] = _Proc()
        _patch(self, Heartbeat, "pid_alive", lambda pid: False)
        Heartbeat.watch_run(self.cfg, 5151, pending)
        self.assertEqual(ticks, [Heartbeat.RELAY_POLL_SECONDS] * 3)
        texts = [t for _, t in self.api.sent]
        self.assertTrue(texts[0].startswith("❓ ZZ asks:\nWhich month?"))
        self.assertTrue(texts[-1].startswith("✅ ZZ completed"))
        self.assertEqual(Heartbeat.AWAITING, {})
        self.assertFalse(self.relay_dir.exists())
        self.assertFalse(pending.exists())

    def test_a_run_without_a_relay_folder_polls_at_the_slow_step(self):
        result = self.root / "tg" / "results" / "7.json"
        pending = Heartbeat.telegram_pending_write(7, 777, "ZZ", 5151, result)
        waited = []

        class _Proc:
            def wait(self_, timeout=None):
                waited.append(timeout)
        Heartbeat.LAUNCHED[5151] = _Proc()
        _patch(self, Heartbeat, "pid_alive", lambda pid: False)
        Heartbeat.watch_run(self.cfg, 5151, pending)
        self.assertEqual(waited, [Heartbeat.WATCH_POLL_SECONDS])


class TokenHygieneTests(unittest.TestCase):
    """The token travels only in the URL; no log line or error may carry it."""

    def test_an_http_error_names_the_method_not_the_url(self):
        def boom(req, timeout):
            raise urllib.error.HTTPError(
                req.full_url, 401, "Unauthorized", {},
                io.BytesIO(b'{"ok":false,"description":"Unauthorized"}'))
        with mock.patch.object(Heartbeat.urllib.request, "urlopen", boom):
            with self.assertRaises(RuntimeError) as cm:
                Heartbeat.telegram_api("SECRET", "getMe")
        self.assertNotIn("SECRET", str(cm.exception))
        self.assertIn("getMe", str(cm.exception))

    def test_a_not_ok_body_is_a_runtime_error_without_the_token(self):
        class _Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        body = b'{"ok":false,"description":"chat not found"}'
        with mock.patch.object(Heartbeat.urllib.request, "urlopen",
                               lambda req, timeout: _Resp(body)):
            with self.assertRaises(RuntimeError) as cm:
                Heartbeat.telegram_api("SECRET", "sendMessage", chat_id=1, text="x")
        self.assertEqual(str(cm.exception), "Telegram sendMessage: chat not found")

    def test_a_good_body_returns_the_result_and_posts_json(self):
        seen = {}

        class _Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def urlopen(req, timeout):
            seen["url"] = req.full_url
            seen["body"] = json.loads(req.data.decode("utf-8"))
            seen["timeout"] = timeout
            return _Resp(b'{"ok":true,"result":[{"update_id":1}]}')
        with mock.patch.object(Heartbeat.urllib.request, "urlopen", urlopen):
            out = Heartbeat.telegram_api("SECRET", "getUpdates", timeout=20, offset=5)
        self.assertEqual(out, [{"update_id": 1}])
        self.assertEqual(seen["url"], "https://api.telegram.org/botSECRET/getUpdates")
        self.assertEqual(seen["body"], {"timeout": 20, "offset": 5})
        self.assertEqual(seen["timeout"], Heartbeat.TELEGRAM_HTTP_TIMEOUT + 20)

    def test_no_log_line_in_the_module_interpolates_the_token(self):
        src = Path(Heartbeat.__file__).read_text(encoding="utf-8")
        for m in re.finditer(r"log\(f?\"[^\n]*", src):
            self.assertNotIn("{token", m.group(0))
            self.assertNotIn("bot_token", m.group(0))


if __name__ == "__main__":
    unittest.main()
