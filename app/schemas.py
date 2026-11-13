from datetime import date, datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing import Optional, Literal
from .validators import normalize_phone, normalize_id_number, mask_id_number


class CommentCreate(BaseModel):
    content: str
    # Nota interna: solo el personal la ve y no se avisa a nadie por correo
    internal: bool = False

    @field_validator("content")
    @classmethod
    def not_empty_or_whitespace(cls, v):
        if not v.strip():
            raise ValueError("El comentario no puede estar vacío")
        return v.strip()


class AttachmentRead(BaseModel):
    id: int
    original_name: str
    content_type: str
    size: int
    # Imágenes y PDF: se muestran dentro del ticket; lo demás solo se descarga
    inline: bool = False

    model_config = ConfigDict(from_attributes=True)


class CommentRead(BaseModel):
    id: int
    ticket_id: int
    user_id: int
    content: str
    created_at: datetime
    # Solo el personal recibe comentarios internos
    internal: bool = False
    attachments: list[AttachmentRead] = []

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
    # Obligatorio (se valida en la ruta para dar el mensaje): departamento que atiende tickets
    department_id: Optional[int] = None

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
    # registered: no se usó, pero el correo ya tiene cuenta (no se reenvía)
    status: Literal["available", "expired", "used", "registered"]
    email_sent: Optional[bool] = None
    department_name: Optional[str] = None


class ActivationResult(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    """Un solo formulario: el rol sale de la cuenta. Un "portal" de una página vieja se ignora."""
    email: str
    password: str


class UserRead(BaseModel):
    id: int
    name: str
    email: str
    role: str
    is_active: bool
    created_at: datetime
    # Solo técnicos: available, vacation o away
    availability: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class StaffRead(BaseModel):
    """Personal activo para el técnico: sin correo."""
    id: int
    name: str
    role: str
    availability: Optional[str] = None


class PersonRead(BaseModel):
    """Persona que aparece en un ticket: solo nombre y rol, nunca correo, teléfono ni cédula."""
    id: int
    name: str
    role: str


class ScheduleDay(BaseModel):
    """Un día del horario de un técnico: 0 = lunes … 6 = domingo, horas "HH:MM" (se validan en la ruta)."""
    weekday: int
    start: str = Field(max_length=5)
    end: str = Field(max_length=5)


class ScheduleUpdate(BaseModel):
    # Lista vacía: sin horario (no recibe asignaciones automáticas)
    days: list[ScheduleDay] = Field(default_factory=list, max_length=7)


class AvailabilityPeriodRead(BaseModel):
    """Vacaciones o incapacidad: del primer al último día (inclusive). Sin motivo: es un dato personal."""
    id: int
    kind: str
    start_date: date
    end_date: date

    model_config = ConfigDict(from_attributes=True)


class AvailabilityPeriodCreate(BaseModel):
    kind: Literal["vacation", "incapacity"]
    start_date: date
    end_date: date


class MeRead(BaseModel):
    id: int
    name: str
    email: str
    role: str
    phone: Optional[str] = None
    id_number: Optional[str] = None
    profile_complete: bool = True
    identity_locked: bool = False
    # Técnicos y coordinadores: disponibilidad automática (horario y periodos)
    availability: Optional[str] = None
    return_date: Optional[date] = None
    # Lo ven, pero lo definen coordinador y admin
    schedule: Optional[list[ScheduleDay]] = None
    schedule_summary: Optional[str] = None
    # Vacaciones e incapacidades vigentes y futuras (las registra la persona o quien gestiona su cuenta)
    periods: list[AvailabilityPeriodRead] = []
    # Técnicos y coordinadores: departamento (ruta)
    department_name: Optional[str] = None
    # Con el interruptor encendido y sin departamento: aviso "Contacte al administrador" (no recibe tickets)
    placement_missing: bool = False
    # Personal: tickets por departamento encendido (la página muestra crear, transferir y la cola del admin)
    routing_enforced: bool = False

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


class PasswordChange(BaseModel):
    current_password: str
    # El mínimo depende del rol (8 o 12): se valida en la ruta
    new_password: str = Field(max_length=200)


class PhoneUpdate(BaseModel):
    phone: str

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v):
        return normalize_phone(v)


class EmailChangeRequest(BaseModel):
    current_password: str
    new_email: str = Field(min_length=3, max_length=150)


class CodeRequest(BaseModel):
    code: str


class AccountChangeRead(BaseModel):
    id: int
    created_at: datetime
    action: str
    actor: str
    account: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None


class AccountChangePage(BaseModel):
    items: list[AccountChangeRead]
    total: int
    page: int
    pages: int


# ----- Registro de cambios por persona (solo administrador). Datos personales siempre enmascarados -----
class PersonHit(BaseModel):
    id: int
    name: str
    role: str
    email: str


class PersonHeader(BaseModel):
    id: int
    name: str
    role: str
    email: str
    state: Literal["active", "locked", "inactive"]
    # Personal
    schedule_today: Optional[str] = None
    on_shift: Optional[bool] = None
    availability: Optional[str] = None
    last_access: Optional[datetime] = None
    microsoft: Optional[bool] = None
    # Cliente
    phone: Optional[str] = None
    reported: Optional[bool] = None
    identity: Optional[str] = None


class ActivitySummary(BaseModel):
    start: datetime
    end: datetime
    assigned: int
    resolved: int
    reassigned_given: int
    reassigned_received: int
    overdue: int
    drafts_confirmed: int
    drafts_discarded: int
    logins: int


class ActivityItem(BaseModel):
    at: datetime
    group: str
    kind: str
    text: str
    ticket_id: Optional[int] = None


class PersonActivityPage(BaseModel):
    person: PersonHeader
    summary: Optional[ActivitySummary] = None
    items: list[ActivityItem]
    next_cursor: Optional[str] = None


class TicketCreate(BaseModel):
    # Nombre y cédula salen del perfil del dueño del ticket; el teléfono es opcional
    title: str
    description: str
    # Obligatorias en el formulario de siempre (se revisan en la ruta); el del personal usa valores por defecto
    category_id: Optional[int] = None
    priority_id: Optional[int] = None
    contact_phone: Optional[str] = None
    requester_id: Optional[int] = None
    # Con tickets por departamento, el personal crea en nombre de quien reporta
    department_id: Optional[int] = None
    reporter_name: Optional[str] = Field(default=None, max_length=100)
    reporter_email: Optional[str] = Field(default=None, max_length=150)
    # Área de quien reporta: cualquier departamento del organigrama
    reporter_area_id: Optional[int] = None
    reporter_phone: Optional[str] = Field(default=None, max_length=30)
    channel: Optional[Literal["email", "phone", "in_person"]] = None

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
    # Asignar a un técnico no disponible solo con confirmación explícita
    confirm_unavailable: bool = False
    # Con el interruptor encendido: obligatoria al pasar a Resuelto
    solution: Optional[str] = Field(default=None, max_length=5000)


class CancellationRead(BaseModel):
    """Motivo de la cancelación: solo para el personal."""
    reason: str
    reason_label: str
    note: Optional[str] = None
    cancelled_by_name: Optional[str] = None
    cancelled_at: datetime


class TicketCancelRequest(BaseModel):
    """Uno o varios tickets con un solo motivo."""
    ticket_ids: list[int] = Field(min_length=1, max_length=50)
    reason: Literal["no_longer_applies", "duplicate", "no_response", "test_spam"]
    note: Optional[str] = Field(default=None, max_length=500)
    notify_requester: bool = True


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
    # Nombre de quien lo atiende: el solicitante no tiene acceso a la lista de cuentas
    assigned_name: Optional[str] = None
    source: str = "web"
    # Avance para el portal: 1 Recibida, 2 Asignada, 3 En atención, 4 Resuelta
    progress_step: int = 1
    # Último cambio o comentario público
    last_activity_at: Optional[datetime] = None
    # Solo para el personal y si está cancelado
    cancellation: Optional[CancellationRead] = None

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


class RequesterFlags(BaseModel):
    """Solo para el personal: resultado de Hacienda (sin el nombre) y si la cuenta está reportada."""
    identity: str
    reported: bool = False


class GroupItem(BaseModel):
    """Un ticket (o borrador) del grupo: solo número, título y estado, nunca textos ni datos del cliente."""
    merge_id: Optional[int] = None
    link_id: Optional[int] = None
    ticket_id: Optional[int] = None
    draft_id: Optional[int] = None
    title: Optional[str] = None
    status: Optional[str] = None
    similarity: Optional[float] = None
    can_separate: bool = False


class GroupingRead(BaseModel):
    """Solo el personal: fusiones y tickets relacionados (posible incidente)."""
    merged_from: list[GroupItem] = []
    merged_into: Optional[GroupItem] = None
    principal: Optional[GroupItem] = None
    related: list[GroupItem] = []
    incident: bool = False


class IncidentRead(BaseModel):
    principal_id: int
    title: str
    clients: int


class TicketReporterRead(BaseModel):
    name: str
    email: Optional[str] = None
    area_name: Optional[str] = None
    phone: Optional[str] = None
    channel: str
    channel_label: str


class TicketSolutionRead(BaseModel):
    text: str
    resolved_by_name: str
    resolved_at: datetime


class EscalationTarget(BaseModel):
    id: int
    path: str


class TicketDetail(TicketRead):
    history: list[HistoryRead] = []
    comments: list[CommentRead] = []
    contact_phone: Optional[str] = None
    contact_id_number: Optional[str] = None
    following: bool = False
    ai_analysis: Optional[AIAnalysisRead] = None
    people: list[PersonRead] = []
    requester_flags: Optional[RequesterFlags] = None
    auto_assigned: bool = False
    # Solo el personal: mismo cálculo de vencimiento que las alertas
    due_state: Optional[str] = None
    consumed_pct: Optional[int] = None
    # Para todos: número del ticket al que se agregó; grouping solo para el personal
    merged_into: Optional[int] = None
    grouping: Optional[GroupingRead] = None
    # Técnico que lo tuvo asignado antes, o administrador con "Ver conversación": solo lectura
    read_only: bool = False
    # Solo el personal: departamento que atiende (None: "Sin departamento"), quién reportó y la solución
    department_id: Optional[int] = None
    department_name: Optional[str] = None
    reporter: Optional[TicketReporterRead] = None
    solution: Optional[TicketSolutionRead] = None
    # Interruptor de tickets por departamento encendido (la pantalla pide la solución al resolver)
    routing_enforced: bool = False
    # Con el interruptor y departamento: técnicos activos del departamento (los únicos para reasignar)
    assignable_ids: Optional[list[int]] = None
    # Siguiente nivel del departamento (botón "Escalar"): solo con el interruptor y el ticket abierto
    escalate_to: Optional[EscalationTarget] = None

    model_config = ConfigDict(from_attributes=True)


class ConversationViewRequest(BaseModel):
    """Motivo corto por el que el administrador abre la conversación de un ticket."""
    reason: str = Field(min_length=5, max_length=150)

    @field_validator("reason", mode="before")
    @classmethod
    def strip_reason(cls, value):
        return " ".join(value.split()) if isinstance(value, str) else value


class CategoryRead(BaseModel):
    id: int
    name: str

    model_config = ConfigDict(from_attributes=True)


class AuthResponse(BaseModel):
    token: str
    user: UserRead

    model_config = ConfigDict(from_attributes=True)


class MicrosoftLoginConfig(BaseModel):
    enabled: bool


class MicrosoftExchange(BaseModel):
    code: str = Field(min_length=1, max_length=200)


class MicrosoftStart(BaseModel):
    auth_url: str


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
    login_locked: bool = False
    identity_locked: bool = False
    availability: Optional[str] = None
    return_date: Optional[date] = None
    identity: Optional[str] = None
    reported: bool = False
    schedule: Optional[list[ScheduleDay]] = None
    schedule_summary: Optional[str] = None
    on_shift: Optional[bool] = None
    # Turno de hoy en hora de la oficina ("08:00–17:00"); None si hoy no trabaja
    schedule_today: Optional[str] = None


class PasswordResetRequest(BaseModel):
    # Sin pestaña: un "portal" de una página vieja se ignora
    email: str = Field(max_length=150)


class PasswordResetConfirm(BaseModel):
    email: str = Field(max_length=150)
    code: str
    # Solo el solicitante elige su contraseña; al personal se le genera una
    new_password: Optional[str] = Field(default=None, max_length=200)


class AccountRequestCreate(BaseModel):
    kind: Literal["role", "name", "id_number"]
    target_user_id: int
    new_value: str = Field(min_length=1, max_length=150)
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def reason_not_blank(cls, v):
        if not v.strip():
            raise ValueError("Escriba el motivo de la solicitud")
        return v.strip()

    @field_validator("new_value")
    @classmethod
    def new_value_not_blank(cls, v):
        v = " ".join(v.split())
        if not v:
            raise ValueError("Escriba el valor nuevo")
        return v


class AccountRequestRead(BaseModel):
    id: int
    kind: str
    target_user_id: int
    target_name: str
    created_by_name: str
    reason: str
    new_value: str
    old_value: Optional[str] = None
    status: str
    resolved_by_name: Optional[str] = None
    resolved_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    created_at: datetime


class RejectRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def reason_not_blank(cls, v):
        if not v.strip():
            raise ValueError("Escriba el motivo del rechazo")
        return v.strip()


class AdminUserUpdate(BaseModel):
    # Sin "admin": el administrador solo se crea con python -m app.create_admin (422 si se pide)
    role: Literal["requester", "technician", "coordinator"] | None = None
    is_active: bool | None = None
    # Con el cambio técnico ↔ coordinador: el departamento nuevo
    department_id: Optional[int] = None


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
    requester_flags: Optional[RequesterFlags] = None
    # Adjuntos del correo y lo que no se tomó (rechazados, más de 5, enlaces a la nube)
    attachments: list[AttachmentRead] = []
    attachment_notes: list[str] = []
    # Se agregó a un ticket abierto del mismo cliente
    merged_into: Optional[int] = None
    # Revisor: nulo es "Sin revisor"
    assigned_to: Optional[int] = None
    assigned_name: Optional[str] = None
    can_review: bool = False
    can_reassign: bool = False
    # Tickets por departamento: departamento elegido por la IA o el administrador; «Por clasificar» si todavía no
    department_name: Optional[str] = None
    classification: Optional[Literal["routed", "pending", "classified"]] = None


class DraftAssign(BaseModel):
    """El coordinador elige otro técnico para revisar el borrador."""
    user_id: int
    confirm_unavailable: bool = False


class DraftTechnician(BaseModel):
    """Técnico del departamento para confirmar un borrador: carga de la asignación automática y disponibilidad."""
    id: int
    name: str
    load: int
    availability: str
    availability_text: str


class DraftConfirm(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1)
    category_id: int
    priority_id: int
    # Vacíos o enmascarados (con *): se usan los guardados. Se validan en la ruta.
    contact_phone: Optional[str] = None
    contact_id_number: Optional[str] = None
    # Tickets por departamento: técnico activo del departamento del borrador (obligatorio; se valida en la ruta)
    assigned_to: Optional[int] = None
    confirm_unavailable: bool = False

    @field_validator("title", "description")
    @classmethod
    def not_blank(cls, v):
        if not v.strip():
            raise ValueError("Este campo no puede estar vacío")
        return v.strip()




class AccountReportCreate(BaseModel):
    target_user_id: int
    reason: str = Field(max_length=30)
    comment: Optional[str] = Field(default=None, max_length=500)


class AccountReportResolve(BaseModel):
    note: str = Field(max_length=500)

    @field_validator("note")
    @classmethod
    def note_required(cls, v):
        if not v.strip():
            raise ValueError("Escriba una nota")
        return v.strip()


class AccountReportRead(BaseModel):
    id: int
    target_user_id: int
    target_name: str
    target_email: str
    reporter_id: int
    reporter: str
    reporter_role: str
    reason: str
    reason_label: str
    comment: Optional[str] = None
    status: str
    resolver: Optional[str] = None
    resolution_note: Optional[str] = None
    created_at: datetime
    resolved_at: Optional[datetime] = None
    # Solo en la lista de coordinador y admin: otros reportes abiertos sobre la misma cuenta
    other_open: Optional[int] = None
    # Reportes automáticos: intentos sumados, el último y cuántas cédulas ajenas distintas probó
    attempts: Optional[int] = None
    last_attempt_at: Optional[datetime] = None
    distinct_id_numbers: Optional[int] = None
    strong_signal: bool = False


class AccountReportCount(BaseModel):
    open: int


class NavShift(BaseModel):
    """Turno de hoy del técnico (hora local) y su carga actual."""
    start: Optional[str] = None
    end: Optional[str] = None
    on_shift: bool = False
    load: int = 0


class NavCounts(BaseModel):
    """Contadores del menú. Cada rol recibe solo sus claves (las demás no se envían)."""
    unassigned: Optional[int] = None
    due_soon: Optional[int] = None
    overdue: Optional[int] = None
    open: Optional[int] = None
    resolved_today: Optional[int] = None
    drafts_pending: Optional[int] = None
    reports_open: Optional[int] = None
    invitations_pending: Optional[int] = None
    approvals_pending: Optional[int] = None
    active_admins: Optional[int] = None
    unclassified: Optional[int] = None
    # Borradores en «Por clasificar» (administrador, con tickets por departamento)
    drafts_unclassified: Optional[int] = None
    mine_open: Optional[int] = None
    mine_due_soon: Optional[int] = None
    mine_resolved_today: Optional[int] = None
    cancelled: Optional[int] = None
    shift: Optional[NavShift] = None


class SystemStatus(BaseModel):
    """Solo activo/inactivo y conteos, nunca valores de configuración."""
    email: bool
    ai_analysis: bool
    auto_assign: bool
    auto_assign_mode: Optional[Literal["levels", "count"]] = None
    technicians_on_shift: int


class QueueItem(BaseModel):
    """Fila de la cola: sin descripción, teléfono, cédula ni correo."""
    id: int
    title: str
    requester_name: str
    # Departamento que atiende; None: "Sin departamento"
    department_name: Optional[str] = None
    level: Optional[int] = None
    source: str
    status: str
    assigned_to: Optional[int] = None
    assigned_name: Optional[str] = None
    reported: bool = False
    auto_assigned: bool = False
    cancelled: bool = False
    due_at: Optional[datetime] = None
    due_state: Optional[str] = None
    remaining_seconds: Optional[int] = None
    consumed_pct: Optional[int] = None
    assignee_note: Optional[Literal["vacation", "incapacity", "away", "after_shift"]] = None
    incident: bool = False
    # Causa registrada del vencimiento (su etiqueta), si la tiene
    overdue_cause: Optional[str] = None


class QueuePage(BaseModel):
    items: list[QueueItem]
    total: int
    page: int
    pages: int


class SuggestionRead(BaseModel):
    """Candidato de la asignación automática: solo nombre y carga."""
    user_id: int
    name: str
    load: int


class AdminAccountRow(BaseModel):
    """Fila de Admin › Cuentas: correo siempre enmascarado, sin teléfono ni cédula."""
    kind: Literal["user", "invitation"]
    id: int
    name: str
    email: str
    role: str
    state: Literal["active", "locked", "inactive", "vacation", "incapacity", "away", "invited"]
    # Última actividad en una sesión abierta (no es el último inicio de sesión)
    last_activity: Optional[datetime] = None
    identity: Optional[str] = None
    invitation_hours_left: Optional[int] = None
    # Solo con el filtro por horario: el turno de ese día ("08:00–17:00")
    schedule_day: Optional[str] = None
    # Personal: "Ruta · N2"; None sin departamento
    department_name: Optional[str] = None


class AdminAccountAlerts(BaseModel):
    pending_role_request_id: Optional[int] = None
    pending_role: Optional[str] = None
    pending_role_by: Optional[str] = None
    locked_until: Optional[datetime] = None
    open_reports: int = 0


class AdminAccountDetail(BaseModel):
    id: int
    name: str
    email: str
    role: str
    state: str
    is_active: bool
    phone: Optional[str] = None
    id_number: Optional[str] = None
    created_at: datetime
    last_activity: Optional[datetime] = None
    active_sessions: int = 0
    identity: Optional[str] = None
    reported: bool = False
    availability: Optional[str] = None
    return_date: Optional[date] = None
    schedule_summary: Optional[str] = None
    alerts: AdminAccountAlerts
    recent_changes: list[AccountChangeRead] = []


class TeamMember(BaseModel):
    """Fila de Equipo / Horarios: correo enmascarado, nunca teléfono ni cédula."""
    id: int
    name: str
    email: str
    role: str
    is_active: bool
    # Técnicos y coordinadores: "Ruta · N2"; None sin departamento
    department_name: Optional[str] = None
    # None: otro coordinador visto por un coordinador (no ve sus tickets)
    tickets_assigned_open: Optional[int] = 0
    login_locked: bool = False
    availability: Optional[str] = None
    return_date: Optional[date] = None
    schedule: Optional[list[ScheduleDay]] = None
    schedule_today: Optional[str] = None
    on_shift: Optional[bool] = None
    periods: list[AvailabilityPeriodRead] = []
    # Solo para el coordinador: carga de la asignación automática (tickets abiertos y borradores pendientes)
    load: Optional[int] = None


class AccountListRow(BaseModel):
    """Fila de Cuentas (admin y coordinador): correo siempre enmascarado, nunca teléfono ni cédula."""
    kind: Literal["user", "invitation"]
    id: int
    name: str
    email: str
    role: str
    state: Literal["active", "locked", "inactive", "vacation", "incapacity", "away", "invited"]
    # Solo clientes
    identity: Optional[str] = None
    reported: bool = False
    # Clientes: tickets abiertos que crearon. Personal: tickets abiertos asignados
    open_tickets: int = 0
    invitation_hours_left: Optional[int] = None
    # Solo con el filtro por horario: el turno de ese día ("08:00–17:00")
    schedule_day: Optional[str] = None
    # Personal: "Ruta · N2"; None sin departamento
    department_name: Optional[str] = None


class AccountCounts(BaseModel):
    all: int = 0
    staff: int = 0
    clients: int = 0
    locked: int = 0
    reported: int = 0
    unverified: int = 0
    invitations: int = 0


class AccountListPage(BaseModel):
    items: list[AccountListRow]
    counts: AccountCounts


class AccountRecentTicket(BaseModel):
    id: int
    title: str
    status: str


class AccountSummary(BaseModel):
    """Panel de Cuentas. actions: solo lo que puede hacer quien mira (el servidor lo vuelve a revisar)."""
    id: int
    name: str
    email: str
    role: str
    state: str
    is_active: bool
    identity: Optional[str] = None
    reported: bool = False
    availability: Optional[str] = None
    return_date: Optional[date] = None
    schedule: Optional[list[ScheduleDay]] = None
    alerts: AdminAccountAlerts
    recent_tickets: list[AccountRecentTicket] = []
    recent_changes: list[AccountChangeRead] = []
    periods: list[AvailabilityPeriodRead] = []
    actions: list[str] = []


class NoticeSuggestion(BaseModel):
    user_id: int
    name: str


class OverdueNotice(BaseModel):
    """Aviso de la campana: solo número, título enmascarado y fechas. Los de borradores traen draft_id."""
    ticket_id: Optional[int] = None
    draft_id: Optional[int] = None
    title: str
    kind: Literal["due_50", "due_80", "overdue", "report", "draft_assigned", "ticket_transferred",
                  "ticket_resolved"]
    created_at: datetime
    due_at: Optional[datetime] = None
    read: bool
    # Avisos de la bandeja (transferido, resuelto): la frase completa
    text: Optional[str] = None
    needs_review: bool = False
    suggestion: Optional[NoticeSuggestion] = None


class OverdueNoticeList(BaseModel):
    items: list[OverdueNotice]
    unread: int


class NoticeReadRequest(BaseModel):
    """Un ticket (ticket_id), un borrador (draft_id) o todos (all)."""
    ticket_id: Optional[int] = None
    draft_id: Optional[int] = None
    all: bool = False


class OverdueCauseCreate(BaseModel):
    cause: Literal["no_staff", "assignee_unavailable", "high_load", "off_hours", "waiting_client", "other"]
    note: Optional[str] = Field(default=None, max_length=500)

    @field_validator("note")
    @classmethod
    def strip_note(cls, value):
        return (value.strip() or None) if value else None

    @model_validator(mode="after")
    def other_needs_note(self):
        if self.cause == "other" and not self.note:
            raise ValueError("Describa la causa en la nota")
        return self


class OverdueCauseRead(BaseModel):
    cause: str
    label: str
    note: Optional[str] = None
    recorded_by_name: Optional[str] = None
    recorded_at: datetime


class OverdueTimelineEvent(BaseModel):
    at: datetime
    kind: str
    actor: Optional[str] = None
    text: str
    auto: bool = False


class OverdueReport(BaseModel):
    id: int
    title: str
    status: str
    cancelled: bool = False
    level: Optional[int] = None
    source: str
    priority: Optional[str] = None
    category: Optional[str] = None
    requester_name: Optional[str] = None
    assigned_to: Optional[int] = None
    assigned_name: Optional[str] = None
    created_at: datetime
    due_at: datetime
    overdue_seconds: int
    first_response_at: Optional[datetime] = None
    without_response_seconds: int
    last_activity_at: Optional[datetime] = None
    contact_phone: Optional[str] = None
    contact_id_number: Optional[str] = None
    timeline: list[OverdueTimelineEvent]
    cause: Optional[OverdueCauseRead] = None
    can_record_cause: bool = False


# ===== Departamentos (organigrama) =====

class DepartmentRead(BaseModel):
    id: int
    name: str
    parent_id: Optional[int] = None
    path: str
    is_active: bool
    # Activo él y todos sus padres
    active_path: bool = True
    handles_tickets: bool = False
    aliases: list[str] = []
    technicians: int = 0
    coordinator_id: Optional[int] = None
    coordinator_name: Optional[str] = None
    # Siguiente nivel (Escalar transfiere a ese departamento)
    next_department_id: Optional[int] = None
    next_department_path: Optional[str] = None
    # Ficha con «Qué atiende»: sin ella no participa en la clasificación de correos
    has_profile: bool = False


class DepartmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    parent_id: Optional[int] = None
    handles_tickets: bool = False


class DepartmentUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    # 0: sin padre (raíz)
    parent_id: Optional[int] = Field(default=None, ge=0)
    is_active: Optional[bool] = None
    handles_tickets: Optional[bool] = None
    add_alias: Optional[str] = Field(default=None, min_length=1, max_length=300)
    remove_alias: Optional[str] = Field(default=None, max_length=300)
    # Siguiente nivel; 0: quitarlo
    next_department_id: Optional[int] = Field(default=None, ge=0)


class DepartmentImportRow(BaseModel):
    line: int
    original: str
    path: Optional[str] = None
    status: Literal["new", "exists", "duplicate", "error"]
    notes: list[str] = []


class DepartmentMerge(BaseModel):
    # "into" queda como nombre oficial y "merge" como alias
    merge: str = Field(max_length=1000)
    into: str = Field(max_length=1000)
    reason: Optional[str] = None
    # False: uno ya existe con otro padre y se conserva (no se puede invertir)
    choosable: bool = True


class DepartmentImportPreview(BaseModel):
    rows: list[DepartmentImportRow]
    suggestions: list[DepartmentMerge]
    counts: dict[str, int]


class DepartmentImportConfirm(BaseModel):
    paths: list[str] = Field(max_length=2000)
    merges: list[DepartmentMerge] = Field(default_factory=list, max_length=2000)


class DepartmentImportResult(BaseModel):
    created: int
    aliases: int


class DepartmentProfileRead(BaseModel):
    department_id: int
    path: str
    handles: str = ""
    # Uno por línea
    examples: str = ""
    not_handles: str = ""
    updated_at: Optional[datetime] = None


class DepartmentProfileUpdate(BaseModel):
    handles: str = Field(default="", max_length=1000)
    examples: str = Field(default="", max_length=7000)
    not_handles: str = Field(default="", max_length=1000)


class ClassificationTestRequest(BaseModel):
    subject: str = Field(default="", max_length=300)
    body: str = Field(min_length=1, max_length=5000)


class ClassificationOption(BaseModel):
    department_id: int
    path: str
    similarity: Optional[float] = None


class ClassificationTestResult(BaseModel):
    # Lo que la IA eligió (None: ninguno o no respondió) y su motivo
    llm_department_id: Optional[int] = None
    llm_department_path: Optional[str] = None
    reason: Optional[str] = None
    # Las dos opciones más probables, con su similitud si hay embeddings
    options: list[ClassificationOption] = []
    margin: Optional[float] = None
    threshold: float
    agrees: bool
    passes: bool
    notes: list[str] = []


class PendingDraftOption(BaseModel):
    department_id: int
    path: str
    similarity: Optional[float] = None
    # Sin coordinador activo no se puede elegir (el servidor responde 409)
    has_coordinator: bool = True
    # Por qué está aquí: ai (lo eligió la IA), similarity (el más parecido a los ejemplos), both (las dos
    # coinciden) o second (el 2.º más parecido)
    role: Literal["ai", "similarity", "both", "second"] = "second"


class PendingDraft(BaseModel):
    """Borrador en «Por clasificar»: solo lo necesario para elegir el departamento (enmascarado)."""
    draft_id: int
    created_at: datetime
    requester_name: str
    subject: str
    excerpt: str
    summary: Optional[str] = None
    pending_reason: Optional[str] = None
    ai_department_path: Optional[str] = None
    ai_reason: Optional[str] = None
    ai_margin: Optional[float] = None
    options: list[PendingDraftOption] = []
    # La IA y la similitud eligieron departamentos distintos (texto para el administrador)
    disagreement: Optional[str] = None


class PendingDrafts(BaseModel):
    threshold: float
    routing_enforced: bool
    items: list[PendingDraft] = []


class DraftDepartmentChoice(BaseModel):
    department_id: int


class DraftDepartmentChosen(BaseModel):
    draft_id: int
    department_name: str
    matched_first: Optional[bool] = None


class ThresholdUpdate(BaseModel):
    threshold: float = Field(ge=0, le=1)


class ThresholdRead(BaseModel):
    threshold: float


class PlacementUpdate(BaseModel):
    department_id: Optional[int] = None


class PlacementRead(BaseModel):
    user_id: int
    role: str
    department_id: Optional[int] = None
    department_name: Optional[str] = None
    placed: bool


class UnplacedPerson(BaseModel):
    id: int
    name: str
    role: str
    # Departamento que ya no sirve (desactivado o que no atiende tickets); None si no tiene
    department_name: Optional[str] = None


class StaffAlerts(BaseModel):
    unplaced: list[UnplacedPerson]



class ChecklistItem(BaseModel):
    key: str
    label: str
    ok: bool
    detail: Optional[str] = None
    # Sección del menú donde se corrige (ruta)
    link: Optional[str] = None
    # Advertencia: se muestra, pero no impide encender
    warning: bool = False


class SwitchRead(BaseModel):
    key: str
    label: str
    enabled: bool
    checklist: list[ChecklistItem]
    # Se puede encender: toda la lista está lista
    ready: bool
    updated_at: Optional[datetime] = None
    updated_by_name: Optional[str] = None


class SwitchUpdate(BaseModel):
    enabled: bool


class OverviewDepartment(BaseModel):
    id: int
    path: str
    coordinator_name: Optional[str] = None
    technicians: int = 0
    # Tickets abiertos y vencidos del departamento
    open: Optional[int] = None
    overdue: Optional[int] = None


class AdminOverview(BaseModel):
    """Panel del administrador: una fila por departamento que atiende tickets."""
    departments: list[OverviewDepartment]
    routing_enforced: bool
    unplaced: list[UnplacedPerson]


class UnclassifiedTicket(BaseModel):
    id: int
    title: str
    status: str
    created_at: datetime
    requester_name: str
    assigned_name: Optional[str] = None


class TicketDepartmentUpdate(BaseModel):
    department_id: int



class StaffTicketCreated(BaseModel):
    """Confirmación inmediata al crear un ticket desde el personal: número y técnico (o pendiente)."""
    id: int
    title: str
    department_name: str
    assigned_to: Optional[int] = None
    assigned_name: Optional[str] = None


class DepartmentOption(BaseModel):
    id: int
    path: str


class PriorityOption(BaseModel):
    id: int
    name: str
    level: int
    # Plazo que fija: "16 horas hábiles", "1 día hábil"
    due_text: str


class CategoryOption(BaseModel):
    id: int
    name: str


class DepartmentOptions(BaseModel):
    """Listas del formulario del personal: departamentos que atienden, el organigrama completo (áreas),
    prioridades con su plazo y categorías."""
    serving: list[DepartmentOption]
    areas: list[DepartmentOption]
    priorities: list[PriorityOption] = []
    categories: list[CategoryOption] = []



class TicketTransfer(BaseModel):
    department_id: int
    # Motivo obligatorio: al menos 15 caracteres
    reason: str = Field(min_length=15, max_length=300)

    @field_validator("reason", mode="before")
    @classmethod
    def strip_reason(cls, value):
        return " ".join(value.split()) if isinstance(value, str) else value


class TicketTransferResult(BaseModel):
    department_name: str
    assigned_to: Optional[int] = None
    # Quien transfirió todavía puede ver el ticket (si no, la página vuelve a la lista)
    still_visible: bool
