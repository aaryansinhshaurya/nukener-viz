import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.db import Base


class ProjectVersion(Base):
    """A full point-in-time snapshot of a project's documents, sentences,
    entities, and review verdicts, stored as one JSON blob. Snapshotting
    the whole tree (instead of diffing) is what keeps revert trivial and
    correct — revert just replaces current rows with what's in the blob,
    there's never a chain of patches that could drift from reality."""

    __tablename__ = "project_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)

    project = relationship("Project")
    creator = relationship("User")
