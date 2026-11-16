"""scripts.eval_classifier: cómo puntúa (la IA real no se usa en pytest: classify se simula)."""
from app import classifier, config
from scripts import eval_classifier


def _fake(monkeypatch, outcome):
    """outcome(nombre del caso) → (departamento elegido por la IA o None, iría directo)."""
    people = [classifier.Participant(1, "Redes", "Internet", ["Sin red"], ""),
              classifier.Participant(2, "Soporte N1", "Equipos", ["Monitor"], "")]
    by_body = {body: name for name, _subject, body, _expected in eval_classifier.CASES}

    def _classify(db, subject, body, cache=True, lock=True):
        chosen, passes = outcome(by_body[body])
        result = classifier.Classification(threshold=0.05, min_similarity=0.4, llm_department_id=chosen,
                                           passes=passes, margin=0.1, top_similarity=0.5 if passes else 0.3)
        return result

    monkeypatch.setattr(config, "APP_ENV", "pruebas")
    monkeypatch.setattr(classifier, "participants", lambda db: people)
    monkeypatch.setattr(classifier, "classify", _classify)


def _expected_id(name):
    expected = next(e for n, _s, _b, e in eval_classifier.CASES if n == name)
    return {"Redes": 1, "Soporte N1": 2}.get(expected)


def test_lunch_question_is_a_none_case():
    cases = {name: (body, expected) for name, _subject, body, expected in eval_classifier.CASES}
    assert cases["Menú (pregunta)"] == ("¿Cuál es el menú del almuerzo del viernes?", classifier.NONE_CHOICE)


def test_all_right_shows_the_top_similarity_column(monkeypatch, capsys):
    _fake(monkeypatch, lambda name: (_expected_id(name), _expected_id(name) is not None))
    assert eval_classifier.run() == 0
    out = capsys.readouterr().out
    assert "Similitud 1.º" in out and "0.500" in out and "8/8 aciertos" in out and "similitud mínima 0.4" in out


def test_none_case_that_would_go_direct_is_a_failure(monkeypatch, capsys):
    """La IA eligió un departamento para el menú y el correo iría directo: FALLA."""
    def outcome(name):
        if name == "Menú (pregunta)":
            return 2, True
        return _expected_id(name), _expected_id(name) is not None

    _fake(monkeypatch, outcome)
    assert eval_classifier.run() == 1
    out = capsys.readouterr().out
    line = next(l for l in out.splitlines() if l.startswith("Menú (pregunta)"))
    assert "FALLA" in line and "directo" in line and "7/8 aciertos" in out
