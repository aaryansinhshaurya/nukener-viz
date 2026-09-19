import uuid
from datetime import datetime, timedelta, timezone
from fastapi import WebSocket
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.config import settings
from app.models.document import Document
from app.models.locking import DocumentLock
from app.models.user import User


class LockError(Exception):
    pass


class LockConnectionManager:
    """One entry per project_id -> the set of live WebSocket connections
    for users currently viewing that project. Kept in-process memory —
    fine for a single Uvicorn worker (V1's deployment target); running
    more than one API process would need a shared pub/sub backplane
    (e.g. Redis) instead of this dict."""

    def __init__(self):
        self._connections: dict[uuid.UUID, set[WebSocket]] = {}

    async def connect(self, project_id: uuid.UUID, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.setdefault(project_id, set()).add(ws)

    def disconnect(self, project_id: uuid.UUID, ws: WebSocket) -> None:
        conns = self._connections.get(project_id)
        if conns:
            conns.discard(ws)
            if not conns:
                self._connections.pop(project_id, None)

    async def broadcast(self, project_id: uuid.UUID, message: dict) -> None:
        for ws in list(self._connections.get(project_id, [])):
            try:
                await ws.send_json(message)
            except Exception:
                self.disconnect(project_id, ws)


# Module-level singleton — imported by the router and shared across
# every request/connection in this process.
lock_manager = LockConnectionManager()


def _is_expired(lock: DocumentLock) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.LOCK_TIMEOUT_MINUTES)
    locked_at = lock.locked_at
    if locked_at.tzinfo is None:
        locked_at = locked_at.replace(tzinfo=timezone.utc)
    return locked_at < cutoff


def acquire_lock(db: Session, document: Document, user: User) -> DocumentLock:
    existing = db.query(DocumentLock).filter(DocumentLock.document_id == document.id).first()
    if existing is not None:
        if existing.locked_by == user.id:
            existing.locked_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(existing)
            return existing
        if _is_expired(existing):
            db.delete(existing)
            db.flush()
        else:
            raise LockError("This document is already locked by another user")

    lock = DocumentLock(document_id=document.id, locked_by=user.id)
    db.add(lock)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise LockError("This document is already locked by another user") from exc
    db.refresh(lock)
    return lock


def release_lock(db: Session, document: Document, user: User, is_owner: bool) -> None:
    lock = db.query(DocumentLock).filter(DocumentLock.document_id == document.id).first()
    if lock is None:
        return  # already unlocked — releasing twice is harmless
    if lock.locked_by != user.id and not is_owner:
        raise LockError("Only the lock holder or the project owner can unlock this document")
    db.delete(lock)
    db.commit()


def get_active_lock(db: Session, document_id: uuid.UUID) -> DocumentLock | None:
    lock = db.query(DocumentLock).filter(DocumentLock.document_id == document_id).first()
    if lock is not None and _is_expired(lock):
        return None
    return lock
