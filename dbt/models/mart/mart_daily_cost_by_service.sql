-- ============================================================
-- dbt model: mart_daily_cost_by_service
-- Mart layer — pre-aggregated daily cost for dashboard charts
-- ============================================================
{{
  config(
    materialized='incremental',
    engine='SummingMergeTree()',
    order_by='(provider_name, service_name, charge_date)',
    partition_by='toYYYYMM(charge_date)',
    unique_key='(provider_name, billing_account_id, service_name, region_id, charge_date)',
    incremental_strategy='delete+insert'
  )
}}

SELECT
    provider_name,
    billing_account_id,
    billing_account_name,
    service_name,
    service_category,
    region_id,
    tag_team,
    tag_env,
    charge_date,
    billing_currency,

    -- Cost aggregates
    SUM(effective_cost)   AS total_effective_cost,
    SUM(list_cost)        AS total_list_cost,
    SUM(billed_cost)      AS total_billed_cost,
    SUM(savings_amount)   AS total_savings,

    -- Usage
    SUM(usage_quantity)   AS total_usage_quantity,
    MAX(usage_unit)       AS usage_unit,

    -- Resource count (distinct resources active that day)
    COUNT(DISTINCT resource_id) AS resource_count,

    -- Commitment utilisation
    countIf(commitment_discount_type != 'None') AS commitment_line_items

FROM {{ ref('stg_focus_billing') }}

{% if is_incremental() %}
WHERE charge_date > (
    SELECT coalesce(max(charge_date), '1970-01-01')
    FROM {{ this }}
)
{% endif %}

GROUP BY
    provider_name, billing_account_id, billing_account_name,
    service_name, service_category, region_id,
    tag_team, tag_env, charge_date, billing_currency
