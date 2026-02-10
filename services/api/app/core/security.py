import time
from typing import Any, Dict, List, Optional

import httpx
from fastapi import Header, HTTPException, status
from jose import jwt
from pydantic import BaseModel, Field

from .config import settings


class AuthUser(BaseModel):
    subject: str
    email: str
    display_name: Optional[str] = None
    department: Optional[str] = None
    roles: List[str] = Field(default_factory=list)
    attributes: Dict[str, Any] = Field(default_factory=dict)
    claims: Dict[str, Any] = Field(default_factory=dict)


_JWKS_CACHE: Dict[str, Any] = {"keys": [], "fetched_at": 0.0}
_JWKS_TTL_SECONDS = 3600


def _fetch_jwks() -> Dict[str, Any]:
    if not settings.oidc_jwks_url:
        raise HTTPException(status_code=500, detail="OIDC JWKS URL not configured")
    with httpx.Client(timeout=10) as client:
        resp = client.get(settings.oidc_jwks_url)
        resp.raise_for_status()
        return resp.json()


def _get_jwks() -> Dict[str, Any]:
    now = time.time()
    if now - _JWKS_CACHE["fetched_at"] > _JWKS_TTL_SECONDS:
        _JWKS_CACHE.update(_fetch_jwks())
        _JWKS_CACHE["fetched_at"] = now
    return _JWKS_CACHE


def _get_signing_key(kid: str) -> Dict[str, Any]:
    jwks = _get_jwks()
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            return key
    raise HTTPException(status_code=401, detail="JWT signing key not found")


def _decode_jwt(token: str) -> Dict[str, Any]:
    try:
        headers = jwt.get_unverified_header(token)
        key = _get_signing_key(headers.get("kid"))
        return jwt.decode(
            token,
            key,
            audience=settings.oidc_audience or None,
            issuer=settings.oidc_issuer or None,
        )
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc


def _decode_local_jwt(token: str) -> Dict[str, Any]:
    try:
        return jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired token") from exc


def get_current_user(
    authorization: Optional[str] = Header(default=None),
    x_mock_user: Optional[str] = Header(default=None),
    x_mock_roles: Optional[str] = Header(default=None),
    x_mock_department: Optional[str] = Header(default=None),
) -> AuthUser:
    if settings.mock_oidc:
        if not x_mock_user:
            raise HTTPException(status_code=401, detail="Missing X-Mock-User header")
        roles = [r.strip() for r in (x_mock_roles or "").split(",") if r.strip()]
        return AuthUser(
            subject=x_mock_user,
            email=x_mock_user,
            display_name=x_mock_user,
            department=x_mock_department,
            roles=roles,
            attributes={},
            claims={"mock": True},
        )

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")

    token = authorization.split(" ", 1)[1].strip()

    # Try local JWT first (self-signed), fall back to OIDC
    if settings.jwt_secret:
        claims = _decode_local_jwt(token)
    else:
        claims = _decode_jwt(token)

    email = claims.get("email") or claims.get(settings.oidc_user_claim)
    if not email:
        raise HTTPException(status_code=401, detail="Missing user claim in token")

    roles = claims.get("roles") or claims.get(settings.oidc_roles_claim) or []
    if isinstance(roles, str):
        roles = [roles]

    return AuthUser(
        subject=str(claims.get("sub") or email),
        email=email,
        display_name=claims.get("name") or claims.get(settings.oidc_name_claim),
        department=claims.get("department") or claims.get(settings.oidc_department_claim),
        roles=roles,
        attributes={},
        claims=claims,
    )
