"""
CloudSense Phase 5.3 — Enterprise Audit Log Exporter
======================================================
Exports the CloudSense immutable audit trail to enterprise SIEM
and compliance systems.

Supported destinations
----------------------
  splunk      — Splunk HTTP Event Collector (HEC) — JSON over HTTPS
  datadog     — Datadog Logs API (v2) — JSON batch ingestion
  cloudtrail  — AWS CloudTrail-compatible JSON (write to S3)
  jsonl       — Raw JSONL file (universal fallback, good for Elastic/Loki)

Audit event schema
------------------
Every event CloudSense records is emitted in this structure:

{
  "event_id":     "uuid",
  "event_type":   "action.executed | insight.approved | user.login | ...",
  "actor_id":     "user-uuid or agent-name",
  "actor_type":   "user | agent | system",
  "tenant_slug":  "acme-corp",
  "resource_id":  "i-abc123",
  "resource_type":"ec2:instance",
  "provider":     "aws | azure | gcp",
  "outcome":      "success | failure",
  "severity":     "info | warning | critical",
  "details":      { ... },
  "timestamp":    "2024-01-15T12:34:56.789Z",
  "ip_address":   "1.2.3.4"
}

Usage
-----
    exporter = AuditExporter(settings)

    # Splunk
    await exporter.export_to_splunk(events, batch_size=500)

    # Datadog
    await exporter.export_to_datadog(events)

    # CloudTrail S3
    await exporter.export_to_cloudtrail(events, s3_bucket="my-audit-bucket")

    # JSONL file
    await exporter.export_to_jsonl(events, path="/var/log/cloudsense/audit.jsonl")
"""
from __future__ import annotations

import asyncio
import gzip
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import structlog

from cloudsense.services.api.config import Settings, get_settings

logger = structlog.get_logger()


# ── Audit event model ─────────────────────────────────────────────────────────

class AuditEvent:
    """
    Single immutable audit event.
    All fields are set at construction time; the object is frozen.
    """

    __slots__ = (
        "event_id", "event_type", "actor_id", "actor_type",
        "tenant_slug", "resource_id", "resource_type", "provider",
        "outcome", "severity", "details", "timestamp", "ip_address",
    )

    def __init__(
        self,
        event_type:   str,
        actor_id:     str           = "system",
        actor_type:   str           = "system",
        tenant_slug:  str           = "",
        resource_id:  str           = "",
        resource_type: str          = "",
        provider:     str           = "",
        outcome:      str           = "success",
        severity:     str           = "info",
        details:      dict[str, Any] | None = None,
        ip_address:   str           = "",
        event_id:     str | None    = None,
        timestamp:    datetime | None = None,
    ) -> None:
        object.__setattr__(self, "event_id",     event_id or str(uuid4()))
        object.__setattr__(self, "event_type",   event_type)
        object.__setattr__(self, "actor_id",     actor_id)
        object.__setattr__(self, "actor_type",   actor_type)
        object.__setattr__(self, "tenant_slug",  tenant_slug)
        object.__setattr__(self, "resource_id",  resource_id)
        object.__setattr__(self, "resource_type", resource_type)
        object.__setattr__(self, "provider",     provider)
        object.__setattr__(self, "outcome",      outcome)
        object.__setattr__(self, "severity",     severity)
        object.__setattr__(self, "details",      details or {})
        object.__setattr__(self, "ip_address",   ip_address)
        object.__setattr__(self, "timestamp",
                           timestamp or datetime.now(timezone.utc))

    def __setattr__(self, *_: Any) -> None:
        raise AttributeError("AuditEvent is immutable")

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id":     self.event_id,
            "event_type":   self.event_type,
            "actor_id":     self.actor_id,
            "actor_type":   self.actor_type,
            "tenant_slug":  self.tenant_slug,
            "resource_id":  self.resource_id,
            "resource_type": self.resource_type,
            "provider":     self.provider,
            "outcome":      self.outcome,
            "severity":     self.severity,
            "details":      self.details,
            "timestamp":    self.timestamp.isoformat(),
            "ip_address":   self.ip_address,
            "source":       "cloudsense",
        }


# ── Main exporter ─────────────────────────────────────────────────────────────

class AuditExporter:
    """
    Multi-destination audit log exporter.
    All export methods are async and idempotent (safe to retry).
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._http     = httpx.AsyncClient(timeout=30.0)

    async def __aenter__(self) -> "AuditExporter":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self._http.aclose()

    # ── Splunk HEC ────────────────────────────────────────────────────────────

    async def export_to_splunk(
        self,
        events:       list[AuditEvent],
        hec_url:      str | None = None,
        hec_token:    str | None = None,
        index:        str        = "cloudsense_audit",
        source:       str        = "cloudsense",
        batch_size:   int        = 500,
    ) -> dict[str, Any]:
        """
        Send audit events to Splunk via the HTTP Event Collector.

        Splunk HEC endpoint: https://<splunk-host>:8088/services/collector/event
        Token: created in Splunk Settings → Data Inputs → HTTP Event Collector.

        Events are sent in batches of up to batch_size to respect HEC limits.
        """
        url   = hec_url   or self._settings.splunk_hec_url   or ""
        token = hec_token or (
            self._settings.splunk_hec_token.get_secret_value()
            if getattr(self._settings, "splunk_hec_token", None)
            else ""
        )

        if not url or not token:
            raise ValueError(
                "Splunk HEC URL and token are required. "
                "Set SPLUNK_HEC_URL and SPLUNK_HEC_TOKEN in .env"
            )

        headers = {
            "Authorization": f"Splunk {token}",
            "Content-Type": "application/json",
        }

        sent_total  = 0
        error_count = 0

        for i in range(0, len(events), batch_size):
            batch = events[i:i + batch_size]
            # Splunk HEC expects newline-delimited JSON objects (not a JSON array)
            payload = "\n".join(
                json.dumps({
                    "time":       int(evt.timestamp.timestamp()),
                    "host":       "cloudsense",
                    "source":     source,
                    "sourcetype": "cloudsense:audit",
                    "index":      index,
                    "event":      evt.to_dict(),
                })
                for evt in batch
            )

            try:
                resp = await self._http.post(url, content=payload, headers=headers)
                resp.raise_for_status()
                sent_total += len(batch)
                logger.info(
                    "audit.splunk.batch_sent",
                    batch=i // batch_size + 1,
                    count=len(batch),
                )
            except Exception as exc:
                error_count += len(batch)
                logger.error("audit.splunk.batch_failed", error=str(exc))

        return {
            "destination": "splunk",
            "sent":        sent_total,
            "errors":      error_count,
            "batches":     (len(events) + batch_size - 1) // batch_size,
        }

    # ── Datadog Logs API ──────────────────────────────────────────────────────

    async def export_to_datadog(
        self,
        events:      list[AuditEvent],
        api_key:     str | None = None,
        dd_site:     str        = "datadoghq.com",
        service:     str        = "cloudsense",
        batch_size:  int        = 1000,
    ) -> dict[str, Any]:
        """
        Send audit events to Datadog via the Logs Intake API v2.

        Endpoint: https://http-intake.logs.<dd_site>/api/v2/logs
        Docs: https://docs.datadoghq.com/api/latest/logs/#send-logs
        """
        key = api_key or (
            self._settings.datadog_api_key.get_secret_value()
            if getattr(self._settings, "datadog_api_key", None)
            else ""
        )
        if not key:
            raise ValueError(
                "Datadog API key required. Set DATADOG_API_KEY in .env"
            )

        url     = f"https://http-intake.logs.{dd_site}/api/v2/logs"
        headers = {
            "DD-API-KEY":    key,
            "Content-Type": "application/json",
        }

        sent_total  = 0
        error_count = 0

        for i in range(0, len(events), batch_size):
            batch = events[i:i + batch_size]
            payload = json.dumps([
                {
                    "ddsource":  "cloudsense",
                    "ddtags":    (
                        f"env:{getattr(self._settings, 'app_env', 'production')},"
                        f"provider:{evt.provider},"
                        f"event_type:{evt.event_type}"
                    ),
                    "hostname":  "cloudsense",
                    "message":   json.dumps(evt.to_dict()),
                    "service":   service,
                    "status":    evt.severity,
                }
                for evt in batch
            ])

            try:
                resp = await self._http.post(url, content=payload, headers=headers)
                resp.raise_for_status()
                sent_total += len(batch)
                logger.info("audit.datadog.batch_sent", count=len(batch))
            except Exception as exc:
                error_count += len(batch)
                logger.error("audit.datadog.batch_failed", error=str(exc))

        return {
            "destination": "datadog",
            "sent":        sent_total,
            "errors":      error_count,
        }

    # ── AWS CloudTrail-compatible S3 export ──────────────────────────────────

    async def export_to_cloudtrail(
        self,
        events:     list[AuditEvent],
        s3_bucket:  str,
        s3_prefix:  str  = "CloudSense/AuditLogs",
        region:     str  = "us-east-1",
        compress:   bool = True,
    ) -> dict[str, Any]:
        """
        Write audit events to S3 in AWS CloudTrail JSON format.

        Produces files compatible with Athena CloudTrail queries and
        AWS Security Hub / GuardDuty log ingestion.

        S3 key pattern:
          {prefix}/{year}/{month}/{day}/{uuid}.json.gz
        """
        import boto3

        if not events:
            return {"destination": "cloudtrail_s3", "sent": 0, "s3_keys": []}

        now     = datetime.now(timezone.utc)
        s3      = boto3.client("s3", region_name=region)
        s3_keys: list[str] = []
        loop    = asyncio.get_event_loop()

        # Group by day so each file covers one day of events
        from collections import defaultdict
        by_day: dict[str, list[AuditEvent]] = defaultdict(list)
        for evt in events:
            day_key = evt.timestamp.strftime("%Y/%m/%d")
            by_day[day_key].append(evt)

        for day_key, day_events in by_day.items():
            ct_record = {
                "Records": [
                    {
                        # CloudTrail standard fields
                        "eventVersion":       "1.09",
                        "eventSource":        "cloudsense.io",
                        "eventName":          evt.event_type,
                        "eventTime":          evt.timestamp.isoformat(),
                        "eventID":            evt.event_id,
                        "awsRegion":          evt.provider == "aws" and "us-east-1" or "global",
                        "sourceIPAddress":    evt.ip_address or "cloudsense.io",
                        "userAgent":          "CloudSense/0.5.0",
                        "userIdentity": {
                            "type":       evt.actor_type,
                            "principalId": evt.actor_id,
                        },
                        "resources": [
                            {
                                "ARN":          evt.resource_id,
                                "resourceType": evt.resource_type,
                            }
                        ] if evt.resource_id else [],
                        "errorCode":    None if evt.outcome == "success" else "CloudSenseError",
                        "requestParameters": evt.details,
                        "responseElements":  {"outcome": evt.outcome},
                        # CloudSense-specific extension
                        "additionalEventData": {
                            "tenantSlug":  evt.tenant_slug,
                            "provider":    evt.provider,
                            "severity":    evt.severity,
                        },
                    }
                    for evt in day_events
                ]
            }

            body = json.dumps(ct_record, indent=None, default=str).encode("utf-8")
            if compress:
                body = gzip.compress(body)

            ext    = ".json.gz" if compress else ".json"
            s3_key = f"{s3_prefix}/{day_key}/{uuid4()}{ext}"

            await loop.run_in_executor(
                None,
                lambda b=body, k=s3_key: s3.put_object(
                    Bucket=s3_bucket, Key=k, Body=b,
                    ContentType="application/json",
                    ContentEncoding="gzip" if compress else "identity",
                    ServerSideEncryption="AES256",
                )
            )
            s3_keys.append(f"s3://{s3_bucket}/{s3_key}")
            logger.info("audit.cloudtrail.written", key=s3_key, events=len(day_events))

        return {
            "destination": "cloudtrail_s3",
            "sent":        len(events),
            "s3_keys":     s3_keys,
            "bucket":      s3_bucket,
        }

    # ── JSONL file export ─────────────────────────────────────────────────────

    async def export_to_jsonl(
        self,
        events:  list[AuditEvent],
        path:    str | Path,
        append:  bool = True,
        compress: bool = False,
    ) -> dict[str, Any]:
        """
        Append audit events to a local JSONL file.
        Compatible with Elasticsearch, Loki, Fluentd, Filebeat.

        One JSON object per line — no array wrapping.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        mode    = "ab" if (append and not compress) else "wb"
        lines   = (
            json.dumps(evt.to_dict(), default=str).encode("utf-8") + b"\n"
            for evt in events
        )
        content = b"".join(lines)

        loop = asyncio.get_event_loop()

        if compress:
            gz_path = path.with_suffix(path.suffix + ".gz")
            existing = gz_path.read_bytes() if (append and gz_path.exists()) else b""
            merged   = gzip.decompress(existing) + content if existing else content
            await loop.run_in_executor(
                None,
                lambda: gz_path.write_bytes(gzip.compress(merged))
            )
            written_path = str(gz_path)
        else:
            await loop.run_in_executor(
                None,
                lambda: path.open(mode).write(content) and None
            )
            written_path = str(path)

        logger.info("audit.jsonl.written", path=written_path, events=len(events))
        return {
            "destination": "jsonl",
            "sent":        len(events),
            "path":        written_path,
            "size_bytes":  len(content),
        }

    # ── Batch export to all configured destinations ───────────────────────────

    async def export_all(
        self,
        events:       list[AuditEvent],
        destinations: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Export events to all configured destinations simultaneously.

        If destinations is None, uses AUDIT_EXPORT_DESTINATIONS env var
        (comma-separated: splunk,datadog,cloudtrail,jsonl).
        """
        if destinations is None:
            env_val      = os.environ.get("AUDIT_EXPORT_DESTINATIONS", "jsonl")
            destinations = [d.strip() for d in env_val.split(",") if d.strip()]

        tasks:   dict[str, Any] = {}
        results: dict[str, Any] = {}

        for dest in destinations:
            if dest == "splunk":
                tasks[dest] = self.export_to_splunk(events)
            elif dest == "datadog":
                tasks[dest] = self.export_to_datadog(events)
            elif dest == "cloudtrail":
                bucket = os.environ.get("AUDIT_S3_BUCKET", "")
                if bucket:
                    tasks[dest] = self.export_to_cloudtrail(events, s3_bucket=bucket)
                else:
                    results[dest] = {"error": "AUDIT_S3_BUCKET not set"}
            elif dest == "jsonl":
                path = os.environ.get("AUDIT_JSONL_PATH", "/var/log/cloudsense/audit.jsonl")
                tasks[dest] = self.export_to_jsonl(events, path=path)
            else:
                results[dest] = {"error": f"Unknown destination: {dest}"}

        # Run all configured destinations concurrently
        if tasks:
            done = await asyncio.gather(
                *tasks.values(), return_exceptions=True
            )
            for dest, result in zip(tasks.keys(), done):
                results[dest] = result if not isinstance(result, Exception) else {
                    "error": str(result)
                }

        return {
            "total_events":   len(events),
            "destinations":   destinations,
            "results":        results,
            "exported_at":    datetime.now(timezone.utc).isoformat(),
        }
