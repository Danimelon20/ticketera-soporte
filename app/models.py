from sqlalchemy import Column, Integer, String, Boolean, Date, DateTime, Float, Text, ForeignKey, CheckConstraint, UniqueConstraint, Index, text
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
    # Solo si está cancelado (ver TicketCancellation)
    cancellation_row = relationship("TicketCancellation", uselist=False, viewonly=True)


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


class DraftAttachment(Base):
    """Adjunto de un correo que todavía es borrador. Al confirmarlo pasa a attachments (mismo archivo en disco)."""
    __tablename__ = "draft_attachments"

    id = Column(Integer, primary_key=True)
    draft_id = Column(Integer, ForeignKey("email_drafts.id", ondelete="CASCADE"), index=True, nullable=False)
    original_name = Column(String(255), nullable=False)
    content_type = Column(String(100), nullable=False)
    size = Column(Integer, nullable=False)
    sha256 = Column(String(64), nullable=False)
    stored_name = Column(String(36), unique=True, nullable=False)
    received_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class DraftAttachmentNote(Base):
    """Lo que no se tomó de un correo: rechazados, los que pasaron de 5 y enlaces a la nube."""
    __tablename__ = "draft_attachment_notes"

    id = Column(Integer, primary_key=True)
    draft_id = Column(Integer, ForeignKey("email_drafts.id", ondelete="CASCADE"), index=True, nullable=False)
    text = Column(String(300), nullable=False)


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

    # Sin CHECK de roles: la lista la validan el esquema y _check_can_invite


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


class EmailChangeAttempt(Base):
    """Última solicitud de cambio de correo (libre o en uso): marca la espera de 60 segundos."""
    __tablename__ = "email_change_attempts"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    requested_at = Column(DateTime, nullable=False)


ACCOUNT_CHANGE_ACTIONS = (
    "password_changed", "email_changed", "phone_changed", "name_changed", "id_number_changed",
    "role_changed", "account_activated", "account_deactivated", "invitation_sent", "invitation_used",
    "login_locked", "password_reset", "access_reset_sent", "access_reset_forced", "login_unlocked", "tickets_released",
    "request_created", "request_approved", "request_rejected",
    "availability_changed", "availability_period_added", "availability_period_removed", "report_created", "report_confirmed", "report_dismissed",
    "schedule_changed", "sessions_closed", "invitation_cancelled",
    # Descarga del PDF de un reporte de vencimiento (target_label: "Ticket #N")
    "overdue_report_downloaded",
    # Adjuntos de los comentarios (target_label: "Ticket #N"; nunca el nombre original del archivo)
    "attachment_uploaded", "attachment_downloaded",
    # Adjuntos que entran o salen por correo (solo la cantidad, nunca nombres)
    "email_attachments_received", "email_attachments_sent",
    # Agrupación de tickets similares (target_label: "Ticket #15 → #12"; new_value: similitud y si fue automático)
    "ticket_merged", "ticket_merge_undone", "ticket_linked", "ticket_link_undone", "incident_marked",
    # "Ver conversación" del administrador (target_label: "Ticket #N"; new_value: el motivo, enmascarado)
    "conversation_viewed",
    # Hechos desde el servidor (scripts.admin_tools): en el registro aparecen como "Servidor"
    "server_account_deactivated", "server_account_activated", "server_role_changed",
    # Inicio de sesión del personal con Microsoft (rechazos: target_label con el correo enmascarado,
    # new_value con el motivo). emergency_access: el administrador entró con contraseña y código
    "microsoft_linked", "microsoft_login", "microsoft_login_rejected", "emergency_access",
    # Intento de usar una cédula de otra cuenta (new_value: cédula enmascarada y origen)
    "id_number_conflict",
    # Ingreso con contraseña (y código, si el rol lo pide) e intento fallido de una cuenta existente
    "login", "login_failed",
)
SERVER_ACTIONS = ("server_account_deactivated", "server_account_activated", "server_role_changed")


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
    # Sin CHECK: audit.record valida la acción contra ACCOUNT_CHANGE_ACTIONS (agregar una no obliga a borrar la base)
    created_at = Column(DateTime, index=True, nullable=False)

    actor = relationship("User", foreign_keys=[actor_id])
    target = relationship("User", foreign_keys=[target_user_id])


class PasswordReset(Base):
    """Código para restablecer la contraseña (lo pide la persona o quien gestiona su cuenta)."""
    __tablename__ = "password_resets"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    code_hash = Column(String(64), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    attempts = Column(Integer, nullable=False, default=0)
    sent_at = Column(DateTime, nullable=False)
    requested_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


ACCOUNT_REQUEST_KINDS = ("role", "name", "id_number")
ACCOUNT_REQUEST_STATUSES = ("pending", "approved", "rejected")


class AccountRequest(Base):
    """Cambio pedido por un coordinador que aplica el administrador. Tipo y estado se validan en Python."""
    __tablename__ = "account_requests"

    id = Column(Integer, primary_key=True)
    kind = Column(String(20), nullable=False)
    target_user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    reason = Column(String(500), nullable=False)
    # Cédula en claro solo mientras está pendiente; al resolverse queda enmascarada
    new_value = Column(String(150), nullable=False)
    old_value = Column(String(150), nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    resolved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    rejection_reason = Column(String(500), nullable=True)
    created_at = Column(DateTime, nullable=False)

    __table_args__ = (
        Index(
            "uq_account_request_pending", "target_user_id", "kind",
            unique=True, sqlite_where=text("status = 'pending'"),
        ),
    )

    target = relationship("User", foreign_keys=[target_user_id])
    creator = relationship("User", foreign_keys=[created_by])
    resolver = relationship("User", foreign_keys=[resolved_by])


class InternalComment(Base):
    """Marca un comentario como interno: solo lo ve el personal."""
    __tablename__ = "internal_comments"

    id = Column(Integer, primary_key=True)
    comment_id = Column(Integer, ForeignKey("comments.id", ondelete="CASCADE"), unique=True, nullable=False)


class Attachment(Base):
    """Archivo adjunto a un comentario. En disco se guarda como stored_name (uuid); el nombre original solo aquí."""
    __tablename__ = "attachments"

    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id", ondelete="CASCADE"), index=True, nullable=False)
    comment_id = Column(Integer, ForeignKey("comments.id", ondelete="CASCADE"), index=True, nullable=False)
    original_name = Column(String(255), nullable=False)
    content_type = Column(String(100), nullable=False)
    size = Column(Integer, nullable=False)
    sha256 = Column(String(64), nullable=False)
    stored_name = Column(String(36), unique=True, nullable=False)
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    uploaded_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


# Disponible y Ausente salen del horario; Vacaciones e Incapacidad, de un periodo registrado (AvailabilityPeriod)
AVAILABILITY_STATUSES = ("available", "vacation", "incapacity", "away")
AVAILABILITY_PERIOD_KINDS = ("vacation", "incapacity")


class AvailabilityPeriod(Base):
    """Vacaciones o incapacidad de un técnico o coordinador, del primer al último día (inclusive).

    Mientras dure, la persona no está Disponible aunque esté dentro de su horario. Tipo validado en Python.
    """
    __tablename__ = "availability_periods"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    kind = Column(String(20), nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False)


class TechnicianAvailability(Base):
    """Estado manual anterior (Disponible / Vacaciones / Ausente). Ya no se usa: el estado es automático
    (availability.statuses_for). La tabla se conserva para no tener que borrar la base de datos."""
    __tablename__ = "technician_availability"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    status = Column(String(20), nullable=False, default="available")
    return_date = Column(Date, nullable=True)
    # Fecha de regreso cuyo aviso ya se envió: uno solo por fecha
    return_notice_for = Column(Date, nullable=True)
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    updated_at = Column(DateTime, nullable=False)

    user = relationship("User", foreign_keys=[user_id])


REPORT_REASONS = {
    "foreign_id": "Cédula ajena",
    "name_mismatch": "El nombre no coincide",
    "repeated_tickets": "Tickets repetidos o masivos",
    "fake_data": "Datos falsos",
    "other": "Otro",
}
REPORT_STATUSES = ("open", "confirmed", "dismissed")


class AccountReport(Base):
    """Reporte del personal sobre una cuenta de solicitante. Motivo y estado se validan en Python."""
    __tablename__ = "account_reports"

    id = Column(Integer, primary_key=True)
    target_user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    reporter_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    reason = Column(String(30), nullable=False)
    comment = Column(String(500), nullable=True)
    status = Column(String(20), nullable=False, default="open")
    resolved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    resolution_note = Column(String(500), nullable=True)
    created_at = Column(DateTime, nullable=False)

    __table_args__ = (
        Index(
            "uq_account_report_open", "target_user_id", "reporter_id",
            unique=True, sqlite_where=text("status = 'open'"),
        ),
    )

    target = relationship("User", foreign_keys=[target_user_id])
    reporter = relationship("User", foreign_keys=[reporter_id])
    resolver = relationship("User", foreign_keys=[resolved_by])
    # Solo los reportes automáticos (suspicion.py) tienen intentos sumados
    activity = relationship("AutoReportActivity", uselist=False)
    id_owners = relationship("AutoReportIdOwner")


class TechnicianSchedule(Base):
    """Horario de un técnico: un rango por día (0 = lunes … 6 = domingo). Día sin fila: no trabaja."""
    __tablename__ = "technician_schedules"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    weekday = Column(Integer, nullable=False)
    # "HH:MM" en OFFICE_TZ; se validan en Python
    start_time = Column(String(5), nullable=False)
    end_time = Column(String(5), nullable=False)

    __table_args__ = (UniqueConstraint("user_id", "weekday", name="uq_schedule_user_day"),)


AUTO_ASSIGN_STATUSES = ("waiting", "assigned", "cancelled")


class TicketAutoAssignment(Base):
    """Asignación automática de un ticket: pendiente (sin candidato), hecha o ya no aplica. Estado en Python."""
    __tablename__ = "ticket_auto_assignments"

    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id", ondelete="CASCADE"), unique=True, nullable=False)
    status = Column(String(20), nullable=False, default="waiting")
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=True)
    assigned_at = Column(DateTime, nullable=True)
    # Entrada del historial de la asignación: si después hay otra, ya no es automática
    history_id = Column(Integer, ForeignKey("history.id"), nullable=True)
    created_at = Column(DateTime, nullable=False)

    ticket = relationship("Ticket")


class DraftAssignment(Base):
    """Revisor de un borrador: en espera (sin revisor), asignado o ya resuelto (done). Estado en Python.

    notify: si se avisa en la campana (no en la primera asignación al instalar). seen_at: el aviso ya se leyó.
    """
    __tablename__ = "draft_assignments"

    id = Column(Integer, primary_key=True)
    draft_id = Column(Integer, ForeignKey("email_drafts.id", ondelete="CASCADE"), unique=True, nullable=False)
    status = Column(String(20), nullable=False, default="waiting")
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=True)
    assigned_at = Column(DateTime, nullable=True)
    # Nulo: la asignación automática
    assigned_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    notify = Column(Boolean, nullable=False, default=True)
    seen_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False)

    draft = relationship("EmailDraft")
    user = relationship("User", foreign_keys=[user_id])


class DraftAssignmentInstall(Base):
    """Una fila: la primera asignación de borradores. Lo que ya estaba pendiente se asigna sin avisos."""
    __tablename__ = "draft_assignment_install"

    id = Column(Integer, primary_key=True)
    installed_at = Column(DateTime, nullable=False)


# Motivos de cancelación: clave guardada en la base y texto que se muestra
CANCEL_REASONS = {
    "no_longer_applies": "Ya no aplica",
    "duplicate": "Duplicado",
    "no_response": "Sin respuesta del cliente",
    "test_spam": "Prueba o spam",
}


class TicketCancellation(Base):
    """Ticket cancelado: queda "closed" y esta fila guarda el motivo. Al reabrirlo, la fila se borra.

    Tabla aparte para no agregar columnas a tickets (create_all no altera tablas). Motivo validado en Python.
    """
    __tablename__ = "ticket_cancellations"

    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id", ondelete="CASCADE"), unique=True, index=True, nullable=False)
    reason = Column(String(30), nullable=False)
    note = Column(Text, nullable=True)
    cancelled_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    cancelled_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    user = relationship("User")


# Avisos de vencimiento de la campana: al 50 % y al 80 % del plazo, al vencer (al responsable)
# y el reporte de vencimiento (a coordinadores y administradores)
DUE_NOTICE_KINDS = ("due_50", "due_80", "overdue", "report")


class TicketDueNotice(Base):
    """Aviso ya generado de un ticket, uno por tipo: así no se repite. Tipo validado en Python.

    baseline: generado en la primera revisión después de instalar los avisos; nunca se muestra.
    """
    __tablename__ = "ticket_due_notices"

    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id", ondelete="CASCADE"), index=True, nullable=False)
    kind = Column(String(20), nullable=False)
    created_at = Column(DateTime, nullable=False)
    # Al 80 %, si el responsable no está disponible: a quién conviene reasignar
    suggested_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    # Al 80 %, sin responsable o con el responsable no disponible: también llega a los coordinadores
    escalated = Column(Boolean, nullable=False, default=False)
    baseline = Column(Boolean, nullable=False, default=False)

    __table_args__ = (UniqueConstraint("ticket_id", "kind", name="uq_due_notice_ticket_kind"),)

    ticket = relationship("Ticket")
    suggested_user = relationship("User")


class DueNoticesInstall(Base):
    """Una fila: cuándo fue la primera revisión de avisos. Lo que ya correspondía ese día no se avisa."""
    __tablename__ = "due_notices_install"

    id = Column(Integer, primary_key=True)
    installed_at = Column(DateTime, nullable=False)


class OverdueNoticeSeen(Base):
    """Aviso de la campana que la persona ya leyó."""
    __tablename__ = "overdue_notices_seen"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    ticket_id = Column(Integer, ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False)
    kind = Column(String(20), nullable=False)
    seen_at = Column(DateTime, nullable=False)

    __table_args__ = (UniqueConstraint("user_id", "ticket_id", "kind", name="uq_notice_seen"),)


OVERDUE_CAUSES = {
    "no_staff": "Sin técnicos en turno",
    "assignee_unavailable": "Responsable no disponible",
    "high_load": "Carga alta",
    "off_hours": "Fuera de horario",
    "waiting_client": "Esperando al cliente",
    "other": "Otro",
}


class OverdueCause(Base):
    """Causa del vencimiento, registrada por un coordinador o administrador. Causa validada en Python."""
    __tablename__ = "overdue_causes"

    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id", ondelete="CASCADE"), unique=True, index=True, nullable=False)
    cause = Column(String(30), nullable=False)
    note = Column(Text, nullable=True)
    recorded_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    recorded_at = Column(DateTime, nullable=False)

    user = relationship("User")


class TicketEmbedding(Base):
    """Vector del texto de un ticket (asunto + inicio del mensaje, enmascarado) para no recalcularlo."""
    __tablename__ = "ticket_embeddings"

    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id", ondelete="CASCADE"), unique=True, nullable=False)
    model = Column(String(100), nullable=False)
    text_hash = Column(String(64), nullable=False)
    vector = Column(Text, nullable=False)  # JSON
    created_at = Column(DateTime, nullable=False)


class TicketMerge(Base):
    """Caso A: un ticket (o un borrador) del mismo cliente agregado a su ticket abierto. undone_at: separado."""
    __tablename__ = "ticket_merges"

    id = Column(Integer, primary_key=True)
    source_ticket_id = Column(Integer, ForeignKey("tickets.id", ondelete="CASCADE"), index=True, nullable=True)
    source_draft_id = Column(Integer, ForeignKey("email_drafts.id", ondelete="CASCADE"), index=True, nullable=True)
    target_ticket_id = Column(Integer, ForeignKey("tickets.id", ondelete="CASCADE"), index=True, nullable=False)
    kind = Column(String(10), nullable=False)  # "ticket" | "draft", validado en Python
    similarity = Column(Float, nullable=True)
    ai_reason = Column(String(300), nullable=True)
    automatic = Column(Boolean, nullable=False, default=True)
    # Comentario agregado al destino: Separar lo quita
    copied_comment_id = Column(Integer, ForeignKey("comments.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, nullable=False)
    undone_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    undone_at = Column(DateTime, nullable=True)


class TicketLink(Base):
    """Caso B: ticket de otro cliente ligado al principal del grupo (posible incidente). undone_at: separado."""
    __tablename__ = "ticket_links"

    id = Column(Integer, primary_key=True)
    principal_ticket_id = Column(Integer, ForeignKey("tickets.id", ondelete="CASCADE"), index=True, nullable=False)
    related_ticket_id = Column(Integer, ForeignKey("tickets.id", ondelete="CASCADE"), index=True, nullable=False)
    similarity = Column(Float, nullable=True)
    ai_reason = Column(String(300), nullable=True)
    automatic = Column(Boolean, nullable=False, default=True)
    # El responsable se le puso por el principal: al separar vuelve a la cola sin responsable
    assigned_by_link = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False)
    undone_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    undone_at = Column(DateTime, nullable=True)


class TicketIncident(Base):
    """Principal marcado como posible incidente (varios clientes con la misma falla en poco tiempo)."""
    __tablename__ = "ticket_incidents"

    id = Column(Integer, primary_key=True)
    principal_ticket_id = Column(Integer, ForeignKey("tickets.id", ondelete="CASCADE"), unique=True, nullable=False)
    marked_at = Column(DateTime, nullable=False)


class UserMicrosoftLink(Base):
    """Cuenta del personal enlazada a su cuenta Microsoft (oid de Entra ID). Después del primer ingreso
    se entra por el oid, aunque cambie el correo."""
    __tablename__ = "user_microsoft_links"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    oid = Column(String(64), unique=True, nullable=False)
    linked_at = Column(DateTime, nullable=False)


class MicrosoftLoginFlow(Base):
    """Ida a Microsoft en curso: un solo uso y 10 minutos. Solo hashes del state y de la cookie del navegador.

    purpose (validado en Python): "login" o "activate" (con invitation_id).
    """
    __tablename__ = "microsoft_login_flows"

    id = Column(Integer, primary_key=True)
    state_hash = Column(String(64), unique=True, nullable=False)
    browser_hash = Column(String(64), nullable=False)
    # Datos de MSAL (state, nonce y code_verifier) para validar la vuelta
    flow_json = Column(Text, nullable=False)
    purpose = Column(String(20), nullable=False)
    portal = Column(String(20), nullable=True)
    invitation_id = Column(Integer, ForeignKey("staff_invitations.id", ondelete="CASCADE"), nullable=True)
    expires_at = Column(DateTime, nullable=False)


class MicrosoftLoginHandoff(Base):
    """Código de un solo uso (60 s) con el que la página canjea la vuelta de Microsoft por la sesión."""
    __tablename__ = "microsoft_login_handoffs"

    id = Column(Integer, primary_key=True)
    code_hash = Column(String(64), unique=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    browser_hash = Column(String(64), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)


class AutoReportActivity(Base):
    """Intentos sumados a un reporte automático abierto, en vez de abrir uno nuevo por cada intento.

    Tabla aparte para no agregar columnas a account_reports (create_all no altera tablas).
    """
    __tablename__ = "auto_report_activity"

    id = Column(Integer, primary_key=True)
    report_id = Column(Integer, ForeignKey("account_reports.id", ondelete="CASCADE"), unique=True, nullable=False)
    attempts = Column(Integer, nullable=False, default=1)
    last_attempt_at = Column(DateTime, nullable=False)


class AutoReportIdOwner(Base):
    """Cuentas dueñas de las cédulas ajenas que probó la cuenta reportada: cuántas cédulas distintas
    intentó usar, sin guardar ninguna cédula."""
    __tablename__ = "auto_report_id_owners"

    id = Column(Integer, primary_key=True)
    report_id = Column(Integer, ForeignKey("account_reports.id", ondelete="CASCADE"), index=True, nullable=False)
    owner_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    __table_args__ = (UniqueConstraint("report_id", "owner_user_id", name="uq_auto_report_id_owner"),)
