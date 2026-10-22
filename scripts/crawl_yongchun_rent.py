#!/usr/bin/env python3
"""Low-frequency crawler for public Yongchun rental listings on Anjuke.

The crawler reads list pages only. It does not log in, solve CAPTCHAs, visit
individual listing pages, or retry blocked requests.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup, Tag


BASE_URL = "https://yongc.zu.anjuke.com/"
ROBOTS_URL = "https://yongc.zu.anjuke.com/robots.txt"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
BLOCK_MARKERS = ("访问过于频繁", "验证码", "人机验证", "异常访问", "verifycode")
CSV_FIELDS = (
    "listing_id",
    "title",
    "rent_yuan_month",
    "area_sqm",
    "rent_yuan_sqm_month",
    "rooms",
    "halls",
    "community",
    "address",
    "detail_url",
    "source_page",
    "collected_at",
)


class CrawlBlocked(RuntimeError):
    """Raised when the website presents an anti-bot page."""


@dataclass(frozen=True)
class RentListing:
    listing_id: str
    title: str
    rent_yuan_month: int
    area_sqm: float | None
    rent_yuan_sqm_month: float | None
    rooms: int | None
    halls: int | None
    community: str
    address: str
    detail_url: str
    source_page: str
    collected_at: str


def clean_text(node: Tag | None) -> str:
    if node is None:
        return ""
    return " ".join(node.get_text(" ", strip=True).split())


def stable_url(raw_url: str) -> str:
    parts = urlsplit(raw_url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def parse_number(pattern: str, text: str, cast: type[int] | type[float]):
    match = re.search(pattern, text)
    return cast(match.group(1)) if match else None


def parse_listings(html: str, source_page: str, collected_at: str) -> list[RentListing]:
    if any(marker.lower() in html.lower() for marker in BLOCK_MARKERS):
        raise CrawlBlocked("站点返回了验证码或访问频率限制页面，已停止。")

    soup = BeautifulSoup(html, "html.parser")
    listings: list[RentListing] = []
    for card in soup.select("div.zu-itemmod"):
        title_node = card.select_one(".zu-info h3 a")
        price_node = card.select_one(".zu-side .price")
        address_node = card.select_one("address.details-item")
        details_node = card.select_one("p.details-item.tag")
        if not title_node or not price_node or not address_node:
            continue

        raw_url = str(card.get("link") or title_node.get("href") or "")
        detail_url = stable_url(raw_url)
        listing_id_match = re.search(r"/fangyuan/(\d+)", detail_url)
        price_match = re.search(r"\d+", clean_text(price_node).replace(",", ""))
        if not listing_id_match or not price_match:
            continue

        details = clean_text(details_node)
        area_sqm = parse_number(r"([\d.]+)\s*平米", details, float)
        rent = int(price_match.group())
        unit_rent = round(rent / area_sqm, 2) if area_sqm else None
        community_node = address_node.select_one("a")

        listings.append(
            RentListing(
                listing_id=listing_id_match.group(1),
                title=clean_text(title_node),
                rent_yuan_month=rent,
                area_sqm=area_sqm,
                rent_yuan_sqm_month=unit_rent,
                rooms=parse_number(r"(\d+)\s*室", details, int),
                halls=parse_number(r"(\d+)\s*厅", details, int),
                community=clean_text(community_node),
                address=clean_text(address_node),
                detail_url=detail_url,
                source_page=source_page,
                collected_at=collected_at,
            )
        )
    return listings


def page_url(page: int) -> str:
    return BASE_URL if page == 1 else f"{BASE_URL}fangyuan/p{page}/"


def check_robots(client: httpx.Client) -> None:
    response = client.get(ROBOTS_URL)
    response.raise_for_status()
    parser = RobotFileParser()
    parser.set_url(ROBOTS_URL)
    parser.parse(response.text.splitlines())
    if not parser.can_fetch(USER_AGENT, BASE_URL):
        raise RuntimeError(f"robots.txt 不允许访问 {BASE_URL}")


def crawl(pages: int, delay: float, jitter: float, timeout: float) -> list[RentListing]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept": "text/html,application/xhtml+xml",
    }
    collected_at = datetime.now().astimezone().isoformat(timespec="seconds")
    seen: dict[str, RentListing] = {}
    # Ignore environment proxy variables by default. This prevents a stale local
    # proxy setting from breaking the task; no proxy pool is used.
    with httpx.Client(
        headers=headers,
        follow_redirects=True,
        timeout=timeout,
        trust_env=False,
    ) as client:
        check_robots(client)
        for page in range(1, pages + 1):
            wait_seconds = delay + random.uniform(0, jitter)
            print(f"等待 {wait_seconds:.1f} 秒后请求第 {page} 页……", file=sys.stderr)
            time.sleep(wait_seconds)

            url = page_url(page)
            print(f"抓取 {url}", file=sys.stderr)
            response = client.get(url)
            response.raise_for_status()
            page_items = parse_listings(response.text, url, collected_at)
            if not page_items:
                raise RuntimeError(
                    f"第 {page} 页未解析到房源；页面结构可能已改变，已停止以避免空跑。"
                )
            for item in page_items:
                seen[item.listing_id] = item
            print(f"第 {page} 页解析 {len(page_items)} 条，累计去重后 {len(seen)} 条。", file=sys.stderr)
    return list(seen.values())


def write_outputs(listings: list[RentListing], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    json_path = output_dir / f"yongchun-rent-{stamp}.json"
    csv_path = output_dir / f"yongchun-rent-{stamp}.csv"
    rows = [asdict(item) for item in listings]
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return json_path, csv_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="低频抓取安居客永春县公开租房挂牌。")
    parser.add_argument("--pages", type=int, default=1, help="抓取页数，默认 1，最多 10。")
    parser.add_argument("--delay", type=float, default=8.0, help="翻页最小间隔秒数，默认 8。")
    parser.add_argument("--jitter", type=float, default=4.0, help="额外随机间隔秒数，默认 0–4。")
    parser.add_argument("--timeout", type=float, default=30.0, help="单次请求超时秒数。")
    parser.add_argument("--output-dir", type=Path, default=Path("output/rent"))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not 1 <= args.pages <= 10:
        raise SystemExit("--pages 必须在 1 到 10 之间。")
    if args.delay < 5:
        raise SystemExit("为控制访问频率，--delay 不能小于 5 秒。")
    if args.jitter < 0:
        raise SystemExit("--jitter 不能为负数。")

    try:
        listings = crawl(args.pages, args.delay, args.jitter, args.timeout)
        json_path, csv_path = write_outputs(listings, args.output_dir)
    except (httpx.HTTPError, CrawlBlocked, RuntimeError) as exc:
        print(f"抓取失败：{exc}", file=sys.stderr)
        return 1

    print(f"完成：{len(listings)} 条")
    print(f"JSON: {json_path}")
    print(f"CSV:  {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
