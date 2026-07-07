import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.db import get_db
from app.models.user import User
from app.models.project import Project, ProjectMember, ProjectRole, MemberStatus
from app.models.document import Document, Sentence, Entity
from app.models.review import Review
from app.models.locking import DocumentLock
from app.models.version import ProjectVersion
from app.schemas.project import ProjectCreate, ProjectOut, InviteRequest, MemberOut
from app.auth.dependencies import get_current_user, require_project_role

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = Project(name=payload.name, owner_id=current_user.id)
    db.add(project)
    db.flush()  # assigns project.id before we reference it below

    owner_membership = ProjectMember(
        project_id=project.id,
        user_id=current_user.id,
        role=ProjectRole.OWNER,
        status=MemberStatus.ACCEPTED,
        invited_by=current_user.id,
    )
    db.add(owner_membership)
    db.commit()
    db.refresh(project)
    return project


@router.get("", response_model=list[ProjectOut])
def list_my_projects(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Only returns projects the current user is an accepted member of —
    this is what makes projects private by default."""
    memberships = (
        db.query(ProjectMember)
        .filter(
            ProjectMember.user_id == current_user.id,
            ProjectMember.status == MemberStatus.ACCEPTED,
        )
        .all()
    )
    project_ids = [m.project_id for m in memberships]
    if not project_ids:
        return []
    return db.query(Project).filter(Project.id.in_(project_ids)).all()


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: uuid.UUID,
    _membership: ProjectMember = Depends(
        require_project_role([ProjectRole.OWNER, ProjectRole.REVIEWER, ProjectRole.VIEWER])
    ),
    db: Session = Depends(get_db),
):
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.post("/{project_id}/invite", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
def invite_member(
    project_id: uuid.UUID,
    payload: InviteRequest,
    _membership: ProjectMember = Depends(require_project_role([ProjectRole.OWNER])),
    db: Session = Depends(get_db),
):
    """V1 simplification: the invited email must already have an account.
    A 'pending invite for a not-yet-registered email' flow is a fast
    follow-up, not required for Module 1 to work end-to-end."""
    invited_user = db.query(User).filter(User.email == payload.email).first()
    if invited_user is None:
        raise HTTPException(
            status_code=404,
            detail="No account with that email exists yet — they need to sign up first",
        )

    existing = (
        db.query(ProjectMember)
        .filter(ProjectMember.project_id == project_id, ProjectMember.user_id == invited_user.id)
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="User is already a member of this project")

    membership = ProjectMember(
        project_id=project_id,
        user_id=invited_user.id,
        role=payload.role,
        # Immediately ACCEPTED — there is no "pending invite, user must
        # accept" flow anywhere in this app (no accept endpoint, no UI
        # for it). Leaving this as PENDING silently locks the invited
        # person out of the project forever, since every authorization
        # check requires status == ACCEPTED. If a real accept-invite step
        # is wanted later, it needs to be built as its own feature with a
        # matching endpoint and UI — not left half-wired like this.
        status=MemberStatus.ACCEPTED,
        invited_by=_membership.user_id,
    )
    db.add(membership)
    db.commit()
    db.refresh(membership)
    return _member_out(membership, invited_user)


def _member_out(membership: ProjectMember, user: User) -> MemberOut:
    return MemberOut(
        id=membership.id,
        user_id=membership.user_id,
        user_name=user.name,
        user_email=user.email,
        role=membership.role,
        status=membership.status,
    )


@router.get("/{project_id}/members", response_model=list[MemberOut])
def list_members(
    project_id: uuid.UUID,
    _membership: ProjectMember = Depends(
        require_project_role([ProjectRole.OWNER, ProjectRole.REVIEWER, ProjectRole.VIEWER])
    ),
    db: Session = Depends(get_db),
):
    """Joins in each member's name/email so the frontend's Team tab can
    show who's who without a separate lookup per row."""
    rows = (
        db.query(ProjectMember, User)
        .join(User, ProjectMember.user_id == User.id)
        .filter(ProjectMember.project_id == project_id)
        .all()
    )
    return [_member_out(m, u) for m, u in rows]


@router.delete("/{project_id}/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(
    project_id: uuid.UUID,
    member_id: uuid.UUID,
    _membership: ProjectMember = Depends(require_project_role([ProjectRole.OWNER])),
    db: Session = Depends(get_db),
):
    """Owner-only. Revokes a member's access to the project entirely.
    The project's actual creator (projects.owner_id) can't be removed
    this way — that would leave the project ownerless — even if their
    membership row happens to also carry the 'owner' role label."""
    target = (
        db.query(ProjectMember)
        .filter(ProjectMember.id == member_id, ProjectMember.project_id == project_id)
        .first()
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Member not found in this project")

    project = db.get(Project, project_id)
    if project is not None and target.user_id == project.owner_id:
        raise HTTPException(
            status_code=400,
            detail="Cannot remove the project's creator/owner.",
        )

    # If this member currently holds a document lock in this project,
    # release it — otherwise removing them could leave a document
    # permanently locked by someone who no longer has any way to unlock
    # it themselves (and an Owner force-unlock shouldn't be the only fix
    # for a case this predictable).
    document_ids = [d.id for d in db.query(Document.id).filter(Document.project_id == project_id).all()]
    if document_ids:
        db.query(DocumentLock).filter(
            DocumentLock.locked_by == target.user_id,
            DocumentLock.document_id.in_(document_ids),
        ).delete(synchronize_session=False)

    db.delete(target)
    db.commit()


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: uuid.UUID,
    _membership: ProjectMember = Depends(require_project_role([ProjectRole.OWNER])),
    db: Session = Depends(get_db),
):
    """Owner-only. Permanently deletes the project and everything under
    it. Every child table is deleted explicitly, in strict dependency
    order, with direct bulk SQL — NOT via multi-level ORM cascade.

    Multi-level cascade (relying on cascade="all, delete-orphan" through
    documents -> sentences -> entities -> reviews) looks correct and
    passes for small test projects, but breaks down on a real project
    with hundreds of entities: SQLAlchemy has to lazily load each
    collection one object at a time to figure out what to delete, and at
    that scale the ordering can fall apart, producing exactly the
    ForeignKeyViolation this replaces (entities deleted before the
    reviews that still reference them). Bulk-deleting each table
    ourselves, in the order the foreign keys actually require, is both
    correct at any scale and far faster than 750+ individual ORM deletes.
    """
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    document_ids = [d.id for d in db.query(Document.id).filter(Document.project_id == project_id).all()]

    if document_ids:
        sentence_ids = [
            s.id for s in db.query(Sentence.id).filter(Sentence.document_id.in_(document_ids)).all()
        ]

        if sentence_ids:
            entity_ids = [
                e.id for e in db.query(Entity.id).filter(Entity.sentence_id.in_(sentence_ids)).all()
            ]

            if entity_ids:
                # Reviews reference entities — must go first.
                db.query(Review).filter(Review.entity_id.in_(entity_ids)).delete(synchronize_session=False)
                db.query(Entity).filter(Entity.id.in_(entity_ids)).delete(synchronize_session=False)

            db.query(Sentence).filter(Sentence.id.in_(sentence_ids)).delete(synchronize_session=False)

        db.query(DocumentLock).filter(DocumentLock.document_id.in_(document_ids)).delete(synchronize_session=False)
        db.query(Document).filter(Document.id.in_(document_ids)).delete(synchronize_session=False)

    db.query(ProjectVersion).filter(ProjectVersion.project_id == project_id).delete(synchronize_session=False)
    db.query(ProjectMember).filter(ProjectMember.project_id == project_id).delete(synchronize_session=False)

    db.delete(project)  # only the project row itself remains — safe now that every child is gone
    db.commit()
