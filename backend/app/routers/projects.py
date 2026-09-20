import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session
from app.db import get_db
from app.models.user import User
from app.models.project import Project, ProjectMember, ProjectRole, MemberStatus
from app.models.document import Document
from app.models.locking import DocumentLock
from app.models.token import ProjectInvitation
from app.schemas.project import (ProjectCreate, ProjectOut, InviteRequest, MemberOut,
                                 InvitationOut, InvitationLinkOut, InvitationPreviewOut, InvitationAccept)
from app.services.tokens import new_token, token_digest
from app.config import settings
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
    projects = db.query(Project).filter(Project.id.in_(project_ids), Project.deleted_at.is_(None)).all()
    roles = {m.project_id: m.role for m in memberships}
    return [ProjectOut(id=p.id, name=p.name, owner_id=p.owner_id,
                       created_at=p.created_at, deleted_at=p.deleted_at, role=roles[p.id]) for p in projects]


@router.get("/deleted/mine", response_model=list[ProjectOut])
def list_deleted_projects(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    recovery_cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    return (db.query(Project).join(ProjectMember, ProjectMember.project_id == Project.id)
            .filter(ProjectMember.user_id == current_user.id, ProjectMember.role == ProjectRole.OWNER,
                    ProjectMember.status == MemberStatus.ACCEPTED,
                    Project.deleted_at.is_not(None), Project.deleted_at >= recovery_cutoff).all())


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


@router.post("/{project_id}/share-link", response_model=InvitationLinkOut, status_code=status.HTTP_201_CREATED)
def create_share_link(
    project_id: uuid.UUID,
    payload: InviteRequest,
    response: Response,
    membership: ProjectMember = Depends(require_project_role([ProjectRole.OWNER])),
    db: Session = Depends(get_db),
):
    project = db.get(Project, project_id)
    if project is None or project.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Project not found")
    email = payload.email.lower()
    invited_user = db.query(User).filter(User.email == email).first()
    if invited_user and db.query(ProjectMember).filter_by(project_id=project_id, user_id=invited_user.id).first():
        raise HTTPException(status_code=409, detail="This user is already a member")
    existing_invitation = db.query(ProjectInvitation).filter_by(project_id=project_id, email=email).first()
    if existing_invitation and not (existing_invitation.accepted_at or existing_invitation.revoked_at):
        raise HTTPException(status_code=409, detail="An invitation already exists; generate a link for it below")
    raw, digest = new_token()
    invitation = existing_invitation or ProjectInvitation(project_id=project_id, email=email)
    invitation.role = payload.role
    invitation.token_hash = digest
    invitation.invited_by = membership.user_id
    invitation.expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    invitation.accepted_at = None
    invitation.revoked_at = None
    db.add(invitation)
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return _invitation_link_out(invitation, raw)


def _invitation_out(invitation: ProjectInvitation) -> InvitationOut:
    now = datetime.now(timezone.utc)
    status_value = ("accepted" if invitation.accepted_at else "revoked" if invitation.revoked_at
                    else "expired" if invitation.expires_at < now else "pending")
    return InvitationOut(id=invitation.id, email=invitation.email, role=invitation.role,
                         status=status_value, expires_at=invitation.expires_at)


def _invitation_link_out(invitation: ProjectInvitation, raw_token: str) -> InvitationLinkOut:
    return InvitationLinkOut(**_invitation_out(invitation).model_dump(),
                             url=f"{settings.FRONTEND_URL}/?invite={quote(raw_token)}")


@router.get("/invitations/preview", response_model=InvitationPreviewOut)
def preview_invitation(token: str, response: Response, db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    row = db.query(ProjectInvitation).filter_by(token_hash=token_digest(token)).first()
    now = datetime.now(timezone.utc)
    if row is None or row.accepted_at or row.revoked_at or row.expires_at < now:
        raise HTTPException(status_code=400, detail="Invitation is invalid or expired")
    project = db.get(Project, row.project_id)
    if project is None or project.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Project not found")
    return InvitationPreviewOut(project_name=project.name, email=row.email,
                                role=row.role, expires_at=row.expires_at)


@router.get("/{project_id}/invitations", response_model=list[InvitationOut])
def list_invitations(project_id: uuid.UUID,
                     _membership: ProjectMember = Depends(require_project_role([ProjectRole.OWNER])),
                     db: Session = Depends(get_db)):
    return [_invitation_out(row) for row in db.query(ProjectInvitation).filter_by(project_id=project_id).all()]


@router.post("/{project_id}/invitations/{invitation_id}/link", response_model=InvitationLinkOut)
def generate_invitation_link(project_id: uuid.UUID, invitation_id: uuid.UUID, response: Response,
                             _membership: ProjectMember = Depends(require_project_role([ProjectRole.OWNER])),
                             db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if project is None or project.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Project not found")
    row = db.query(ProjectInvitation).filter_by(project_id=project_id, id=invitation_id).with_for_update().first()
    if row is None or row.accepted_at or row.revoked_at:
        raise HTTPException(status_code=404, detail="Pending invitation not found")
    raw, row.token_hash = new_token()
    row.expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return _invitation_link_out(row, raw)


@router.delete("/{project_id}/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_invitation(project_id: uuid.UUID, invitation_id: uuid.UUID,
                      _membership: ProjectMember = Depends(require_project_role([ProjectRole.OWNER])),
                      db: Session = Depends(get_db)):
    row = db.query(ProjectInvitation).filter_by(project_id=project_id, id=invitation_id).with_for_update().first()
    if row is None or row.accepted_at:
        raise HTTPException(status_code=404, detail="Pending invitation not found")
    row.revoked_at = datetime.now(timezone.utc)
    db.commit()


@router.post("/invitations/accept", response_model=MemberOut)
def accept_invitation(payload: InvitationAccept, current_user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    row = db.query(ProjectInvitation).filter_by(token_hash=token_digest(payload.token)).with_for_update().first()
    now = datetime.now(timezone.utc)
    if row is None or row.accepted_at or row.revoked_at or row.expires_at < now:
        raise HTTPException(status_code=400, detail="Invitation is invalid or expired")
    if row.email != current_user.email.lower():
        raise HTTPException(status_code=403, detail="Sign in with the invited email address")
    if db.get(Project, row.project_id).deleted_at:
        raise HTTPException(status_code=404, detail="Project not found")
    membership = ProjectMember(project_id=row.project_id, user_id=current_user.id,
                               role=row.role, status=MemberStatus.ACCEPTED, invited_by=row.invited_by)
    db.add(membership)
    row.accepted_at = now
    db.commit()
    db.refresh(membership)
    return _member_out(membership, current_user)


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
    """Hide a project and allow any owner to restore it for 30 days."""
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    project.deleted_at = datetime.now(timezone.utc)
    db.query(DocumentLock).filter(DocumentLock.document_id.in_(
        db.query(Document.id).filter(Document.project_id == project_id)
    )).delete(synchronize_session=False)
    db.commit()


@router.post("/{project_id}/restore", response_model=ProjectOut)
def restore_project(project_id: uuid.UUID, current_user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    owner = db.query(ProjectMember).filter_by(project_id=project_id, user_id=current_user.id,
                                               role=ProjectRole.OWNER, status=MemberStatus.ACCEPTED).first()
    if project is None or owner is None or project.deleted_at is None:
        raise HTTPException(status_code=404, detail="Deleted project not found")
    if project.deleted_at < datetime.now(timezone.utc) - timedelta(days=30):
        raise HTTPException(status_code=410, detail="The 30-day recovery window has ended")
    project.deleted_at = None
    db.commit()
    db.refresh(project)
    return project
