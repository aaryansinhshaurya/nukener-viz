import uuid
from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.db import Base


class DocumentLock(Base):
    """At most one active lock row per document. Acquiring a lock inserts
    a row; releasing it deletes the row — there's no 'locked=false' state
    stored, absence of a row *is* unlocked. Keeping this as its own tiny
    table (rather than a column on Document) means checking lock status
    never requires touching the Document row itself."""

    __tablename__ = "document_locks"
    __table_args__ = (UniqueConstraint("document_id", name="uq_document_lock"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False)
    locked_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    locked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    document = relationship("Document")
    user = relationship("User")
