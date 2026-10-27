import uuid
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator
from app.models.document import EntitySource
from app.schemas.review import ReviewOut


class EntityIn(BaseModel):
    entity_text: str
    entity_class: str
    entity_confidence_score: float | None = None
    start_char: int | None = None
    end_char: int | None = None

    @model_validator(mode="after")
    def check_offsets(self):
        if (self.start_char is None) != (self.end_char is None):
            raise ValueError(f"supply both offsets or neither for entity {self.entity_text!r}")
        if self.start_char is not None and (self.start_char < 0 or self.end_char <= self.start_char):
            raise ValueError(f"invalid offsets for entity {self.entity_text!r}")
        if not self.entity_text or not self.entity_class:
            raise ValueError("entity_text and entity_class cannot be empty")
        return self


class ChunkIn(BaseModel):
    document_id: str
    source: str | None = None
    cleaned_title: str | None = None
    chunk_id: str
    chunk_text: str
    entities: list[EntityIn] = Field(default_factory=list)

    @field_validator("source", "cleaned_title", mode="before")
    @classmethod
    def blank_metadata_is_missing(cls, value):
        return value.strip() or None if isinstance(value, str) else value


class UploadSummary(BaseModel):
    documents_created: int
    chunks_created: int
    entities_created: int
    entities_relocated: int = 0
    entities_without_offsets: int = 0
    entities_skipped: int = 0


class EntityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    text: str
    label: str
    start_char: int | None
    end_char: int | None
    confidence_score: float | None = None
    source: EntitySource
    current_review: Optional[ReviewOut] = None


class ChunkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    chunk_id_external: str
    text: str
    entities: list[EntityOut]


class DocumentSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    doc_id_external: str
    chunk_count: int
    source: str | None = None
    cleaned_title: str | None = None


class DocumentDetailOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    doc_id_external: str
    source: str | None = None
    cleaned_title: str | None = None
    chunks: list[ChunkOut]
