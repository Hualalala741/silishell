from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .storage import database_url


ROOT = Path(__file__).resolve().parents[1]
SITE_SELECTION_MIGRATION = ROOT / "sql" / "016_site_selection.sql"


class SiteSelectionRepository:
    def __init__(self, url: str | None = None):
        self.connection = psycopg.connect(url or database_url(), row_factory=dict_row)
        with self.connection.transaction():
            self.connection.execute(SITE_SELECTION_MIGRATION.read_text(encoding="utf-8"))

    def close(self) -> None:
        self.connection.close()

    def list_candidates(self, candidate_type: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT
                candidate.land_id, candidate.candidate_type, candidate.area_mu,
                candidate.protected_overlap, candidate.climate_pass,
                candidate.soil_pass, candidate.irrigation_available,
                metric.cost_score, metric.market_score, metric.risk_score,
                metric.major_road_km, metric.highway_exit_km,
                metric.settlement_km, metric.river_km, metric.population_5km,
                metric.residential_rent_yuan_sqm_month,
                metric.residential_rent_community_count,
                metric.residential_rent_sample_count,
                metric.residential_rent_nearest_km,
                metric.residential_rent_source_level,
                metric.minimum_wage_yuan_month,
                metric.nonresidential_water_yuan_ton,
                metric.agricultural_electricity_yuan_kwh,
                metric.town_name, metric.data_completeness,
                metric.road_access_score, metric.highway_access_score,
                metric.settlement_access_score, metric.residential_rent_score,
                metric.population_score, metric.market_access_score,
                metric.logistics_access_score, metric.agri_service_score,
                ST_AsGeoJSON(candidate.geom)::jsonb AS geometry,
                ST_X(ST_PointOnSurface(candidate.geom)) AS longitude,
                ST_Y(ST_PointOnSurface(candidate.geom)) AS latitude
            FROM dwd.site_candidate_lands candidate
            JOIN ads.site_candidate_metrics metric USING (land_id)
            WHERE candidate.candidate_type = %s
              AND metric.cost_score IS NOT NULL
              AND metric.market_score IS NOT NULL
              AND metric.risk_score IS NOT NULL
              AND metric.major_road_km IS NOT NULL
              AND metric.highway_exit_km IS NOT NULL
              AND metric.settlement_km IS NOT NULL
              AND metric.river_km IS NOT NULL
              AND metric.population_5km IS NOT NULL
              AND metric.minimum_wage_yuan_month IS NOT NULL
              AND metric.nonresidential_water_yuan_ton IS NOT NULL
              AND metric.agricultural_electricity_yuan_kwh IS NOT NULL
              AND metric.data_completeness IS NOT NULL
            ORDER BY candidate.land_id
            """,
            (candidate_type,),
        ).fetchall()
        self.connection.commit()
        return [dict(row) for row in rows]

    def candidate_count(self, candidate_type: str) -> int:
        row = self.connection.execute(
            "SELECT count(*) AS count FROM dwd.site_candidate_lands WHERE candidate_type = %s",
            (candidate_type,),
        ).fetchone()
        self.connection.commit()
        return int(row["count"])

    def list_candidate_lands(self, candidate_type: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT
                land_id, area_mu, protected_overlap, climate_pass, soil_pass,
                irrigation_available,
                ST_AsGeoJSON(geom)::jsonb AS geometry,
                ST_X(ST_PointOnSurface(geom)) AS longitude,
                ST_Y(ST_PointOnSurface(geom)) AS latitude
            FROM dwd.site_candidate_lands
            WHERE candidate_type = %s
            ORDER BY land_id
            """,
            (candidate_type,),
        ).fetchall()
        self.connection.commit()
        return [dict(row) for row in rows]

    def list_industry_profiles(self, region: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT industry, parent_industry, industry_aliases, development_goal,
                   development_directions, planned_projects, source_document,
                   source_url, plan_period
            FROM ads.industry_plan_profiles
            WHERE region = %s
            ORDER BY plan_period, industry
            """,
            (region,),
        ).fetchall()
        self.connection.commit()
        return [dict(row) for row in rows]

    def get_bamboo_policy_score(self) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT score.overall_unweighted_score_100 AS score,
                   score.score_version, score.dimensions,
                   run.id AS run_id, run.requirement_name, run.completed_at
            FROM dws.county_policy_scores score
            JOIN ops.crawl_runs run ON run.id = score.run_id
            WHERE run.status = 'completed'
              AND (run.topic_key = 'zhuchanye' OR run.requirement_name LIKE '%%竹产业%%')
            ORDER BY COALESCE(run.completed_at, run.started_at) DESC
            LIMIT 1
            """
        ).fetchone()
        self.connection.commit()
        return dict(row) if row else None

    def list_rental_communities(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT community, address, sample_count, avg_rent_yuan_month,
                   avg_rent_yuan_sqm_month,
                   ST_X(geom) AS longitude, ST_Y(geom) AS latitude
            FROM ads.rental_community_samples
            ORDER BY community
            """
        ).fetchall()
        self.connection.commit()
        return [dict(row) for row in rows]

    def replace_rental_communities(self, communities: Iterable[dict[str, Any]]) -> int:
        rows = list(communities)
        with self.connection.transaction():
            self.connection.execute("TRUNCATE ads.rental_community_samples")
            if rows:
                with self.connection.cursor() as cursor:
                    cursor.executemany(
                        """
                        INSERT INTO ads.rental_community_samples (
                            community, address, sample_count, avg_rent_yuan_month,
                            avg_rent_yuan_sqm_month, longitude_gcj02, latitude_gcj02,
                            geom, geocoder, source_file, geocoded_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s,
                            ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s, now()
                        )
                        """,
                        [
                            (
                                row["community"], row["address"], row["sample_count"],
                                row["avg_rent_yuan_month"], row["avg_rent_yuan_sqm_month"],
                                row["longitude_gcj02"], row["latitude_gcj02"],
                                row["longitude"], row["latitude"], row["geocoder"],
                                row["source_file"],
                            )
                            for row in rows
                        ],
                    )
        return len(rows)

    def list_candidate_geometries(self, candidate_type: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT land_id, area_mu, ST_AsGeoJSON(geom)::jsonb AS geometry
            FROM dwd.site_candidate_lands
            WHERE candidate_type = %s
            ORDER BY land_id
            """,
            (candidate_type,),
        ).fetchall()
        self.connection.commit()
        return [dict(row) for row in rows]

    def replace_metrics(self, land_ids: list[str], metrics: Iterable[dict[str, Any]]) -> int:
        rows = list(metrics)
        if not land_ids:
            return 0
        columns = (
            "cost_score", "market_score", "risk_score", "major_road_km",
            "highway_exit_km", "settlement_km", "river_km", "market_km",
            "logistics_km", "agri_poi_10km", "population_5km", "town_name",
            "land_rent_yuan_per_mu_year", "land_rent_source_level",
            "minimum_wage_yuan_month", "labor_cost_score",
            "nonresidential_water_yuan_ton", "agricultural_electricity_yuan_kwh",
            "utility_cost_score", "climate_risk_score", "data_completeness",
            "residential_rent_yuan_sqm_month", "residential_rent_community_count",
            "residential_rent_sample_count", "residential_rent_nearest_km",
            "residential_rent_source_level", "road_access_score",
            "highway_access_score", "settlement_access_score",
            "residential_rent_score", "population_score", "market_access_score",
            "logistics_access_score", "agri_service_score",
        )
        with self.connection.transaction():
            self.connection.execute(
                "DELETE FROM ads.site_candidate_metrics WHERE land_id = ANY(%s)",
                (land_ids,),
            )
            if rows:
                with self.connection.cursor() as cursor:
                    cursor.executemany(
                        f"""
                        INSERT INTO ads.site_candidate_metrics (
                            land_id, {', '.join(columns)}, calculation_version,
                            source_metadata, calculated_at
                        ) VALUES (
                            %s, {', '.join(['%s'] * len(columns))}, %s, %s, now()
                        )
                        """,
                        [
                            (
                                row["land_id"],
                                *(row.get(column) for column in columns),
                                row.get("calculation_version") or "site-selection-v1",
                                Jsonb(row.get("source_metadata") or {}),
                            )
                            for row in rows
                        ],
                    )
        return len(rows)

    def replace_candidates(
        self,
        candidate_type: str,
        candidates: Iterable[dict[str, Any]],
        source_metadata: dict[str, Any],
    ) -> int:
        rows = list(candidates)
        with self.connection.transaction():
            self.connection.execute(
                "DELETE FROM dwd.site_candidate_lands WHERE candidate_type = %s",
                (candidate_type,),
            )
            if rows:
                with self.connection.cursor() as cursor:
                    cursor.executemany(
                        """
                        INSERT INTO dwd.site_candidate_lands (
                            land_id, candidate_type, area_mu, resource_type,
                            historical_class, current_class, abandoned_confidence,
                            resource_stability_years, protected_overlap,
                            protection_screened, climate_pass, soil_pass,
                            irrigation_available, geom, source_metadata
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)), %s
                        )
                        """,
                        [
                            (
                                row["land_id"], candidate_type, row["area_mu"],
                                row.get("resource_type"), row.get("historical_class"),
                                row.get("current_class"), row.get("abandoned_confidence"),
                                row.get("resource_stability_years"), row.get("protected_overlap"),
                                row.get("protection_screened", False), row.get("climate_pass"),
                                row.get("soil_pass"), row.get("irrigation_available"),
                                json.dumps(row["geometry"], ensure_ascii=False),
                                Jsonb(source_metadata),
                            )
                            for row in rows
                        ],
                    )
        return len(rows)
