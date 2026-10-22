CREATE SCHEMA IF NOT EXISTS ops;

COMMENT ON SCHEMA ops IS '数据采集与调度运行管理；不保存仓库业务事实';

CREATE TABLE IF NOT EXISTS ods.policy_documents (
    topic_key TEXT NOT NULL,
    source_url TEXT NOT NULL,
    topic TEXT NOT NULL,
    document_id TEXT,
    title TEXT NOT NULL,
    published_at TEXT,
    source_type TEXT,
    kind TEXT,
    template TEXT,
    document_number TEXT,
    publisher TEXT,
    channel TEXT,
    snippet TEXT,
    first_seen_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    crawl_status TEXT NOT NULL DEFAULT 'pending',
    crawled_at TIMESTAMPTZ,
    resolved_url TEXT,
    content_hash TEXT,
    markdown_content TEXT,
    html_content TEXT,
    last_error TEXT,
    PRIMARY KEY (topic_key, source_url)
);

COMMENT ON TABLE ods.policy_documents IS
'政策页面发现结果和抓取原文；HTML、Markdown 及来源元数据按采集结果保存';

CREATE INDEX IF NOT EXISTS ods_policy_documents_status_idx
ON ods.policy_documents (topic_key, crawl_status);

CREATE INDEX IF NOT EXISTS ods_policy_documents_hash_idx
ON ods.policy_documents (content_hash)
WHERE content_hash IS NOT NULL;

CREATE TABLE IF NOT EXISTS ods.policy_extractions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_url TEXT NOT NULL,
    crawled_at TIMESTAMPTZ,
    status TEXT NOT NULL,
    data JSONB NOT NULL,
    requested_fields JSONB NOT NULL DEFAULT '[]'::jsonb,
    requirements TEXT,
    model_provider TEXT,
    schema_version TEXT NOT NULL DEFAULT 'generic-policy-extraction-v1',
    error_text TEXT,
    stored_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (status IN ('success', 'failed')),
    CHECK (jsonb_typeof(data) = 'object'),
    CHECK (jsonb_typeof(requested_fields) = 'array')
);

CREATE INDEX IF NOT EXISTS ods_policy_extractions_url_idx
ON ods.policy_extractions (source_url, stored_at DESC);

CREATE TABLE IF NOT EXISTS ops.crawl_runs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_type TEXT NOT NULL,
    requirement_name TEXT,
    topic_key TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'running',
    request JSONB NOT NULL DEFAULT '{}'::jsonb,
    result JSONB
);

CREATE TABLE IF NOT EXISTS ops.project_profiles (
    topic_key TEXT PRIMARY KEY,
    requirement_name TEXT NOT NULL,
    queries JSONB NOT NULL,
    subject_terms JSONB NOT NULL,
    context_terms JSONB NOT NULL,
    exclude_terms JSONB NOT NULL DEFAULT '[]'::jsonb,
    minimum_relevance_score INTEGER NOT NULL,
    start_date DATE NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ops.crawl_schedules (
    topic_key TEXT PRIMARY KEY,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    schedule_kind TEXT NOT NULL DEFAULT 'daily',
    interval_value INTEGER,
    interval_unit TEXT,
    day_of_month INTEGER,
    weekday INTEGER,
    hour INTEGER,
    minute INTEGER,
    last_run_at TIMESTAMPTZ,
    last_status TEXT,
    last_message TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT crawl_schedules_kind_check CHECK (schedule_kind IN ('interval', 'daily', 'monthly')),
    CONSTRAINT crawl_schedules_shape_check CHECK (
        (schedule_kind = 'daily' AND hour BETWEEN 0 AND 23 AND minute BETWEEN 0 AND 59)
        OR (schedule_kind = 'monthly' AND day_of_month BETWEEN 1 AND 28 AND hour BETWEEN 0 AND 23 AND minute BETWEEN 0 AND 59)
        OR (schedule_kind = 'interval' AND interval_unit = 'hours' AND interval_value BETWEEN 1 AND 168)
        OR (schedule_kind = 'interval' AND interval_unit = 'days' AND interval_value BETWEEN 1 AND 180 AND hour BETWEEN 0 AND 23 AND minute BETWEEN 0 AND 59)
        OR (schedule_kind = 'interval' AND interval_unit = 'weeks' AND interval_value BETWEEN 1 AND 26 AND weekday BETWEEN 0 AND 6 AND hour BETWEEN 0 AND 23 AND minute BETWEEN 0 AND 59)
        OR (schedule_kind = 'interval' AND interval_unit = 'months' AND interval_value BETWEEN 1 AND 6 AND hour BETWEEN 0 AND 23 AND minute BETWEEN 0 AND 59)
    )
);

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

CREATE TABLE IF NOT EXISTS dwd.policy_measures (
    run_id BIGINT NOT NULL REFERENCES ops.crawl_runs(id),
    source_record_number INTEGER NOT NULL,
    topic_key TEXT NOT NULL,
    measure_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    section_id TEXT NOT NULL,
    section_path JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_url TEXT NOT NULL,
    region TEXT NOT NULL,
    industry_topic TEXT NOT NULL,
    industry_segments JSONB NOT NULL DEFAULT '[]'::jsonb,
    measure_text TEXT NOT NULL,
    evidence_text TEXT NOT NULL,
    record_type TEXT NOT NULL,
    implementation_stage TEXT NOT NULL,
    policy_tool_type_id INTEGER NOT NULL REFERENCES dwd.policy_tool_types(type_id),
    secondary_policy_tool_type_id INTEGER REFERENCES dwd.policy_tool_types(type_id),
    policy_target JSONB NOT NULL DEFAULT '[]'::jsonb,
    implementing_agencies JSONB NOT NULL DEFAULT '[]'::jsonb,
    time_scope TEXT,
    start_date DATE,
    end_date DATE,
    quantities JSONB NOT NULL DEFAULT '[]'::jsonb,
    strength_score INTEGER,
    specificity_score INTEGER,
    design_score INTEGER,
    design_score_100 DOUBLE PRECISION,
    score_version TEXT,
    score_reasons JSONB NOT NULL DEFAULT '{}'::jsonb,
    dedup_group_id TEXT,
    is_primary BOOLEAN,
    duplicate_of TEXT,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, source_record_number),
    FOREIGN KEY (topic_key, source_url)
        REFERENCES ods.policy_documents(topic_key, source_url),
    CHECK (record_type IN ('target', 'policy_action', 'implementation', 'result')),
    CHECK (implementation_stage IN (
        'planned', 'requested', 'allocated', 'disbursed', 'ongoing',
        'completed', 'verified', 'not_applicable', 'unknown'
    )),
    CHECK (secondary_policy_tool_type_id IS NULL
        OR secondary_policy_tool_type_id <> policy_tool_type_id),
    CHECK (strength_score IS NULL OR strength_score BETWEEN 1 AND 5),
    CHECK (specificity_score IS NULL OR specificity_score BETWEEN 1 AND 5),
    CHECK (design_score IS NULL OR design_score BETWEEN 1 AND 25),
    CHECK (design_score_100 IS NULL OR design_score_100 BETWEEN 4 AND 100),
    CHECK (start_date IS NULL OR end_date IS NULL OR start_date <= end_date),
    CHECK (jsonb_typeof(section_path) = 'array'),
    CHECK (jsonb_typeof(industry_segments) = 'array'),
    CHECK (jsonb_typeof(policy_target) = 'array'),
    CHECK (jsonb_typeof(implementing_agencies) = 'array'),
    CHECK (jsonb_typeof(quantities) = 'array')
);

CREATE INDEX IF NOT EXISTS dwd_policy_measures_region_topic_idx
ON dwd.policy_measures (region, industry_topic);

CREATE INDEX IF NOT EXISTS dwd_policy_measures_document_idx
ON dwd.policy_measures (topic_key, document_id);

CREATE INDEX IF NOT EXISTS dwd_policy_measures_measure_id_idx
ON dwd.policy_measures (measure_id);

CREATE TABLE IF NOT EXISTS dws.county_policy_scores (
    run_id BIGINT PRIMARY KEY REFERENCES ops.crawl_runs(id),
    score_version TEXT NOT NULL,
    aggregation_method TEXT NOT NULL,
    overall_unweighted_score_100 DOUBLE PRECISION NOT NULL,
    dimensions JSONB NOT NULL,
    raw_primary_measure_count INTEGER NOT NULL,
    excluded_duplicate_count INTEGER NOT NULL,
    warning TEXT,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ads.county_policy_portraits (
    run_id BIGINT PRIMARY KEY REFERENCES ops.crawl_runs(id),
    region TEXT NOT NULL,
    industry_topic TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    document_count INTEGER NOT NULL,
    measure_count INTEGER NOT NULL,
    coverage JSONB NOT NULL,
    policy_scores JSONB NOT NULL,
    quantitative_indicators JSONB NOT NULL,
    measures JSONB NOT NULL,
    note TEXT,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ads_policy_portraits_region_topic_idx
ON ads.county_policy_portraits (region, industry_topic, generated_at);
