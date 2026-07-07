import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict


class LockOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_id: uuid.UUID
    doc_id_external: str
    locked_by: uuid.UUID
    locked_by_name: str
    locked_at: datetime
