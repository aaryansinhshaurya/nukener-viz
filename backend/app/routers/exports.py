"""Portable, deterministic exports of model predictions and their reviews."""

import csv
import io
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session, selectinload

from app.auth.dependencies import require_project_role
from app.db import get_db
from app.models.document import Document, Sentence, Entity, EntitySource
from app.models.project import ProjectMember, ProjectRole
from app.models.user import User
from app.services.reviews import get_latest_reviews_map

router = APIRouter(prefix="/projects/{project_id}", tags=["exports"])
ANY_ROLE = [ProjectRole.OWNER, ProjectRole.REVIEWER, ProjectRole.VIEWER]
FIELDS = ["document_id", "filename", "source", "cleaned_title", "sentence_id", "sentence",
          "entity_text", "entity_label", "start_char", "end_char", "verdict", "reviewer", "review_note"]


@router.get("/export")
def export_reviews(project_id: uuid.UUID, format: str = "csv",
                   _membership: ProjectMember = Depends(require_project_role(ANY_ROLE)),
                   db: Session = Depends(get_db)):
    if format not in {"csv", "json"}:
        raise HTTPException(status_code=422, detail="format must be csv or json")

    documents = (db.query(Document)
                 .options(selectinload(Document.sentences).selectinload(Sentence.entities))
                 .filter(Document.project_id == project_id)
                 .order_by(Document.doc_id_external).all())
    entity_ids = [e.id for d in documents for s in d.sentences for e in s.entities
                  if e.source == EntitySource.MODEL]
    latest = get_latest_reviews_map(db, entity_ids)
    names = {u.id: u.name for u in db.query(User).filter(
        User.id.in_({r.reviewer_id for r in latest.values()})).all()} if latest else {}

    rows = []
    for doc in documents:
        for sentence in sorted(doc.sentences, key=lambda s: s.sentence_id_external):
            for entity in sorted(sentence.entities, key=lambda e: (e.start_char, e.end_char)):
                if entity.source != EntitySource.MODEL:
                    continue
                review = latest.get(entity.id)
                rows.append({
                    "document_id": doc.doc_id_external, "filename": doc.filename,
                    "source": doc.source, "cleaned_title": doc.cleaned_title,
                    "sentence_id": sentence.sentence_id_external, "sentence": sentence.text,
                    "entity_text": entity.text, "entity_label": entity.label,
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
