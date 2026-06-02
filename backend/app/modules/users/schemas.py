import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr

from app.modules.users.models import UserRole, UserStatus


class UserOut(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str
    phone: str | None
    role: UserRole
    status: UserStatus
    created_at: datetime
    last_login_at: datetime | None

    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    full_name: str
    email: EmailStr
    password: str
    phone: str | None = None


class UserUpdate(BaseModel):
    full_name: str | None = None
    phone: str | None = None


class PasswordReset(BaseModel):
    new_password: str
