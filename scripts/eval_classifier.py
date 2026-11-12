"""Evaluación de la clasificación de correos contra el Ollama real, con las fichas actuales. No es parte de pytest
(pytest simula la IA).

Uso (entorno de pruebas encendido):
    docker compose -f docker-compose.pruebas.yml exec app python -m scripts.eval_classifier

Solo lee: no crea borradores, tickets ni historial y no guarda los vectores de los ejemplos. Solo corre con
APP_ENV=pruebas. El departamento esperado se busca por su nombre corto (sin etiquetas como «[DEMO]»), por ejemplo
«Redes» y «Soporte N1». Los correos no traen datos personales.
"""
import sys
import time

from app import classifier, config
from app.database import SessionLocal

NONE = classifier.NONE_CHOICE
CASES = (
    ("Internet", "", "Buenos días, desde las 8 no tengo conexión a internet. Ya reinicié la computadora y sigue "
                     "igual. Los compañeros de al lado tampoco tienen.", "Redes"),
    ("Impresora", "Impresora", "La impresora del segundo piso saca las hojas en blanco", "Soporte N1"),
    ("Instalar programa", "", "Necesito que me instalen un programa de diseño", "Soporte N1"),
    ("VPN", "VPN", "Desde la casa no puedo entrar a la VPN, me dice tiempo de espera agotado", "Redes"),
    ("Cuenta bloqueada", "Cuenta", "Me bloquearon la cuenta por intentar la clave varias veces", "Soporte N1"),
    ("Menú del almuerzo", "Saludos", "Les comparto el menú del almuerzo del viernes", NONE),
)


def _table(headers: tuple, rows: list[tuple]) -> None:
    widths = [max(len(h), *(len(str(r[i])) for r in rows)) for i, h in enumerate(headers)]
    print(" | ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print("-+-".join("-" * w for w in widths))
    for row in rows:
        print(" | ".join(str(v).ljust(w) for v, w in zip(row, widths)))


def run() -> int:
    if config.APP_ENV != "pruebas":
        print("Solo corre en el entorno de pruebas (APP_ENV=pruebas). No se evaluó nada.")
        return 1
    db = SessionLocal()
    try:
        people = classifier.participants(db)
        if not people:
            print(classifier.NO_DEPARTMENTS + ". No se evaluó nada.")
            return 1
        names = dict(zip((p.id for p in people), classifier.labels(people)))
        missing = sorted({expected for *_, expected in CASES if expected != NONE and expected not in names.values()})
        if missing:
            print(f"Faltan departamentos que atiendan tickets con estos nombres: {', '.join(missing)}. "
                  f"Hay: {', '.join(names.values())}. No se evaluó nada.")
            return 1
        rows, details, correct = [], [], 0
        current_threshold = classifier.threshold(db)
        for name, subject, body, expected in CASES:
            start = time.monotonic()
            result = classifier.classify(db, subject, body, cache=False)
            seconds = time.monotonic() - start
            chosen = names.get(result.llm_department_id, NONE)
            ok = chosen == expected
            correct += ok
            margin = "—" if result.margin is None else f"{result.margin:.3f}"
            route = "directo" if result.passes else "Por clasificar"
            rows.append((name, expected, chosen, margin, route, f"{seconds:.1f} s", "OK" if ok else "FALLA"))
            details.append((name, result.reason, result.notes))
        db.rollback()
    finally:
        db.close()
    _table(("Correo", "Esperado", "Elección de la IA", "Margen", "Iría", "Tiempo", "OK/FALLA"), rows)
    print()
    for name, reason, notes in details:
        print(f"{name}: {reason or 'sin motivo'}")
        for note in notes:
            print(f"    nota: {note}")
    print()
    print(f"{correct}/{len(CASES)} aciertos (umbral actual {current_threshold})")
    return 0 if correct == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(run())
