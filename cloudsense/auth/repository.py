"""
Tenant Repository (Phase 5.1).

Handles all PostgreSQL I/O for:
  - Tenants (CRUD, slug lookup)
  - TenantUsers (membership, role management)
  - SSO configuration per tenant
  - Password verification (bcrypt via pgcrypto)
"""
from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import structlog

from cloudsense.auth.models import BuiltinRole, SSOConfig, Tenant, TenantUser

logger = structlog.get_logger()


class TenantRepository:
    """
    Async PostgreSQL repository for multi-tenant data.
    Uses asyncpg pool (same pattern as ActionLogRepository).
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: Any = None

    async def connect(self) -> None:
        try:
            import asyncpg
            self._pool = await asyncpg.create_pool(self._dsn, min_size=2, max_size=10)
            logger.info("tenant_repo_connected")
        except Exception as exc:
            logger.warning("tenant_repo_connect_failed", error=str(exc))

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None

    # ── Tenants ──────────────────────────────────────────────────

    async def create_tenant(
        self, name: str, slug: str, plan: str = "community"
    ) -> Tenant:
        tenant = Tenant(name=name, slug=slug, plan=plan)
        if self._pool:
            await self._pool.execute(
                """
                INSERT INTO tenants (id, name, slug, plan, sso_enabled, max_users, is_active)
                VALUES ($1,$2,$3,$4,false,$5,true)
                ON CONFLICT (slug) DO NOTHING
                """,
                tenant.id, name, slug, plan, tenant.max_users,
            )
        logger.info("tenant_created", tenant_id=tenant.id, slug=slug)
        return tenant

    async def get_tenant_by_slug(self, slug: str) -> Tenant | None:
        if not self._pool:
            # Dev fallback — return a default tenant
            return Tenant(id="dev-tenant", name="Dev Tenant", slug=slug)
        row = await self._pool.fetchrow(
            "SELECT * FROM tenants WHERE slug=$1 AND is_active=true", slug
        )
        if not row:
            return None
        return Tenant(**dict(row))

    async def get_tenant_by_id(self, tenant_id: str) -> Tenant | None:
        if not self._pool:
            return Tenant(id=tenant_id, name="Dev Tenant", slug="dev")
        row = await self._pool.fetchrow(
            "SELECT * FROM tenants WHERE id=$1 AND is_active=true", tenant_id
        )
        if not row:
            return None
        return Tenant(**dict(row))

    async def list_tenants(self) -> list[Tenant]:
        if not self._pool:
            return []
        rows = await self._pool.fetch(
            "SELECT * FROM tenants WHERE is_active=true ORDER BY name"
        )
        return [Tenant(**dict(r)) for r in rows]

    async def update_tenant_sso(
        self, tenant_id: str, sso_config: SSOConfig
    ) -> bool:
        if not self._pool:
            return True
        result = await self._pool.execute(
            """
            UPDATE tenants
            SET sso_enabled=true,
                sso_provider=$2,
                saml_metadata_url=$3,
                oidc_issuer=$4,
                oidc_client_id=$5,
                oidc_client_secret=$6,
                updated_at=NOW()
            WHERE id=$1
            """,
            tenant_id,
            sso_config.provider,
            sso_config.saml_metadata_url,
            sso_config.oidc_issuer,
            sso_config.oidc_client_id,
            sso_config.oidc_client_secret,
        )
        return result == "UPDATE 1"

    async def get_sso_config(self, tenant_id: str) -> SSOConfig | None:
        if not self._pool:
            return None
        row = await self._pool.fetchrow(
            "SELECT * FROM tenants WHERE id=$1 AND sso_enabled=true", tenant_id
        )
        if not row:
            return None
        d = dict(row)
        return SSOConfig(
            tenant_id=tenant_id,
            provider=d.get("sso_provider") or "oidc",
            saml_metadata_url=d.get("saml_metadata_url"),
            oidc_issuer=d.get("oidc_issuer"),
            oidc_client_id=d.get("oidc_client_id"),
            oidc_client_secret=d.get("oidc_client_secret"),
        )

    # ── Users / Memberships ───────────────────────────────────────

    async def get_member_by_email(
        self, tenant_id: str, email: str
    ) -> TenantUser | None:
        if not self._pool:
            # Dev fallback — admin user
            return TenantUser(
                tenant_id=tenant_id,
                user_id="dev-user",
                email=email,
                display_name="Dev User",
                role=BuiltinRole.ADMIN,
            )
        row = await self._pool.fetchrow(
            """
            SELECT tm.*, u.email, u.display_name
            FROM tenant_members tm
            JOIN users u ON u.id = tm.user_id
            WHERE tm.tenant_id=$1 AND u.email=$2 AND tm.is_active=true
            """,
            tenant_id, email,
        )
        if not row:
            return None
        d = dict(row)
        return TenantUser(
            id=d.get("id", str(uuid4())),
            tenant_id=tenant_id,
            user_id=str(d["user_id"]),
            email=d["email"],
            display_name=d.get("display_name", ""),
            role=BuiltinRole(d.get("role", "viewer")),
        )

    async def provision_sso_user(
        self,
        tenant_id: str,
        email: str,
        display_name: str,
        role: BuiltinRole = BuiltinRole.VIEWER,
    ) -> TenantUser:
        """Auto-provision a user from SSO login (first-time only)."""
        user_id = str(uuid4())
        member_id = str(uuid4())

        if self._pool:
            try:
                await self._pool.execute(
                    """
                    INSERT INTO users (id, email, display_name, password_hash)
                    VALUES ($1,$2,$3,'sso-provisioned')
                    ON CONFLICT (email) DO UPDATE SET display_name=EXCLUDED.display_name
                    RETURNING id
                    """,
                    user_id, email, display_name,
                )
                await self._pool.execute(
                    """
                    INSERT INTO tenant_members (id, tenant_id, user_id, role, is_active)
                    VALUES ($1,$2,(SELECT id FROM users WHERE email=$3),$4,true)
                    ON CONFLICT (tenant_id, user_id) DO NOTHING
                    """,
                    member_id, tenant_id, email, role.value,
                )
            except Exception as exc:
                logger.error("provision_sso_user_failed", email=email, error=str(exc))

        logger.info("sso_user_provisioned", email=email, tenant_id=tenant_id)
        return TenantUser(
            id=member_id,
            tenant_id=tenant_id,
            user_id=user_id,
            email=email,
            display_name=display_name,
            role=role,
        )

    async def list_members(self, tenant_id: str) -> list[TenantUser]:
        if not self._pool:
            return []
        rows = await self._pool.fetch(
            """
            SELECT tm.id, tm.tenant_id, tm.user_id, tm.role, tm.is_active,
                   tm.joined_at, tm.last_login, u.email, u.display_name
            FROM tenant_members tm
            JOIN users u ON u.id = tm.user_id
            WHERE tm.tenant_id=$1 AND tm.is_active=true
            ORDER BY tm.joined_at
            """,
            tenant_id,
        )
        result = []
        for row in rows:
            d = dict(row)
            result.append(TenantUser(
                id=str(d["id"]),
                tenant_id=str(d["tenant_id"]),
                user_id=str(d["user_id"]),
                email=d["email"],
                display_name=d.get("display_name", ""),
                role=BuiltinRole(d.get("role", "viewer")),
                is_active=d.get("is_active", True),
                joined_at=d.get("joined_at"),
                last_login=d.get("last_login"),
            ))
        return result

    async def update_member_role(
        self, tenant_id: str, user_id: str, role: BuiltinRole
    ) -> bool:
        if not self._pool:
            return True
        result = await self._pool.execute(
            """
            UPDATE tenant_members SET role=$3, updated_at=NOW()
            WHERE tenant_id=$1 AND user_id=$2
            """,
            tenant_id, user_id, role.value,
        )
        return result == "UPDATE 1"

    async def deactivate_member(self, tenant_id: str, user_id: str) -> bool:
        if not self._pool:
            return True
        result = await self._pool.execute(
            "UPDATE tenant_members SET is_active=false WHERE tenant_id=$1 AND user_id=$2",
            tenant_id, user_id,
        )
        return result == "UPDATE 1"

    # ── Password ──────────────────────────────────────────────────

    async def verify_password(self, user_id: str, password: str) -> bool:
        """Verify bcrypt password hash via pgcrypto (crypt function)."""
        if not self._pool:
            return True  # dev mode — skip password check
        try:
            row = await self._pool.fetchrow(
                "SELECT (password_hash = crypt($2, password_hash)) AS ok FROM users WHERE id=$1",
                user_id, password,
            )
            return bool(row and row["ok"])
        except Exception as exc:
            logger.error("password_verify_failed", user_id=user_id, error=str(exc))
            return False
