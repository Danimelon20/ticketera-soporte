import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..database import get_db
from .. import attachments, audit, comments as internal_comments, models
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
    get_visible_ticket(db, row.ticket_id, current_user)
    # El solicitante nunca ve adjuntos de notas internas: 404, ni siquiera sabe que existen
    if current_user.role == "requester" and internal_comments.internal_ids(db, [row.comment_id]):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    path = attachments.path_of(row)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="El archivo ya no está disponible")
    if not preview:
        audit.record(
            db, current_user.id, "attachment_downloaded",
            target_label=f"Ticket #{row.ticket_id}", new=f"Adjunto #{row.id}",
        )
        db.commit()
    return FileResponse(
        path,
        media_type=row.content_type,
        filename=row.original_name,
        content_disposition_type="inline" if attachments.is_inline(row) else "attachment",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"},
    )
