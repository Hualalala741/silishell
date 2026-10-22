from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from policy_crawler.policy_portrait import (
    DocumentSource,
    extract_article_body,
    load_document_sources,
    split_sections,
    validate_and_complete_measures,
)


def markdown(title: str, body: str) -> str:
    return f"""# 文件头标题

- URL: https://example.com
- 发布时间: 2026.01.01

---

导航栏

{title}
时间：2026-01-01

{body}

扫一扫在手机上查看当前页面
页脚
"""


class DocumentLoadingTests(unittest.TestCase):
    def test_remaps_swapped_markdown_bodies_by_manifest_title(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = {
                "candidates": [
                    {"document_id": "1", "title": "政策甲", "source_url": "https://a"},
                    {"document_id": "2", "title": "政策乙", "source_url": "https://b"},
                ]
            }
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (root / "1.md").write_text(markdown("政策乙", "乙正文内容足够长。"), encoding="utf-8")
            (root / "2.md").write_text(markdown("政策甲", "甲正文内容足够长。"), encoding="utf-8")

            documents, warnings = load_document_sources(root)

            self.assertEqual([item.markdown_path.name for item in documents], ["2.md", "1.md"])
            self.assertEqual(len(warnings), 2)
            self.assertIn("甲正文", documents[0].body)

    def test_extracts_only_article_body(self) -> None:
        body = extract_article_body(markdown("政策甲", "有效正文。"), "政策甲")
        self.assertEqual(body, "有效正文。")


class MeasureValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = DocumentSource(
            document_id="1",
            title="测试政策",
            source_url="https://example.com/1",
            published_at="2026-01-01",
            source_type="政策文件",
            publisher="测试局",
            document_number="测试〔2026〕1号",
            markdown_path=Path("1.md"),
            body="**一、措施**\n争取补助资金153万元，用于支持设备购置。",
            remapped=False,
        )

    def test_splits_document_into_traceable_sections(self) -> None:
        sections = split_sections(self.document)
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0].section_id, "1_S001")
        self.assertIn("一、措施", sections[0].section_path)

    def test_accepts_status_on_measure_and_not_quantity(self) -> None:
        sections = [
            {
                "section_id": "1_S001",
                "document_id": "1",
                "section_path": ["测试政策", "一、措施"],
                "text": "争取补助资金153万元，用于支持设备购置。",
            }
        ]
        raw = {
            "measures": [
                {
                    "section_id": "1_S001",
                    "industry_segments": ["笋竹精深加工"],
                    "measure_text": "争取153万元补助支持设备购置",
                    "evidence_text": "争取补助资金153万元，用于支持设备购置。",
                    "record_type": "policy_action",
                    "implementation_stage": "requested",
                    "policy_tool_type_id": 1,
                    "secondary_policy_tool_type_id": None,
                    "policy_target": [],
                    "implementing_agencies": [],
                    "time_scope": None,
                    "start_date": None,
                    "end_date": None,
                    "quantities": [
                        {
                            "metric": "补助资金",
                            "raw_text": "153万元",
                            "value": 153,
                            "unit": "万元",
                            "dimension": "金额",
                            "normalized_value": 1530000,
                            "normalized_unit": "元",
                            "value_status": "requested",
                        }
                    ],
                }
            ]
        }

        measures, warnings = validate_and_complete_measures(
            raw, self.document, sections, "竹产业", "福建省泉州市永春县"
        )

        self.assertFalse(warnings)
        self.assertEqual(measures[0]["implementation_stage"], "requested")
        self.assertNotIn("value_status", measures[0]["quantities"][0])
        self.assertEqual(measures[0]["quantities"][0]["normalized_value"], 1530000)

    def test_downgrades_disbursed_without_completed_payment_signal(self) -> None:
        sections = [
            {
                "section_id": "1_S001",
                "document_id": "1",
                "section_path": ["测试政策"],
                "text": "县林业局参照直达资金管理拨付补助资金。",
            }
        ]
        raw = {
            "measures": [
                {
                    "section_id": "1_S001",
                    "industry_segments": ["竹林经营与生态保护"],
                    "measure_text": "县林业局拨付补助资金",
                    "evidence_text": "县林业局参照直达资金管理拨付补助资金。",
                    "record_type": "result",
                    "implementation_stage": "disbursed",
                    "policy_tool_type_id": 1,
                    "quantities": [],
                }
            ]
        }

        measures, warnings = validate_and_complete_measures(
            raw, self.document, sections, "竹产业", "福建省泉州市永春县"
        )

        self.assertEqual(measures[0]["implementation_stage"], "planned")
        self.assertTrue(any("缺少明确原文信号" in warning for warning in warnings))

    def test_does_not_treat_bamboo_breakdown_site_as_fund_allocation(self) -> None:
        sections = [
            {
                "section_id": "1_S001",
                "document_id": "1",
                "section_path": ["测试政策"],
                "text": "投资1亿元建设2万亩丰产竹林示范区，配套5个竹山分解点。",
            }
        ]
        raw = {
            "measures": [
                {
                    "section_id": "1_S001",
                    "industry_segments": ["竹种与竹林资源培育"],
                    "measure_text": "投资建设丰产竹林示范区",
                    "evidence_text": "投资1亿元建设2万亩丰产竹林示范区，配套5个竹山分解点。",
                    "record_type": "policy_action",
                    "implementation_stage": "allocated",
                    "policy_tool_type_id": 1,
                    "quantities": [],
                }
            ]
        }

        measures, warnings = validate_and_complete_measures(
            raw, self.document, sections, "竹产业", "福建省泉州市永春县"
        )

        self.assertEqual(measures[0]["implementation_stage"], "unknown")
        self.assertTrue(any("allocated 缺少明确原文信号" in warning for warning in warnings))

    def test_corrects_section_id_and_expands_evidence_before_stage_validation(self) -> None:
        sections = [
            {
                "section_id": "1_S001",
                "document_id": "1",
                "section_path": ["测试政策"],
                "text": "其他无关内容足够长，不包含待核验的证据。",
            },
            {
                "section_id": "1_S002",
                "document_id": "1",
                "section_path": ["测试政策", "资金分解"],
                "text": "现将天然竹林地管护补助81981元进行分解。请按要求执行。",
            },
        ]
        raw = {
            "measures": [
                {
                    "section_id": "1_S001",
                    "industry_segments": ["竹林经营与生态保护"],
                    "measure_text": "分解天然竹林地管护补助",
                    "evidence_text": "天然竹林地管护补助81981元",
                    "record_type": "implementation",
                    "implementation_stage": "allocated",
                    "policy_tool_type_id": 1,
                    "quantities": [],
                }
            ]
        }

        measures, warnings = validate_and_complete_measures(
            raw, self.document, sections, "竹产业", "福建省泉州市永春县"
        )

        self.assertEqual(measures[0]["section_id"], "1_S002")
        self.assertEqual(measures[0]["implementation_stage"], "allocated")
        self.assertEqual(
            measures[0]["evidence_text"], "现将天然竹林地管护补助81981元进行分解。"
        )
        self.assertTrue(any("section_id 已纠正" in warning for warning in warnings))

    def test_recalculates_known_units_and_rejects_quantity_outside_evidence(self) -> None:
        sections = [
            {
                "section_id": "1_S001",
                "document_id": "1",
                "section_path": ["测试政策"],
                "text": "建设丰产竹林示范基地1.5万亩。",
            }
        ]
        raw = {
            "measures": [
                {
                    "section_id": "1_S001",
                    "industry_segments": ["竹种与竹林资源培育"],
                    "measure_text": "建设丰产竹林示范基地",
                    "evidence_text": "建设丰产竹林示范基地1.5万亩。",
                    "record_type": "target",
                    "implementation_stage": "planned",
                    "policy_tool_type_id": 9,
                    "quantities": [
                        {
                            "metric": "面积",
                            "raw_text": "1.5万亩",
                            "value": 1.5,
                            "unit": "万亩",
                            "dimension": "面积",
                            "normalized_value": 10000,
                            "normalized_unit": "平方米",
                        },
                        {
                            "metric": "不存在的资金",
                            "raw_text": "200万元",
                            "value": 200,
                            "unit": "万元",
                            "dimension": "金额",
                        },
                    ],
                }
            ]
        }

        measures, warnings = validate_and_complete_measures(
            raw, self.document, sections, "竹产业", "福建省泉州市永春县"
        )

        self.assertEqual(len(measures[0]["quantities"]), 1)
        self.assertEqual(measures[0]["quantities"][0]["normalized_value"], 15000)
        self.assertEqual(measures[0]["quantities"][0]["normalized_unit"], "亩")
        self.assertTrue(any("raw_text 不在 evidence_text" in warning for warning in warnings))


if __name__ == "__main__":
    unittest.main()
