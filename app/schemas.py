from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional, Literal


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

    