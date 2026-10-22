from __future__ import annotations

import unittest
from datetime import date, datetime, timezone

from policy_crawler.web_api import scheduled_search_start


TODAY = date(2026, 10, 12)
PROFILE_START = date(2024, 10, 12)


class ScheduledSearchStartTest(unittest.TestCase):
    def test_uses_previous_schedule_run_instead_of_project_start(self):
        start = scheduled_search_start(
            {"last_run_at": datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc)},
            {"last_crawled_at": datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)},
            PROFILE_START,
            TODAY,
        )
        self.assertEqual(start, date(2026, 10, 5))

    def test_first_schedule_starts_at_last_successful_crawl(self):
        start = scheduled_search_start(
            {"last_run_at": None},
            {"last_crawled_at": "2026-10-10T16:00:00+08:00"},
            PROFILE_START,
            TODAY,
        )
        self.assertEqual(start, date(2026, 10, 10))

    def test_falls_back_to_saved_profile_start(self):
        start = scheduled_search_start({}, {}, PROFILE_START, TODAY)
        self.assertEqual(start, PROFILE_START)


if __name__ == "__main__":
    unittest.main()
