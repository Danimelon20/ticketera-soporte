from sqlalchemy import Column, Integer, String, Boolean, Date, DateTime, Text, ForeignKey, CheckConstraint, UniqueConstraint, Index, text
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from .database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    email = Column(String(150), unique=True, nullable=False)
    phone = Column(String(20), nullable=True)
    id_number = Column(String(20), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    role = Column(String(20), nullable=False)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        CheckConstraint("role IN ('requester', 'technician', 'coordinator', 'agent', 'admin')", name="chk_users_role"),
    )

    tickets = relationship("Ticket", back_populates="assigned_user", foreign_keys="Ticket.assigned_to")
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
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    contact_phone = Column(String(20), nullable=True)
    contact_id_number = Column(String(20), nullable=True)
    source = Column(String(10), nullable=False, default="web")
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        CheckConstraint("status IN ('new', 'in_progress', 'resolved', 'closed')", name="chk_tickets_status"),
        CheckConstraint("source IN ('web', 'email')", name="chk_tickets_source"),
    )

    category = relationship("Category", back_populates="tickets")
    priority = relationship("Priority", back_populates="tickets")
    assigned_user = relationship("User", back_populates="tickets", foreign_keys=[assigned_to])
    comments = relationship("Comment", back_populates="ticket")
    history = relationship("History", back_populates="ticket")
    ai_analysis = relationship("TicketAIAnalysis", back_populates="ticket", uselist=False)


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


class Session(Base):
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True)
    token = Column(String(64), unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    user = relationship("User")
class InviteCode(Base):
    __tablename__ = "invite_codes"

    id = Column(Integer, primary_key=True)
    code_hash = Column(String(64), unique=True, nullable=False)
    role = Column(String(20), nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    used_at = Column(DateTime, nullable=True)

    creator = relationship("User", foreign_keys=[created_by])
    used_user = relationship("User", foreign_keys=[used_by])

    __table_args__ = (
        CheckConstraint("role IN ('technician', 'coordinator')", name="chk_invite_role"),
    )

    @property
    def used(self) -> bool:
        return self.used_by is not None

    @property
    def used_by_name(self) -> str | None:
        return self.used_user.name if self.used_user else None


class LoginChallenge(Base):
    __tablename__ = "login_challenges"

    id = Column(String(36), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    code_hash = Column(String(64), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    attempts = Column(Integer, nullable=False, default=0)
    used = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class ProcessedEmail(Base):
    __tablename__ = "processed_emails"

    id = Column(Integer, primary_key=True)
    message_id = Column(String(512), unique=True, index=True, nullable=False)
    result = Column(String(40), nullable=False)
    processed_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class TicketFollower(Base):
    __tablename__ = "ticket_followers"

    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        UniqueConstraint("ticket_id", "user_id", name="uq_follower"),
    )


class TicketAIAnalysis(Base):
    __tablename__ = "ticket_ai_analysis"

    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id"), unique=True, nullable=False)
    status = Column(String(10), nullable=False, default="pending")
    attempts = Column(Integer, nullable=False, default=0)
    ai_level = Column(Integer, nullable=True)
    ai_category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    summary = Column(String(300), nullable=True)
    needs_review = Column(Boolean, nullable=False, default=False)
    human_level = Column(Integer, nullable=True)
    human_level_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    due_at = Column(DateTime, nullable=True)
    model = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    analyzed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('pending', 'done', 'error')", name="chk_ai_status"),
        CheckConstraint("ai_level IS NULL OR ai_level BETWEEN 0 AND 4", name="chk_ai_level"),
        CheckConstraint("human_level IS NULL OR human_level BETWEEN 0 AND 4", name="chk_ai_human_level"),
    )

    ticket = relationship("Ticket", back_populates="ai_analysis")
    ai_category = relationship("Category")


class EmailDraft(Base):
    __tablename__ = "email_drafts"

    id = Column(Integer, primary_key=True)
    message_id = Column(String(512), nullable=False)
    requester_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    subject = Column(String(300), nullable=False, default="")
    body = Column(Text, nullable=False, default="")
    status = Column(String(20), nullable=False, default="analyzing")
    attempts = Column(Integer, nullable=False, default=0)
    title = Column(String(200), nullable=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    priority_id = Column(Integer, ForeignKey("priorities.id"), nullable=True)
    description = Column(Text, nullable=True)
    contact_phone = Column(String(20), nullable=True)
    contact_id_number = Column(String(20), nullable=True)
    ai_level = Column(Integer, nullable=True)
    summary = Column(String(300), nullable=True)
    needs_review = Column(Boolean, nullable=False, default=False)
    reviewed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('analyzing', 'pending_review', 'waiting_contact', 'unclear', 'not_support', "
            "'status_reply', 'confirmed', 'discarded', 'error')",
            name="chk_draft_status",
        ),
        CheckConstraint("ai_level IS NULL OR ai_level BETWEEN 0 AND 4", name="chk_draft_ai_level"),
    )

    requester = relationship("User", foreign_keys=[requester_id])
    reviewer = relationship("User", foreign_keys=[reviewed_by])
    category = relationship("Category")
    priority = relationship("Priority")


class UnclearNotice(Base):
    """Cuándo se le pidió más detalles a un solicitante (antibucle)."""
    __tablename__ = "unclear_notices"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    sent_at = Column(DateTime, nullable=False)


class DailySummary(Base):
    """Resumen diario automático ya enviado (uno por día, aunque la app se reinicie)."""
    __tablename__ = "daily_summaries"

    id = Column(Integer, primary_key=True)
    day = Column(Date, unique=True, nullable=False)
    sent_at = Column(DateTime, nullable=False)


class AlertSent(Base):
    """Alerta ya enviada: una sola vez por ticket o borrador y tipo."""
    __tablename__ = "alerts_sent"

    id = Column(Integer, primary_key=True)
    kind = Column(String(20), nullable=False)
    ticket_id = Column(Integer, ForeignKey("tickets.id", ondelete="CASCADE"), nullable=True)
    draft_id = Column(Integer, ForeignKey("email_drafts.id", ondelete="CASCADE"), nullable=True)
    sent_at = Column(DateTime, nullable=False)

    __table_args__ = (
        CheckConstraint("kind IN ('critical', 'due_soon', 'overdue')", name="chk_alert_kind"),
        UniqueConstraint("kind", "ticket_id", "draft_id", name="uq_alert"),
        # En SQLite los NULL no se repiten en un UNIQUE: estos índices sí evitan duplicados
        Index("uq_alert_ticket", "kind", "ticket_id", unique=True, sqlite_where=text("ticket_id IS NOT NULL")),
        Index("uq_alert_draft", "kind", "draft_id", unique=True, sqlite_where=text("draft_id IS NOT NULL")),
    )


class PendingSignup(Base):
    """Registro de cliente sin confirmar: la cuenta (User) se crea al confirmar el código."""
    __tablename__ = "pending_signups"

    id = Column(Integer, primary_key=True)
    email = Column(String(150), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    code_hash = Column(String(64), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    attempts = Column(Integer, nullable=False, default=0)
    sent_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class StaffInvitation(Base):
    """Invitación del personal enviada por correo con un código de activación."""
    __tablename__ = "staff_invitations"

    id = Column(Integer, primary_key=True)
    email = Column(String(150), index=True, nullable=False)
    name = Column(String(100), nullable=False)
    role = Column(String(20), nullable=False)
    code_hash = Column(String(64), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    attempts = Column(Integer, nullable=False, default=0)
    sent_at = Column(DateTime, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    used_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        CheckConstraint("role IN ('technician', 'coordinator')", name="chk_staff_invitation_role"),
    )


class RequesterIdentity(Base):
    """Resultado de Hacienda (sin el nombre que devuelve) y fecha en que nombre y cédula quedaron fijos."""
    __tablename__ = "requester_identities"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    hacienda_result = Column(String(20), nullable=True)
    checked_at = Column(DateTime, nullable=True)
    locked_at = Column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "hacienda_result IS NULL OR hacienda_result IN ('match', 'mismatch', 'not_found', 'unverified')",
            name="chk_identity_hacienda",
        ),
    )


class DraftPendingReason(Base):
    """Motivo por el que un borrador quedó pendiente para que lo revise una persona."""
    __tablename__ = "draft_pending_reasons"

    id = Column(Integer, primary_key=True)
    draft_id = Column(Integer, ForeignKey("email_drafts.id", ondelete="CASCADE"), unique=True, nullable=False)
    reason = Column(String(200), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class SessionActivity(Base):
    """Último uso de una sesión, para vencerla por inactividad."""
    __tablename__ = "session_activity"

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("sessions.id", ondelete="CASCADE"), unique=True, nullable=False)
    last_used_at = Column(DateTime, nullable=False)


class LoginThrottle(Base):
    """Intentos fallidos seguidos por correo (exista o no la cuenta) y hasta cuándo está bloqueado."""
    __tablename__ = "login_throttles"

    id = Column(Integer, primary_key=True)
    email = Column(String(150), unique=True, nullable=False)
    failures = Column(Integer, nullable=False, default=0)
    locked_until = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=False)


class EmailChange(Base):
    """Cambio de correo pendiente: se aplica al confirmar el código que llegó al correo nuevo."""
    __tablename__ = "email_changes"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    new_email = Column(String(150), nullable=False)
    code_hash = Column(String(64), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    attempts = Column(Integer, nullable=False, default=0)
    sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


ACCOUNT_CHANGE_ACTIONS = (
    "password_changed", "email_changed", "phone_changed", "name_changed", "id_number_changed",
    "role_changed", "account_activated", "account_deactivated", "invitation_sent", "invitation_used",
    "login_locked",
)


class AccountChange(Base):
    """Registro de cambios de cuentas. Los valores se guardan ya enmascarados; nunca contraseñas ni códigos."""
    __tablename__ = "account_changes"

    id = Column(Integer, primary_key=True)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    target_user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=True)
    target_label = Column(String(150), nullable=True)
    action = Column(String(30), index=True, nullable=False)
    old_value = Column(String(150), nullable=True)
    new_value = Column(String(150), nullable=True)
    created_at = Column(DateTime, index=True, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "action IN (" + ", ".join(f"'{a}'" for a in ACCOUNT_CHANGE_ACTIONS) + ")",
            name="chk_account_change_action",
        ),
    )

    actor = relationship("User", foreign_keys=[actor_id])
    target = relationship("User", foreign_keys=[target_user_id])
