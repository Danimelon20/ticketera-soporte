"""Mide al asistente contra el modelo real (Ollama del entorno de pruebas). Solo lee: no cambia nada.

Uso (con el demo cargado):
    docker compose -f docker-compose.pruebas.yml exec app python -m scripts.assistant_eval
    docker compose -f docker-compose.pruebas.yml exec app python -m scripts.assistant_eval --solo-eleccion

Por cada pregunta muestra la consulta esperada y la que eligió la IA y, si es de datos, si la redacción pasó la
verificación o cayó al texto del servidor (y con qué número). Al final: aciertos de elección y caídas al texto
del servidor, agrupadas por motivo. Las preguntas de consultas que todavía no existen se omiten.
Meta antes de la fase 5: al menos 9 de cada 10 elecciones correctas.
"""
import argparse
import sys
import time
from collections import Counter

from app import assistant_queries as aq, chatbot, models
from app.database import SessionLocal

A, C, T = "admin", "coordinator", "technician"
ALL = (A, C, T)
# «ninguna» ya no existe: lo que no es de datos va a la ayuda general
HELP = NONE = chatbot.HELP

# (roles, pregunta, consulta esperada). Para un rol que no puede usar la consulta, se espera «ninguna».
QUESTIONS = [
    ((A, C), "¿Cuántos tickets hay abiertos?", "open_tickets"),
    ((A, C), "¿Cuántos tickets abiertos tenemos?", "open_tickets"),
    ((A, C), "Resume el estado del equipo", "open_tickets"),
    ((A, C), "¿Cuántos tickets están sin asignar?", "open_tickets"),
    ((A, C), "¿Cuántos tickets nuevos hay?", "open_tickets"),
    ((A, C), "Dame el total de tickets pendientes", "open_tickets"),
    ((A, C), "¿Cómo estamos de tickets?", "open_tickets"),
    ((A, C), "¿Cuántos tickets están en proceso?", "open_tickets"),
    ((T,), "¿Cuántos tickets tengo?", "open_tickets"),
    ((T,), "¿Qué tickets tengo asignados?", "open_tickets"),
    ((T,), "Resume mis tickets abiertos", "open_tickets"),
    ((T,), "¿Tengo tickets pendientes?", "open_tickets"),
    (ALL, "Tickets abiertos por departamento", "open_by_department"),
    (ALL, "¿Qué departamento tiene más tickets?", "open_by_department"),
    (ALL, "¿Cuántos tickets abiertos hay en cada departamento?", "open_by_department"),
    ((A, C), "Reparto de tickets por área", "open_by_department"),
    ((A,), "¿Cómo invito a un técnico?", HELP),
    (ALL, "Pasos para diagnosticar una red lenta", HELP),
    (ALL, "¿Cómo cambio mi contraseña?", HELP),
    ((T, C), "¿Qué significa En proceso?", HELP),
    ((C,), "¿Cómo reasigno un ticket?", HELP),
    ((T,), "¿Qué hago si una impresora no imprime?", HELP),
    (ALL, "¿Cuál es la capital de Francia?", NONE),
    ((A, T), "Cuéntame un chiste", NONE),
    ((A,), "¿Cuántas personas trabajan en la empresa?", NONE),
    ((C,), "¿Cuánto cuesta una licencia de Office?", NONE),
    # Fases siguientes: se omiten mientras la consulta no exista
    (ALL, "¿Qué tickets están vencidos?", "overdue"),
    ((A, C), "¿Hay tickets atrasados?", "overdue"),
    (ALL, "¿Cuáles están por vencer?", "due_soon"),
    ((A, C), "¿Qué tickets vencen pronto?", "due_soon"),
    ((A, C), "¿Quién tiene más carga?", "technician_load"),
    ((A, C), "Carga de trabajo por técnico", "technician_load"),
    ((A, C), "¿Quién está de vacaciones?", "staff_away"),
    ((A, C), "¿Quién no está hoy?", "staff_away"),
    ((A,), "¿Cuántos correos hay por clasificar?", "drafts_unclassified"),
    ((A,), "¿Hay correos sin clasificar?", "drafts_unclassified"),
    ((A,), "¿Qué tan precisa fue la IA esta semana?", "ai_precision_week"),
    ((A,), "Precisión de la clasificación", "ai_precision_week"),
    (ALL, "¿Cuántos tickets se resolvieron esta semana?", "resolved_period"),
    ((A, C), "Resueltos el mes pasado", "resolved_period"),
]


def _user(db, role):
    query = db.query(models.User).filter(models.User.role == role, models.User.is_active.is_(True))
    if role == C:
        query = query.join(models.DepartmentCoordinator, models.DepartmentCoordinator.user_id == models.User.id)
    return query.order_by(models.User.id).first()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.assistant_eval", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--solo-eleccion", action="store_true", help="No pide la redacción (más rápido)")
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        users = {role: _user(db, role) for role in ALL}
        missing = [role for role, user in users.items() if user is None]
        if missing:
            print(f"Faltan cuentas activas con rol: {', '.join(missing)}. Cargue el demo (scripts.demo_data).")
            return 1
        hits = total = skipped = checked = fallbacks = 0
        reasons: Counter = Counter()
        misses = []
        for roles, question, expected in QUESTIONS:
            if expected not in (HELP, NONE) and expected not in aq.BY_ID:
                skipped += len(roles)
                continue
            for role in roles:
                user = users[role]
                want = expected if expected in (HELP, NONE) or aq.resolve(user, expected) else NONE
                started = time.monotonic()
                chosen, period = aq.choose(user, question)
                total += 1
                ok = chosen == want
                hits += ok
                if not ok:
                    misses.append((role, question, want, chosen))
                line = f"{'OK ' if ok else 'MAL'} [{role}] {question} → {chosen}" + ("" if ok else f" (esperada: {want})")
                query = aq.resolve(user, chosen)
                if query is not None and not args.solo_eleccion:
                    result = aq.run(db, user, query, period)
                    checked += 1
                    try:
                        reply = chatbot.write_answer(result.facts, question)
                        passed, reason = aq.verify(reply, result)
                    except chatbot.ChatbotUnavailable:
                        passed, reason = False, "la IA no redactó"
                    if passed:
                        line += " · verificación: pasó"
                    else:
                        fallbacks += 1
                        reasons[reason] += 1
                        line += f" · texto del servidor: {reason}"
                        line += f"\n      redacción descartada: {reply!r}" if reason != "la IA no redactó" else ""
                print(f"{line}  ({time.monotonic() - started:.1f} s)")
        print()
        print(f"Elección: {hits}/{total} correctas ({hits * 100 // max(total, 1)} %). Meta: 90 %.")
        if skipped:
            print(f"Omitidas: {skipped} (consultas de fases siguientes).")
        if not args.solo_eleccion:
            print(f"Caídas al texto del servidor: {fallbacks}/{checked}.")
            for reason, count in reasons.most_common():
                print(f"  {count} × {reason}")
        if misses:
            print("Elecciones equivocadas:")
            for role, question, want, chosen in misses:
                print(f"  [{role}] {question}: eligió {chosen}, esperada {want}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
