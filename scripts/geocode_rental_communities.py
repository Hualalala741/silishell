#!/usr/bin/env python3
"""Geocode unique rental communities with the AMap Web Service API."""

from __future__ import annotations

import argparse
import csv
import os
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv


GEOCODE_URL = "https://restapi.amap.com/v3/geocode/geo"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="为租房样本中的小区生成高德 GCJ-02 坐标")
    parser.add_argument("--rent-listings", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--city", default="泉州市")
    parser.add_argument("--delay", type=float, default=0.35)
    return parser.parse_args()


def unique_communities(path: Path) -> list[tuple[str, str]]:
    communities: dict[str, str] = {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            community = (row.get("community") or "").strip()
            if community:
                communities.setdefault(community, (row.get("address") or "").strip())
    return sorted(communities.items())


def main() -> None:
    args = parse_args()
    load_dotenv()
    key = os.getenv("AMAP_WEB_SERVICE_KEY", "").strip()
    if not key:
        raise RuntimeError("缺少 AMAP_WEB_SERVICE_KEY；请使用高德 Web 服务 Key，不要复用 JS API Key")

    results = []
    failures = []
    with httpx.Client(timeout=30, follow_redirects=True) as http:
        for index, (community, address) in enumerate(unique_communities(args.rent_listings)):
            if index:
                time.sleep(args.delay)
            query = address if community in address else f"福建省泉州市永春县{community}"
            response = http.get(
                GEOCODE_URL,
                params={"key": key, "address": query, "city": args.city},
            )
            response.raise_for_status()
            payload = response.json()
            geocodes = payload.get("geocodes") or []
            if payload.get("status") != "1" or not geocodes:
                failures.append(community)
                continue
            match = geocodes[0]
            longitude, latitude = match["location"].split(",", 1)
            results.append(
                {
                    "community": community,
                    "formatted_address": match.get("formatted_address") or query,
                    "longitude_gcj02": longitude,
                    "latitude_gcj02": latitude,
                    "geocode_level": match.get("level") or "",
                    "geocoder": "AMap Web Service Geocoding API",
                }
            )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "community",
        "formatted_address",
        "longitude_gcj02",
        "latitude_gcj02",
        "geocode_level",
        "geocoder",
    ]
    with args.output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)
    print(f"已写入 {len(results)} 个小区：{args.output}")
    if failures:
        raise RuntimeError("以下小区未定位，请手工复核：" + "、".join(failures))


if __name__ == "__main__":
    main()
