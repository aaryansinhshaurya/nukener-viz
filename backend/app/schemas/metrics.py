from typing import Optional
from pydantic import BaseModel


class MetricsOut(BaseModel):
    tp: int
    fp: int
    fn: int
    total_model_entities: int
    reviewed_model_entities: int
    percent_reviewed: float
    precision: Optional[float] = None
    recall: Optional[float] = None
    f1: Optional[float] = None
