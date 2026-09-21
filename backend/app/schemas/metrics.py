from typing import Optional
from pydantic import BaseModel


class ClassMetricsOut(BaseModel):
    label: str
    tp: int
    fp: int
    total_model_entities: int
    reviewed_model_entities: int
    percent_reviewed: float
    precision: Optional[float] = None


class MetricsOut(BaseModel):
    tp: int
    fp: int
    total_model_entities: int
    reviewed_model_entities: int
    percent_reviewed: float
    precision: Optional[float] = None
    class_metrics: list[ClassMetricsOut]
