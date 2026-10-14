from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Optional, Literal
from .validators import normalize_phone, normalize_id_number, mask_id_number


class CommentCreate(BaseModel):
    content: str

    @field_validator("content")
    @classmethod
    def not_empty_or_whitespace(cls, v):
        if not v.strip():
            raise ValueError("El comentario no puede estar vacío")
        return v.strip()


class CommentRead(BaseModel):
    id: int
    ticket_id: int
    user_id: int
    content: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UserCreate(BaseModel):
    name: str
    email: str
    role: Literal["requester", "technician", "coordinator"]


class RegisterRequest(BaseModel):
    name: str
    email: str
    invite_code: Optional[str] = None
    password: str = Field(min_length=8)

    @field_validator("name")
    @classmethod
    def not_empty_or_whitespace(cls, v):
        if not v.strip():
            raise ValueError("El nombre no puede estar vacío")
        return v.strip()


class InviteCreate(BaseModel):
    role: Literal["technician", "coordinator"]


class InviteCreated(BaseModel):
    code: str
    role: str
    expires_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InviteRead(BaseModel):
    id: int
    role: str
    created_at: datetime
    expires_at: datetime
    used: bool
    used_by_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class LoginRequest(BaseModel):
    email: str
    password: str


class UserRead(BaseModel):
    id: int
    name: str
    email: str
    role: str
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MeRead(BaseModel):
    id: int
    name: str
    email: str
    role: str
    phone: Optional[str] = None
    id_number: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class TicketCreate(BaseModel):
    title: str
    description: str
    category_id: int
    priority_id: int
    contact_phone: str
    contact_id_number: str
    requester_id: Optional[int] = None

    @field_validator("contact_phone")
    @classmethod
    def validate_contact_phone(cls, v):
        return normalize_phone(v)

    @field_validator("contact_id_number")
    @classmethod
    def validate_contact_id_number(cls, v):
        return normalize_id_number(v)


class TicketUpdate(BaseModel):
    status: Optional[Literal["new", "in_progress", "resolved", "closed"]] = None
    assigned_to: Optional[int] = None
    priority_id: Optional[int] = None
    category_id: Optional[int] = None


class TicketRead(BaseModel):
    id: int
    title: str
    description: str
    category_id: int
    priority_id: int
    status: str
    assigned_to: Optional[int]
    created_by: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class HistoryRead(BaseModel):
    id: int
    field: str
    old_value: Optional[str]
    new_value: Optional[str]
    changed_by: int
    changed_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RequesterRead(BaseModel):
    id: int
    name: str
    email: str
    phone: Optional[str] = None
    id_number: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class PriorityRead(BaseModel):
    id: int
    name: str
    level: int
    color: Optional[str]

    model_config = ConfigDict(from_attributes=True)


class TicketDetail(TicketRead):
    history: list[HistoryRead] = []
    comments: list[CommentRead] = []
    contact_phone: Optional[str] = None
    contact_id_number: Optional[str] = None
    following: bool = False

    model_config = ConfigDict(from_attributes=True)


class CategoryRead(BaseModel):
    id: int
    name: str

    model_config = ConfigDict(from_attributes=True)


class AuthResponse(BaseModel):
    token: str
    user: UserRead

    model_config = ConfigDict(from_attributes=True)


class LoginChallengeResponse(BaseModel):
    requires_code: bool = True
    challenge_id: str
    email_hint: str


class VerifyCodeRequest(BaseModel):
    challenge_id: str
    code: str


class ResendCodeRequest(BaseModel):
    challenge_id: str


class DirectoryUser(BaseModel):
    id: int
    name: str
    email: str
    role: str
    phone: Optional[str] = None
    id_number: Optional[str] = None
    is_active: bool
    tickets_created: int = 0
    tickets_assigned_open: int = 0


class AdminUserUpdate(BaseModel):
    role: Literal["requester", "technician", "coordinator", "admin"] | None = None
    is_active: bool | None = None
