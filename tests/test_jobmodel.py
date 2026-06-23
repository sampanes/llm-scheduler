"""Unit tests for catcore.jobmodel (pure: identity, descriptions, next_fire,
construction, lookup). next_fire takes an injectable `now`, so everything here
is clock-proof and deterministic.
"""

import unittest
from datetime import datetime, timedelta

from catcore.config import TASK_FOLDER, DAY_ORDER, DEFAULT_SETTINGS
from catcore.jobmodel import (
    sanitize_name, task_name_for, describe_target, describe_schedule,
    next_fire, make_job, find_job, _parse_task_dt, once_run_info, job_status,
)

NOW = datetime(2026, 6, 17, 12, 0, 0)  # fixed reference instant


def once_job(dt_iso, delete_after_run=True):
    return {"schedule": {"type": "once", "datetime": dt_iso},
            "delete_after_run": delete_after_run}


class SanitizeName(unittest.TestCase):
    def test_strips_disallowed_and_spaces(self):
        self.assertEqual(sanitize_name("My Job!! (v2)"), "My-Job-v2")

    def test_empty_after_strip_falls_back(self):
        self.assertEqual(sanitize_name("$$$"), "job")
        self.assertEqual(sanitize_name("   "), "job")

    def test_caps_at_60(self):
        self.assertEqual(len(sanitize_name("a" * 200)), 60)


class TaskNameAndLookup(unittest.TestCase):
    def setUp(self):
        self.job = {"id": "abc12345", "name": "nightly"}

    def test_task_name_for(self):
        self.assertEqual(task_name_for(self.job), f"{TASK_FOLDER}\\nightly-abc12345")

    def test_find_job_by_id_name_and_taskname(self):
        jobs = [self.job]
        self.assertIs(find_job(jobs, "abc12345"), self.job)
        self.assertIs(find_job(jobs, "nightly"), self.job)
        self.assertIs(find_job(jobs, f"{TASK_FOLDER}\\nightly-abc12345"), self.job)

    def test_find_job_miss(self):
        self.assertIsNone(find_job([self.job], "nope"))


class Describe(unittest.TestCase):
    def test_describe_target_modes(self):
        base = {"dir": r"C:\repo"}
        self.assertEqual(
            describe_target({**base, "target": {"mode": "resume", "session_id": "5e158237-aaaa"}}),
            r"resume 5e158237… in C:\repo")
        self.assertEqual(
            describe_target({**base, "target": {"mode": "continue"}}),
            r"continue latest in C:\repo")
        self.assertEqual(
            describe_target({**base, "target": {"mode": "new"}}),
            r"new session in C:\repo")

    def test_describe_schedule_types(self):
        self.assertEqual(
            describe_schedule({"schedule": {"type": "once", "datetime": "2026-06-18T13:00"}}),
            "once @ 2026-06-18 13:00")
        self.assertEqual(
            describe_schedule({"schedule": {"type": "daily", "time": "06:45"}}),
            "daily @ 06:45")
        self.assertEqual(
            describe_schedule({"schedule": {"type": "weekly", "days": ["MON", "WED"], "time": "06:45"}}),
            "weekly MON,WED @ 06:45")


class NextFireOnce(unittest.TestCase):
    def test_future_returns_dt(self):
        future = (NOW + timedelta(days=1)).isoformat(timespec="minutes")
        self.assertEqual(next_fire(once_job(future), now=NOW),
                         datetime.fromisoformat(future))

    def test_past_within_catchup_window_still_fires(self):
        dt = (NOW - timedelta(hours=1)).isoformat(timespec="minutes")
        self.assertEqual(next_fire(once_job(dt), now=NOW),
                         datetime.fromisoformat(dt))

    def test_past_beyond_window_expires(self):
        window = DEFAULT_SETTINGS["missed_run_window_hours"]
        dt = (NOW - timedelta(hours=window + 1)).isoformat(timespec="minutes")
        self.assertIsNone(next_fire(once_job(dt, delete_after_run=True), now=NOW))

    def test_keep_flag_catches_up_regardless(self):
        dt = (NOW - timedelta(hours=500)).isoformat(timespec="minutes")
        self.assertEqual(next_fire(once_job(dt, delete_after_run=False), now=NOW),
                         datetime.fromisoformat(dt))


class NextFireDaily(unittest.TestCase):
    def test_time_later_today(self):
        self.assertEqual(next_fire({"schedule": {"type": "daily", "time": "13:00"}}, now=NOW),
                         NOW.replace(hour=13, minute=0))

    def test_time_passed_rolls_to_tomorrow(self):
        self.assertEqual(next_fire({"schedule": {"type": "daily", "time": "11:00"}}, now=NOW),
                         (NOW + timedelta(days=1)).replace(hour=11, minute=0))


class NextFireWeekly(unittest.TestCase):
    def test_tomorrow_weekday(self):
        tomorrow_day = DAY_ORDER[(NOW.weekday() + 1) % 7]
        nf = next_fire({"schedule": {"type": "weekly", "days": [tomorrow_day], "time": "09:00"}}, now=NOW)
        self.assertEqual(nf, (NOW + timedelta(days=1)).replace(hour=9, minute=0))

    def test_today_but_time_passed_rolls_a_week(self):
        today = DAY_ORDER[NOW.weekday()]
        nf = next_fire({"schedule": {"type": "weekly", "days": [today], "time": "09:00"}}, now=NOW)
        self.assertEqual(nf, (NOW + timedelta(days=7)).replace(hour=9, minute=0))

    def test_today_time_later(self):
        today = DAY_ORDER[NOW.weekday()]
        nf = next_fire({"schedule": {"type": "weekly", "days": [today], "time": "20:00"}}, now=NOW)
        self.assertEqual(nf, NOW.replace(hour=20, minute=0))


class MakeJob(unittest.TestCase):
    def test_shape_and_coercion(self):
        sched = {"type": "daily", "time": "06:45"}
        j = make_job("My Job!", r"C:\repo", "resume", "sid-123", sched,
                     "opus", "auto", "wezterm", "go", "--fork-session",
                     require_network=1, delete_after_run=0)
        self.assertEqual(len(j["id"]), 8)
        int(j["id"], 16)  # id is hex — raises if not
        self.assertEqual(j["name"], "My-Job")
        self.assertEqual(j["target"], {"mode": "resume", "session_id": "sid-123"})
        self.assertIs(j["require_network"], True)
        self.assertIs(j["delete_after_run"], False)
        self.assertEqual(j["schedule"], sched)
        self.assertIn("created_at", j)

    def test_effort_stored_when_set(self):
        sched = {"type": "daily", "time": "06:45"}
        j = make_job("j", r"C:\repo", "continue", "", sched, "opus", "auto",
                     "wezterm", "", "", require_network=True,
                     delete_after_run=True, effort="high")
        self.assertEqual(j["effort"], "high")

    def test_effort_defaults_to_empty_when_omitted(self):
        sched = {"type": "daily", "time": "06:45"}
        j = make_job("j", r"C:\repo", "continue", "", sched, "opus", "auto",
                     "wezterm", "", "", require_network=True,
                     delete_after_run=True)
        self.assertEqual(j["effort"], "")


SCHED = "2026-06-22T20:01"  # a one-shot's scheduled datetime


def once_with_q(last_run, last_result="0", delete_after_run=True, status="Ready"):
    job = {"schedule": {"type": "once", "datetime": SCHED},
           "delete_after_run": delete_after_run}
    q = {"status": status, "next_run": "N/A",
         "last_run": last_run, "last_result": last_result}
    return job, q


class ParseTaskDt(unittest.TestCase):
    def test_24h_format(self):
        self.assertEqual(_parse_task_dt("6/22/2026 20:01:00"),
                         datetime(2026, 6, 22, 20, 1, 0))

    def test_12h_ampm_format(self):
        self.assertEqual(_parse_task_dt("6/22/2026 8:01:00 PM"),
                         datetime(2026, 6, 22, 20, 1, 0))

    def test_never_run_sentinels_are_none(self):
        self.assertIsNone(_parse_task_dt("N/A"))
        self.assertIsNone(_parse_task_dt(""))
        self.assertIsNone(_parse_task_dt("11/30/1999 12:00:00 AM"))  # pre-2000

    def test_garbage_is_none(self):
        self.assertIsNone(_parse_task_dt("whenever"))


class OnceRunInfo(unittest.TestCase):
    def test_fired_success(self):
        job, q = once_with_q("6/22/2026 20:01:00", "0")
        info = once_run_info(job, q)
        self.assertIsNotNone(info)
        self.assertTrue(info["ok"])
        self.assertEqual(info["last_run"], datetime(2026, 6, 22, 20, 1, 0))

    def test_fired_failure_is_still_fired(self):
        job, q = once_with_q("6/22/2026 20:05:00", "1")
        info = once_run_info(job, q)
        self.assertIsNotNone(info)
        self.assertFalse(info["ok"])

    def test_never_ran_is_none(self):
        job, q = once_with_q("N/A")
        self.assertIsNone(once_run_info(job, q))

    def test_run_before_schedule_ignored(self):
        # a Last Run Time from an earlier task is not THIS one-shot's firing
        job, q = once_with_q("6/20/2026 09:00:00", "0")
        self.assertIsNone(once_run_info(job, q))

    def test_not_once_is_none(self):
        job = {"schedule": {"type": "daily", "time": "06:45"}}
        q = {"last_run": "6/22/2026 06:45:00", "last_result": "0"}
        self.assertIsNone(once_run_info(job, q))

    def test_no_task_info_is_none(self):
        job, _ = once_with_q("6/22/2026 20:01:00")
        self.assertIsNone(once_run_info(job, None))


class JobStatus(unittest.TestCase):
    def test_missing_when_no_task(self):
        job, _ = once_with_q("N/A")
        self.assertEqual(job_status(job, None), "MISSING")

    def test_fired_delete_after_run(self):
        job, q = once_with_q("6/22/2026 20:01:00", "0", delete_after_run=True)
        self.assertEqual(job_status(job, q), "Ran")

    def test_fired_kept(self):
        job, q = once_with_q("6/22/2026 20:01:00", "0", delete_after_run=False)
        self.assertEqual(job_status(job, q), "Ran (kept)")

    def test_label_is_ascii(self):  # CLI prints these to a cp1252 console
        job, q = once_with_q("6/22/2026 20:01:00", "0")
        job_status(job, q).encode("cp1252")  # raises if a non-ASCII glyph sneaks in

    def test_failed_shows_result_code(self):
        job, q = once_with_q("6/22/2026 20:01:00", "2147942402")
        self.assertEqual(job_status(job, q), "Failed (2147942402)")

    def test_not_yet_run_passes_raw_status(self):
        job, q = once_with_q("N/A", status="Ready")
        self.assertEqual(job_status(job, q), "Ready")

    def test_recurring_passes_raw_status(self):
        job = {"schedule": {"type": "daily", "time": "06:45"}}
        q = {"status": "Running", "last_run": "6/22/2026 06:45:00", "last_result": "0"}
        self.assertEqual(job_status(job, q), "Running")


if __name__ == "__main__":
    unittest.main()
