import uuid
import enum
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, ForeignKey, Integer, UniqueConstraint, Text, Enum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.db import Base


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("project_id", "doc_id_external", name="uq_project_doc"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False)
    # The doc_id from the uploaded CSV/JSON (e.g. "D085") — kept separate
    # from our internal UUID primary key.
    doc_id_external: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    project = relationship("Project", back_populates="documents")
    sentences = relationship("Sentence", back_populates="document", cascade="all, delete-orphan")


class Sentence(Base):
    __tablename__ = "sentences"
    __table_args__ = (UniqueConstraint("document_id", "sentence_id_external", name="uq_document_sentence"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False)
    # The sentence_id from the uploaded file (e.g. "D085-S052")
    sentence_id_external: Mapped[str] = mapped_column(String(255), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    document = relationship("Document", back_populates="sentences")
    entities = relationship("Entity", back_populates="sentence", cascade="all, delete-orphan")


class EntitySource(str, enum.Enum):
    MODEL = "model"   # came from the uploaded CSV/JSON prediction
    HUMAN = "human"   # added by a reviewer marking a missed (FN) entity


class Entity(Base):
    __tablename__ = "entities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sentence_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sentences.id"), nullable=False)
    text: Mapped[str] = mapped_column(String(500), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    # Character offsets into the parent sentence's text — required at
    # ingestion, not computed later. This is what makes highlighting in
    # the UI unambiguous even when a word repeats in a sentence.
    start_char: Mapped[int] = mapped_column(Integer, nullable=False)
    end_char: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[EntitySource] = mapped_column(Enum(EntitySource), default=EntitySource.MODEL, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    sentence = relationship("Sentence", back_populates="entities")
    reviews = relationship("Review", back_populates="entity", cascade="all, delete-orphan")
