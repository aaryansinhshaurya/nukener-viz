import uuid
from dataclasses import asdict
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_db
from app.models.project import ProjectMember, ProjectRole
from app.models.document import Document
from app.schemas.metrics import MetricsOut
from app.auth.dependencies import require_project_role
from app.services.metrics import get_document_metrics, get_project_metrics

router = APIRouter(prefix="/projects/{project_id}", tags=["metrics"])

ANY_ROLE = [ProjectRole.OWNER, ProjectRole.REVIEWER, ProjectRole.VIEWER]


@router.get("/metrics", response_model=MetricsOut)
def project_metrics(
    project_id: uuid.UUID,
    _membership: ProjectMember = Depends(require_project_role(ANY_ROLE)),
    db: Session = Depends(get_db),
):
    """Overall and per-class precision/coverage across the project."""
    result = get_project_metrics(db, project_id)
    return MetricsOut(**asdict(result))


@router.get("/documents/{doc_id_external}/metrics", response_model=MetricsOut)
def document_metrics(
    project_id: uuid.UUID,
    doc_id_external: str,
    _membership: ProjectMember = Depends(require_project_role(ANY_ROLE)),
    db: Session = Depends(get_db),
):
    document = (
        db.query(Document)
        .filter(Document.project_id == project_id, Document.doc_id_external == doc_id_external)
        .first()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")

    result = get_document_metrics(db, document.id)
    return MetricsOut(**asdict(result))
