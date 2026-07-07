import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict


class VersionCreate(BaseModel):
    label: Optional[str] = None


class VersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    label: str
    created_by: uuid.UUID
    created_by_name: str
    created_at: datetime


class RevertResult(BaseModel):
    version_id: uuid.UUID
    documents_restored: int
    sentences_restored: int
    entities_restored: int
    reviews_restored: int
