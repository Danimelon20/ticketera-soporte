import os

# Correo (Microsoft Graph con permisos delegados)
EMAIL_ENABLED = os.getenv("EMAIL_ENABLED", "false").lower() == "true"
MS_TENANT_ID = os.getenv("MS_TENANT_ID", "")
MS_CLIENT_ID = os.getenv("MS_CLIENT_ID", "")
SUPPORT_MAILBOX = os.getenv("SUPPORT_MAILBOX", "").lower()
MAIL_FOLDER = os.getenv("MAIL_FOLDER", "Ticketera")
MS_TOKEN_CACHE = os.getenv("MS_TOKEN_CACHE", "data/ms_token_cache.json")
EMAIL_POLL_SECONDS = int(os.getenv("EMAIL_POLL_SECONDS", "60"))
APP_URL = os.getenv("APP_URL", "http://localhost:8000")

# Inicio de sesión del personal con Microsoft (Entra ID). Mismo registro de app que el buzón, pero
# con su propio secreto y sus propias variables: nunca usa MS_TENANT_ID, MS_CLIENT_ID ni MS_TOKEN_CACHE
MS_LOGIN_ENABLED = os.getenv("MS_LOGIN_ENABLED", "false").lower() == "true"
MS_LOGIN_TENANT_ID = os.getenv("MS_LOGIN_TENANT_ID", "")
MS_LOGIN_CLIENT_ID = os.getenv("MS_LOGIN_CLIENT_ID", "")
MS_LOGIN_CLIENT_SECRET = os.getenv("MS_LOGIN_CLIENT_SECRET", "")
MS_LOGIN_REDIRECT_URI = os.getenv("MS_LOGIN_REDIRECT_URI", "http://localhost:8000/auth/microsoft/callback")

# Dominios de la empresa (separados por coma): sus correos no pueden ser cuentas de solicitante. Vacío: no bloquea
INTERNAL_DOMAINS = [d.strip().lower().lstrip("@") for d in os.getenv("INTERNAL_DOMAINS", "").split(",") if d.strip()]

# Asistente de la página (modelo local servido por Ollama)
CHATBOT_ENABLED = os.getenv("CHATBOT_ENABLED", "false").lower() == "true"
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://ollama:11434").rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen3:1.7b")

# Análisis de los correos con el mismo modelo local (solo se muestra, no actúa)
AI_ANALYSIS_ENABLED = os.getenv("AI_ANALYSIS_ENABLED", "false").lower() == "true"

# Horario de oficina para los tiempos meta
OFFICE_DAYS = os.getenv("OFFICE_DAYS", "1-5")
OFFICE_START = os.getenv("OFFICE_START", "08:00")
OFFICE_END = os.getenv("OFFICE_END", "17:00")
OFFICE_TZ = os.getenv("OFFICE_TZ", "America/Costa_Rica")
OFFICE_HOLIDAYS = os.getenv("OFFICE_HOLIDAYS", "")

# Resumen diario por correo a coordinadores y administradores (hora local de la oficina)
DAILY_SUMMARY_ENABLED = os.getenv("DAILY_SUMMARY_ENABLED", "false").lower() == "true"
DAILY_SUMMARY_HOUR = int(os.getenv("DAILY_SUMMARY_HOUR", "8"))

# Alertas por correo: críticos, por vencer (porcentaje del tiempo meta hábil) y vencidos
ALERTS_ENABLED = os.getenv("ALERTS_ENABLED", "false").lower() == "true"
ALERT_WARN_PERCENT = int(os.getenv("ALERT_WARN_PERCENT", "75"))
ALERTS_CHECK_SECONDS = int(os.getenv("ALERTS_CHECK_SECONDS", "120"))

# Asignación automática por carga a técnicos disponibles y en turno (no depende del correo)
AUTO_ASSIGN_ENABLED = os.getenv("AUTO_ASSIGN_ENABLED", "false").lower() == "true"
# levels: suma de niveles de sus tickets abiertos; count: cantidad de tickets abiertos
AUTO_ASSIGN_LOAD_MODE = os.getenv("AUTO_ASSIGN_LOAD_MODE", "levels").lower()
AUTO_ASSIGN_CHECK_SECONDS = int(os.getenv("AUTO_ASSIGN_CHECK_SECONDS", "60"))

# Agrupación de tickets similares (mismo cliente: se fusiona; varios clientes: incidente)
GROUPING_ENABLED = os.getenv("GROUPING_ENABLED", "false").lower() == "true"
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "qwen3-embedding:0.6b")
# Similitud mínima (coseno de los embeddings, 0 a 1). Altas a propósito: mejor no agrupar que agrupar mal
GROUPING_MERGE_THRESHOLD = float(os.getenv("GROUPING_MERGE_THRESHOLD", "0.80"))
GROUPING_INCIDENT_THRESHOLD = float(os.getenv("GROUPING_INCIDENT_THRESHOLD", "0.85"))
GROUPING_INCIDENT_MIN = int(os.getenv("GROUPING_INCIDENT_MIN", "3"))
GROUPING_INCIDENT_HOURS = int(os.getenv("GROUPING_INCIDENT_HOURS", "2"))
GROUPING_TIMEOUT_SECONDS = float(os.getenv("GROUPING_TIMEOUT_SECONDS", "15"))

# Carpeta de los adjuntos de los comentarios (dentro de data/, que ya persiste el volumen)
ATTACHMENTS_DIR = os.getenv("ATTACHMENTS_DIR", "data/attachments")


# Hacienda: se leen en cada llamada para poder cambiarlas sin reiniciar (y en pruebas)
def hacienda_check_enabled() -> bool:
    return os.getenv("HACIENDA_CHECK", "true").lower() != "false"


def hacienda_strict() -> bool:
    return os.getenv("HACIENDA_STRICT", "false").lower() == "true"

# Vencimiento de sesión: sin uso durante SESSION_IDLE_MINUTES o SESSION_MAX_HOURS desde que se abrió
SESSION_IDLE_MINUTES = int(os.getenv("SESSION_IDLE_MINUTES", "240"))
SESSION_MAX_HOURS = int(os.getenv("SESSION_MAX_HOURS", "12"))
