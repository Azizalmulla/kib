import hashlib
import time

from fastapi import APIRouter, Depends, HTTPException
from jose import jwt
from pydantic import BaseModel

from ..core.config import settings
from ..core.db import get_db
from ..core.security import AuthUser, get_current_user
from ..core.users import ensure_user
from ..schemas import UserMeResponse, UserProfile

router = APIRouter()

DEMO_USERS = {
    "frontdesk@kib.com": {
        "password_hash": hashlib.sha256("frontdesk123".encode()).hexdigest(),
        "name": "Sarah Al-Mutairi",
        "roles": ["front_desk"],
        "department": "Customer Service",
    },
    "compliance@kib.com": {
        "password_hash": hashlib.sha256("compliance123".encode()).hexdigest(),
        "name": "Ahmed Al-Rashidi",
        "roles": ["compliance"],
        "department": "Compliance & Risk",
    },
}


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    token: str
    email: str
    name: str
    roles: list[str]


@router.post("/auth/login", response_model=LoginResponse)
def login(request: LoginRequest):
    user = DEMO_USERS.get(request.email.lower())
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    password_hash = hashlib.sha256(request.password.encode()).hexdigest()
    if password_hash != user["password_hash"]:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    payload = {
        "sub": request.email.lower(),
        "email": request.email.lower(),
        "name": user["name"],
        "roles": user["roles"],
        "department": user["department"],
        "iat": int(time.time()),
        "exp": int(time.time()) + settings.jwt_expiry_hours * 3600,
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)

    return LoginResponse(
        token=token,
        email=request.email.lower(),
        name=user["name"],
        roles=user["roles"],
    )


@router.get("/auth/me", response_model=UserMeResponse)
def me(current_user: AuthUser = Depends(get_current_user)) -> UserMeResponse:
    with get_db() as conn:
        user_id = ensure_user(conn, current_user)

    return UserMeResponse(
        user=UserProfile(
            id=user_id,
            email=current_user.email,
            display_name=current_user.display_name,
            department=current_user.department,
            attributes=current_user.attributes,
        ),
        roles=current_user.roles,
        claims=current_user.claims,
    )
