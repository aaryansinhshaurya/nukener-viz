import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict
from app.models.review import ReviewVerdict
from app.models.document import EntitySource


class ReviewCreate(BaseModel):
    verdict: ReviewVerdict
    note: Optional[str] = None


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    verdict: ReviewVerdict
    reviewer_id: uuid.UUID
    reviewer_name: str
    note: Optional[str] = None
    created_at: datetime


class MissedEntityCreate(BaseModel):
    """What a reviewer submits after click-and-dragging a span of plain
    text the model missed."""

    text: str
    label: str
    start_char: int
    end_char: int
    note: Optional[str] = None


class MissedEntityOut(BaseModel):
    id: uuid.UUID
    text: str
    label: str
    start_char: int
    end_char: int
    source: EntitySource
    review: ReviewOut
