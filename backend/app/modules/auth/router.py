from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.core.exceptions import AppException
from app.core.ratelimit import LOGIN_LIMIT, client_ip_key, limiter
from app.core.security import create_access_token, verify_password
from app.modules.auth.schemas import LoginRequest, TokenResponse
from app.modules.users.models import User, UserStatus
from app.modules.users.schemas import UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
@limiter.limit(LOGIN_LIMIT, key_func=client_ip_key)
async def login(request: Request, payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    # Case-insensitive: emails are stored lowercased on write, but pre-existing
    # rows may be mixed-case, so compare lowered-to-lowered.
    result = await db.execute(select(User).where(func.lower(User.email) == payload.email.strip().lower()))
    user = result.scalar_one_or_none()

    if not user or not verify_password(payload.password, user.password_hash):
        raise AppException(401, "invalid_credentials", "Invalid email or password.")

    if user.status == UserStatus.inactive:
        raise AppException(401, "account_inactive", "Your account has been deactivated.")

    await db.execute(
        update(User).where(User.id == user.id).values(last_login_at=datetime.now(timezone.utc))
    )
    await db.commit()
    await db.refresh(user)

    token = create_access_token({"sub": str(user.id), "role": user.role.value})
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)):
    return UserOut.model_validate(current_user)
