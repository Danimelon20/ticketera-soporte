"""Departamentos y ubicación del personal: una sola fuente para el árbol, la asignación y los permisos.

Adaptado de la rama areas-y-clientes sin las áreas de atención: el ancla es el departamento que atiende
tickets (casilla "Atiende tickets", que no se hereda a los subdepartamentos).
"""
from sqlalchemy.orm import Session

from . import models

SEPARATOR = " / "


# ----- Árbol de departamentos -----

def departments_by_id(db: Session) -> dict[int, models.Department]:
    return {d.id: d for d in db.query(models.Department).all()}


def path_names(dept: models.Department, by_id: dict[int, models.Department]) -> list[str]:
    names, seen = [], set()
    node = dept
    while node is not None and node.id not in seen:
        seen.add(node.id)
        names.append(node.name)
        node = by_id.get(node.parent_id) if node.parent_id else None
    return list(reversed(names))


def path_label(dept: models.Department | None, by_id: dict[int, models.Department]) -> str | None:
    return SEPARATOR.join(path_names(dept, by_id)) if dept is not None else None


def is_active_path(dept: models.Department, by_id: dict[int, models.Department]) -> bool:
    """Activo él y todos sus padres: desactivar un departamento desactiva lo que cuelga de él."""
    seen = set()
    node = dept
    while node is not None and node.id not in seen:
        if not node.is_active:
            return False
        seen.add(node.id)
        node = by_id.get(node.parent_id) if node.parent_id else None
    return True


def descendants(by_id: dict[int, models.Department], root_ids) -> set[int]:
    """Los departamentos indicados y todo lo que cuelga de ellos."""
    children: dict[int, list[int]] = {}
    for d in by_id.values():
        if d.parent_id:
            children.setdefault(d.parent_id, []).append(d.id)
    result, stack = set(), list(root_ids)
    while stack:
        current = stack.pop()
        if current in result:
            continue
        result.add(current)
        stack.extend(children.get(current, ()))
    return result


def serves_tickets(dept: models.Department | None, by_id: dict[int, models.Department]) -> bool:
    """Atiende tickets y está activo (él y sus padres)."""
    return dept is not None and dept.handles_tickets and is_active_path(dept, by_id)
