"""FastAPI dependencies."""
from __future__ import annotations
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from cloudsense.services.api.config import Settings, get_settings
security = HTTPBearer(auto_error=False)

async def require_auth(credentials: HTTPAuthorizationCredentials | None = Depends(security),
                       settings: Settings = Depends(get_settings)) -> str:
    if settings.app_env == "development" and not credentials:
        return "dev-user"
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing authentication token")
    if len(credentials.credentials) < 10:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return "authenticated-user"
