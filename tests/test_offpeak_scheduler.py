"""Tests for the offpeak-scheduler skill helper."""
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".opencode/skills/offpeak-scheduler/scripts/offpeak.py"
spec = importlib.util.spec_from_file_location("offpeak", SCRIPT)
offpeak = importlib.util.module_from_spec(spec)
spec.loader.exec_module(offpeak)
WINDOWS = offpeak.DEFAULT_WINDOWS
DIRECT = "deepseek:deepseek-v4.1-flash"
UTC = timezone.utc

class SchedulerTests(unittest.TestCase):
    def invoke(self, *args, sleep_fn=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = offpeak.main(["--windows", str(WINDOWS), *args], sleep_fn=sleep_fn or (lambda _: None))
        return code, out.getvalue()

    def test_tuesday_midnight_phoenix_is_peak_and_next_0300(self):
        code, output = self.invoke("gate", "--target", DIRECT, "--now", "2026-11-10T07:00:00Z")
        data = json.loads(output)
        self.assertEqual(code, 3)
        self.assertEqual(data["next_offpeak_start_local"], "2026-11-10T03:00:00-07:00")
        self.assertEqual(data["next_offpeak_start_utc"], "2026-11-10T10:00:00Z")

    def test_monday_1900_phoenix_peak_next_2100(self):
        data = json.loads(self.invoke("status", "--target", DIRECT, "--now", "2026-11-10T02:00:00Z")[1])
        self.assertTrue(data["is_peak"])
        self.assertEqual(data["next_offpeak_start_local"], "2026-11-09T21:00:00-07:00")

    def test_saturday_window_merges_weekend(self):
        data = json.loads(self.invoke("status", "--target", DIRECT, "--now", "2026-11-07T19:00:00Z")[1])
        self.assertFalse(data["is_peak"])
        self.assertEqual(data["current_window_ends_local"], "2026-11-08T18:00:00-07:00")

    def test_friday_0200_phoenix_peak_next_0300(self):
        data = json.loads(self.invoke("status", "--target", DIRECT, "--now", "2026-11-06T09:00:00Z")[1])
        self.assertEqual(data["next_offpeak_start_local"], "2026-11-06T03:00:00-07:00")

    def test_estimate_defers_if_only_thirty_minutes_remain(self):
        code, output = self.invoke("gate", "--target", DIRECT, "--now", "2026-11-10T05:30:00Z", "--estimate-minutes", "60")
        data = json.loads(output)
        self.assertEqual(code, 3)
        self.assertEqual(data["reason"], "insufficient off-peak time for estimate")

    def test_go_not_gated_by_default(self):
        code, data = self.invoke("gate", "--target", "opencode:opencode-go/deepseek-v4.1-flash", "--now", "2026-11-10T07:00:00Z")
        self.assertEqual(code, 0)
        self.assertIn("opt-in", json.loads(data)["reason"])

    def test_go_gated_with_cli_opt_in(self):
        code, _ = self.invoke("gate", "--target", "opencode:opencode-go/deepseek-v4.1-flash", "--now", "2026-11-10T07:00:00Z", "--opt-in")
        self.assertEqual(code, 3)

    def test_go_gated_with_environment_opt_in(self):
        with patch.dict("os.environ", {"OFFPEAK_OPT_IN": "1"}):
            code, _ = self.invoke("gate", "--target", "opencode:opencode-go/deepseek-v4.1-flash", "--now", "2026-11-10T07:00:00Z")
        self.assertEqual(code, 3)

    def test_force_runs_and_records_reason(self):
        code, output = self.invoke("gate", "--target", DIRECT, "--now", "2026-11-10T07:00:00Z", "--force")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["reason"], "forced")

    def test_unmatched_never_gated(self):
        data = json.loads(self.invoke("status", "--target", "claude:opus", "--now", "2026-11-10T07:00:00Z")[1])
        self.assertFalse(data["gated"])

    def test_from_select_reads_canonical_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "selection.json"
            path.write_text(json.dumps({"canonical": DIRECT}))
            code, output = self.invoke("gate", "--target", "claude:opus", "--from-select", str(path), "--now", "2026-11-10T07:00:00Z")
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(output)["rule_id"], "deepseek-direct")

    def test_holiday_uses_shanghai_calendar_date(self):
        holiday = ROOT / "tests/fixtures/offpeak/holiday.json"
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = offpeak.main(["--windows", str(holiday), "status", "--target", DIRECT, "--now", "2026-11-10T02:00:00Z", "--tz", "America/Phoenix"])
        self.assertEqual(code, 0)
        self.assertFalse(json.loads(output.getvalue())["is_peak"])

    def test_defer_due_done_are_append_only(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.jsonl"
            plan = Path(temp) / "plan.json"
            plan.write_text(json.dumps({"units": [{"id": "u1", "target": DIRECT}]}))
            self.assertEqual(self.invoke("defer", "--plan", str(plan), "--unit", "u1", "--state", str(path), "--now", "2026-11-10T07:00:00Z")[0], 0)
            before = path.read_text().splitlines()
            code, due = self.invoke("due", "--state", str(path), "--now", "2026-11-10T10:00:00Z")
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(due)[0]["unit_id"], "u1")
            self.invoke("done", "--unit", "u1", "--state", str(path), "--now", "2026-11-10T10:05:00Z")
            self.assertEqual(len(path.read_text().splitlines()), len(before) + 1)
            self.assertEqual(json.loads(self.invoke("due", "--state", str(path), "--now", "2026-11-10T10:10:00Z")[1]), [])

    def test_due_excludes_current_peak(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.jsonl"
            offpeak.append_state(path, {"unit_id":"u", "target":DIRECT, "defer_until_utc":"2026-11-10T10:00:00Z", "created":"2026-11-10T07:00:00Z"})
            self.assertEqual(json.loads(self.invoke("due", "--state", str(path), "--now", "2026-11-10T07:30:00Z")[1]), [])

    def test_defer_refuses_non_gated_target(self):
        with tempfile.TemporaryDirectory() as temp:
            plan = Path(temp)/"plan.json"
            plan.write_text(json.dumps({"units":[{"id":"u", "target":"claude:opus"}]}))
            code = self.invoke("defer", "--plan", str(plan), "--unit", "u", "--state", str(Path(temp)/"state"), "--now", "2026-11-10T07:00:00Z")[0]
        self.assertEqual(code, 2)

    def test_wait_injected_sleep_reaches_window_start(self):
        chunks = []
        code = offpeak.main(["--windows", str(WINDOWS), "wait", "--target", DIRECT, "--now", "2026-11-10T07:00:00Z"], sleep_fn=chunks.append)
        self.assertEqual(code, 0)
        self.assertTrue(chunks)
        self.assertLessEqual(max(chunks), 600)
        self.assertAlmostEqual(sum(chunks), 3*3600)

    def test_wait_max_wait_exceeded_defers(self):
        code, output = self.invoke("wait", "--target", DIRECT, "--now", "2026-11-10T07:00:00Z", "--max-wait-hours", "2")
        self.assertEqual(code, 3)
        self.assertIn("2026-11-10T03:00", output)

    def test_schedule_hint_has_t3_local_and_utc_times(self):
        code, output = self.invoke("schedule-hint", "--target", DIRECT, "--now", "2026-11-10T07:00:00Z", "--host", "t3", "--prompt", "resume")
        self.assertEqual(code, 0)
        self.assertIn("2026-11-10T10:00:00Z", output)
        self.assertIn("2026-11-10T03:00:00-07:00", output)
        self.assertIn("resume", output)

    def test_cron_hint_has_five_fields(self):
        _, output = self.invoke("schedule-hint", "--target", DIRECT, "--now", "2026-11-10T07:00:00Z", "--host", "cron")
        self.assertEqual(len(output.split("#")[0].split()[:5]), 5)
        self.assertIn("python3 offpeak.py due", output)

    def test_shipped_windows_validate(self):
        self.assertEqual(len(offpeak.load_windows(WINDOWS)["rules"]), 3)

    def test_bad_time_fixture_fails(self):
        with self.assertRaises(offpeak.InputError):
            offpeak.load_windows(ROOT / "tests/fixtures/offpeak/bad-time.json")

    def test_equal_time_window_is_invalid_but_overnight_is_supported(self):
        with self.assertRaises(offpeak.InputError):
            offpeak.load_windows(ROOT / "tests/fixtures/offpeak/equal-time.json")
        rule = {"holidays":{"timezone":"UTC","dates":[]},"peak_windows_utc":[{"start":"23:00","end":"02:00","days":["mon"]}]}
        self.assertTrue(offpeak.is_peak(datetime(2026, 11, 3, 1, tzinfo=UTC), rule))

    def test_missing_sources_fixture_fails(self):
        with self.assertRaises(offpeak.InputError):
            offpeak.load_windows(ROOT / "tests/fixtures/offpeak/missing-sources.json")

    def test_phoenix_timezone_fallback(self):
        rule = offpeak.find_rule(DIRECT, offpeak.load_windows(WINDOWS)["rules"])
        original = offpeak.ZoneInfo
        def no_phoenix(name):
            if name == "America/Phoenix":
                raise Exception("no zone database")
            return original(name)
        with patch.object(offpeak, "ZoneInfo", side_effect=no_phoenix):
            zone = offpeak.get_zone("America/Phoenix")
            self.assertTrue(offpeak.is_peak(datetime(2026, 11, 10, 7, tzinfo=UTC), rule))
            self.assertEqual(datetime(2026, 11, 10, 7, tzinfo=UTC).astimezone(zone).hour, 0)

if __name__ == "__main__":
    unittest.main()
