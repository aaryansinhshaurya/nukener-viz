"""Portable, deterministic exports of model predictions and their reviews."""

import csv
import io
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session, selectinload

from app.auth.dependencies import require_project_role
from app.db import get_db
from app.models.document import Document, Chunk, Entity, EntitySource
from app.models.project import ProjectMember, ProjectRole
from app.models.user import User
from app.services.reviews import get_latest_reviews_map

router = APIRouter(prefix="/projects/{project_id}", tags=["exports"])
ANY_ROLE = [ProjectRole.OWNER, ProjectRole.REVIEWER, ProjectRole.VIEWER]
FIELDS = ["document_id", "source", "cleaned_title", "chunk_id", "chunk_text",
          "entity_text", "entity_class", "entity_confidence_score", "start_char", "end_char",
          "verdict", "reviewer", "review_note"]


@router.get("/export")
def export_reviews(project_id: uuid.UUID, format: str = "csv",
                   _membership: ProjectMember = Depends(require_project_role(ANY_ROLE)),
                   db: Session = Depends(get_db)):
    if format not in {"csv", "json"}:
        raise HTTPException(status_code=422, detail="format must be csv or json")

    documents = (db.query(Document)
                 .options(selectinload(Document.chunks).selectinload(Chunk.entities))
                 .filter(Document.project_id == project_id)
                 .order_by(Document.doc_id_external).all())
    entity_ids = [e.id for d in documents for s in d.chunks for e in s.entities
                  if e.source == EntitySource.MODEL]
    latest = get_latest_reviews_map(db, entity_ids)
    names = {u.id: u.name for u in db.query(User).filter(
        User.id.in_({r.reviewer_id for r in latest.values()})).all()} if latest else {}

    rows = []
    for doc in documents:
        for chunk in sorted(doc.chunks, key=lambda s: (s.position, s.chunk_id_external)):
            for entity in sorted(chunk.entities, key=lambda e: (e.start_char, e.end_char)):
                if entity.source != EntitySource.MODEL:
                    continue
                review = latest.get(entity.id)
                rows.append({
                    "document_id": doc.doc_id_external,
                    "source": doc.source, "cleaned_title": doc.cleaned_title,
                    "chunk_id": chunk.chunk_id_external, "chunk_text": chunk.text,
                    "entity_text": entity.text, "entity_class": entity.label,
                    "entity_confidence_score": entity.confidence_score,
                    "start_char": entity.start_char, "end_char": entity.end_char,
                    "verdict": review.verdict.value if review else None,
                    "reviewer": names.get(review.reviewer_id) if review else None,
                    "review_note": review.note if review else None,
                })

    if format == "json":
        content = json.dumps(rows, ensure_ascii=False, indent=2)
        media_type = "application/json"
    else:
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
        content = buffer.getvalue()
        media_type = "text/csv"
    return Response(content=content, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="ner-reviews-{project_id}.{format}"'})
