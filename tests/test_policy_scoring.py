from __future__ import annotations

import unittest

from policy_crawler.policy_scoring import (
    aggregate_policy_scores,
    score_and_deduplicate_measures,
)


TOOLS = {index: (f"T{index}", f"工具{index}") for index in range(1, 11)}


def measure(measure_id: str, **overrides):
    row = {
        "measure_id": measure_id,
        "document_id": "D1",
        "policy_tool_type_id": 1,
        "implementation_stage": "requested",
        "measure_text": "争取补助资金153万元",
        "evidence_text": "2026年由县林业局争取补助资金153万元。",
        "quantities": [
            {
                "dimension": "金额",
                "normalized_value": 1530000,
                "normalized_unit": "元",
            }
        ],
        "time_scope": "2026年",
        "start_date": None,
        "end_date": None,
        "policy_target": ["竹产业企业"],
        "implementing_agencies": ["县林业局"],
    }
    row.update(overrides)
    return row


class PolicyScoringTests(unittest.TestCase):
    def test_scores_quantified_measure_with_complete_elements(self) -> None:
        rows = score_and_deduplicate_measures([measure("M1")])
        self.assertEqual(rows[0]["strength_score"], 4)
        self.assertEqual(rows[0]["specificity_score"], 5)
        self.assertEqual(rows[0]["design_score"], 20)
        self.assertEqual(rows[0]["design_score_100"], 80)

    def test_marks_exact_policy_duplicate_without_deleting_it(self) -> None:
        rows = score_and_deduplicate_measures(
            [measure("M1"), measure("M2", evidence_text="另一处重复表述。")]
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(item["is_primary"] for item in rows), 1)
        self.assertEqual(len({item["dedup_group_id"] for item in rows}), 1)

    def test_keeps_annual_target_separate_from_multi_year_target(self) -> None:
        rows = score_and_deduplicate_measures(
            [measure("M1"), measure("M2", time_scope="2026-2030年")]
        )
        self.assertTrue(all(item["is_primary"] for item in rows))

    def test_aggregation_excludes_duplicates_and_uses_top_three_average(self) -> None:
        rows = score_and_deduplicate_measures(
            [
                measure("M1"),
                measure("M2", evidence_text="重复证据。"),
                measure(
                    "M3",
                    measure_text="明确提供财政支持",
                    quantities=[],
                    time_scope=None,
                ),
            ]
        )
        result = aggregate_policy_scores(rows, TOOLS)
        fiscal = result["dimensions"][0]
        self.assertEqual(fiscal["primary_measure_count"], 2)
        self.assertEqual(fiscal["duplicate_measure_count"], 1)
        self.assertEqual(fiscal["dimension_score_100"], 56)


if __name__ == "__main__":
    unittest.main()
