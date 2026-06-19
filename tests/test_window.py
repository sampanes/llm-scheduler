"""Unit tests for catcore.window — the 5-hour-window slot grid.

Focus: the pure build_slots() math (the +5h/+10h chaining the chips expose) and
window_slots()'s active / idle / fetch-error branches. The network call itself
(fetch_resets_at) is exercised only through a stubbed fetch so the tests are
offline and deterministic — no credentials, no usage API.
"""

import unittest
from datetime import datetime, timedelta, timezone

import catcore.window as window
from catcore.window import build_slots, window_slots, _next_cutoff


class BuildSlots(unittest.TestCase):
    def test_active_grid_is_open_plus_one_minute_then_five_hour_steps(self):
        anchor = datetime(2026, 6, 18, 19, 41)  # next window opens 19:41
        slots = build_slots(anchor, count=3, active=True)
        self.assertEqual([s["label"] for s in slots], ["Next window", "+5h", "+10h"])
        self.assertEqual([s["time"] for s in slots], ["19:42", "00:42", "05:42"])
        # the +5h/+10h crossings roll the date forward
        self.assertEqual([s["date"] for s in slots],
                         ["2026-06-18", "2026-06-19", "2026-06-19"])
        self.assertEqual([s["offset_h"] for s in slots], [0, 5, 10])

    def test_idle_slot_zero_is_open_now(self):
        anchor = datetime(2026, 6, 18, 9, 0)
        slots = build_slots(anchor, count=2, active=False)
        self.assertEqual(slots[0]["label"], "Open now")
        self.assertEqual(slots[1]["label"], "+5h")

    def test_seconds_round_up_to_next_whole_minute(self):
        # A reset at 19:41:30 must not yield a fire time that's already passed;
        # we ceil to 19:42, then +1 min -> 19:43.
        anchor = datetime(2026, 6, 18, 19, 41, 30)
        slots = build_slots(anchor, count=1, active=True)
        self.assertEqual(slots[0]["time"], "19:43")

    def test_iso_matches_date_and_time(self):
        anchor = datetime(2026, 6, 18, 19, 41)
        s = build_slots(anchor, count=1)[0]
        self.assertEqual(s["iso"], f"{s['date']}T{s['time']}")

    def test_count_controls_number_of_slots(self):
        self.assertEqual(len(build_slots(datetime(2026, 1, 1, 0, 0), count=4)), 4)

    def test_cutoff_bounds_the_chain(self):
        # Evening anchor; chain only through the next 04:30, so the 09:xx window
        # (past the cutoff) is dropped — exactly "don't span past next morning".
        anchor = datetime(2026, 6, 18, 18, 11)
        cutoff = datetime(2026, 6, 19, 4, 30)
        slots = build_slots(anchor, cutoff=cutoff, active=True)
        self.assertEqual([s["time"] for s in slots], ["18:12", "23:12", "04:12"])
        self.assertEqual([s["date"] for s in slots],
                         ["2026-06-18", "2026-06-18", "2026-06-19"])

    def test_slot_zero_kept_even_past_cutoff(self):
        # If the very next window opens after the cutoff, still offer it (slot 0
        # is the default pick) — but nothing beyond it.
        anchor = datetime(2026, 6, 19, 8, 0)
        cutoff = datetime(2026, 6, 19, 4, 30)
        slots = build_slots(anchor, cutoff=cutoff, active=True)
        self.assertEqual(len(slots), 1)
        self.assertEqual(slots[0]["label"], "Next window")


class NextCutoff(unittest.TestCase):
    def test_picks_tomorrow_when_already_past_today(self):
        self.assertEqual(_next_cutoff(datetime(2026, 6, 18, 18, 0), (4, 30)),
                         datetime(2026, 6, 19, 4, 30))

    def test_picks_today_when_still_ahead(self):
        self.assertEqual(_next_cutoff(datetime(2026, 6, 18, 1, 0), (4, 30)),
                         datetime(2026, 6, 18, 4, 30))

    def test_none_hhmm_disables_cutoff(self):
        self.assertIsNone(_next_cutoff(datetime(2026, 6, 18, 1, 0), None))


class WindowSlotsBranches(unittest.TestCase):
    def setUp(self):
        self._orig = window.fetch_resets_at
        self.addCleanup(lambda: setattr(window, "fetch_resets_at", self._orig))

    def _stub(self, resets_utc, active, error):
        window.fetch_resets_at = lambda creds_path=None: (resets_utc, active, error)

    def test_active_window_anchors_on_resets_at_local(self):
        # 18:41Z -> verify it converts to local and the first slot is +1 min.
        resets = datetime(2026, 6, 18, 18, 41, tzinfo=timezone.utc)
        self._stub(resets, True, None)
        res = window_slots(now=datetime(2026, 6, 18, 12, 0))
        self.assertTrue(res["ok"])
        self.assertTrue(res["active"])
        local = resets.astimezone().replace(tzinfo=None)
        expected = (local + timedelta(minutes=1)).strftime("%H:%M")
        self.assertEqual(res["slots"][0]["time"], expected)

    def test_idle_anchors_on_now(self):
        self._stub(None, False, None)
        now = datetime(2026, 6, 18, 9, 0)
        res = window_slots(now=now)
        self.assertTrue(res["ok"])
        self.assertFalse(res["active"])
        self.assertEqual(res["slots"][0]["label"], "Open now")
        self.assertEqual(res["slots"][0]["time"], "09:01")

    def test_fetch_error_returns_not_ok_with_no_slots(self):
        self._stub(None, False, "couldn't reach the usage API: boom")
        res = window_slots(now=datetime(2026, 6, 18, 9, 0))
        self.assertFalse(res["ok"])
        self.assertEqual(res["slots"], [])
        self.assertIn("usage API", res["error"])


if __name__ == "__main__":
    unittest.main()
