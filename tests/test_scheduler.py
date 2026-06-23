"""prune_jobs behaviour, with all schtasks side effects mocked.

Locks in the run-state-aware pruning: a fired one-shot is finished (dropped, and
its lingering Windows task cleaned) UNLESS it's a keep-the-task job; a vanished
task is dropped; a never-ran one-shot past its catch-up window is dropped; a
future one-shot is kept. No real schtasks calls — load/save/query/delete are
patched, so this is deterministic and offline.
"""

import unittest
from datetime import datetime, timedelta
from unittest import mock

from catcore import scheduler
from catcore.config import DEFAULT_SETTINGS

WINDOW = DEFAULT_SETTINGS["missed_run_window_hours"]
_TS = "%m/%d/%Y %H:%M:%S"  # schtasks Last Run Time format


def job(jid, sched_dt, delete_after_run=True):
    return {"id": jid, "name": jid, "task_name": f"ClaudeAt\\{jid}-{jid}",
            "schedule": {"type": "once",
                         "datetime": sched_dt.isoformat(timespec="minutes")},
            "delete_after_run": delete_after_run}


def q(status="Ready", last_run="N/A", last_result="267011"):
    return {"status": status, "next_run": "N/A",
            "last_run": last_run, "last_result": last_result}


class PruneJobs(unittest.TestCase):
    def _run(self, jobs, qall):
        saved = {}

        def fake_save(js):
            saved["jobs"] = js

        with mock.patch.object(scheduler, "load_jobs", return_value=jobs), \
             mock.patch.object(scheduler, "save_jobs", side_effect=fake_save), \
             mock.patch.object(scheduler, "task_query_all", return_value=qall), \
             mock.patch.object(scheduler, "task_delete") as td:
            dropped = scheduler.prune_jobs(verbose=False)
        return dropped, saved.get("jobs"), td

    def test_fired_oneshot_dropped_and_task_cleaned(self):
        now = datetime.now()
        sched = now - timedelta(hours=1)            # inside the catch-up window
        ran = (sched + timedelta(minutes=1)).strftime(_TS)
        j = job("fired", sched, delete_after_run=True)
        dropped, kept, td = self._run([j], {j["task_name"]: q(last_run=ran, last_result="0")})
        self.assertEqual([d["id"] for d in dropped], ["fired"])
        self.assertEqual(kept, [])
        td.assert_called_once_with(j["task_name"])   # lingering task cleaned

    def test_fired_keep_job_is_retained(self):
        now = datetime.now()
        sched = now - timedelta(hours=1)
        ran = (sched + timedelta(minutes=1)).strftime(_TS)
        j = job("kept", sched, delete_after_run=False)
        dropped, kept, td = self._run([j], {j["task_name"]: q(last_run=ran, last_result="0")})
        self.assertEqual(dropped, [])                # kept jobs survive prune
        td.assert_not_called()

    def test_vanished_task_dropped_no_delete(self):
        now = datetime.now()
        j = job("gone", now + timedelta(days=1))     # future, but task missing
        dropped, kept, td = self._run([j], {})       # not in query -> q is None
        self.assertEqual([d["id"] for d in dropped], ["gone"])
        td.assert_not_called()                       # nothing to clean

    def test_never_ran_past_window_dropped(self):
        now = datetime.now()
        sched = now - timedelta(hours=WINDOW + 1)    # past catch-up -> abandoned
        j = job("missed", sched, delete_after_run=True)
        dropped, kept, td = self._run([j], {j["task_name"]: q(last_run="N/A")})
        self.assertEqual([d["id"] for d in dropped], ["missed"])
        td.assert_called_once_with(j["task_name"])

    def test_future_oneshot_kept(self):
        now = datetime.now()
        j = job("future", now + timedelta(days=1))
        dropped, kept, td = self._run([j], {j["task_name"]: q()})
        self.assertEqual(dropped, [])
        td.assert_not_called()


if __name__ == "__main__":
    unittest.main()
