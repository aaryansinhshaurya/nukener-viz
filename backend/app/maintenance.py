"""Run daily with ``python -m app.maintenance`` to purge expired projects."""

from datetime import datetime, timedelta, timezone

from app.db import SessionLocal
from app.models.document import Document, Sentence, Entity
from app.models.locking import DocumentLock
from app.models.project import Project, ProjectMember
from app.models.review import Review
from app.models.token import ProjectInvitation, PasswordReset
from app.models.version import ProjectVersion


def purge_expired_projects() -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    removed = 0
    with SessionLocal() as db:
        projects = db.query(Project).filter(Project.deleted_at.is_not(None), Project.deleted_at < cutoff).all()
        for project in projects:
            try:
                document_ids = db.query(Document.id).filter(Document.project_id == project.id)
                sentence_ids = db.query(Sentence.id).filter(Sentence.document_id.in_(document_ids))
                entity_ids = db.query(Entity.id).filter(Entity.sentence_id.in_(sentence_ids))
                db.query(Review).filter(Review.entity_id.in_(entity_ids)).delete(synchronize_session=False)
                db.query(Entity).filter(Entity.sentence_id.in_(sentence_ids)).delete(synchronize_session=False)
                db.query(Sentence).filter(Sentence.document_id.in_(document_ids)).delete(synchronize_session=False)
                db.query(DocumentLock).filter(DocumentLock.document_id.in_(document_ids)).delete(synchronize_session=False)
                db.query(Document).filter(Document.project_id == project.id).delete(synchronize_session=False)
                db.query(ProjectVersion).filter(ProjectVersion.project_id == project.id).delete(synchronize_session=False)
                db.query(ProjectInvitation).filter(ProjectInvitation.project_id == project.id).delete(synchronize_session=False)
                db.query(ProjectMember).filter(ProjectMember.project_id == project.id).delete(synchronize_session=False)
                db.delete(project)
                db.commit()
                removed += 1
            except Exception:
                db.rollback()
                raise
        db.query(PasswordReset).filter(PasswordReset.expires_at < cutoff).delete(synchronize_session=False)
        db.commit()
    return removed


if __name__ == "__main__":
    print(f"Purged {purge_expired_projects()} expired projects")
