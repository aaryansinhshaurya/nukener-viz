import uuid
from datetime import datetime
from pydantic import BaseModel, EmailStr, ConfigDict
from app.models.project import ProjectRole, MemberStatus


class ProjectCreate(BaseModel):
    name: str


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    owner_id: uuid.UUID
    created_at: datetime
    deleted_at: datetime | None = None
    role: ProjectRole | None = None


class InviteRequest(BaseModel):
    email: EmailStr
    role: ProjectRole


class InvitationOut(BaseModel):
    id: uuid.UUID
    email: EmailStr
    role: ProjectRole
    status: str
    expires_at: datetime


class InvitationAccept(BaseModel):
    token: str


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    user_name: str
    user_email: str
    role: ProjectRole
    status: MemberStatus
