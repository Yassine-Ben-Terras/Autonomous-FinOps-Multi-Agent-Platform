-- ============================================================
-- dbt model: stg_focus_billing
-- Staging layer — light transformations on the raw FOCUS table
-- ============================================================
-- Materialized as: incremental (appends only new charge_period dates)
-- Target: ClickHouse (dbt-clickhouse adapter)
-- ============================================================

{{
  config(
    materialized='incremental',
    engine='MergeTree()',
    order_by='(provider_name, billing_account_id, charge_period_start)',
    partition_by='toYYYYMM(charge_period_start)',
    unique_key='(provider_name, billing_account_id, resource_id, charge_period_start)',
    incremental_strategy='delete+insert'
  )
}}

WITH source AS (
    SELECT *
    FROM {{ source('focus', 'billing') }}
    {% if is_incremental() %}
    -- Only process dates not yet in the staging table
    WHERE charge_period_start > (
        SELECT coalesce(max(charge_period_start), '1970-01-01')
        FROM {{ this }}
    )
    {% endif %}
),

cleaned AS (
    SELECT
        -- Provider
        provider_name,
        billing_account_id,
        billing_account_name,

        -- Normalise sub-account: trim whitespace, default to account_id
        NULLIF(trim(sub_account_id), '')           AS sub_account_id,
        NULLIF(trim(sub_account_name), '')         AS sub_account_name,

        -- Time (ensure UTC)
        billing_period_start,
        billing_period_end,
        charge_period_start,
        charge_period_end,
        toDate(charge_period_start)                AS charge_date,
        toYYYYMM(charge_period_start)              AS charge_month,

        -- Charge classification
        charge_category,
        charge_frequency,

        -- Resource — normalise empty strings to NULL
        NULLIF(trim(resource_id),   '')            AS resource_id,
        NULLIF(trim(resource_name), '')            AS resource_name,
        NULLIF(trim(resource_type), '')            AS resource_type,

        -- Location normalisation
        NULLIF(lower(trim(region_id)),   '')       AS region_id,
        NULLIF(lower(trim(region_name)), '')       AS region_name,

        -- Service
        service_name,
        service_category,
        publisher_name,

        -- Costs: clip negative effective_cost to 0 (credits handled separately)
        billed_cost,
        greatest(effective_cost, 0)                AS effective_cost,
        list_cost,
        billing_currency,

        -- Usage
        usage_quantity,
        usage_unit,
        pricing_category,

        -- Commitment discounts
        NULLIF(commitment_discount_id, '')         AS commitment_discount_id,
        commitment_discount_type,

        -- Tags: extract most-used keys as separate columns for fast filtering
        tags['team']                               AS tag_team,
        tags['env']                                AS tag_env,
        tags['project']                            AS tag_project,
        tags['cost-center']                        AS tag_cost_center,
        tags,

        -- Derived: savings vs list price
        greatest(list_cost - effective_cost, 0)   AS savings_amount,
        CASE
            WHEN list_cost > 0
            THEN greatest(list_cost - effective_cost, 0) / list_cost * 100
            ELSE 0
        END                                        AS savings_pct,

        -- CloudSense metadata
        cs_ingested_at

    FROM source
    WHERE
        -- Remove zero-cost rows (credits are handled as separate negative rows)
        (billed_cost != 0 OR effective_cost != 0)
        -- Remove tax rows from cost analysis (kept in raw, excluded from analysis)
        AND charge_category != 'Tax'
)

SELECT * FROM cleaned
