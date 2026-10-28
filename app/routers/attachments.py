import os

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from .. import attachments, audit, comments as internal_comments, models, ticket_access
from ..security import get_current_user
from .tickets import get_visible_ticket

router = APIRouter(prefix="/api", tags=["attachments"])

NOT_FOUND = "El adjunto no existe"


@router.get("/attachments/{attachment_id}")
def download_attachment(
    attachment_id: int, preview: bool = False, db: Session = Depends(get_db), current_user=Depends(get_current_user)
):
    """Único camino a un adjunto: mismas reglas que el ticket. preview=1 (miniaturas) no queda en el registro."""
    row = db.get(models.Attachment, attachment_id)
    if row is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    if current_user.role == "admin":
        # Solo durante la hora siguiente a "Ver conversación" en ese ticket
        if not ticket_access.admin_files_open(db, current_user, row.ticket_id):
            raise HTTPException(status_code=403, detail="Use Ver conversación en este ticket para abrir sus adjuntos")
    else:
        get_visible_ticket(db, row.ticket_id, current_user)
    # El solicitante nunca ve adjuntos de notas internas: 404, ni siquiera sabe que existen
    if current_user.role == "requester" and internal_comments.internal_ids(db, [row.comment_id]):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    if not os.path.isfile(attachments.path_of(row)):
        raise HTTPException(status_code=404, detail="El archivo ya no está disponible")
    if not preview:
        audit.record(
            db, current_user.id, "attachment_downloaded",
            target_label=f"Ticket #{row.ticket_id}", new=f"Adjunto #{row.id}",
        )
        db.commit()
    return attachments.file_response(row)
