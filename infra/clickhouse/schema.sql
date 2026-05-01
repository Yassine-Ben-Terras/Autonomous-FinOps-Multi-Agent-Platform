-- CloudSense ClickHouse Schema
-- FOCUS 1.0 billing table with optimized partitioning and indexing

CREATE DATABASE IF NOT EXISTS cloudsense;

CREATE TABLE IF NOT EXISTS cloudsense.focus_billing (
    billing_account_id LowCardinality(String),
    billing_period_start Date,
    billing_period_end Date,
    charge_period_start DateTime,
    charge_period_end DateTime,
    resource_id String,
    resource_name String,
    resource_type LowCardinality(String),
    service_name LowCardinality(String),
    service_category LowCardinality(String),
    region_id LowCardinality(String),
    region_name LowCardinality(String),
    availability_zone LowCardinality(String),
    list_cost Float64,
    effective_cost Float64,
    amortized_cost Float64 DEFAULT 0,
    usage_quantity Float64,
    usage_unit LowCardinality(String),
    charge_category LowCardinality(String),
    charge_subcategory LowCardinality(String),
    pricing_category LowCardinality(String),
    commitment_discount_id String,
    tags Map(String, String),
    provider LowCardinality(String),
    provider_account_id LowCardinality(String),
    billing_currency LowCardinality(String) DEFAULT 'USD',
    ingested_at DateTime DEFAULT now()
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(billing_period_start)
ORDER BY (provider, billing_account_id, service_name, billing_period_start)
TTL billing_period_start + INTERVAL 3 YEAR
SETTINGS index_granularity = 8192;

-- Materialized view: daily cost aggregation
CREATE MATERIALIZED VIEW IF NOT EXISTS cloudsense.mv_daily_costs
ENGINE = SummingMergeTree()
PARTITION BY toYYYYMM(day)
ORDER BY (provider, billing_account_id, service_name, day)
AS SELECT
    provider,
    billing_account_id,
    service_name,
    service_category,
    region_id,
    toDate(charge_period_start) AS day,
    sum(effective_cost) AS total_effective_cost,
    sum(list_cost) AS total_list_cost,
    sum(usage_quantity) AS total_usage_quantity,
    count() AS row_count
FROM cloudsense.focus_billing
GROUP BY provider, billing_account_id, service_name, service_category, region_id, day;

-- Materialized view: monthly service summary
CREATE MATERIALIZED VIEW IF NOT EXISTS cloudsense.mv_monthly_services
ENGINE = SummingMergeTree()
PARTITION BY toYYYYMM(month)
ORDER BY (provider, service_name, month)
AS SELECT
    provider,
    service_name,
    service_category,
    toStartOfMonth(charge_period_start) AS month,
    sum(effective_cost) AS total_cost,
    sum(usage_quantity) AS total_usage,
    count() AS record_count
FROM cloudsense.focus_billing
GROUP BY provider, service_name, service_category, month;
