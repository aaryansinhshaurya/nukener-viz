import uuid
import enum
from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, Enum, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.db import Base


class ReviewVerdict(str, enum.Enum):
    TP = "TP"
    FP = "FP"
    FN = "FN"


class Review(Base):
    """Append-only. Every review action (including changing your mind on
    an entity) adds a new row rather than overwriting one — this table
    *is* the audit trail. The "current" verdict for an entity is simply
    its most recent row here, ordered by created_at."""

    __tablename__ = "reviews"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("entities.id"), nullable=False)
    reviewer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    verdict: Mapped[ReviewVerdict] = mapped_column(Enum(ReviewVerdict), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    entity = relationship("Entity", back_populates="reviews")
    reviewer = relationship("User")
