CREATE EXTENSION IF NOT EXISTS postgis;

CREATE SCHEMA IF NOT EXISTS ods;
CREATE SCHEMA IF NOT EXISTS dwd;
CREATE SCHEMA IF NOT EXISTS dws;
CREATE SCHEMA IF NOT EXISTS ads;

COMMENT ON SCHEMA ods IS 'Operational Data Store：原始文件与忠实解析后的源数据';
COMMENT ON SCHEMA dwd IS 'Data Warehouse Detail：清洗、标准化后的明细数据';
COMMENT ON SCHEMA dws IS 'Data Warehouse Summary：按主题汇总的数据';
COMMENT ON SCHEMA ads IS 'Application Data Service：面向地图与应用接口的数据';

CREATE TABLE IF NOT EXISTS ods.source_files (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset_key TEXT NOT NULL,
    source_type TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    source_url TEXT,
    represented_year SMALLINT,
    license_name TEXT,
    storage_path TEXT NOT NULL,
    sha256 CHAR(64) NOT NULL UNIQUE,
    byte_size BIGINT NOT NULL CHECK (byte_size >= 0),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE ods.source_files IS '原始文件清单；文件保存在项目 data 目录，数据库记录路径、指纹和来源元数据';

CREATE TABLE IF NOT EXISTS ods.county_climate_economy (
    source_file_id BIGINT NOT NULL REFERENCES ods.source_files(id),
    sheet_name TEXT NOT NULL,
    source_row_number INTEGER NOT NULL CHECK (source_row_number > 0),
    row_data JSONB NOT NULL,
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source_file_id, sheet_name, source_row_number)
);

COMMENT ON TABLE ods.county_climate_economy IS '县域气候与社会经济原始记录；row_data 按来源列顺序保存，包含表头行';

CREATE INDEX IF NOT EXISTS county_climate_economy_sheet_idx
ON ods.county_climate_economy (source_file_id, sheet_name, source_row_number);

CREATE TABLE IF NOT EXISTS ods.boundary_features (
    source_file_id BIGINT NOT NULL REFERENCES ods.source_files(id),
    source_feature_number INTEGER NOT NULL CHECK (source_feature_number > 0),
    source_feature_id TEXT,
    source_feature_name TEXT,
    source_properties JSONB NOT NULL DEFAULT '{}'::jsonb,
    geom geometry(Geometry, 4326) NOT NULL,
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source_file_id, source_feature_number)
);

COMMENT ON TABLE ods.boundary_features IS 'geoBoundaries 原始 Feature；属性不改名，几何统一声明为 EPSG:4326';

CREATE INDEX IF NOT EXISTS boundary_features_geom_gix
ON ods.boundary_features USING GIST (geom);

CREATE INDEX IF NOT EXISTS boundary_features_source_id_idx
ON ods.boundary_features (source_file_id, source_feature_id);
