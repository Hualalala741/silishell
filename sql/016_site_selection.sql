CREATE EXTENSION IF NOT EXISTS postgis;
CREATE SCHEMA IF NOT EXISTS dwd;
CREATE SCHEMA IF NOT EXISTS ads;

CREATE TABLE IF NOT EXISTS dwd.site_candidate_lands (
    land_id TEXT PRIMARY KEY,
    candidate_type TEXT NOT NULL,
    area_mu DOUBLE PRECISION NOT NULL CHECK (area_mu > 0),
    resource_type TEXT,
    historical_class TEXT,
    current_class TEXT,
    abandoned_confidence DOUBLE PRECISION,
    resource_stability_years SMALLINT,
    protected_overlap BOOLEAN,
    protection_screened BOOLEAN NOT NULL DEFAULT FALSE,
    climate_pass BOOLEAN,
    soil_pass BOOLEAN,
    irrigation_available BOOLEAN,
    geom geometry(MultiPolygon, 4326) NOT NULL,
    source_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (candidate_type IN ('abandoned_cropland', 'stable_natural_resource')),
    CHECK (abandoned_confidence IS NULL OR abandoned_confidence BETWEEN 0 AND 1),
    CHECK (resource_stability_years IS NULL OR resource_stability_years > 0),
    CHECK (jsonb_typeof(source_metadata) = 'object')
);

COMMENT ON TABLE dwd.site_candidate_lands IS
'由年度土地覆盖栅格按规则识别、矢量化并完成面积筛选的候选斑块；不是前端快照。';

CREATE INDEX IF NOT EXISTS site_candidate_lands_type_area_idx
ON dwd.site_candidate_lands (candidate_type, area_mu DESC);

CREATE INDEX IF NOT EXISTS site_candidate_lands_geom_gix
ON dwd.site_candidate_lands USING GIST (geom);

CREATE TABLE IF NOT EXISTS ads.site_candidate_metrics (
    land_id TEXT PRIMARY KEY REFERENCES dwd.site_candidate_lands(land_id) ON DELETE CASCADE,
    cost_score DOUBLE PRECISION CHECK (cost_score BETWEEN 0 AND 100),
    market_score DOUBLE PRECISION CHECK (market_score BETWEEN 0 AND 100),
    risk_score DOUBLE PRECISION CHECK (risk_score BETWEEN 0 AND 100),
    major_road_km DOUBLE PRECISION CHECK (major_road_km >= 0),
    highway_exit_km DOUBLE PRECISION CHECK (highway_exit_km >= 0),
    settlement_km DOUBLE PRECISION CHECK (settlement_km >= 0),
    river_km DOUBLE PRECISION CHECK (river_km >= 0),
    market_km DOUBLE PRECISION CHECK (market_km >= 0),
    logistics_km DOUBLE PRECISION CHECK (logistics_km >= 0),
    agri_poi_10km INTEGER CHECK (agri_poi_10km >= 0),
    population_5km DOUBLE PRECISION CHECK (population_5km >= 0),
    town_name TEXT,
    land_rent_yuan_per_mu_year DOUBLE PRECISION CHECK (land_rent_yuan_per_mu_year >= 0),
    land_rent_source_level TEXT,
    minimum_wage_yuan_month DOUBLE PRECISION CHECK (minimum_wage_yuan_month >= 0),
    labor_cost_score DOUBLE PRECISION CHECK (labor_cost_score BETWEEN 0 AND 100),
    nonresidential_water_yuan_ton DOUBLE PRECISION CHECK (nonresidential_water_yuan_ton >= 0),
    agricultural_electricity_yuan_kwh DOUBLE PRECISION CHECK (agricultural_electricity_yuan_kwh >= 0),
    utility_cost_score DOUBLE PRECISION CHECK (utility_cost_score BETWEEN 0 AND 100),
    climate_risk_score DOUBLE PRECISION CHECK (climate_risk_score BETWEEN 0 AND 100),
    data_completeness DOUBLE PRECISION CHECK (data_completeness BETWEEN 0 AND 1),
    calculation_version TEXT,
    source_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    calculated_at TIMESTAMPTZ,
    CHECK (jsonb_typeof(source_metadata) = 'object')
);

ALTER TABLE ads.site_candidate_metrics
    ADD COLUMN IF NOT EXISTS residential_rent_yuan_sqm_month DOUBLE PRECISION
        CHECK (residential_rent_yuan_sqm_month >= 0),
    ADD COLUMN IF NOT EXISTS residential_rent_community_count INTEGER
        CHECK (residential_rent_community_count >= 0),
    ADD COLUMN IF NOT EXISTS residential_rent_sample_count INTEGER
        CHECK (residential_rent_sample_count >= 0),
    ADD COLUMN IF NOT EXISTS residential_rent_nearest_km DOUBLE PRECISION
        CHECK (residential_rent_nearest_km >= 0),
    ADD COLUMN IF NOT EXISTS residential_rent_source_level TEXT,
    ADD COLUMN IF NOT EXISTS road_access_score DOUBLE PRECISION
        CHECK (road_access_score BETWEEN 0 AND 100),
    ADD COLUMN IF NOT EXISTS highway_access_score DOUBLE PRECISION
        CHECK (highway_access_score BETWEEN 0 AND 100),
    ADD COLUMN IF NOT EXISTS settlement_access_score DOUBLE PRECISION
        CHECK (settlement_access_score BETWEEN 0 AND 100),
    ADD COLUMN IF NOT EXISTS residential_rent_score DOUBLE PRECISION
        CHECK (residential_rent_score BETWEEN 0 AND 100),
    ADD COLUMN IF NOT EXISTS population_score DOUBLE PRECISION
        CHECK (population_score BETWEEN 0 AND 100),
    ADD COLUMN IF NOT EXISTS market_access_score DOUBLE PRECISION
        CHECK (market_access_score BETWEEN 0 AND 100),
    ADD COLUMN IF NOT EXISTS logistics_access_score DOUBLE PRECISION
        CHECK (logistics_access_score BETWEEN 0 AND 100),
    ADD COLUMN IF NOT EXISTS agri_service_score DOUBLE PRECISION
        CHECK (agri_service_score BETWEEN 0 AND 100);

COMMENT ON TABLE ads.site_candidate_metrics IS
'选址应用直接消费的空间距离、人口、成本与软排序指标；建表后保持为空，等待指标任务写入。';

CREATE TABLE IF NOT EXISTS ads.rental_community_samples (
    community TEXT PRIMARY KEY,
    address TEXT NOT NULL,
    sample_count INTEGER NOT NULL CHECK (sample_count > 0),
    avg_rent_yuan_month DOUBLE PRECISION NOT NULL CHECK (avg_rent_yuan_month >= 0),
    avg_rent_yuan_sqm_month DOUBLE PRECISION NOT NULL CHECK (avg_rent_yuan_sqm_month >= 0),
    longitude_gcj02 DOUBLE PRECISION NOT NULL,
    latitude_gcj02 DOUBLE PRECISION NOT NULL,
    geom geometry(Point, 4326) NOT NULL,
    geocoder TEXT NOT NULL,
    source_file TEXT NOT NULL,
    geocoded_at TIMESTAMPTZ,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE ads.rental_community_samples IS
'租赁明细按小区聚合并完成坐标解析后的应用层样本；候选斑块仅使用五公里内样本。';

CREATE INDEX IF NOT EXISTS rental_community_samples_geom_gix
ON ads.rental_community_samples USING GIST (geom);

CREATE TABLE IF NOT EXISTS ads.industry_plan_profiles (
    profile_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    region TEXT NOT NULL,
    industry TEXT NOT NULL,
    parent_industry TEXT,
    industry_aliases JSONB NOT NULL DEFAULT '[]'::jsonb,
    development_goal TEXT NOT NULL DEFAULT '',
    development_directions JSONB NOT NULL DEFAULT '[]'::jsonb,
    planned_projects JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_document TEXT NOT NULL,
    source_url TEXT NOT NULL,
    plan_period TEXT NOT NULL,
    evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (region, industry, plan_period),
    CHECK (jsonb_typeof(industry_aliases) = 'array'),
    CHECK (jsonb_typeof(development_directions) = 'array'),
    CHECK (jsonb_typeof(planned_projects) = 'array'),
    CHECK (jsonb_typeof(evidence) = 'array')
);

COMMENT ON TABLE ads.industry_plan_profiles IS
'选址应用的产业规划检索资料；建表后不预填，由政策画像流程后续发布。';

CREATE INDEX IF NOT EXISTS industry_plan_profiles_period_idx
ON ads.industry_plan_profiles (region, plan_period, industry);
