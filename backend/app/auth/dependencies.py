import uuid
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from app.db import get_db
from app.auth.jwt_handler import decode_token
from app.models.user import User
from app.models.project import ProjectMember, ProjectRole, MemberStatus
from app.models.project import Project

bearer_scheme = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Decodes the JWT access token and loads the corresponding user.
    Runs on every protected request, before any business logic."""
    token = credentials.credentials
    try:
        payload = decode_token(token)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

    user_id = payload.get("sub")
    user = db.get(User, uuid.UUID(user_id))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if payload.get("sv", 0) != user.session_version:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    return user


def require_project_role(allowed_roles: list[ProjectRole]):
    """Factory for a dependency that checks the current user is an accepted
    member of the project (from the path parameter `project_id`) with one
    of the allowed roles. Use it like:

        Depends(require_project_role([ProjectRole.OWNER]))

    on any route whose path includes `{project_id}`.
    """

    def checker(
        project_id: uuid.UUID,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> ProjectMember:
        membership = (
            db.query(ProjectMember)
            .filter(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == current_user.id,
                ProjectMember.status == MemberStatus.ACCEPTED,
            )
            .first()
        )
        if membership is None:
            # Same error whether the project doesn't exist or the user just
            # isn't a member of it — don't leak whether a project exists.
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
        project = db.get(Project, project_id)
        if project is None or project.deleted_at is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

        if membership.role not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")

        return membership

    return checker
