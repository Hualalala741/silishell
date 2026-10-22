from __future__ import annotations

import unittest

import numpy as np

from policy_crawler.site_candidates import (
    build_abandonment_mask,
    build_stable_natural_resource_mask,
)
from policy_crawler.site_selection import (
    compare_plan_mentions,
    filter_candidates,
    normalize_weights,
    rank_candidates,
)


class CandidateMaskTests(unittest.TestCase):
    def test_abandonment_requires_historical_crop_and_persistent_proxy(self):
        historical = np.array([[5, 5], [1, 5]])
        recent = [np.array([[8, 8], [8, 11]]), np.array([[11, 5], [8, 8]])]
        result = build_abandonment_mask(
            historical, recent, np.ones((2, 2), dtype=bool), [5], [8, 11]
        )
        np.testing.assert_array_equal(result, np.array([[True, False], [False, True]]))

    def test_natural_resource_requires_every_year(self):
        layers = [np.array([[8, 11], [8, 5]]), np.array([[11, 11], [5, 8]])]
        result = build_stable_natural_resource_mask(
            layers, np.ones((2, 2), dtype=bool), [8, 11]
        )
        np.testing.assert_array_equal(result, np.array([[True, True], [False, False]]))


class SiteSelectionRankingTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {
                "land_id": "A",
                "area_mu": 60,
                "protected_overlap": None,
                "climate_pass": None,
                "soil_pass": None,
                "cost_score": 90,
                "market_score": 10,
                "risk_score": 50,
            },
            {
                "land_id": "B",
                "area_mu": 70,
                "protected_overlap": False,
                "climate_pass": True,
                "soil_pass": True,
                "cost_score": 20,
                "market_score": 90,
                "risk_score": 50,
            },
        ]

    def test_unknown_hard_constraint_is_not_rejected(self):
        self.assertEqual(len(filter_candidates(self.rows, 50)), 2)

    def test_zero_weights_become_equal_weights(self):
        self.assertEqual(normalize_weights({}), {key: 0.25 for key in ("cost", "market", "risk", "policy")})

    def test_ranking_uses_backend_weights_and_policy_score(self):
        ranked = rank_candidates(
            self.rows, {"cost": 100, "market": 0, "risk": 0, "policy": 0}, 33.3
        )
        self.assertEqual([row["land_id"] for row in ranked], ["A", "B"])
        self.assertEqual(ranked[0]["totalScore"], 90)
        self.assertEqual(ranked[0]["policy_score"], 33.3)

    def test_plan_comparison_preserves_original_levels(self):
        profiles = [
            {"industry": "竹产业", "industry_aliases": ["竹子"], "plan_period": "2021-2025"},
            {"industry": "竹产业", "industry_aliases": [], "plan_period": "2026-2030"},
        ]
        result = compare_plan_mentions("竹子", profiles)
        self.assertEqual(result["level"], 2)
        self.assertEqual(result["score"], 66.7)


if __name__ == "__main__":
    unittest.main()
