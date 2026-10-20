"""Comentarios internos: solo los ve el personal, nunca el solicitante."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models


def add_internal(db: Session, ticket_id: int, user_id: int, content: str) -> models.Comment:
    """Agrega un comentario interno. No avisa a los seguidores ni hace commit."""
    comment = models.Comment(ticket_id=ticket_id, user_id=user_id, content=content)
    db.add(comment)
    db.flush()
    db.add(models.InternalComment(comment_id=comment.id))
    return comment


def internal_ids(db: Session, comment_ids: list[int]) -> set[int]:
    if not comment_ids:
        return set()
    rows = (
        db.query(models.InternalComment.comment_id)
        .filter(models.InternalComment.comment_id.in_(comment_ids))
        .all()
    )
    return {row[0] for row in rows}


def not_internal(query):
    """Quita los comentarios internos de una consulta de Comment."""
    return query.filter(~models.Comment.id.in_(select(models.InternalComment.comment_id)))
