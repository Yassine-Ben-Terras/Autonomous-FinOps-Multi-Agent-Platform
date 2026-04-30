"""
BI Tool Adapters (Phase 5.2).

Three adapters built on top of FocusExportEngine, each producing
the exact format expected by the target BI tool:

  LookerAdapter   — Parquet files + LookML dimension/measure manifest
  TableauAdapter  — XLSX hyper extract + Tableau Data Source (.tds) XML
  PowerBIAdapter  — XLSX with Power Query M-script + connection metadata

All adapters respect FOCUS 1.0 column names so downstream dashboards
are portable across tools.
"""
from __future__ import annotations

import io
import json
import textwrap
from datetime import datetime, timezone
from typing import Any

import structlog

from cloudsense.exporters.focus_export import ExportFormat, FocusExportEngine, ExportResult

logger = structlog.get_logger()


# ── Looker Adapter ─────────────────────────────────────────────────────────────

class LookerAdapter:
    """
    Produces a Parquet export + LookML manifest for Looker.

    Output:
      focus_export_{dates}.parquet   — columnar billing data
      focus_looker_manifest.json     — LookML dimension/measure hints
    """

    def __init__(self, engine: FocusExportEngine) -> None:
        self._engine = engine

    async def export(
        self,
        start_date: str,
        end_date: str,
        billing_account_ids: list[str] | None = None,
        providers: list[str] | None = None,
    ) -> dict[str, Any]:
        result = await self._engine.export(
            format=ExportFormat.PARQUET,
            start_date=start_date,
            end_date=end_date,
            billing_account_ids=billing_account_ids,
            providers=providers,
        )
        manifest = self._build_lookml_manifest(result)
        return {
            "parquet": result,
            "manifest": manifest,
            "instructions": (
                "1. Upload the .parquet file to your GCS/S3 bucket connected to Looker. "
                "2. Import the manifest.json as a LookML model to auto-generate dimensions/measures."
            ),
        }

    def _build_lookml_manifest(self, result: ExportResult) -> dict[str, Any]:
        return {
            "standard": "FOCUS 1.0",
            "generated_at": result.generated_at,
            "row_count": result.row_count,
            "view_name": "focus_billing",
            "dimensions": [
                {"name": "billing_account_id",  "type": "string",     "sql": "${TABLE}.BillingAccountId"},
                {"name": "provider",             "type": "string",     "sql": "${TABLE}.Provider"},
                {"name": "service_name",         "type": "string",     "sql": "${TABLE}.ServiceName"},
                {"name": "resource_id",          "type": "string",     "sql": "${TABLE}.ResourceId"},
                {"name": "region_id",            "type": "string",     "sql": "${TABLE}.RegionId"},
                {"name": "charge_category",      "type": "string",     "sql": "${TABLE}.ChargeCategory"},
                {"name": "billing_period_start", "type": "date",       "sql": "${TABLE}.BillingPeriodStart",
                 "datatype": "date", "timeframes": ["date", "week", "month", "quarter", "year"]},
            ],
            "measures": [
                {"name": "total_effective_cost",  "type": "sum",     "sql": "${TABLE}.EffectiveCost",
                 "value_format_name": "usd"},
                {"name": "total_list_cost",       "type": "sum",     "sql": "${TABLE}.ListCost",
                 "value_format_name": "usd"},
                {"name": "avg_effective_cost",    "type": "average", "sql": "${TABLE}.EffectiveCost",
                 "value_format_name": "usd"},
                {"name": "total_usage_quantity",  "type": "sum",     "sql": "${TABLE}.UsageQuantity"},
                {"name": "resource_count",        "type": "count_distinct", "sql": "${TABLE}.ResourceId"},
            ],
        }


# ── Tableau Adapter ────────────────────────────────────────────────────────────

class TableauAdapter:
    """
    Produces an XLSX export + Tableau Data Source (.tds) XML definition.

    The .tds file can be opened directly in Tableau Desktop to connect
    to the XLSX without manual column mapping.
    """

    def __init__(self, engine: FocusExportEngine) -> None:
        self._engine = engine

    async def export(
        self,
        start_date: str,
        end_date: str,
        billing_account_ids: list[str] | None = None,
        providers: list[str] | None = None,
    ) -> dict[str, Any]:
        result = await self._engine.export(
            format=ExportFormat.XLSX,
            start_date=start_date,
            end_date=end_date,
            billing_account_ids=billing_account_ids,
            providers=providers,
        )
        tds_xml = self._build_tds(result)
        return {
            "xlsx": result,
            "tds_xml": tds_xml,
            "instructions": (
                "1. Save the .xlsx file locally. "
                "2. Open the .tds file in Tableau Desktop — it points to the xlsx. "
                "3. Refresh data source to see the latest export."
            ),
        }

    def _build_tds(self, result: ExportResult) -> str:
        """Generate a Tableau Data Source XML (.tds) for the FOCUS export."""
        columns_xml = "\n".join([
            f'    <column datatype="{_tableau_type(col)}" name="[{col}]" role="{_tableau_role(col)}" type="{_tableau_role(col)}" />'
            for col in [
                "BillingAccountId", "Provider", "ServiceName", "ResourceId",
                "RegionId", "ChargeCategory", "BillingPeriodStart",
                "EffectiveCost", "ListCost", "UsageQuantity",
            ]
        ])
        return textwrap.dedent(f"""
        <?xml version='1.0' encoding='utf-8' ?>
        <datasource formatted-name='CloudSense FOCUS Export' inline='true'
                    name='cloudsense_focus' version='18.1'
                    xmlns:user='http://www.tableausoftware.com/xml/user'>
          <connection class='excel-direct'
                      filename='{result.filename}'
                      validate='no'>
            <relation name='FOCUS Billing' table='[FOCUS Billing$]' type='table' />
          </connection>
          <aliases enabled='yes' />
          <extract count='{result.row_count}' enabled='true' units='records'>
            <connection class='hyper' dbname='focus_export.hyper' schema='Extract' tablename='Extract' />
          </extract>
        {columns_xml}
          <_.fcp.ObjectModelTableType.true..._.fcp.ObjectModelTableType />
        </datasource>
        """).strip()


# ── Power BI Adapter ───────────────────────────────────────────────────────────

class PowerBIAdapter:
    """
    Produces an XLSX export + Power Query M-script for Power BI.

    The M-script can be pasted directly into Power BI Desktop's
    Advanced Query Editor to load the FOCUS data with correct types.
    """

    def __init__(self, engine: FocusExportEngine) -> None:
        self._engine = engine

    async def export(
        self,
        start_date: str,
        end_date: str,
        billing_account_ids: list[str] | None = None,
        providers: list[str] | None = None,
    ) -> dict[str, Any]:
        result = await self._engine.export(
            format=ExportFormat.XLSX,
            start_date=start_date,
            end_date=end_date,
            billing_account_ids=billing_account_ids,
            providers=providers,
        )
        m_script = self._build_power_query_m(result)
        pbids = self._build_pbids(result)
        return {
            "xlsx": result,
            "power_query_m": m_script,
            "pbids": pbids,
            "instructions": (
                "1. Open Power BI Desktop → Get Data → Excel. "
                "2. Select the .xlsx file and the 'FOCUS Billing' sheet. "
                "3. Alternatively, paste the M-script into Advanced Query Editor for typed columns."
            ),
        }

    def _build_power_query_m(self, result: ExportResult) -> str:
        return textwrap.dedent(f"""
        let
            Source = Excel.Workbook(File.Contents("{result.filename}"), null, true),
            FocusBilling_Sheet = Source{{[Item="FOCUS Billing",Kind="Sheet"]}}[Data],
            PromotedHeaders = Table.PromoteHeaders(FocusBilling_Sheet, [PromoteAllScalars=true]),
            ChangedTypes = Table.TransformColumnTypes(PromotedHeaders,
            {{
                {{"BillingPeriodStart", type date}},
                {{"BillingPeriodEnd",   type date}},
                {{"ChargePeriodStart",  type datetime}},
                {{"ChargePeriodEnd",    type datetime}},
                {{"EffectiveCost",      type number}},
                {{"ListCost",           type number}},
                {{"BilledCost",         type number}},
                {{"ContractedCost",     type number}},
                {{"UsageQuantity",      type number}},
                {{"BillingAccountId",   type text}},
                {{"Provider",           type text}},
                {{"ServiceName",        type text}},
                {{"ResourceId",         type text}},
                {{"RegionId",           type text}},
                {{"ChargeCategory",     type text}}
            }}),
            // Computed columns for FinOps dashboards
            AddedMonthYear = Table.AddColumn(ChangedTypes, "MonthYear",
                each Date.ToText([BillingPeriodStart], "yyyy-MM"), type text),
            AddedDiscountPct = Table.AddColumn(AddedMonthYear, "DiscountPct",
                each if [ListCost] > 0 then ([ListCost] - [EffectiveCost]) / [ListCost] else 0,
                type number)
        in
            AddedDiscountPct
        """).strip()

    def _build_pbids(self, result: ExportResult) -> dict[str, Any]:
        """Power BI Data Source (.pbids) connection file."""
        return {
            "version": "0.1",
            "connections": [
                {
                    "details": {
                        "protocol": "file",
                        "address": {"path": result.filename},
                    },
                    "mode": "Import",
                }
            ],
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _tableau_type(col: str) -> str:
    date_cols = {"BillingPeriodStart", "BillingPeriodEnd", "ChargePeriodStart", "ChargePeriodEnd"}
    num_cols = {"EffectiveCost", "ListCost", "BilledCost", "ContractedCost", "UsageQuantity",
                "ListUnitPrice", "ContractedUnitPrice"}
    if col in date_cols:
        return "date"
    if col in num_cols:
        return "real"
    return "string"


def _tableau_role(col: str) -> str:
    num_cols = {"EffectiveCost", "ListCost", "BilledCost", "ContractedCost", "UsageQuantity",
                "ListUnitPrice", "ContractedUnitPrice"}
    return "measure" if col in num_cols else "dimension"
