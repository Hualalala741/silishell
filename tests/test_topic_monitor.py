from __future__ import annotations

import unittest
from types import SimpleNamespace

from policy_crawler.topic_monitor import (
    _article_index,
    _normalized_url,
    _pair_candidates_with_results,
)


class CrawlResultMatchingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.first = {
            "document_id": "3315323",
            "source_url": "https://www.fjyc.gov.cn/a/t20260803_3315323.htm",
        }
        self.second = {
            "document_id": "3233732",
            "source_url": "https://www.fjyc.gov.cn/b/t20251124_3233732.htm",
        }

    def test_extracts_article_index(self) -> None:
        self.assertEqual(_article_index(self.first["source_url"]), "3315323")
        self.assertEqual(_article_index("https://example.com/index.html"), "")

    def test_normalizes_url(self) -> None:
        self.assertEqual(
            _normalized_url("HTTPS://WWW.FJYC.GOV.CN/a/page.htm?x=1#top"),
            "https://www.fjyc.gov.cn/a/page.htm",
        )

    def test_matches_results_returned_out_of_order(self) -> None:
        results = [
            SimpleNamespace(url=self.second["source_url"], redirected_url=None),
            SimpleNamespace(url=self.first["source_url"], redirected_url=None),
        ]
        pairs = _pair_candidates_with_results([self.first, self.second], results)
        self.assertEqual([candidate["document_id"] for candidate, _ in pairs], ["3315323", "3233732"])
        self.assertEqual([result.url for _, result in pairs], [self.first["source_url"], self.second["source_url"]])

    def test_uses_redirected_url_when_it_contains_the_article_index(self) -> None:
        result = SimpleNamespace(
            url="https://www.fjyc.gov.cn/redirect?id=3315323",
            redirected_url=self.first["source_url"],
        )
        pairs = _pair_candidates_with_results([self.first], [result])
        self.assertIs(pairs[0][1], result)

    def test_rejects_unmatched_result(self) -> None:
        result = SimpleNamespace(
            url="https://www.fjyc.gov.cn/a/t20260101_9999999.htm",
            redirected_url=None,
        )
        with self.assertRaisesRegex(RuntimeError, "无法匹配"):
            _pair_candidates_with_results([self.first], [result])


if __name__ == "__main__":
    unittest.main()
