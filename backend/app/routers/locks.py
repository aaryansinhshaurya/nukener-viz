import uuid
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from sqlalchemy.orm import Session
from app.db import get_db, SessionLocal
from app.models.project import Project, ProjectMember, ProjectRole, MemberStatus
from app.models.document import Document
from app.models.locking import DocumentLock
from app.models.user import User
from app.schemas.locking import LockOut
from app.auth.dependencies import require_project_role, get_current_user
from app.auth.jwt_handler import decode_token
from app.services.locking import lock_manager, acquire_lock, release_lock, get_active_lock, LockError

router = APIRouter(prefix="/projects/{project_id}", tags=["locking"])

ANY_ROLE = [ProjectRole.OWNER, ProjectRole.REVIEWER, ProjectRole.VIEWER]
EDIT_ROLES = [ProjectRole.OWNER, ProjectRole.REVIEWER]


def _get_document(db: Session, project_id: uuid.UUID, doc_id_external: str) -> Document:
    document = (
        db.query(Document)
        .filter(Document.project_id == project_id, Document.doc_id_external == doc_id_external)
        .first()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


def _lock_out(db: Session, document: Document, lock: DocumentLock) -> LockOut:
    user = db.get(User, lock.locked_by)
    return LockOut(
        document_id=lock.document_id,
        doc_id_external=document.doc_id_external,
        locked_by=lock.locked_by,
        locked_by_name=user.name if user else "Unknown",
        locked_at=lock.locked_at,
    )


@router.post("/documents/{doc_id_external}/lock", response_model=LockOut, status_code=status.HTTP_201_CREATED)
async def lock_document(
    project_id: uuid.UUID,
    doc_id_external: str,
    _membership: ProjectMember = Depends(require_project_role(EDIT_ROLES)),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Locks a document for exclusive editing by the current user and
    broadcasts the new state over WebSocket to everyone else watching
    this project — this is what makes the other tabs grey the doc out
    instantly instead of only finding out on their next request."""
    document = _get_document(db, project_id, doc_id_external)
    try:
        lock = acquire_lock(db, document, current_user)
    except LockError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

    payload = _lock_out(db, document, lock)
    await lock_manager.broadcast(project_id, {
        "type": "lock",
        "doc_id_external": doc_id_external,
        "locked_by": str(current_user.id),
        "locked_by_name": current_user.name,
    })
    return payload


@router.post("/documents/{doc_id_external}/unlock", status_code=status.HTTP_204_NO_CONTENT)
async def unlock_document(
    project_id: uuid.UUID,
    doc_id_external: str,
    membership: ProjectMember = Depends(require_project_role(EDIT_ROLES)),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    document = _get_document(db, project_id, doc_id_external)
    try:
        release_lock(db, document, current_user, is_owner=membership.role == ProjectRole.OWNER)
    except LockError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))

    await lock_manager.broadcast(project_id, {
        "type": "unlock",
        "doc_id_external": doc_id_external,
    })


@router.get("/locks", response_model=list[LockOut])
def list_locks(
    project_id: uuid.UUID,
    _membership: ProjectMember = Depends(require_project_role(ANY_ROLE)),
    db: Session = Depends(get_db),
):
    """Full current lock state for the project — fetched once when a
    project is opened so the UI can grey out already-locked documents
    before the first WebSocket event even arrives."""
    rows = (
        db.query(Document, DocumentLock)
        .join(DocumentLock, DocumentLock.document_id == Document.id)
        .filter(Document.project_id == project_id)
        .all()
    )
    out = []
    for document, lock in rows:
        if get_active_lock(db, document.id) is None:
            continue  # expired — treat as unlocked without needing a write here
        out.append(_lock_out(db, document, lock))
    return out


@router.websocket("/ws")
async def project_lock_socket(websocket: WebSocket, project_id: uuid.UUID, token: str):
    """One persistent connection per active user per project, used only
    for lock/unlock broadcasts — all real data (sentences, entities,
    reviews) still goes through the plain REST endpoints above.
    Browsers can't set an Authorization header on a WebSocket handshake,
    so the access token travels as a query param instead: wss://.../ws?token=...
    """
    db = SessionLocal()
    try:
        try:
            claims = decode_token(token)
            if claims.get("type") != "access":
                raise ValueError("not an access token")
            user_id = uuid.UUID(claims["sub"])
            user = db.get(User, user_id)
            if user is None or claims.get("sv", 0) != user.session_version:
                raise ValueError("session expired")
        except (ValueError, KeyError):
            await websocket.close(code=4401)
            return

        membership = (
            db.query(ProjectMember)
            .filter(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == user_id,
                ProjectMember.status == MemberStatus.ACCEPTED,
            )
            .first()
        )
        project = db.get(Project, project_id)
        if membership is None or project is None or project.deleted_at is not None:
            await websocket.close(code=4403)
            return
    finally:
        db.close()

    await lock_manager.connect(project_id, websocket)
    try:
        while True:
            # Clients don't send anything meaningful here — this just
            # keeps the connection open until they disconnect.
            await websocket.receive_text()
    except WebSocketDisconnect:
        lock_manager.disconnect(project_id, websocket)
