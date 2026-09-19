import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.db import get_db
from app.models.project import ProjectMember, ProjectRole
from app.models.version import ProjectVersion
from app.models.user import User
from app.models.document import Document
from app.models.locking import DocumentLock
from app.services.locking import get_active_lock
from app.schemas.version import VersionCreate, VersionOut, RevertResult, RevertRequest
from app.auth.dependencies import require_project_role, get_current_user
from app.services.versioning import create_version, list_versions, revert_to_version, _serialize_project, compare_snapshots, snapshot_hash

router = APIRouter(prefix="/projects/{project_id}", tags=["versions"])

ANY_ROLE = [ProjectRole.OWNER, ProjectRole.REVIEWER, ProjectRole.VIEWER]
EDIT_ROLES = [ProjectRole.OWNER, ProjectRole.REVIEWER]


def _version_out(db: Session, version: ProjectVersion) -> VersionOut:
    user = db.get(User, version.created_by)
    return VersionOut(
        id=version.id,
        label=version.label,
        created_by=version.created_by,
        created_by_name=user.name if user else "Unknown",
        created_at=version.created_at,
    )


@router.post("/versions", response_model=VersionOut, status_code=status.HTTP_201_CREATED)
def save_version(
    project_id: uuid.UUID,
    payload: VersionCreate,
    _membership: ProjectMember = Depends(require_project_role(EDIT_ROLES)),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The 'Save' button: takes a full snapshot of the project's current
    documents/sentences/entities/reviews right now. Any accepted member
    can save a checkpoint — reverting to one is Owner-only, below."""
    version = create_version(db, project_id, current_user.id, payload.label)
    return _version_out(db, version)


@router.get("/versions", response_model=list[VersionOut])
def get_versions(
    project_id: uuid.UUID,
    _membership: ProjectMember = Depends(require_project_role(ANY_ROLE)),
    db: Session = Depends(get_db),
):
    """Newest first — this is what backs the Version Timeline UI."""
    return [_version_out(db, v) for v in list_versions(db, project_id)]


@router.get("/versions/{version_id}")
def inspect_version(
    project_id: uuid.UUID,
    version_id: uuid.UUID,
    _membership: ProjectMember = Depends(require_project_role(ANY_ROLE)),
    db: Session = Depends(get_db),
):
    version = db.query(ProjectVersion).filter_by(id=version_id, project_id=project_id).first()
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found")
    return {"version": _version_out(db, version), "snapshot": version.snapshot}


@router.post("/versions/{version_id}/revert", response_model=RevertResult)
def revert_version(
    project_id: uuid.UUID,
    version_id: uuid.UUID,
    payload: RevertRequest,
    _membership: ProjectMember = Depends(require_project_role([ProjectRole.OWNER])),
    db: Session = Depends(get_db),
):
    """Owner-only. Replaces the project's current state with the chosen
    snapshot — all metrics, review verdicts, and entity states afterward
    reflect that version, not whatever was there a moment ago."""
    version = (
        db.query(ProjectVersion)
        .filter(ProjectVersion.id == version_id, ProjectVersion.project_id == project_id)
        .first()
    )
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found")
    if snapshot_hash(_serialize_project(db, project_id)) != payload.expected_current_hash:
        raise HTTPException(status_code=409, detail="Project changed since preview; inspect the comparison again")

    locks = (db.query(DocumentLock).join(Document, DocumentLock.document_id == Document.id)
             .filter(Document.project_id == project_id).all())
    if any(get_active_lock(db, lock.document_id) for lock in locks):
        raise HTTPException(status_code=409, detail="Unlock all documents before reverting")
    for lock in locks:
        db.delete(lock)
    db.flush()
    create_version(db, project_id, _membership.user_id, f"Before revert to {version.label}")
    counts = revert_to_version(db, project_id, version)
    return RevertResult(
        version_id=version.id,
        documents_restored=counts.documents,
        sentences_restored=counts.sentences,
        entities_restored=counts.entities,
        reviews_restored=counts.reviews,
    )


@router.get("/versions/{version_id}/compare")
def compare_version(project_id: uuid.UUID, version_id: uuid.UUID, against: str = "current",
                    _membership: ProjectMember = Depends(require_project_role(ANY_ROLE)),
                    db: Session = Depends(get_db)):
    version = db.query(ProjectVersion).filter_by(id=version_id, project_id=project_id).first()
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found")
    if against == "current":
        other = _serialize_project(db, project_id)
    else:
        try:
            other_id = uuid.UUID(against)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="against must be current or a version ID") from exc
        selected = db.query(ProjectVersion).filter_by(id=other_id, project_id=project_id).first()
        if selected is None:
            raise HTTPException(status_code=404, detail="Comparison version not found")
        other = selected.snapshot
    result = compare_snapshots(version.snapshot, other)
    result["current_hash"] = snapshot_hash(other) if against == "current" else None
    return result
