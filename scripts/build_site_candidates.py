from __future__ import annotations

import argparse
import math
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from dotenv import load_dotenv
from rasterio.features import geometry_mask, shapes
from shapely.geometry import mapping, shape

from policy_crawler.site_candidates import (
    build_abandonment_mask,
    build_stable_natural_resource_mask,
)
from policy_crawler.site_selection_storage import SiteSelectionRepository


AREA_CRS = "EPSG:32650"
load_dotenv()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="按原选址MVP规则生成候选斑块并写入DWD。")
    subparsers = parser.add_subparsers(dest="candidate_type", required=True)
    abandoned = subparsers.add_parser("abandoned_cropland", help="识别疑似撂荒耕地")
    abandoned.add_argument("--historical", type=Path, required=True)
    abandoned.add_argument("--recent", type=Path, nargs="+", required=True)
    abandoned.add_argument("--protected", type=Path)
    abandoned.add_argument("--historical-crop-values", type=int, nargs="+", default=[5])
    abandoned.add_argument("--recent-proxy-values", type=int, nargs="+", default=[8, 11])
    abandoned.add_argument("--minimum-patch-mu", type=float, default=5.0)
    natural = subparsers.add_parser("stable_natural_resource", help="识别多年稳定裸地/草灌地")
    natural.add_argument("--annual", type=Path, nargs="+", required=True)
    natural.add_argument("--resource-values", type=int, nargs="+", default=[8, 11])
    natural.add_argument("--minimum-patch-mu", type=float, default=20.0)
    natural.add_argument("--maximum-patch-mu", type=float, default=500.0)
    return parser.parse_args()


def read_aligned(path: Path, reference: rasterio.DatasetReader) -> np.ndarray:
    with rasterio.open(path) as source:
        if (
            source.crs != reference.crs
            or source.transform != reference.transform
            or source.width != reference.width
            or source.height != reference.height
        ):
            raise ValueError(f"栅格未对齐：{path}")
        return source.read(1)


def vectorize_mask(mask, transform, crs, minimum_patch_mu, maximum_patch_mu):
    geometries = [
        shape(geometry)
        for geometry, value in shapes(mask.astype("uint8"), mask=mask, transform=transform)
        if value == 1
    ]
    if not geometries:
        return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs="EPSG:4326")
    lands = gpd.GeoDataFrame({"geometry": geometries}, crs=crs).to_crs(AREA_CRS)
    lands["area_mu"] = lands.area / 666.6666667
    selected = lands["area_mu"] >= minimum_patch_mu
    if maximum_patch_mu is not None:
        selected &= lands["area_mu"] <= maximum_patch_mu
    return lands.loc[selected].copy().to_crs("EPSG:4326").reset_index(drop=True)


def build_abandoned(args):
    with rasterio.open(args.historical) as reference:
        historical = reference.read(1)
        recent_layers = [read_aligned(path, reference) for path in args.recent]
        candidate_mask = build_abandonment_mask(
            historical,
            recent_layers,
            reference.dataset_mask().astype(bool),
            args.historical_crop_values,
            args.recent_proxy_values,
        )
        if args.protected:
            protected = gpd.read_file(args.protected).to_crs(reference.crs)
            if not protected.empty:
                protected_mask = geometry_mask(
                    protected.geometry,
                    out_shape=candidate_mask.shape,
                    transform=reference.transform,
                    invert=True,
                )
                candidate_mask &= ~protected_mask
        lands = vectorize_mask(
            candidate_mask, reference.transform, reference.crs, args.minimum_patch_mu, None
        )
    lands["land_id"] = [f"YC-LAND-{index:05d}" for index in range(1, len(lands) + 1)]
    lands["historical_class"] = "crops"
    lands["current_class"] = "rangeland/bare"
    lands["abandoned_confidence"] = 0.65
    lands["protected_overlap"] = False if args.protected else None
    lands["protection_screened"] = bool(args.protected)
    lands["climate_pass"] = None
    lands["soil_pass"] = None
    lands["irrigation_available"] = None
    return lands, {
        "algorithm": "historical_crop_and_persistent_recent_proxy",
        "historical": str(args.historical),
        "recent": [str(path) for path in args.recent],
        "protected": str(args.protected) if args.protected else None,
        "historical_crop_values": args.historical_crop_values,
        "recent_proxy_values": args.recent_proxy_values,
        "minimum_patch_mu": args.minimum_patch_mu,
    }


def build_natural(args):
    with rasterio.open(args.annual[0]) as reference:
        layers = [reference.read(1), *[read_aligned(path, reference) for path in args.annual[1:]]]
        resource_mask = build_stable_natural_resource_mask(
            layers, reference.dataset_mask().astype(bool), args.resource_values
        )
        lands = vectorize_mask(
            resource_mask,
            reference.transform,
            reference.crs,
            args.minimum_patch_mu,
            args.maximum_patch_mu,
        )
    lands = lands.sort_values("area_mu", ascending=False).reset_index(drop=True)
    lands["land_id"] = [f"YC-NR-{index:05d}" for index in range(1, len(lands) + 1)]
    lands["resource_type"] = "stable_bare_or_rangeland"
    lands["historical_class"] = "bare/rangeland"
    lands["current_class"] = "bare/rangeland"
    lands["resource_stability_years"] = len(args.annual)
    lands["protected_overlap"] = None
    lands["protection_screened"] = False
    lands["climate_pass"] = None
    lands["soil_pass"] = None
    lands["irrigation_available"] = None
    return lands, {
        "algorithm": "stable_resource_class_in_every_supplied_year",
        "annual": [str(path) for path in args.annual],
        "resource_values": args.resource_values,
        "minimum_patch_mu": args.minimum_patch_mu,
        "maximum_patch_mu": args.maximum_patch_mu,
    }


def records(lands):
    result = []
    for _, row in lands.iterrows():
        item = {}
        for key, value in row.items():
            if key == "geometry":
                continue
            if hasattr(value, "item"):
                value = value.item()
            if isinstance(value, float) and not math.isfinite(value):
                value = None
            item[key] = value
        item["geometry"] = mapping(row.geometry)
        result.append(item)
    return result


def main() -> None:
    args = parse_args()
    if args.candidate_type == "abandoned_cropland":
        lands, metadata = build_abandoned(args)
    else:
        lands, metadata = build_natural(args)
    repository = SiteSelectionRepository()
    try:
        count = repository.replace_candidates(args.candidate_type, records(lands), metadata)
    finally:
        repository.close()
    print(f"已写入 dwd.site_candidate_lands：{args.candidate_type} 共 {count} 个斑块")


if __name__ == "__main__":
    main()
