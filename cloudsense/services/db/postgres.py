"""
PostgreSQL Action Log Repository (Phase 4).

Provides persistence for:
  - action_approvals table  — pending / approved / rejected action requests
  - audit_log table         — immutable event stream
  - rollback_plans          — stored rollback descriptors (JSONB)

Uses asyncpg for performance; no heavy ORM on the hot path.
SQLAlchemy async engine also available for ORM models (via get_db dependency).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import structlog

logger = structlog.get_logger()


class ActionLogRepository:
    """
    Async repository for action approvals, rollback plans, and audit logs.

    Usage:
        repo = ActionLogRepository(dsn)
        await repo.connect()
        await repo.save_rollback_plan(action_id, plan)
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: Any = None  # asyncpg.Pool

    async def connect(self) -> None:
        try:
            import asyncpg
            self._pool = await asyncpg.create_pool(self._dsn, min_size=2, max_size=10)
            logger.info("action_log_repo_connected")
        except Exception as exc:
            logger.warning("action_log_repo_connect_failed", error=str(exc))
            self._pool = None

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None

    # ── Action Approvals ────────────────────────────────────────

    async def create_approval_request(
        self,
        action_id: str,
        recommendation_id: str,
        provider: str,
        environment: str,
        action_type: str,
        target_resource_id: str,
        parameters: dict[str, Any],
        rollback_plan: dict[str, Any],
        requested_by: str,
        rollback_window_days: int = 7,
    ) -> str:
        """Insert a new action approval request. Returns action_id."""
        if not self._pool:
            logger.warning("create_approval_skip", reason="Pool not connected")
            return action_id
        expires_at = datetime.now(tz=timezone.utc) + timedelta(days=7)
        await self._pool.execute(
            """
            INSERT INTO action_approvals
              (id, recommendation_id, provider, environment, action_type,
               target_resource_id, parameters, rollback_plan, status,
               requested_by, expires_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'pending',$9,$10)
            ON CONFLICT (id) DO NOTHING
            """,
            action_id, recommendation_id, provider, environment, action_type,
            target_resource_id, json.dumps(parameters), json.dumps(rollback_plan),
            requested_by, expires_at,
        )
        await self._audit("action.created", action_id, "action_approvals", requested_by,
                          {"action_type": action_type, "provider": provider})
        return action_id

    async def approve_action(self, action_id: str, approved_by: str) -> bool:
        if not self._pool:
            return False
        result = await self._pool.execute(
            "UPDATE action_approvals SET status='approved', approved_by=$2, updated_at=NOW() WHERE id=$1 AND status='pending'",
            action_id, approved_by,
        )
        ok = result == "UPDATE 1"
        if ok:
            await self._audit("action.approved", action_id, "action_approvals", approved_by, {})
        return ok

    async def reject_action(self, action_id: str, rejected_by: str, reason: str) -> bool:
        if not self._pool:
            return False
        result = await self._pool.execute(
            "UPDATE action_approvals SET status='rejected', rejected_reason=$3, updated_at=NOW() WHERE id=$1 AND status='pending'",
            action_id, rejected_by, reason,
        )
        ok = result == "UPDATE 1"
        if ok:
            await self._audit("action.rejected", action_id, "action_approvals", rejected_by, {"reason": reason})
        return ok

    async def get_action(self, action_id: str) -> dict[str, Any] | None:
        if not self._pool:
            return None
        row = await self._pool.fetchrow("SELECT * FROM action_approvals WHERE id=$1", action_id)
        if not row:
            return None
        d = dict(row)
        for k in ("parameters", "rollback_plan"):
            if d.get(k) and isinstance(d[k], str):
                d[k] = json.loads(d[k])
        return d

    async def list_pending_actions(self, environment: str | None = None) -> list[dict[str, Any]]:
        if not self._pool:
            return []
        sql = "SELECT * FROM action_approvals WHERE status='pending'"
        params: list[Any] = []
        if environment:
            sql += " AND environment=$1"
            params.append(environment)
        sql += " ORDER BY created_at DESC"
        rows = await self._pool.fetch(sql, *params)
        result = []
        for row in rows:
            d = dict(row)
            for k in ("parameters", "rollback_plan"):
                if d.get(k) and isinstance(d[k], str):
                    d[k] = json.loads(d[k])
            result.append(d)
        return result

    # ── Rollback Plans ──────────────────────────────────────────

    async def save_rollback_plan(self, action_id: str, plan: dict[str, Any]) -> None:
        if not self._pool:
            logger.warning("rollback_persist_skip", reason="Pool not connected")
            return
        try:
            await self._pool.execute(
                "UPDATE action_approvals SET rollback_plan=$2, updated_at=NOW() WHERE id=$1",
                action_id, json.dumps(plan),
            )
        except Exception as exc:
            logger.error("rollback_plan_save_failed", action_id=action_id, error=str(exc))

    async def load_rollback_plan(self, action_id: str) -> dict[str, Any] | None:
        if not self._pool:
            return None
        try:
            row = await self._pool.fetchrow(
                "SELECT rollback_plan, expires_at FROM action_approvals WHERE id=$1", action_id
            )
        except Exception:
            return None
        if not row:
            return None
        plan_raw = row["rollback_plan"]
        plan = json.loads(plan_raw) if isinstance(plan_raw, str) else (plan_raw or {})
        expires_at = row["expires_at"]
        return {
            "plan": plan,
            "expires_at": expires_at.isoformat() if expires_at else None,
            "registered_at": datetime.now(tz=timezone.utc).isoformat(),
        }

    async def mark_action_executed(self, action_id: str) -> None:
        if not self._pool:
            return
        try:
            await self._pool.execute(
                "UPDATE action_approvals SET status='completed', executed_at=NOW(), updated_at=NOW() WHERE id=$1",
                action_id,
            )
            await self._audit("action.executed", action_id, "action_approvals", "agent", {})
        except Exception as exc:
            logger.error("mark_executed_failed", action_id=action_id, error=str(exc))

    async def mark_action_rolled_back(self, action_id: str) -> None:
        if not self._pool:
            return
        try:
            await self._pool.execute(
                "UPDATE action_approvals SET status='rolled_back', updated_at=NOW() WHERE id=$1",
                action_id,
            )
            await self._audit("action.rolled_back", action_id, "action_approvals", "agent", {})
        except Exception as exc:
            logger.error("mark_rolled_back_failed", action_id=action_id, error=str(exc))

    # ── Audit Log ───────────────────────────────────────────────

    async def write_audit_event(
        self,
        event_type: str,
        actor_id: str | None,
        resource_type: str,
        resource_id: str,
        payload: dict[str, Any],
    ) -> None:
        await self._audit(event_type, resource_id, resource_type, actor_id or "system", payload)

    async def list_audit_events(
        self, resource_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        if not self._pool:
            return []
        try:
            if resource_id:
                rows = await self._pool.fetch(
                    "SELECT id, event_type, actor_id, resource_type, resource_id, payload, occurred_at FROM audit_log WHERE resource_id=$1 ORDER BY occurred_at DESC LIMIT $2",
                    resource_id, limit,
                )
            else:
                rows = await self._pool.fetch(
                    "SELECT id, event_type, actor_id, resource_type, resource_id, payload, occurred_at FROM audit_log ORDER BY occurred_at DESC LIMIT $1",
                    limit,
                )
        except Exception:
            return []
        result = []
        for row in rows:
            d = dict(row)
            if d.get("payload") and isinstance(d["payload"], str):
                d["payload"] = json.loads(d["payload"])
            result.append(d)
        return result

    # ── Private ─────────────────────────────────────────────────

    async def _audit(
        self,
        event_type: str,
        resource_id: str,
        resource_type: str,
        actor_id: str,
        payload: dict[str, Any],
    ) -> None:
        if not self._pool:
            return
        try:
            await self._pool.execute(
                "INSERT INTO audit_log (event_type, actor_id, resource_type, resource_id, payload) VALUES ($1,$2,$3,$4,$5)",
                event_type, actor_id, resource_type, resource_id, json.dumps(payload),
            )
        except Exception as exc:
            logger.error("audit_write_failed", event=event_type, error=str(exc))


# ── SQLAlchemy async session (for ORM-based code) ────────────────────────────

def get_postgres_dsn_sync(settings: Any) -> str:
    return (
        f"postgresql://{settings.postgres_user}:"
        f"{settings.postgres_password.get_secret_value()}@"
        f"{settings.postgres_host}:{settings.postgres_port}/"
        f"{settings.postgres_db}"
    )


async def get_db():  # type: ignore[return]
    """FastAPI dependency that yields an async SQLAlchemy session."""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker
    from cloudsense.services.api.config import get_settings

    settings = get_settings()
    engine = create_async_engine(settings.postgres_dsn, echo=False, pool_pre_ping=True)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
