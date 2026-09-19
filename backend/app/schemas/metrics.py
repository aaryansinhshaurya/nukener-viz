from typing import Optional
from pydantic import BaseModel


class MetricsOut(BaseModel):
    tp: int
    fp: int
    total_model_entities: int
    reviewed_model_entities: int
    percent_reviewed: float
    precision: Optional[float] = None
