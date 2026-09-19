import uuid
from dataclasses import dataclass
from typing import Optional
from sqlalchemy.orm import Session
from app.models.document import Document, Sentence, Entity, EntitySource
from app.models.review import ReviewVerdict
from app.services.reviews import get_latest_reviews_map


@dataclass
class MetricsResult:
    tp: int
    fp: int
    total_model_entities: int
    reviewed_model_entities: int
    percent_reviewed: float
    precision: Optional[float]


def _compute_metrics_for_entities(entities: list[Entity], latest_reviews: dict) -> MetricsResult:
    """Evaluate only model predictions that have a TP or FP review."""
    model_entities = [e for e in entities if e.source == EntitySource.MODEL]

    tp = sum(
        1 for e in model_entities
        if (r := latest_reviews.get(e.id)) is not None and r.verdict == ReviewVerdict.TP
    )
    fp = sum(
        1 for e in model_entities
        if (r := latest_reviews.get(e.id)) is not None and r.verdict == ReviewVerdict.FP
    )
    total_model = len(model_entities)
    reviewed_model = tp + fp
    # Nothing to review = fully done, not "0% reviewed" — avoids a
    # freshly-uploaded-but-empty document looking incomplete.
    percent_reviewed = 100.0 if total_model == 0 else round(100.0 * reviewed_model / total_model, 2)

    precision = round(tp / (tp + fp), 4) if (tp + fp) > 0 else None
    return MetricsResult(
        tp=tp,
        fp=fp,
        total_model_entities=total_model,
        reviewed_model_entities=reviewed_model,
        percent_reviewed=percent_reviewed,
        precision=precision,
    )


def get_document_metrics(db: Session, document_id: uuid.UUID) -> MetricsResult:
    entities = (
        db.query(Entity)
        .join(Sentence, Entity.sentence_id == Sentence.id)
        .filter(Sentence.document_id == document_id)
        .all()
    )
    latest_reviews = get_latest_reviews_map(db, [e.id for e in entities])
    return _compute_metrics_for_entities(entities, latest_reviews)


def get_project_metrics(db: Session, project_id: uuid.UUID) -> MetricsResult:
    entities = (
        db.query(Entity)
        .join(Sentence, Entity.sentence_id == Sentence.id)
        .join(Document, Sentence.document_id == Document.id)
        .filter(Document.project_id == project_id)
        .all()
    )
    latest_reviews = get_latest_reviews_map(db, [e.id for e in entities])
    return _compute_metrics_for_entities(entities, latest_reviews)
