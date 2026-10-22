from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import geopandas as gpd
import pandas as pd
from dotenv import load_dotenv
from shapely.geometry import shape

from policy_crawler.site_selection_storage import SiteSelectionRepository
from policy_crawler.spatial_metrics import (
    METRIC_CRS,
    nearby_count,
    nearest_attribute,
    nearest_distance_km,
    population_in_radius,
    scale_score,
)

load_dotenv()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="计算候选斑块空间指标并发布到ADS。")
    parser.add_argument(
        "candidate_type", choices=["abandoned_cropland", "stable_natural_resource"]
    )
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--population", type=Path, required=True)
    parser.add_argument("--county-context", type=Path, required=True)
    return parser.parse_args()


def read_optional(path: Path, layer: str, crs: str) -> gpd.GeoDataFrame:
    try:
        return gpd.read_file(path, layer=layer).to_crs(crs)
    except Exception:
        return gpd.GeoDataFrame(geometry=[], crs=crs)


def nullable(value):
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return None
    return value.item() if hasattr(value, "item") else value


def main() -> None:
    args = parse_args()
    repository = SiteSelectionRepository()
    try:
        source_rows = repository.list_candidate_geometries(args.candidate_type)
        if not source_rows:
            raise RuntimeError("DWD中没有该类型候选斑块，请先运行 build_site_candidates.py")
        lands = gpd.GeoDataFrame(
            [{"land_id": row["land_id"], "area_mu": row["area_mu"]} for row in source_rows],
            geometry=[shape(row["geometry"]) for row in source_rows],
            crs="EPSG:4326",
        ).to_crs(METRIC_CRS)
        centroids = gpd.GeoDataFrame(
            lands.drop(columns="geometry"), geometry=lands.centroid, crs=lands.crs
        )
        major_roads = read_optional(args.features, "major_roads", METRIC_CRS)
        highway_exits = read_optional(args.features, "highway_exits", METRIC_CRS)
        rivers = read_optional(args.features, "rivers", METRIC_CRS)
        settlements = read_optional(args.features, "settlements", METRIC_CRS)
        town_centers = read_optional(args.features, "town_centers", METRIC_CRS)
        markets = read_optional(args.features, "markets", METRIC_CRS)
        logistics = read_optional(args.features, "logistics", METRIC_CRS)
        agriculture = read_optional(args.features, "agriculture_pois", METRIC_CRS)

        lands["major_road_km"] = nearest_distance_km(centroids, major_roads)
        lands["highway_exit_km"] = nearest_distance_km(centroids, highway_exits)
        lands["settlement_km"] = nearest_distance_km(centroids, settlements)
        lands["town_name"] = nearest_attribute(centroids, town_centers, "name")
        lands["river_km"] = nearest_distance_km(centroids, rivers)
        lands["market_km"] = nearest_distance_km(centroids, markets)
        lands["logistics_km"] = nearest_distance_km(centroids, logistics)
        lands["agri_poi_10km"] = nearby_count(centroids, agriculture, 10_000)
        lands["population_5km"] = population_in_radius(centroids, args.population, 5_000)

        rental_rows = repository.list_rental_communities()
        if rental_rows:
            rental_communities = gpd.GeoDataFrame(
                rental_rows,
                geometry=gpd.points_from_xy(
                    [row["longitude"] for row in rental_rows],
                    [row["latitude"] for row in rental_rows],
                ),
                crs="EPSG:4326",
            ).to_crs(METRIC_CRS)
        else:
            rental_communities = gpd.GeoDataFrame(geometry=[], crs=METRIC_CRS)

        rent_values = []
        rent_community_counts = []
        rent_sample_counts = []
        rent_nearest = []
        for point in centroids.geometry:
            if rental_communities.empty:
                distances = pd.Series(dtype=float)
            else:
                distances = rental_communities.geometry.distance(point) / 1000
            nearby = rental_communities.loc[distances <= 5] if not distances.empty else rental_communities
            rent_values.append(
                float(nearby["avg_rent_yuan_sqm_month"].mean()) if not nearby.empty else math.nan
            )
            rent_community_counts.append(int(len(nearby)))
            rent_sample_counts.append(int(nearby["sample_count"].sum()) if not nearby.empty else 0)
            rent_nearest.append(float(distances.min()) if not distances.empty else math.nan)
        lands["residential_rent_yuan_sqm_month"] = rent_values
        lands["residential_rent_community_count"] = rent_community_counts
        lands["residential_rent_sample_count"] = rent_sample_counts
        lands["residential_rent_nearest_km"] = rent_nearest
        lands["residential_rent_source_level"] = lands["residential_rent_community_count"].map(
            lambda count: "within_5km" if count > 0 else "missing"
        )

        road_score = scale_score(lands["major_road_km"], False)
        highway_score = scale_score(lands["highway_exit_km"], False)
        settlement_score = scale_score(lands["settlement_km"], False)
        population_score = scale_score(lands["population_5km"], True)
        market_score = scale_score(lands["market_km"], False)
        logistics_score = scale_score(lands["logistics_km"], False)
        agri_score = scale_score(lands["agri_poi_10km"], True)
        residential_rent_score = scale_score(
            lands["residential_rent_yuan_sqm_month"], False
        )

        context = json.loads(args.county_context.read_text(encoding="utf-8"))
        minimum_wage = float(context["labor"]["minimum_wage_yuan_month"])
        wage_reference = context["labor"]["provincial_minimum_wage_tiers"]
        labor_cost_score = (
            (max(wage_reference) - minimum_wage)
            / (max(wage_reference) - min(wage_reference))
            * 100
        )
        lands["minimum_wage_yuan_month"] = minimum_wage
        lands["labor_cost_score"] = round(labor_cost_score, 1)
        lands["nonresidential_water_yuan_ton"] = context["utilities"]["nonresidential_water_yuan_ton"]
        lands["agricultural_electricity_yuan_kwh"] = context["utilities"]["agricultural_electricity_low_voltage_yuan_kwh"]
        lands["utility_cost_score"] = 50.0
        lands["cost_score"] = (
            road_score * 0.25
            + highway_score * 0.20
            + settlement_score * 0.10
            + residential_rent_score * 0.20
            + labor_cost_score * 0.10
            + lands["utility_cost_score"] * 0.15
        ).round(1)
        lands["market_score"] = (
            population_score * 0.45
            + market_score * 0.20
            + logistics_score * 0.20
            + agri_score * 0.15
        ).round(1)
        lands["climate_risk_score"] = 50.0
        lands["risk_score"] = 50.0
        lands["road_access_score"] = road_score.round(1)
        lands["highway_access_score"] = highway_score.round(1)
        lands["settlement_access_score"] = settlement_score.round(1)
        lands["residential_rent_score"] = residential_rent_score.round(1)
        lands["population_score"] = population_score.round(1)
        lands["market_access_score"] = market_score.round(1)
        lands["logistics_access_score"] = logistics_score.round(1)
        lands["agri_service_score"] = agri_score.round(1)
        completeness_inputs = [
            "major_road_km", "highway_exit_km", "settlement_km", "river_km",
            "market_km", "logistics_km", "population_5km", "town_name",
            "residential_rent_yuan_sqm_month",
        ]
        lands["data_completeness"] = lands[completeness_inputs].notna().mean(axis=1).round(2)
        metric_columns = [
            "cost_score", "market_score", "risk_score", "major_road_km",
            "highway_exit_km", "settlement_km", "river_km", "market_km",
            "logistics_km", "agri_poi_10km", "population_5km", "town_name",
            "minimum_wage_yuan_month", "labor_cost_score",
            "nonresidential_water_yuan_ton", "agricultural_electricity_yuan_kwh",
            "utility_cost_score", "climate_risk_score", "data_completeness",
            "residential_rent_yuan_sqm_month", "residential_rent_community_count",
            "residential_rent_sample_count", "residential_rent_nearest_km",
            "residential_rent_source_level", "road_access_score",
            "highway_access_score", "settlement_access_score",
            "residential_rent_score", "population_score", "market_access_score",
            "logistics_access_score", "agri_service_score",
        ]
        source_metadata = {
            "features": str(args.features),
            "population": str(args.population),
            "rental_communities": "ads.rental_community_samples (5 km radius)",
            "county_context": str(args.county_context),
            "risk_rule": "no source data; neutral score 50",
        }
        payload = []
        for _, row in lands.iterrows():
            payload.append({
                "land_id": row["land_id"],
                **{column: nullable(row[column]) for column in metric_columns},
                "calculation_version": "site-selection-bamboo-v2",
                "source_metadata": source_metadata,
            })
        count = repository.replace_metrics(
            [str(row["land_id"]) for _, row in lands.iterrows()], payload
        )
    finally:
        repository.close()
    print(f"已写入 ads.site_candidate_metrics：{count} 条")


if __name__ == "__main__":
    main()
