"""
RBAC & Multi-tenant domain models (Phase 5.1).

Hierarchy:
  Tenant  ──< TenantMember >── User
  Role    ──< RolePermission >── Permission
  User has one Role per Tenant

Roles (built-in):
  admin       — full read/write + user management
  engineer    — read + execute approved actions
  viewer      — read-only (costs, reports, anomalies)
  billing     — read costs + export reports only
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, EmailStr, Field


# ── Enums ────────────────────────────────────────────────────────────────────

class BuiltinRole(str, Enum):
    ADMIN = "admin"
    ENGINEER = "engineer"
    VIEWER = "viewer"
    BILLING = "billing"


class Permission(str, Enum):
    # Cost data
    COSTS_READ = "costs:read"
    COSTS_EXPORT = "costs:export"
    # Recommendations
    RECOMMENDATIONS_READ = "recommendations:read"
    RECOMMENDATIONS_EXECUTE = "recommendations:execute"
    # Actions
    ACTIONS_READ = "actions:read"
    ACTIONS_APPROVE = "actions:approve"
    ACTIONS_EXECUTE = "actions:execute"
    ACTIONS_ROLLBACK = "actions:rollback"
    # Tags
    TAGS_READ = "tags:read"
    TAGS_WRITE = "tags:write"
    # Reports
    REPORTS_READ = "reports:read"
    REPORTS_EXPORT = "reports:export"
    # Admin
    USERS_READ = "users:read"
    USERS_WRITE = "users:write"
    TENANTS_MANAGE = "tenants:manage"
    SETTINGS_WRITE = "settings:write"
    # K8s
    K8S_COSTS_READ = "k8s:costs:read"


# Role → Permission mapping (built-in policy)
ROLE_PERMISSIONS: dict[BuiltinRole, list[Permission]] = {
    BuiltinRole.ADMIN: list(Permission),  # all permissions
    BuiltinRole.ENGINEER: [
        Permission.COSTS_READ,
        Permission.COSTS_EXPORT,
        Permission.RECOMMENDATIONS_READ,
        Permission.RECOMMENDATIONS_EXECUTE,
        Permission.ACTIONS_READ,
        Permission.ACTIONS_APPROVE,
        Permission.ACTIONS_EXECUTE,
        Permission.ACTIONS_ROLLBACK,
        Permission.TAGS_READ,
        Permission.TAGS_WRITE,
        Permission.REPORTS_READ,
        Permission.REPORTS_EXPORT,
        Permission.K8S_COSTS_READ,
    ],
    BuiltinRole.VIEWER: [
        Permission.COSTS_READ,
        Permission.RECOMMENDATIONS_READ,
        Permission.ACTIONS_READ,
        Permission.TAGS_READ,
        Permission.REPORTS_READ,
        Permission.K8S_COSTS_READ,
    ],
    BuiltinRole.BILLING: [
        Permission.COSTS_READ,
        Permission.COSTS_EXPORT,
        Permission.REPORTS_READ,
        Permission.REPORTS_EXPORT,
    ],
}


# ── Pydantic models ──────────────────────────────────────────────────────────

class Tenant(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    slug: str                          # URL-safe identifier, e.g. "acme-corp"
    plan: str = "community"            # community | enterprise
    sso_enabled: bool = False
    sso_provider: str | None = None    # "saml" | "oidc"
    saml_metadata_url: str | None = None
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    max_users: int = 10
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    is_active: bool = True

    def has_sso(self) -> bool:
        return self.sso_enabled and self.sso_provider is not None


class TenantUser(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    tenant_id: str
    user_id: str
    email: str
    display_name: str = ""
    role: BuiltinRole = BuiltinRole.VIEWER
    is_active: bool = True
    joined_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    last_login: datetime | None = None

    def permissions(self) -> list[Permission]:
        return ROLE_PERMISSIONS.get(self.role, [])

    def has_permission(self, perm: Permission) -> bool:
        return perm in self.permissions()


class TokenClaims(BaseModel):
    """
    Decoded JWT / SAML / OIDC claims normalised into a single model.
    Used by all FastAPI dependencies as the request identity context.
    """
    sub: str                       # user id
    email: str
    tenant_id: str
    role: BuiltinRole
    permissions: list[Permission]
    display_name: str = ""
    sso: bool = False              # was this issued via SSO?
    exp: int | None = None

    def has(self, perm: Permission) -> bool:
        return perm in self.permissions


class SSOConfig(BaseModel):
    """SSO configuration stored per-tenant."""
    tenant_id: str
    provider: str                  # "saml" | "oidc"
    # SAML
    saml_metadata_url: str | None = None
    saml_entity_id: str | None = None
    saml_acs_url: str | None = None
    # OIDC
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_scopes: list[str] = Field(default_factory=lambda: ["openid", "email", "profile"])
    oidc_redirect_uri: str | None = None
