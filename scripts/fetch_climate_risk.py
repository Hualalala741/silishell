#!/usr/bin/env python3
"""Download the NASA POWER daily series used by county_context.json."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx


API_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从 NASA POWER 下载永春县中心点逐日气候数据")
    parser.add_argument("--longitude", type=float, default=118.12)
    parser.add_argument("--latitude", type=float, default=25.37)
    parser.add_argument("--start", default="20210101")
    parser.add_argument("--end", default="20251231")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/raw/nasa_power_yongchun_2021_2025.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    response = httpx.get(
        API_URL,
        params={
            "parameters": "PRECTOTCORR,T2M_MAX,T2M_MIN",
            "community": "AG",
            "longitude": args.longitude,
            "latitude": args.latitude,
            "start": args.start,
            "end": args.end,
            "format": "JSON",
        },
        timeout=90,
        follow_redirects=True,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("messages"):
        raise ValueError(f"NASA POWER 返回提示：{payload['messages']}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"已写入 {args.start}—{args.end} 逐日气候数据：{args.output}")


if __name__ == "__main__":
    main()
