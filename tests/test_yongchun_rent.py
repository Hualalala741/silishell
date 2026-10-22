import unittest

from scripts.crawl_yongchun_rent import parse_listings


HTML = """
<div class="zu-itemmod clearfix" link="https://yongc.zu.anjuke.com/fangyuan/123?tracking=x">
  <div class="zu-info">
    <h3><a href="https://yongc.zu.anjuke.com/fangyuan/123?tracking=x">测试房源</a></h3>
    <p class="details-item tag">3室2厅<span>|</span>90平米<span>|</span>高层</p>
    <address class="details-item tag">
      <a>测试小区</a> 永春城区<span class="horiz-line">-</span>城区
      <span class="horiz-line">-</span>桃源南路1号
    </address>
  </div>
  <div class="zu-side"><strong class="price">1800</strong><span>元/月</span></div>
</div>
"""


class ParseListingsTests(unittest.TestCase):
    def test_parse_listing(self) -> None:
        result = parse_listings(
            HTML,
            "https://example.test/page",
            "2026-10-12T00:00:00+08:00",
        )

        self.assertEqual(len(result), 1)
        item = result[0]
        self.assertEqual(item.listing_id, "123")
        self.assertEqual(item.title, "测试房源")
        self.assertEqual(item.rent_yuan_month, 1800)
        self.assertEqual(item.area_sqm, 90)
        self.assertEqual(item.rent_yuan_sqm_month, 20)
        self.assertEqual(item.rooms, 3)
        self.assertEqual(item.halls, 2)
        self.assertEqual(item.community, "测试小区")
        self.assertEqual(item.address, "测试小区 永春城区 - 城区 - 桃源南路1号")
        self.assertEqual(item.detail_url, "https://yongc.zu.anjuke.com/fangyuan/123")

    def test_block_page_stops_crawl(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "验证码"):
            parse_listings(
                "请完成验证码",
                "https://example.test",
                "2026-10-12T00:00:00+08:00",
            )


if __name__ == "__main__":
    unittest.main()
