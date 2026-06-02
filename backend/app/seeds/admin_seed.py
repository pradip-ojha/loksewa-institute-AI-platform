import logging

from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.security import hash_password
from app.modules.users.models import User, UserRole, UserStatus

logger = logging.getLogger("neurafix")


async def create_default_admin() -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User).where(User.role == UserRole.institute_admin)
        )
        existing_admin = result.scalar_one_or_none()

        if existing_admin:
            return

        admin = User(
            full_name=settings.DEFAULT_ADMIN_NAME,
            email=settings.DEFAULT_ADMIN_EMAIL,
            password_hash=hash_password(settings.DEFAULT_ADMIN_PASSWORD),
            role=UserRole.institute_admin,
            status=UserStatus.active,
        )
        db.add(admin)
        await db.commit()

        logger.warning(
            "Default admin created: %s — CHANGE THIS PASSWORD IN PRODUCTION.",
            settings.DEFAULT_ADMIN_EMAIL,
        )
