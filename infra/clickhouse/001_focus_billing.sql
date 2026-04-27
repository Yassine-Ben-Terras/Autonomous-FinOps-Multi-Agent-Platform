-- ============================================================
-- CloudSense · ClickHouse DDL
-- FOCUS 1.0 billing table — optimised for FinOps query patterns
-- Engine: ReplacingMergeTree (idempotent re-ingestion)
-- ============================================================

CREATE DATABASE IF NOT EXISTS focus;

-- ── Main billing fact table ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS focus.billing
(
    -- Provider / account hierarchy
    provider_name              LowCardinality(String),
    billing_account_id         String,
    billing_account_name       String,
    sub_account_id             String,
    sub_account_name           String,

    -- Time window
    billing_period_start       DateTime64(3, 'UTC'),
    billing_period_end         DateTime64(3, 'UTC'),
    charge_period_start        DateTime64(3, 'UTC'),
    charge_period_end          DateTime64(3, 'UTC'),

    -- Charge metadata
    charge_category            LowCardinality(String),
    charge_frequency           LowCardinality(String),
    charge_description         String,

    -- Resource
    resource_id                String,
    resource_name              String,
    resource_type              LowCardinality(String),
    region_id                  LowCardinality(String),
    region_name                LowCardinality(String),
    availability_zone          LowCardinality(String),

    -- Service
    service_name               LowCardinality(String),
    service_category           LowCardinality(String),
    publisher_name             LowCardinality(String),

    -- Cost dimensions (stored as Float64 for aggregation perf)
    billed_cost                Float64,
    effective_cost             Float64,
    list_cost                  Float64,
    list_unit_price            Float64,
    contracted_cost            Float64,
    contracted_unit_price      Float64,
    billing_currency           LowCardinality(String),

    -- Usage dimensions
    usage_quantity             Nullable(Float64),
    usage_unit                 LowCardinality(String),
    pricing_quantity           Nullable(Float64),
    pricing_unit               LowCardinality(String),
    pricing_category           LowCardinality(String),

    -- Commitment discount columns
    commitment_discount_id     String,
    commitment_discount_name   String,
    commitment_discount_type   LowCardinality(String),
    commitment_discount_status LowCardinality(String),

    -- Tags stored as Map for arbitrary key-value lookups
    tags                       Map(String, String),

    -- CloudSense internal
    cs_ingested_at             DateTime64(3, 'UTC') DEFAULT now64()
)
ENGINE = ReplacingMergeTree(cs_ingested_at)
-- Primary key drives the MergeTree index — chosen for most common FinOps query patterns
PRIMARY KEY (provider_name, billing_account_id, charge_period_start)
ORDER BY   (provider_name, billing_account_id, charge_period_start,
            service_name, resource_id, billing_currency)
-- Monthly partitions — makes dropping old data trivial
PARTITION BY toYYYYMM(charge_period_start)
-- TTL: keep raw data for 18 months (configurable via environment)
TTL charge_period_start + INTERVAL 18 MONTH
SETTINGS
    -- Collapse duplicates during merge (idempotent ingestion)
    allow_nullable_key = 1,
    index_granularity  = 8192;

-- ── Tag index for fast tag-based filtering ────────────────────────────────────
ALTER TABLE focus.billing
    ADD INDEX idx_tag_env  mapKeys(tags) TYPE bloom_filter GRANULARITY 4,
    ADD INDEX idx_tag_team (tags['team']) TYPE bloom_filter GRANULARITY 4,
    ADD INDEX idx_resource_id resource_id TYPE bloom_filter GRANULARITY 4;


-- ── Materialized view: daily cost rollup per service ─────────────────────────
--   Queried by the dashboard for the cost-over-time chart.
CREATE TABLE IF NOT EXISTS focus.mv_daily_cost_target
(
    provider_name     LowCardinality(String),
    billing_account_id String,
    service_name      LowCardinality(String),
    region_id         LowCardinality(String),
    charge_date       Date,
    billing_currency  LowCardinality(String),
    total_effective   AggregateFunction(sum, Float64),
    total_list        AggregateFunction(sum, Float64),
    total_billed      AggregateFunction(sum, Float64)
)
ENGINE = AggregatingMergeTree()
ORDER BY (provider_name, billing_account_id, service_name, region_id, charge_date, billing_currency)
PARTITION BY toYYYYMM(charge_date);

CREATE MATERIALIZED VIEW IF NOT EXISTS focus.mv_daily_cost
TO focus.mv_daily_cost_target
AS
SELECT
    provider_name,
    billing_account_id,
    service_name,
    region_id,
    toDate(charge_period_start)   AS charge_date,
    billing_currency,
    sumState(effective_cost)      AS total_effective,
    sumState(list_cost)           AS total_list,
    sumState(billed_cost)         AS total_billed
FROM focus.billing
GROUP BY
    provider_name, billing_account_id, service_name,
    region_id, charge_date, billing_currency;


-- ── Materialized view: tag-team cost allocation ───────────────────────────────
--   Used by chargeback / showback reports.
CREATE TABLE IF NOT EXISTS focus.mv_team_cost_target
(
    provider_name  LowCardinality(String),
    team_tag       String,
    env_tag        String,
    charge_date    Date,
    total_effective AggregateFunction(sum, Float64)
)
ENGINE = AggregatingMergeTree()
ORDER BY (provider_name, team_tag, env_tag, charge_date);

CREATE MATERIALIZED VIEW IF NOT EXISTS focus.mv_team_cost
TO focus.mv_team_cost_target
AS
SELECT
    provider_name,
    tags['team']                 AS team_tag,
    tags['env']                  AS env_tag,
    toDate(charge_period_start)  AS charge_date,
    sumState(effective_cost)     AS total_effective
FROM focus.billing
GROUP BY provider_name, team_tag, env_tag, charge_date;


-- ── Helper: readable query for dashboard (use sumMerge to resolve AggState) ───
-- Example: SELECT provider_name, service_name, charge_date,
--                 sumMerge(total_effective) AS cost
--          FROM focus.mv_daily_cost_target
--          WHERE charge_date >= today() - 30
--          GROUP BY provider_name, service_name, charge_date
--          ORDER BY charge_date;
