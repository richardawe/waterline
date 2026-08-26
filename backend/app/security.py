import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import get_settings


basic_auth = HTTPBasic(auto_error=False)


def require_admin(credentials: HTTPBasicCredentials | None = Depends(basic_auth)) -> None:
    settings = get_settings()
    username = settings.admin_api_username
    password = settings.admin_api_password

    if not username or not password:
        if settings.environment.lower() == "production":
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "admin API authentication is not configured")
        return

    valid = credentials is not None and secrets.compare_digest(credentials.username, username)
    valid = valid and secrets.compare_digest(credentials.password, password)
    if not valid:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "invalid admin credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
