from __future__ import annotations

import argparse
import math
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from policy_crawler.site_selection_storage import SiteSelectionRepository

load_dotenv()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="聚合租房样本并发布小区租金到 ADS。")
    parser.add_argument("--rent-listings", type=Path, required=True)
    parser.add_argument("--geocodes", type=Path, required=True)
    return parser.parse_args()


def _transform_latitude(longitude: float, latitude: float) -> float:
    value = -100 + 2 * longitude + 3 * latitude + 0.2 * latitude**2
    value += 0.1 * longitude * latitude + 0.2 * math.sqrt(abs(longitude))
    value += (20 * math.sin(6 * longitude * math.pi) + 20 * math.sin(2 * longitude * math.pi)) * 2 / 3
    value += (20 * math.sin(latitude * math.pi) + 40 * math.sin(latitude / 3 * math.pi)) * 2 / 3
    value += (160 * math.sin(latitude / 12 * math.pi) + 320 * math.sin(latitude * math.pi / 30)) * 2 / 3
    return value


def _transform_longitude(longitude: float, latitude: float) -> float:
    value = 300 + longitude + 2 * latitude + 0.1 * longitude**2
    value += 0.1 * longitude * latitude + 0.1 * math.sqrt(abs(longitude))
    value += (20 * math.sin(6 * longitude * math.pi) + 20 * math.sin(2 * longitude * math.pi)) * 2 / 3
    value += (20 * math.sin(longitude * math.pi) + 40 * math.sin(longitude / 3 * math.pi)) * 2 / 3
    value += (150 * math.sin(longitude / 12 * math.pi) + 300 * math.sin(longitude / 30 * math.pi)) * 2 / 3
    return value


def gcj02_to_wgs84(longitude: float, latitude: float) -> tuple[float, float]:
    earth_a = 6378245.0
    earth_ee = 0.006693421622965943
    lat_offset = _transform_latitude(longitude - 105, latitude - 35)
    lon_offset = _transform_longitude(longitude - 105, latitude - 35)
    radian_latitude = latitude / 180 * math.pi
    magic = 1 - earth_ee * math.sin(radian_latitude) ** 2
    sqrt_magic = math.sqrt(magic)
    lat_offset = lat_offset * 180 / ((earth_a * (1 - earth_ee)) / (magic * sqrt_magic) * math.pi)
    lon_offset = lon_offset * 180 / (earth_a / sqrt_magic * math.cos(radian_latitude) * math.pi)
    return longitude * 2 - (longitude + lon_offset), latitude * 2 - (latitude + lat_offset)


def main() -> None:
    args = parse_args()
    listings = pd.read_csv(args.rent_listings)
    geocodes = pd.read_csv(args.geocodes)
    aggregated = (
        listings.groupby(["community", "address"], as_index=False)
        .agg(
            sample_count=("listing_id", "count"),
            avg_rent_yuan_month=("rent_yuan_month", "mean"),
            avg_rent_yuan_sqm_month=("rent_yuan_sqm_month", "mean"),
        )
        .merge(geocodes, on="community", how="left", validate="one_to_one")
    )
    missing = aggregated.loc[aggregated["longitude_gcj02"].isna(), "community"].tolist()
    if missing:
        raise RuntimeError(f"以下小区缺少坐标：{', '.join(missing)}")

    rows = []
    for row in aggregated.to_dict("records"):
        longitude, latitude = gcj02_to_wgs84(
            float(row["longitude_gcj02"]), float(row["latitude_gcj02"])
        )
        rows.append(
            {
                **row,
                "longitude": longitude,
                "latitude": latitude,
                "geocoder": f"AMap JS Geocoder ({row['geocode_level']})",
                "source_file": str(args.rent_listings),
            }
        )

    repository = SiteSelectionRepository()
    try:
        count = repository.replace_rental_communities(rows)
    finally:
        repository.close()
    print(f"已写入 ads.rental_community_samples：{count} 个小区，{len(listings)} 条租赁样本")


if __name__ == "__main__":
    main()
