-- CloudSense — ClickHouse Schema Bootstrap
-- This script runs on first container start.

CREATE DATABASE IF NOT EXISTS cloudsense;

-- FOCUS 1.0 normalized billing records
CREATE TABLE IF NOT EXISTS cloudsense.focus_records
(
    id                      UUID,
    provider                LowCardinality(String),
    billing_account_id      String,
    billing_account_name    Nullable(String),
    resource_id             Nullable(String),
    resource_name           Nullable(String),
    resource_type           Nullable(LowCardinality(String)),
    service_name            LowCardinality(String),
    service_category        Nullable(LowCardinality(String)),
    region_id               Nullable(LowCardinality(String)),
    availability_zone       Nullable(String),
    billing_period_start    DateTime,
    billing_period_end      DateTime,
    charge_period_start     DateTime,
    charge_period_end       DateTime,
    effective_cost          Decimal(18, 6),
    list_cost               Decimal(18, 6),
    billed_cost             Decimal(18, 6),
    contracted_cost         Nullable(Decimal(18, 6)),
    currency                LowCardinality(FixedString(3)),
    usage_quantity          Nullable(Decimal(18, 6)),
    usage_unit              Nullable(LowCardinality(String)),
    charge_category         LowCardinality(String),
    charge_class            Nullable(String),
    charge_frequency        Nullable(LowCardinality(String)),
    charge_description      Nullable(String),
    commitment_discount_id  Nullable(String),
    commitment_discount_type Nullable(LowCardinality(String)),
    sub_account_id          Nullable(String),
    sub_account_name        Nullable(String),
    tags                    Map(String, String),
    ingested_at             DateTime DEFAULT now()
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(billing_period_start)
ORDER BY (provider, billing_account_id, billing_period_start, service_name)
SETTINGS index_granularity = 8192;

-- Cost anomalies detected by the Anomaly Agent
CREATE TABLE IF NOT EXISTS cloudsense.cost_anomalies
(
    id              UUID,
    provider        LowCardinality(String),
    billing_account_id String,
    detected_at     DateTime,
    period_start    DateTime,
    period_end      DateTime,
    service_name    Nullable(LowCardinality(String)),
    region_id       Nullable(LowCardinality(String)),
    resource_id     Nullable(String),
    expected_cost   Decimal(18, 6),
    actual_cost     Decimal(18, 6),
    anomaly_score   Float32,
    root_cause      Nullable(String)
)
ENGINE = MergeTree()
ORDER BY (provider, detected_at)
TTL detected_at + INTERVAL 1 YEAR;
