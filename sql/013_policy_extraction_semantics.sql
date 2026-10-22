-- Enrich generic extraction provenance and normalized policy-measure semantics.
ALTER TABLE ods.policy_extractions
    ADD COLUMN IF NOT EXISTS requested_fields JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS requirements TEXT,
    ADD COLUMN IF NOT EXISTS model_provider TEXT,
    ADD COLUMN IF NOT EXISTS schema_version TEXT NOT NULL DEFAULT 'generic-policy-extraction-v1',
    ADD COLUMN IF NOT EXISTS error_text TEXT;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'policy_extractions_status_check') THEN
        ALTER TABLE ods.policy_extractions ADD CONSTRAINT policy_extractions_status_check
            CHECK (status IN ('success', 'failed'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'policy_extractions_data_object_check') THEN
        ALTER TABLE ods.policy_extractions ADD CONSTRAINT policy_extractions_data_object_check
            CHECK (jsonb_typeof(data) = 'object');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'policy_extractions_requested_fields_array_check') THEN
        ALTER TABLE ods.policy_extractions ADD CONSTRAINT policy_extractions_requested_fields_array_check
            CHECK (jsonb_typeof(requested_fields) = 'array');
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS dwd.policy_tool_types (
    type_id INTEGER PRIMARY KEY,
    type_code TEXT NOT NULL UNIQUE,
    type_name TEXT NOT NULL UNIQUE,
    description TEXT,
    sort_order INTEGER NOT NULL UNIQUE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

INSERT INTO dwd.policy_tool_types (
    type_id, type_code, type_name, description, sort_order
) VALUES
    (1, 'FISCAL_SUPPORT', '财政支持', '补贴、奖补、财政投入和政府采购等', 1),
    (2, 'FINANCIAL_SUPPORT', '金融支持', '信贷、担保、保险和融资支持等', 2),
    (3, 'LAND_FOREST_SUPPORT', '土地与林地保障', '用地、林地、流转和要素保障等', 3),
    (4, 'INFRASTRUCTURE', '基础设施建设', '道路、园区、仓储和公共设施等', 4),
    (5, 'TECH_STANDARD', '技术创新与标准', '技术研发、成果转化、质量和标准等', 5),
    (6, 'TALENT', '人才培养', '人才引进、培训和专家服务等', 6),
    (7, 'MARKET_APPLICATION', '市场推广与应用示范', '品牌、展销、示范和市场拓展等', 7),
    (8, 'ENTERPRISE_ORGANIZATION', '企业、园区与产业组织', '企业培育、招商、园区和产业组织等', 8),
    (9, 'RESOURCE_ECOLOGY', '资源培育与生态管护', '资源培育、生态修复和管护等', 9),
    (10, 'ADMIN_SERVICE', '行政审批与政务服务', '审批优化、政务服务和制度保障等', 10),
    (99, 'OTHER', '其他', '无法归入现有类型的政策工具', 99)
ON CONFLICT (type_id) DO UPDATE SET
    type_code = EXCLUDED.type_code,
    type_name = EXCLUDED.type_name,
    description = EXCLUDED.description,
    sort_order = EXCLUDED.sort_order;

ALTER TABLE dwd.policy_measures
    ADD COLUMN IF NOT EXISTS topic_key TEXT;

UPDATE dwd.policy_measures measure
SET topic_key = run.topic_key
FROM ops.crawl_runs run
WHERE run.id = measure.run_id
  AND measure.topic_key IS NULL;

ALTER TABLE dwd.policy_measures
    ALTER COLUMN topic_key SET NOT NULL;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'dwd'
          AND table_name = 'policy_measures'
          AND column_name = 'start_date'
          AND data_type <> 'date'
    ) THEN
        ALTER TABLE dwd.policy_measures
            ALTER COLUMN start_date TYPE DATE USING NULLIF(start_date, '')::date;
    END IF;
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'dwd'
          AND table_name = 'policy_measures'
          AND column_name = 'end_date'
          AND data_type <> 'date'
    ) THEN
        ALTER TABLE dwd.policy_measures
            ALTER COLUMN end_date TYPE DATE USING NULLIF(end_date, '')::date;
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'policy_measures_source_document_fkey') THEN
        ALTER TABLE dwd.policy_measures ADD CONSTRAINT policy_measures_source_document_fkey
            FOREIGN KEY (topic_key, source_url)
            REFERENCES ods.policy_documents(topic_key, source_url);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'policy_measures_policy_tool_type_fkey') THEN
        ALTER TABLE dwd.policy_measures ADD CONSTRAINT policy_measures_policy_tool_type_fkey
            FOREIGN KEY (policy_tool_type_id) REFERENCES dwd.policy_tool_types(type_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'policy_measures_secondary_policy_tool_type_fkey') THEN
        ALTER TABLE dwd.policy_measures ADD CONSTRAINT policy_measures_secondary_policy_tool_type_fkey
            FOREIGN KEY (secondary_policy_tool_type_id) REFERENCES dwd.policy_tool_types(type_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'policy_measures_record_type_check') THEN
        ALTER TABLE dwd.policy_measures ADD CONSTRAINT policy_measures_record_type_check
            CHECK (record_type IN ('target', 'policy_action', 'implementation', 'result'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'policy_measures_implementation_stage_check') THEN
        ALTER TABLE dwd.policy_measures ADD CONSTRAINT policy_measures_implementation_stage_check
            CHECK (implementation_stage IN ('planned', 'requested', 'allocated', 'disbursed', 'ongoing', 'completed', 'verified', 'not_applicable', 'unknown'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'policy_measures_tool_types_distinct_check') THEN
        ALTER TABLE dwd.policy_measures ADD CONSTRAINT policy_measures_tool_types_distinct_check
            CHECK (secondary_policy_tool_type_id IS NULL OR secondary_policy_tool_type_id <> policy_tool_type_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'policy_measures_scores_check') THEN
        ALTER TABLE dwd.policy_measures ADD CONSTRAINT policy_measures_scores_check
            CHECK (
                (strength_score IS NULL OR strength_score BETWEEN 1 AND 5)
                AND (specificity_score IS NULL OR specificity_score BETWEEN 1 AND 5)
                AND (design_score IS NULL OR design_score BETWEEN 1 AND 25)
                AND (design_score_100 IS NULL OR design_score_100 BETWEEN 4 AND 100)
            );
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'policy_measures_date_order_check') THEN
        ALTER TABLE dwd.policy_measures ADD CONSTRAINT policy_measures_date_order_check
            CHECK (start_date IS NULL OR end_date IS NULL OR start_date <= end_date);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'policy_measures_json_arrays_check') THEN
        ALTER TABLE dwd.policy_measures ADD CONSTRAINT policy_measures_json_arrays_check
            CHECK (
                jsonb_typeof(section_path) = 'array'
                AND jsonb_typeof(industry_segments) = 'array'
                AND jsonb_typeof(policy_target) = 'array'
                AND jsonb_typeof(implementing_agencies) = 'array'
                AND jsonb_typeof(quantities) = 'array'
            );
    END IF;
END
$$;

DO $$
BEGIN
    IF to_regclass('dwd.dwd_policy_measures_document_idx') IS NOT NULL
       AND pg_get_indexdef('dwd.dwd_policy_measures_document_idx'::regclass)
           NOT LIKE '%(topic_key, document_id)%' THEN
        DROP INDEX dwd.dwd_policy_measures_document_idx;
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS dwd_policy_measures_document_idx
ON dwd.policy_measures (topic_key, document_id);

CREATE OR REPLACE VIEW dwd.policy_measure_details AS
SELECT
    measure.*,
    document.title AS document_title,
    document.document_number,
    document.published_at AS document_published_at,
    document.source_type AS document_source_type,
    document.publisher AS document_publisher,
    tool.type_code AS policy_tool_type_code,
    tool.type_name AS policy_tool_type_name,
    secondary_tool.type_code AS secondary_policy_tool_type_code,
    secondary_tool.type_name AS secondary_policy_tool_type_name
FROM dwd.policy_measures measure
JOIN ods.policy_documents document
  ON document.topic_key = measure.topic_key
 AND document.source_url = measure.source_url
JOIN dwd.policy_tool_types tool
  ON tool.type_id = measure.policy_tool_type_id
LEFT JOIN dwd.policy_tool_types secondary_tool
  ON secondary_tool.type_id = measure.secondary_policy_tool_type_id;

COMMENT ON VIEW dwd.policy_measure_details IS
'原子政策措施明细，展开来源文档和政策工具名称，便于直接查询';
