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
    email: str = Field(min_length=3, max_length=150)
    password: str = Field(min_length=8)


class SignupStarted(BaseModel):
    email_hint: str


class EmailCodeRequest(BaseModel):
    email: str
    code: str


class EmailRequest(BaseModel):
    email: str


class StaffInvitationCreate(BaseModel):
    email: str = Field(min_length=3, max_length=150)
    name: str = Field(min_length=1, max_length=100)
    role: Literal["technician", "coordinator"]

    @field_validator("name")
    @classmethod
    def not_empty_or_whitespace(cls, v):
        if not v.strip():
            raise ValueError("El nombre no puede estar vacío")
        return v.strip()


class StaffInvitationRead(BaseModel):
    id: int
    email: str
    name: str
    role: str
    created_at: datetime
    expires_at: datetime
    status: Literal["available", "expired", "used"]
    email_sent: Optional[bool] = None


class ActivationResult(BaseModel):
    email: str
    password: str


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
    profile_complete: bool = True
    identity_locked: bool = False

    model_config = ConfigDict(from_attributes=True)


class ProfileUpdate(BaseModel):
    name: str = Field(max_length=100)
    phone: str
    id_number: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        v = " ".join(v.split())
        if not v:
            raise ValueError("Escriba su nombre completo")
        return v

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v):
        return normalize_phone(v)

    @field_validator("id_number")
    @classmethod
    def validate_id_number(cls, v):
        return normalize_id_number(v)


class TicketCreate(BaseModel):
    # Nombre y cédula salen del perfil del dueño del ticket; el teléfono es opcional
    title: str
    description: str
    category_id: int
    priority_id: int
    contact_phone: Optional[str] = None
    requester_id: Optional[int] = None

    @field_validator("contact_phone")
    @classmethod
    def validate_contact_phone(cls, v):
        if v is None or not v.strip():
            return None
        return normalize_phone(v)


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


class AIAnalysisRead(BaseModel):
    status: str
    ai_level: Optional[int] = None
    ai_category_id: Optional[int] = None
    ai_category_name: Optional[str] = None
    summary: Optional[str] = None
    needs_review: bool = False
    human_level: Optional[int] = None
    effective_level: Optional[int] = None
    due_at: Optional[datetime] = None
    overdue: bool = False


class AILevelUpdate(BaseModel):
    level: int = Field(ge=0, le=4)


class TicketDetail(TicketRead):
    history: list[HistoryRead] = []
    comments: list[CommentRead] = []
    contact_phone: Optional[str] = None
    contact_id_number: Optional[str] = None
    following: bool = False
    ai_analysis: Optional[AIAnalysisRead] = None

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


class ChatTurn(BaseModel):
    role: Literal["user", "model"]
    text: str = Field(max_length=2000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=10)


class ChatResponse(BaseModel):
    reply: str


class DraftRead(BaseModel):
    id: int
    status: str
    created_at: datetime
    requester_id: int
    requester_name: str
    subject: str
    body: str
    title: Optional[str] = None
    category_id: Optional[int] = None
    priority_id: Optional[int] = None
    description: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_id_number: Optional[str] = None
    ai_level: Optional[int] = None
    summary: Optional[str] = None
    needs_review: bool = False
    ticket_id: Optional[int] = None
    pending_reason: Optional[str] = None


class DraftConfirm(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1)
    category_id: int
    priority_id: int
    # Vacíos o enmascarados (con *): se usan los guardados. Se validan en la ruta.
    contact_phone: Optional[str] = None
    contact_id_number: Optional[str] = None

    @field_validator("title", "description")
    @classmethod
    def not_blank(cls, v):
        if not v.strip():
            raise ValueError("Este campo no puede estar vacío")
        return v.strip()
