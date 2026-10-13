"""Inicia sesión en Microsoft con el buzón de soporte (flujo de código de dispositivo).

Uso: docker compose exec -it app python -m app.ms_login
"""
import sys

from . import config, graph

IT_ERROR_CODES = ("AADSTS65001", "AADSTS90094", "AADSTS53003")
IT_KEYWORDS = ("consent", "approval", "administrador", "conditional access")


def main() -> int:
    cache = graph.load_cache()
    app = graph.build_app(cache)

    flow = app.initiate_device_flow(scopes=graph.SCOPES)
    if "user_code" not in flow:
        print("No se pudo iniciar el flujo de código de dispositivo:")
        print(flow.get("error_description") or flow.get("error") or flow)
        return 1

    print(flow["message"], flush=True)
    result = app.acquire_token_by_device_flow(flow)

    if "error" in result:
        description = result.get("error_description") or result["error"]
        print(f"No se pudo iniciar sesión: {description}")
        lowered = description.lower()
        if any(code in description for code in IT_ERROR_CODES) or any(k in lowered for k in IT_KEYWORDS):
            print("Esto necesita a TI: envíales el mensaje del paso 5.0.")
        return 1

    username = (result.get("id_token_claims") or {}).get("preferred_username", "").lower()
    if username != config.SUPPORT_MAILBOX:
        print(
            f"Iniciaste sesión como {username or 'una cuenta desconocida'}, pero el buzón de soporte "
            f"es {config.SUPPORT_MAILBOX or '(SUPPORT_MAILBOX vacío)'}. No se guardó la sesión."
        )
        return 1

    graph.save_cache(cache)
    print(f"Sesión de Microsoft guardada para {username}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
