from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey, CheckConstraint
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from .database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    email = Column(String(150), unique=True, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    tickets = relationship("Ticket", back_populates="assigned_user")
    history = relationship("History", back_populates="user")


class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True)
    name = Column(String(80), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)

    tickets = relationship("Ticket", back_populates="category")


class Priority(Base):
    __tablename__ = "priorities"

    id = Column(Integer, primary_key=True)
    name = Column(String(30), unique=True, nullable=False)
    level = Column(Integer, nullable=False)
    color = Column(String(20), nullable=True)

    tickets = relationship("Ticket", back_populates="priority")


class Ticket(Base):
    __tablename__ = "tickets"

    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    priority_id = Column(Integer, ForeignKey("priorities.id"), nullable=False)
    status = Column(String(20), nullable=False, default="new")
    assigned_to = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        CheckConstraint("status IN ('new', 'in_progress', 'resolved', 'closed')", name="chk_tickets_status"),
    )

    category = relationship("Category", back_populates="tickets")
    priority = relationship("Priority", back_populates="tickets")
    assigned_user = relationship("User", back_populates="tickets")
    comments = relationship("Comment", back_populates="ticket")
    history = relationship("History", back_populates="ticket")


class Comment(Base):
    __tablename__ = "comments"

    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    ticket = relationship("Ticket", back_populates="comments")
    user = relationship("User")


class History(Base):
    __tablename__ = "history"

    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id"), nullable=False)
    field = Column(String(50), nullable=False)
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)
    changed_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    changed_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    ticket = relationship("Ticket", back_populates="history")
    user = relationship("User", back_populates="history")