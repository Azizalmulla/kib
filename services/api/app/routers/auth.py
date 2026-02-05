from fastapi import APIRouter, Depends

from ..core.db import get_db
from ..core.security import AuthUser, get_current_user
from ..core.users import ensure_user
from ..schemas import UserMeResponse, UserProfile

router = APIRouter()


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
