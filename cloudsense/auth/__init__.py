"""Auth package — JWT, SAML 2.0, OIDC, RBAC (Phase 5.1)."""
from cloudsense.auth.models import (
    BuiltinRole, Permission, ROLE_PERMISSIONS,
    Tenant, TenantUser, TokenClaims, SSOConfig,
)
from cloudsense.auth.service import AuthService, AuthError
from cloudsense.auth.repository import TenantRepository

__all__ = [
    "BuiltinRole", "Permission", "ROLE_PERMISSIONS",
    "Tenant", "TenantUser", "TokenClaims", "SSOConfig",
    "AuthService", "AuthError", "TenantRepository",
]
