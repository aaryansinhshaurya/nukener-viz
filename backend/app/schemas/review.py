import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict
from app.models.review import ReviewVerdict


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

