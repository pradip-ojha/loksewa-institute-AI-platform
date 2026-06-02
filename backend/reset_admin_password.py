"""
Dev utility: reset the default admin password to Admin@123.
Run from backend/ directory:  python reset_admin_password.py
"""
import asyncio
import bcrypt
from sqlalchemy import select, update
from app.core.database import AsyncSessionLocal
from app.modules.users.models import User, UserRole


async def reset():
    new_password = "Admin@123"
    new_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User).where(User.role == UserRole.institute_admin)
        )
        admin = result.scalar_one_or_none()

        if not admin:
            print("No admin user found in database.")
            return

        await db.execute(
            update(User).where(User.id == admin.id).values(password_hash=new_hash)
        )
        await db.commit()
        print(f"Password reset for: {admin.email}")
        print("New credentials — Email: admin@neurafix.ai  Password: Admin@123")


asyncio.run(reset())
