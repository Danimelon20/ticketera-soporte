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

# Asistente de la página (modelo local servido por Ollama)
CHATBOT_ENABLED = os.getenv("CHATBOT_ENABLED", "false").lower() == "true"
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://ollama:11434").rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen3:1.7b")


# Hacienda: se leen en cada llamada para poder cambiarlas sin reiniciar (y en pruebas)
def hacienda_check_enabled() -> bool:
    return os.getenv("HACIENDA_CHECK", "true").lower() != "false"


def hacienda_strict() -> bool:
    return os.getenv("HACIENDA_STRICT", "false").lower() == "true"
