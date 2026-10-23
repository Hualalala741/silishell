from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_county_context import build_context, climate_summary


class CountyContextTests(unittest.TestCase):
    def setUp(self) -> None:
        parameters = {name: {} for name in ("PRECTOTCORR", "T2M_MAX", "T2M_MIN")}
        for year in range(2021, 2026):
            date = f"{year}0101"
            parameters["PRECTOTCORR"][date] = 60
            parameters["T2M_MAX"][date] = 36
            parameters["T2M_MIN"][date] = -1
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "power.json"
        self.path.write_text(
            json.dumps({"properties": {"parameter": parameters}}), encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_climate_summary_covers_each_year(self) -> None:
        summary = climate_summary(self.path)
        self.assertEqual([row["year"] for row in summary["annual_daily_metrics"]], list(range(2021, 2026)))
        self.assertEqual(summary["annual_daily_metrics"][0]["heavy_rain_days_ge_50mm"], 1)

    def test_context_keeps_source_urls_and_cost_values(self) -> None:
        context = build_context(self.path)
        self.assertEqual(context["labor"]["minimum_wage_yuan_month"], 2045)
        self.assertEqual(context["utilities"]["nonresidential_water_yuan_ton"], 2.35)
        self.assertTrue(context["labor"]["minimum_wage_source_url"].startswith("https://"))


if __name__ == "__main__":
    unittest.main()
