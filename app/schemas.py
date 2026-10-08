from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Optional, Literal


class CommentCreate(BaseModel):
    actor_id: int
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


class UserRead(BaseModel):
    id: int
    name: str
    email: str
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class CategoryRead(BaseModel):
    id: int
    name: str
    description: Optional[str]
    is_active: bool

    class Config:
        from_attributes = True


class PriorityRead(BaseModel):
    id: int
    name: str
    level: int
    color: Optional[str]

    class Config:
        from_attributes = True


class TicketCreate(BaseModel):
    title: str
    description: str
    category_id: int
    priority_id: int


class TicketUpdate(BaseModel):
    actor_id: int
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
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

    

class HistoryRead(BaseModel):
    id: int
    field: str
    old_value: Optional[str]
    new_value: Optional[str]
    changed_by: int
    changed_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TicketDetail(TicketRead):
    history: list[HistoryRead] = []
    comments: list[CommentRead] = []

    model_config = ConfigDict(from_attributes=True)
