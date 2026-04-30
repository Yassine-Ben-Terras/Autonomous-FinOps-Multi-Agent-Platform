"""
Auth & SSO API (Phase 5.1) — /api/v1/auth/*

Endpoints:
  POST /auth/login                       — Local JWT login
  POST /auth/refresh                     — Refresh access token
  GET  /auth/me                          — Current user claims
  GET  /auth/sso/{tenant_slug}/oidc      — OIDC authorization redirect URL
  GET  /auth/sso/{tenant_slug}/callback  — OIDC callback (exchange code)
  POST /auth/sso/{tenant_slug}/saml/acs  — SAML ACS endpoint
  POST /auth/sso/{tenant_slug}/config    — Configure SSO for a tenant (admin)
  GET  /auth/tenants                     — List tenants (admin)
  POST /auth/tenants                     — Create tenant (admin)
  GET  /auth/tenants/{slug}/members      — List members (admin/engineer)
  POST /auth/tenants/{slug}/members      — Invite member (admin)
  PUT  /auth/tenants/{slug}/members/{uid}/role — Change role (admin)
  DELETE /auth/tenants/{slug}/members/{uid}    — Remove member (admin)
"""
from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from cloudsense.auth.deps import get_token_claims, require_permission
from cloudsense.auth.models import BuiltinRole, Permission, SSOConfig, TokenClaims
from cloudsense.auth.repository import TenantRepository
from cloudsense.auth.service import AuthError, AuthService
from cloudsense.services.api.config import Settings, get_settings

router = APIRouter(prefix="/auth", tags=["Auth & SSO (Phase 5.1)"])


# ── Request / Response models ─────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: str
    password: str
    tenant_slug: str


class RefreshRequest(BaseModel):
    refresh_token: str


class SSOConfigRequest(BaseModel):
    provider: str = Field(..., description="oidc | saml")
    saml_metadata_url: str | None = None
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_redirect_uri: str | None = None
    oidc_scopes: list[str] = Field(default_factory=lambda: ["openid", "email", "profile"])


class InviteMemberRequest(BaseModel):
    email: str
    display_name: str = ""
    role: str = "viewer"


class UpdateRoleRequest(BaseModel):
    role: str


class CreateTenantRequest(BaseModel):
    name: str
    slug: str
    plan: str = "community"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_repo(settings: Settings = Depends(get_settings)) -> TenantRepository:
    return TenantRepository(dsn=settings.postgres_dsn)


def _get_auth_svc(
    settings: Settings = Depends(get_settings),
    repo: TenantRepository = Depends(_get_repo),
) -> AuthService:
    return AuthService(tenant_repo=repo, settings=settings)


# ── Local auth ────────────────────────────────────────────────────────────────

@router.post("/login", response_model=dict[str, Any])
async def login(
    body: LoginRequest,
    auth_svc: AuthService = Depends(_get_auth_svc),
    repo: TenantRepository = Depends(_get_repo),
) -> dict[str, Any]:
    """Local email/password login. Returns access + refresh tokens."""
    await repo.connect()
    try:
        result = await auth_svc.login(body.email, body.password, body.tenant_slug)
        return result
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))
    finally:
        await repo.close()


@router.post("/refresh", response_model=dict[str, Any])
async def refresh_token(
    body: RefreshRequest,
    auth_svc: AuthService = Depends(_get_auth_svc),
    repo: TenantRepository = Depends(_get_repo),
) -> dict[str, Any]:
    """Exchange a refresh token for a new access token."""
    await repo.connect()
    try:
        return await auth_svc.refresh(body.refresh_token)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))
    finally:
        await repo.close()


@router.get("/me", response_model=dict[str, Any])
async def me(claims: TokenClaims = Depends(get_token_claims)) -> dict[str, Any]:
    """Return current user identity and permissions."""
    return {
        "sub": claims.sub,
        "email": claims.email,
        "tenant_id": claims.tenant_id,
        "role": claims.role.value if hasattr(claims.role, "value") else claims.role,
        "permissions": [p.value if hasattr(p, "value") else p for p in claims.permissions],
        "display_name": claims.display_name,
        "sso": claims.sso,
    }


# ── OIDC ──────────────────────────────────────────────────────────────────────

@router.get("/sso/{tenant_slug}/oidc", response_model=dict[str, Any])
async def oidc_authorize(
    tenant_slug: str,
    settings: Settings = Depends(get_settings),
    repo: TenantRepository = Depends(_get_repo),
) -> dict[str, Any]:
    """
    Return the OIDC authorization URL.
    Frontend redirects the user to this URL.
    """
    await repo.connect()
    try:
        tenant = await repo.get_tenant_by_slug(tenant_slug)
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")
        sso_cfg = await repo.get_sso_config(tenant.id)
        if not sso_cfg or sso_cfg.provider != "oidc":
            raise HTTPException(status_code=400, detail="OIDC not configured for this tenant")
    finally:
        await repo.close()

    state = secrets.token_urlsafe(24)
    auth_svc = AuthService(tenant_repo=repo, settings=settings)
    url = auth_svc.oidc_authorization_url(sso_cfg, state=state)
    return {"authorization_url": url, "state": state}


@router.get("/sso/{tenant_slug}/callback", response_model=dict[str, Any])
async def oidc_callback(
    tenant_slug: str,
    code: str,
    state: str | None = None,
    settings: Settings = Depends(get_settings),
    repo: TenantRepository = Depends(_get_repo),
) -> dict[str, Any]:
    """OIDC Authorization Code callback. Exchanges code for CloudSense tokens."""
    await repo.connect()
    try:
        tenant = await repo.get_tenant_by_slug(tenant_slug)
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")
        sso_cfg = await repo.get_sso_config(tenant.id)
        if not sso_cfg:
            raise HTTPException(status_code=400, detail="SSO not configured")
        auth_svc = AuthService(tenant_repo=repo, settings=settings)
        result = await auth_svc.oidc_callback(code=code, sso_config=sso_cfg, tenant=tenant)
        return result
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))
    finally:
        await repo.close()


# ── SAML 2.0 ─────────────────────────────────────────────────────────────────

@router.post("/sso/{tenant_slug}/saml/acs", response_model=dict[str, Any])
async def saml_acs(
    tenant_slug: str,
    request: Request,
    settings: Settings = Depends(get_settings),
    repo: TenantRepository = Depends(_get_repo),
) -> dict[str, Any]:
    """
    SAML Assertion Consumer Service endpoint.
    IdP posts the SAMLResponse here after authentication.
    """
    form = await request.form()
    saml_response = form.get("SAMLResponse", "")
    if not saml_response:
        raise HTTPException(status_code=400, detail="Missing SAMLResponse in POST body")

    await repo.connect()
    try:
        tenant = await repo.get_tenant_by_slug(tenant_slug)
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")
        sso_cfg = await repo.get_sso_config(tenant.id)
        if not sso_cfg or sso_cfg.provider != "saml":
            raise HTTPException(status_code=400, detail="SAML not configured for this tenant")

        auth_svc = AuthService(tenant_repo=repo, settings=settings)
        user_info = auth_svc.saml_acs_callback(
            saml_response_b64=str(saml_response),
            sso_config=sso_cfg,
            tenant=tenant,
        )
        # Provision or retrieve user
        member = await repo.get_member_by_email(tenant.id, user_info["email"])
        if not member:
            member = await repo.provision_sso_user(
                tenant_id=tenant.id,
                email=user_info["email"],
                display_name=user_info.get("display_name", ""),
                role=BuiltinRole.VIEWER,
            )

        from cloudsense.auth.models import ROLE_PERMISSIONS
        from cloudsense.auth.service import _sign_jwt, ACCESS_TOKEN_TTL
        import time
        claims_dict = {
            "sub": member.user_id,
            "email": member.email,
            "tenant_id": member.tenant_id,
            "role": member.role.value,
            "permissions": [p.value for p in member.permissions()],
            "display_name": member.display_name,
            "sso": True,
            "exp": int(time.time()) + ACCESS_TOKEN_TTL,
        }
        token = _sign_jwt(claims_dict, settings.secret_key.get_secret_value())
        return {"access_token": token, "token_type": "bearer",
                "expires_in": ACCESS_TOKEN_TTL, "email": member.email}
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))
    finally:
        await repo.close()


# ── SSO config management ─────────────────────────────────────────────────────

@router.post("/sso/{tenant_slug}/config", response_model=dict[str, Any])
async def configure_sso(
    tenant_slug: str,
    body: SSOConfigRequest,
    claims: TokenClaims = Depends(require_permission(Permission.SETTINGS_WRITE)),
    settings: Settings = Depends(get_settings),
    repo: TenantRepository = Depends(_get_repo),
) -> dict[str, Any]:
    """Configure OIDC or SAML SSO for a tenant (admin only)."""
    await repo.connect()
    try:
        tenant = await repo.get_tenant_by_slug(tenant_slug)
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")
        if claims.tenant_id != tenant.id:
            raise HTTPException(status_code=403, detail="Cannot configure another tenant's SSO")
        sso_cfg = SSOConfig(
            tenant_id=tenant.id,
            provider=body.provider,
            saml_metadata_url=body.saml_metadata_url,
            oidc_issuer=body.oidc_issuer,
            oidc_client_id=body.oidc_client_id,
            oidc_client_secret=body.oidc_client_secret,
            oidc_redirect_uri=body.oidc_redirect_uri,
            oidc_scopes=body.oidc_scopes,
        )
        ok = await repo.update_tenant_sso(tenant.id, sso_cfg)
        return {"tenant_id": tenant.id, "sso_configured": ok, "provider": body.provider}
    finally:
        await repo.close()


# ── Tenant management ─────────────────────────────────────────────────────────

@router.get("/tenants", response_model=list[dict[str, Any]])
async def list_tenants(
    claims: TokenClaims = Depends(require_permission(Permission.TENANTS_MANAGE)),
    repo: TenantRepository = Depends(_get_repo),
) -> list[dict[str, Any]]:
    """List all tenants (platform admin only)."""
    await repo.connect()
    try:
        tenants = await repo.list_tenants()
        return [t.model_dump(mode="json") for t in tenants]
    finally:
        await repo.close()


@router.post("/tenants", response_model=dict[str, Any], status_code=201)
async def create_tenant(
    body: CreateTenantRequest,
    claims: TokenClaims = Depends(require_permission(Permission.TENANTS_MANAGE)),
    repo: TenantRepository = Depends(_get_repo),
) -> dict[str, Any]:
    """Create a new tenant (platform admin only)."""
    await repo.connect()
    try:
        tenant = await repo.create_tenant(name=body.name, slug=body.slug, plan=body.plan)
        return tenant.model_dump(mode="json")
    finally:
        await repo.close()


# ── Member management ─────────────────────────────────────────────────────────

@router.get("/tenants/{tenant_slug}/members", response_model=list[dict[str, Any]])
async def list_members(
    tenant_slug: str,
    claims: TokenClaims = Depends(require_permission(Permission.USERS_READ)),
    settings: Settings = Depends(get_settings),
    repo: TenantRepository = Depends(_get_repo),
) -> list[dict[str, Any]]:
    """List all members of a tenant."""
    await repo.connect()
    try:
        tenant = await repo.get_tenant_by_slug(tenant_slug)
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")
        if claims.tenant_id != tenant.id:
            raise HTTPException(status_code=403, detail="Access denied")
        members = await repo.list_members(tenant.id)
        return [m.model_dump(mode="json") for m in members]
    finally:
        await repo.close()


@router.post("/tenants/{tenant_slug}/members", response_model=dict[str, Any], status_code=201)
async def invite_member(
    tenant_slug: str,
    body: InviteMemberRequest,
    claims: TokenClaims = Depends(require_permission(Permission.USERS_WRITE)),
    repo: TenantRepository = Depends(_get_repo),
) -> dict[str, Any]:
    """Invite a new member to a tenant (admin only)."""
    await repo.connect()
    try:
        tenant = await repo.get_tenant_by_slug(tenant_slug)
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")
        if claims.tenant_id != tenant.id:
            raise HTTPException(status_code=403, detail="Access denied")
        try:
            role = BuiltinRole(body.role)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid role: {body.role}")
        member = await repo.provision_sso_user(
            tenant_id=tenant.id,
            email=body.email,
            display_name=body.display_name or body.email.split("@")[0],
            role=role,
        )
        return member.model_dump(mode="json")
    finally:
        await repo.close()


@router.put("/tenants/{tenant_slug}/members/{user_id}/role", response_model=dict[str, Any])
async def update_member_role(
    tenant_slug: str,
    user_id: str,
    body: UpdateRoleRequest,
    claims: TokenClaims = Depends(require_permission(Permission.USERS_WRITE)),
    repo: TenantRepository = Depends(_get_repo),
) -> dict[str, Any]:
    """Change a member's role (admin only)."""
    await repo.connect()
    try:
        tenant = await repo.get_tenant_by_slug(tenant_slug)
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")
        try:
            role = BuiltinRole(body.role)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid role: {body.role}")
        ok = await repo.update_member_role(tenant.id, user_id, role)
        if not ok:
            raise HTTPException(status_code=404, detail="Member not found")
        return {"tenant_id": tenant.id, "user_id": user_id, "new_role": role.value}
    finally:
        await repo.close()


@router.delete("/tenants/{tenant_slug}/members/{user_id}", status_code=204)
async def remove_member(
    tenant_slug: str,
    user_id: str,
    claims: TokenClaims = Depends(require_permission(Permission.USERS_WRITE)),
    repo: TenantRepository = Depends(_get_repo),
) -> None:
    """Remove a member from a tenant (admin only)."""
    await repo.connect()
    try:
        tenant = await repo.get_tenant_by_slug(tenant_slug)
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")
        await repo.deactivate_member(tenant.id, user_id)
    finally:
        await repo.close()
