"""Unit tests for catcore.config persistence (settings + jobs).

Focus on the soundness fixes:
  * _atomic_write_text writes via .tmp + os.replace (no torn target, no stray .tmp);
  * load_jobs degrades to [] on every bad-input shape, including a top-level JSON
    list where ["jobs"] raises TypeError (the case the old except tuple missed).

The module's JOBS_FILE / SETTINGS_FILE are repo-root constants, so each test
redirects them at a temp dir rather than touching the real files.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from catcore import config


class AtomicWriteText(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def test_round_trips(self):
        p = self.dir / "x.json"
        config._atomic_write_text(p, '{"a":1}')
        self.assertEqual(p.read_text(encoding="utf-8"), '{"a":1}')

    def test_leaves_no_tmp_behind(self):
        p = self.dir / "x.json"
        config._atomic_write_text(p, "hello")
        self.assertEqual([f.name for f in self.dir.iterdir()], ["x.json"])

    def test_overwrites_existing(self):
        p = self.dir / "x.json"
        p.write_text("old contents that are longer", encoding="utf-8")
        config._atomic_write_text(p, "new")
        self.assertEqual(p.read_text(encoding="utf-8"), "new")


class LoadJobs(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.jobs = self.dir / "jobs.json"
        self.p = mock.patch.object(config, "JOBS_FILE", self.jobs)
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def test_missing_file_returns_empty(self):
        self.assertEqual(config.load_jobs(), [])

    def test_valid_wrapper_returns_list(self):
        self.jobs.write_text(json.dumps({"jobs": [{"id": "a"}]}), encoding="utf-8")
        self.assertEqual(config.load_jobs(), [{"id": "a"}])

    def test_top_level_list_returns_empty(self):
        # ["jobs"] on a list raises TypeError, not KeyError — the fix added
        # TypeError to the except tuple so this degrades to [] instead of crashing.
        self.jobs.write_text(json.dumps([{"id": "a"}]), encoding="utf-8")
        self.assertEqual(config.load_jobs(), [])

    def test_missing_jobs_key_returns_empty(self):
        self.jobs.write_text(json.dumps({"other": 1}), encoding="utf-8")
        self.assertEqual(config.load_jobs(), [])

    def test_malformed_json_returns_empty(self):
        self.jobs.write_text("{not json", encoding="utf-8")
        self.assertEqual(config.load_jobs(), [])


class SaveLoadRoundTrip(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.pj = mock.patch.object(config, "JOBS_FILE", self.dir / "jobs.json")
        self.ps = mock.patch.object(config, "SETTINGS_FILE", self.dir / "settings.json")
        self.pj.start()
        self.ps.start()

    def tearDown(self):
        self.pj.stop()
        self.ps.stop()

    def test_jobs_round_trip(self):
        jobs = [{"id": "1", "name": "x"}, {"id": "2", "name": "y"}]
        config.save_jobs(jobs)
        self.assertEqual(config.load_jobs(), jobs)

    def test_settings_merge_overrides_defaults(self):
        config.save_settings({"model": "sonnet"})
        s = config.load_settings()
        self.assertEqual(s["model"], "sonnet")        # override applied
        self.assertEqual(s["terminal"], "")           # default preserved ("" = auto)

    def test_bad_settings_falls_back_to_defaults(self):
        (self.dir / "settings.json").write_text("{bad", encoding="utf-8")
        s = config.load_settings()
        self.assertEqual(s["model"], config.DEFAULT_SETTINGS["model"])


class EffortConstants(unittest.TestCase):
    def test_effort_levels(self):
        self.assertEqual(config.EFFORT_LEVELS,
                         ["low", "medium", "high", "xhigh", "max"])

    def test_effort_default_is_inherit(self):
        # "" => emit no --effort flag => claude inherits its own effortLevel
        self.assertEqual(config.DEFAULT_SETTINGS["effort"], "")


if __name__ == "__main__":
    unittest.main()
