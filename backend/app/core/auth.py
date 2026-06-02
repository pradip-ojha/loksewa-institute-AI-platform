import uuid

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import AppException
from app.core.security import decode_token
from app.modules.users.models import User, UserRole, UserStatus

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not credentials:
        raise AppException(status.HTTP_401_UNAUTHORIZED, "missing_token", "Authentication required.")

    try:
        payload = decode_token(credentials.credentials)
        user_id: str = payload.get("sub")
        if not user_id:
            raise AppException(status.HTTP_401_UNAUTHORIZED, "invalid_token", "Invalid token payload.")
    except jwt.ExpiredSignatureError:
        raise AppException(status.HTTP_401_UNAUTHORIZED, "token_expired", "Token has expired.")
    except jwt.InvalidTokenError:
        raise AppException(status.HTTP_401_UNAUTHORIZED, "invalid_token", "Invalid token.")

    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()

    if not user:
        raise AppException(status.HTTP_401_UNAUTHORIZED, "user_not_found", "User not found.")
    if user.status == UserStatus.inactive:
        raise AppException(status.HTTP_401_UNAUTHORIZED, "account_inactive", "Account is deactivated.")

    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != UserRole.institute_admin:
        raise AppException(status.HTTP_403_FORBIDDEN, "forbidden", "Admin access required.")
    return user


async def require_student(user: User = Depends(get_current_user)) -> User:
    if user.role != UserRole.student:
        raise AppException(status.HTTP_403_FORBIDDEN, "forbidden", "Student access required.")
    return user
