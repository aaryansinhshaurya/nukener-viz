import uuid
from typing import Optional
from pydantic import BaseModel, ConfigDict, model_validator, field_validator
from app.models.document import EntitySource
from app.schemas.review import ReviewOut


class EntityIn(BaseModel):
    text: str
    label: str
    start_char: int
    end_char: int

    @model_validator(mode="after")
    def check_offsets(self):
        if self.start_char < 0 or self.end_char <= self.start_char:
            raise ValueError(
                f"invalid character offsets for entity '{self.text}': "
                f"start_char={self.start_char}, end_char={self.end_char}"
            )
        return self


class SentenceIn(BaseModel):
    document_id: str
    filename: str | None = None
    source: str | None = None
    cleaned_title: str | None = None
    sentence_id: str
    sentence: str
    entities: list[EntityIn] = []

    @field_validator("filename", "source", "cleaned_title", mode="before")
    @classmethod
    def blank_metadata_is_missing(cls, value):
        return value.strip() or None if isinstance(value, str) else value


class UploadSummary(BaseModel):
    documents_created: int
    sentences_created: int
    entities_created: int
    entities_skipped_missing_offsets: int = 0


class EntityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    text: str
    label: str
    start_char: int
    end_char: int
    source: EntitySource
    # The most recent review verdict for this entity, or None if nobody
    # has reviewed it yet. Populated by the documents router, not by a
    # plain ORM attribute — see get_document().
    current_review: Optional[ReviewOut] = None


class SentenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sentence_id_external: str
    text: str
    entities: list[EntityOut]


class DocumentSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    doc_id_external: str
    sentence_count: int
    source: str | None = None


class DocumentDetailOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    doc_id_external: str
    source: str | None = None
    sentences: list[SentenceOut]
