"""
FastAPI Auth Dependencies (Phase 5.1).

Replaces the simple require_auth stub with full JWT + RBAC enforcement.

Usage in routers:
    from cloudsense.auth.deps import require_permission
    from cloudsense.auth.models import Permission

    @router.get("/costs")
    async def get_costs(claims: TokenClaims = Depends(require_permission(Permission.COSTS_READ))):
        ...

The dependency chain:
    get_token_claims → JWT decode → TokenClaims
    require_permission(perm) → get_token_claims + permission check
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from cloudsense.auth.models import Permission, TokenClaims
from cloudsense.auth.service import AuthService, AuthError
from cloudsense.auth.repository import TenantRepository
from cloudsense.services.api.config import Settings, get_settings

_bearer = HTTPBearer(auto_error=False)


# ── Base dependency: decode JWT ───────────────────────────────────────────────

async def get_token_claims(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
) -> TokenClaims:
    """
    Decode the Bearer JWT and return TokenClaims.
    In development mode with no token → returns a dev admin identity.
    """
    if settings.app_env == "development" and not credentials:
        return TokenClaims(
            sub="dev-user",
            email="dev@cloudsense.local",
            tenant_id="dev-tenant",
            role="admin",  # type: ignore[arg-type]
            permissions=list(Permission),
            display_name="Dev Admin",
        )

    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    repo = TenantRepository(dsn=settings.postgres_dsn)
    auth_svc = AuthService(tenant_repo=repo, settings=settings)
    claims = auth_svc.decode_token(credentials.credentials)

    if not claims:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return claims


# ── Permission factory ────────────────────────────────────────────────────────

def require_permission(permission: Permission):
    """
    Dependency factory — ensures the caller has a specific permission.

    Usage:
        Depends(require_permission(Permission.ACTIONS_APPROVE))
    """
    async def _check(
        claims: TokenClaims = Depends(get_token_claims),
    ) -> TokenClaims:
        if not claims.has(permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission required: {permission.value}",
            )
        return claims
    return _check


# ── Tenant-scoped dependency ──────────────────────────────────────────────────

def require_tenant_permission(permission: Permission):
    """
    Like require_permission but also injects the tenant_id from claims
    so routers can scope queries automatically.
    """
    async def _check(
        claims: TokenClaims = Depends(get_token_claims),
        settings: Settings = Depends(get_settings),
    ) -> TokenClaims:
        if not claims.has(permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission required: {permission.value}",
            )
        return claims
    return _check


# ── Backward compatibility: keep require_auth working ────────────────────────

async def require_auth(
    claims: TokenClaims = Depends(get_token_claims),
) -> str:
    """
    Backward-compatible dependency used by Phase 1-4 routers.
    Returns the user sub (string) so existing router signatures don't change.
    """
    return claims.sub
