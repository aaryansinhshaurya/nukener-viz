import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.db import get_db
from app.models.project import ProjectMember, ProjectRole
from app.models.version import ProjectVersion
from app.models.user import User
from app.schemas.version import VersionCreate, VersionOut, RevertResult
from app.auth.dependencies import require_project_role, get_current_user
from app.services.versioning import create_version, list_versions, revert_to_version

router = APIRouter(prefix="/projects/{project_id}", tags=["versions"])

ANY_ROLE = [ProjectRole.OWNER, ProjectRole.REVIEWER, ProjectRole.VIEWER]


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
    _membership: ProjectMember = Depends(require_project_role(ANY_ROLE)),
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


@router.post("/versions/{version_id}/revert", response_model=RevertResult)
def revert_version(
    project_id: uuid.UUID,
    version_id: uuid.UUID,
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

    counts = revert_to_version(db, project_id, version)
    return RevertResult(
        version_id=version.id,
        documents_restored=counts.documents,
        sentences_restored=counts.sentences,
        entities_restored=counts.entities,
        reviews_restored=counts.reviews,
    )
