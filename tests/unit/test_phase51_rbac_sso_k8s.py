"""
Unit tests — Phase 5.1: RBAC, JWT, AuthService, TenantRepository, K8sCostService.

All tests run without external services:
  - DB calls mocked via mock_tenant_repo fixture
  - Redis fallback uses in-memory dict
  - K8s ClickHouse queries mocked
"""
from __future__ import annotations

import json
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from cloudsense.auth.models import (
    BuiltinRole, Permission, ROLE_PERMISSIONS,
    Tenant, TenantUser, TokenClaims,
)
from cloudsense.auth.service import (
    AuthService, AuthError,
    _sign_jwt, _verify_jwt, ACCESS_TOKEN_TTL,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_tenant_repo():
    repo = MagicMock()
    repo.connect = AsyncMock()
    repo.close = AsyncMock()
    repo.get_tenant_by_slug = AsyncMock(return_value=Tenant(
        id="tenant-001", name="Acme Corp", slug="acme", plan="enterprise"
    ))
    repo.get_tenant_by_id = AsyncMock(return_value=Tenant(
        id="tenant-001", name="Acme Corp", slug="acme", plan="enterprise"
    ))
    repo.get_member_by_email = AsyncMock(return_value=TenantUser(
        id="member-001",
        tenant_id="tenant-001",
        user_id="user-001",
        email="alice@acme.com",
        display_name="Alice",
        role=BuiltinRole.ADMIN,
    ))
    repo.verify_password = AsyncMock(return_value=True)
    repo.get_sso_config = AsyncMock(return_value=None)
    repo.update_tenant_sso = AsyncMock(return_value=True)
    repo.provision_sso_user = AsyncMock(return_value=TenantUser(
        id="member-002",
        tenant_id="tenant-001",
        user_id="user-002",
        email="bob@acme.com",
        display_name="Bob",
        role=BuiltinRole.VIEWER,
    ))
    repo.list_tenants = AsyncMock(return_value=[])
    repo.list_members = AsyncMock(return_value=[])
    repo.create_tenant = AsyncMock(return_value=Tenant(
        id="tenant-002", name="Test Co", slug="testco"
    ))
    repo.update_member_role = AsyncMock(return_value=True)
    repo.deactivate_member = AsyncMock(return_value=True)
    return repo


@pytest.fixture
def auth_svc(mock_tenant_repo):
    from cloudsense.services.api.config import Settings
    settings = Settings(secret_key="test-secret-key-1234567890")
    return AuthService(tenant_repo=mock_tenant_repo, settings=settings)


@pytest.fixture
def admin_claims():
    return TokenClaims(
        sub="user-001",
        email="alice@acme.com",
        tenant_id="tenant-001",
        role=BuiltinRole.ADMIN,
        permissions=list(Permission),
        display_name="Alice",
    )


@pytest.fixture
def viewer_claims():
    return TokenClaims(
        sub="user-002",
        email="bob@acme.com",
        tenant_id="tenant-001",
        role=BuiltinRole.VIEWER,
        permissions=ROLE_PERMISSIONS[BuiltinRole.VIEWER],
        display_name="Bob",
    )


# ── RBAC Model Tests ──────────────────────────────────────────────────────────

class TestRBACModels:

    def test_admin_has_all_permissions(self):
        perms = ROLE_PERMISSIONS[BuiltinRole.ADMIN]
        for p in Permission:
            assert p in perms, f"Admin missing permission: {p}"

    def test_viewer_cannot_approve_actions(self):
        perms = ROLE_PERMISSIONS[BuiltinRole.VIEWER]
        assert Permission.ACTIONS_APPROVE not in perms
        assert Permission.ACTIONS_EXECUTE not in perms
        assert Permission.ACTIONS_ROLLBACK not in perms

    def test_viewer_can_read_costs(self):
        perms = ROLE_PERMISSIONS[BuiltinRole.VIEWER]
        assert Permission.COSTS_READ in perms
        assert Permission.RECOMMENDATIONS_READ in perms

    def test_engineer_can_execute_not_manage_tenants(self):
        perms = ROLE_PERMISSIONS[BuiltinRole.ENGINEER]
        assert Permission.ACTIONS_EXECUTE in perms
        assert Permission.TENANTS_MANAGE not in perms

    def test_billing_only_reads_costs(self):
        perms = ROLE_PERMISSIONS[BuiltinRole.BILLING]
        assert Permission.COSTS_READ in perms
        assert Permission.COSTS_EXPORT in perms
        assert Permission.ACTIONS_EXECUTE not in perms
        assert Permission.TAGS_WRITE not in perms

    def test_tenant_user_has_permission(self):
        member = TenantUser(
            tenant_id="t1", user_id="u1", email="a@b.com",
            role=BuiltinRole.ENGINEER,
        )
        assert member.has_permission(Permission.ACTIONS_EXECUTE)
        assert not member.has_permission(Permission.TENANTS_MANAGE)

    def test_tenant_user_permissions_match_role(self):
        member = TenantUser(
            tenant_id="t1", user_id="u1", email="a@b.com",
            role=BuiltinRole.VIEWER,
        )
        assert set(member.permissions()) == set(ROLE_PERMISSIONS[BuiltinRole.VIEWER])

    def test_token_claims_has_permission(self, admin_claims, viewer_claims):
        assert admin_claims.has(Permission.ACTIONS_APPROVE)
        assert not viewer_claims.has(Permission.ACTIONS_APPROVE)
        assert viewer_claims.has(Permission.COSTS_READ)

    def test_tenant_has_sso_false_by_default(self):
        t = Tenant(name="Test", slug="test")
        assert not t.has_sso()

    def test_tenant_has_sso_true_when_configured(self):
        t = Tenant(name="Test", slug="test", sso_enabled=True, sso_provider="oidc")
        assert t.has_sso()


# ── JWT Tests ─────────────────────────────────────────────────────────────────

class TestJWT:

    def test_sign_and_verify(self):
        payload = {"sub": "user-1", "exp": 9999999999, "role": "admin"}
        token = _sign_jwt(payload, "my-secret")
        result = _verify_jwt(token, "my-secret")
        assert result is not None
        assert result["sub"] == "user-1"

    def test_wrong_secret_fails(self):
        token = _sign_jwt({"sub": "x", "exp": 9999999999}, "secret-a")
        result = _verify_jwt(token, "secret-b")
        assert result is None

    def test_expired_token_fails(self):
        import time
        token = _sign_jwt({"sub": "x", "exp": int(time.time()) - 10}, "secret")
        result = _verify_jwt(token, "secret")
        assert result is None

    def test_malformed_token_fails(self):
        result = _verify_jwt("not.a.valid.jwt.token", "secret")
        assert result is None

    def test_token_has_three_parts(self):
        token = _sign_jwt({"sub": "x", "exp": 9999999999}, "secret")
        parts = token.split(".")
        assert len(parts) == 3

    def test_decode_token_returns_claims(self, auth_svc, admin_claims):
        token = auth_svc._issue_access_token(admin_claims)
        decoded = auth_svc.decode_token(token)
        assert decoded is not None
        assert decoded.email == "alice@acme.com"
        assert decoded.tenant_id == "tenant-001"

    def test_decode_token_invalid_returns_none(self, auth_svc):
        result = auth_svc.decode_token("garbage.token.here")
        assert result is None


# ── AuthService Tests ─────────────────────────────────────────────────────────

class TestAuthService:

    @pytest.mark.asyncio
    async def test_login_success(self, auth_svc, mock_tenant_repo):
        result = await auth_svc.login("alice@acme.com", "password123", "acme")
        assert "access_token" in result
        assert "refresh_token" in result
        assert result["token_type"] == "bearer"
        assert result["expires_in"] == ACCESS_TOKEN_TTL

    @pytest.mark.asyncio
    async def test_login_tenant_not_found(self, auth_svc, mock_tenant_repo):
        mock_tenant_repo.get_tenant_by_slug = AsyncMock(return_value=None)
        with pytest.raises(AuthError, match="Tenant not found"):
            await auth_svc.login("a@b.com", "pass", "nonexistent")

    @pytest.mark.asyncio
    async def test_login_user_not_found(self, auth_svc, mock_tenant_repo):
        mock_tenant_repo.get_member_by_email = AsyncMock(return_value=None)
        with pytest.raises(AuthError, match="Invalid credentials"):
            await auth_svc.login("nobody@acme.com", "pass", "acme")

    @pytest.mark.asyncio
    async def test_login_wrong_password(self, auth_svc, mock_tenant_repo):
        mock_tenant_repo.verify_password = AsyncMock(return_value=False)
        with pytest.raises(AuthError, match="Invalid credentials"):
            await auth_svc.login("alice@acme.com", "wrongpass", "acme")

    @pytest.mark.asyncio
    async def test_refresh_token_success(self, auth_svc, admin_claims):
        # Issue a refresh token (stored in in-memory fallback)
        refresh_token = await auth_svc._issue_refresh_token(admin_claims)
        result = await auth_svc.refresh(refresh_token)
        assert "access_token" in result
        assert result["token_type"] == "bearer"

    @pytest.mark.asyncio
    async def test_refresh_token_invalid(self, auth_svc):
        with pytest.raises(AuthError, match="Invalid or expired"):
            await auth_svc.refresh("invalid-refresh-token")

    def test_build_claims_from_member(self, auth_svc):
        member = TenantUser(
            tenant_id="t1", user_id="u1",
            email="x@y.com", role=BuiltinRole.ENGINEER,
        )
        claims = auth_svc._build_claims(member)
        assert claims.role == BuiltinRole.ENGINEER
        assert Permission.ACTIONS_EXECUTE in claims.permissions
        assert not claims.sso

    def test_build_claims_sso_flag(self, auth_svc):
        member = TenantUser(
            tenant_id="t1", user_id="u1",
            email="x@y.com", role=BuiltinRole.VIEWER,
        )
        claims = auth_svc._build_claims(member, sso=True)
        assert claims.sso is True

    def test_issue_access_token_is_valid_jwt(self, auth_svc, admin_claims):
        token = auth_svc._issue_access_token(admin_claims)
        assert len(token.split(".")) == 3
        decoded = auth_svc.decode_token(token)
        assert decoded is not None
        assert decoded.sub == "user-001"

    def test_oidc_authorization_url(self, auth_svc):
        from cloudsense.auth.models import SSOConfig
        sso_cfg = SSOConfig(
            tenant_id="t1",
            provider="oidc",
            oidc_issuer="https://dev-123.okta.com",
            oidc_client_id="client-abc",
            oidc_redirect_uri="https://app.cloudsense.io/auth/sso/acme/callback",
        )
        url = auth_svc.oidc_authorization_url(sso_cfg, state="state-xyz")
        assert "dev-123.okta.com" in url
        assert "client-abc" in url
        assert "state-xyz" in url
        assert "response_type=code" in url

    def test_oidc_authorization_url_not_configured(self, auth_svc):
        from cloudsense.auth.models import SSOConfig
        sso_cfg = SSOConfig(tenant_id="t1", provider="oidc")
        with pytest.raises(AuthError, match="OIDC not configured"):
            auth_svc.oidc_authorization_url(sso_cfg, state="s")

    def test_saml_acs_parses_assertion(self, auth_svc, mock_tenant_repo):
        """SAML response XML parsing extracts email from NameID."""
        import base64
        xml = """<?xml version="1.0"?>
<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
                xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion">
  <saml:Assertion>
    <saml:Subject>
      <saml:NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress">
        carol@acme.com
      </saml:NameID>
    </saml:Subject>
    <saml:AttributeStatement>
      <saml:Attribute Name="displayName">
        <saml:AttributeValue>Carol Smith</saml:AttributeValue>
      </saml:Attribute>
    </saml:AttributeStatement>
  </saml:Assertion>
</samlp:Response>"""
        from cloudsense.auth.models import SSOConfig, Tenant
        saml_b64 = base64.b64encode(xml.encode()).decode()
        sso_cfg = SSOConfig(tenant_id="t1", provider="saml",
                            saml_metadata_url="https://idp.example.com/metadata")
        tenant = Tenant(id="t1", name="Test", slug="test")
        result = auth_svc.saml_acs_callback(saml_b64, sso_cfg, tenant)
        assert result["email"] == "carol@acme.com"
        assert result["display_name"] == "Carol Smith"

    def test_saml_acs_invalid_xml(self, auth_svc):
        from cloudsense.auth.models import SSOConfig, Tenant
        sso_cfg = SSOConfig(tenant_id="t1", provider="saml")
        tenant = Tenant(id="t1", name="T", slug="t")
        with pytest.raises(AuthError, match="Invalid SAML"):
            auth_svc.saml_acs_callback("not-valid-base64!!!", sso_cfg, tenant)


# ── K8sCostService Tests ──────────────────────────────────────────────────────

class TestK8sCostService:

    @pytest.fixture
    def mock_ch(self):
        ch = MagicMock()
        ch._client = MagicMock()
        ch._client.execute = AsyncMock(return_value=([], []))
        return ch

    @pytest.fixture
    def k8s_svc(self, mock_ch):
        from cloudsense.k8s.cost_service import K8sCostService
        return K8sCostService(ch=mock_ch)

    @pytest.mark.asyncio
    async def test_namespace_costs_empty_db(self, k8s_svc):
        result = await k8s_svc.allocation_by_namespace(window_days=7)
        assert result == []

    @pytest.mark.asyncio
    async def test_namespace_costs_with_data(self, k8s_svc, mock_ch):
        rows = [["frontend", "prod-cluster", 450.0, 64.28, 12, "2024-01-01", "2024-01-07"]]
        cols = [["namespace","String"],["cluster","String"],["total_cost","Float64"],
                ["daily_cost","Float64"],["resource_count","Int64"],
                ["window_start","Date"],["window_end","Date"]]
        mock_ch._client.execute = AsyncMock(return_value=(rows, cols))
        result = await k8s_svc.allocation_by_namespace(window_days=7)
        assert len(result) == 1
        assert result[0]["namespace"] == "frontend"
        assert result[0]["totalCost"] == 450.0
        # monthlyCost = daily_cost * 30
        assert abs(result[0]["monthlyCost"] - 64.28 * 30) < 1.0

    @pytest.mark.asyncio
    async def test_workload_costs_empty(self, k8s_svc):
        result = await k8s_svc.allocation_by_workload()
        assert result == []

    @pytest.mark.asyncio
    async def test_node_costs_empty(self, k8s_svc):
        result = await k8s_svc.node_cost_breakdown()
        assert result == []

    @pytest.mark.asyncio
    async def test_kubecost_allocation_namespace(self, k8s_svc, mock_ch):
        rows = [["default", "cluster-1", 100.0, 14.28, 5, "2024-01-01", "2024-01-07"]]
        cols = [["namespace","String"],["cluster","String"],["total_cost","Float64"],
                ["daily_cost","Float64"],["resource_count","Int64"],
                ["window_start","Date"],["window_end","Date"]]
        mock_ch._client.execute = AsyncMock(return_value=(rows, cols))
        result = await k8s_svc.kubecost_allocation(window="7d", aggregate="namespace")
        assert result["code"] == 200
        assert "data" in result
        assert "default" in result["data"][0]

    def test_parse_window_days(self):
        from cloudsense.k8s.cost_service import _parse_window
        assert _parse_window("7d") == 7
        assert _parse_window("30d") == 30
        assert _parse_window("24h") == 1
        assert _parse_window("48h") == 2

    def test_to_kubecost_alloc_format(self):
        from cloudsense.k8s.cost_service import _to_kubecost_alloc
        row = {"namespace": "backend", "cluster": "prod", "totalCost": 200.0,
               "cpuHours": 100.0, "window": {}}
        result = _to_kubecost_alloc(row)
        assert result["name"] == "backend"
        assert result["totalCost"] == 200.0
        assert "cpuCost" in result
        assert "efficiency" in result
