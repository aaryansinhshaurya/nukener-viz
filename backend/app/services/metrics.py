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
    fn: int
    total_model_entities: int
    reviewed_model_entities: int
    percent_reviewed: float
    precision: Optional[float]
    recall: Optional[float]
    f1: Optional[float]


def _compute_metrics_for_entities(entities: list[Entity], latest_reviews: dict) -> MetricsResult:
    """Precision/Recall only ever apply to model-predicted entities (a
    human-added entity IS a miss by definition, so it can't be a TP/FP).
    FN comes from human-added entities' automatic FN verdict. This is why
    ingestion tracks `source` on every entity — it's what keeps these two
    entity populations from being mixed together in the math."""
    model_entities = [e for e in entities if e.source == EntitySource.MODEL]
    human_entities = [e for e in entities if e.source == EntitySource.HUMAN]

    tp = sum(
        1 for e in model_entities
        if (r := latest_reviews.get(e.id)) is not None and r.verdict == ReviewVerdict.TP
    )
    fp = sum(
        1 for e in model_entities
        if (r := latest_reviews.get(e.id)) is not None and r.verdict == ReviewVerdict.FP
    )
    fn = sum(
        1 for e in human_entities
        if (r := latest_reviews.get(e.id)) is not None and r.verdict == ReviewVerdict.FN
    )

    total_model = len(model_entities)
    reviewed_model = sum(1 for e in model_entities if e.id in latest_reviews)
    # Nothing to review = fully done, not "0% reviewed" — avoids a
    # freshly-uploaded-but-empty document looking incomplete.
    percent_reviewed = 100.0 if total_model == 0 else round(100.0 * reviewed_model / total_model, 2)

    precision = round(tp / (tp + fp), 4) if (tp + fp) > 0 else None
    recall = round(tp / (tp + fn), 4) if (tp + fn) > 0 else None
    f1 = None
    if precision is not None and recall is not None and (precision + recall) > 0:
        f1 = round(2 * precision * recall / (precision + recall), 4)

    return MetricsResult(
        tp=tp,
        fp=fp,
        fn=fn,
        total_model_entities=total_model,
        reviewed_model_entities=reviewed_model,
        percent_reviewed=percent_reviewed,
        precision=precision,
        recall=recall,
        f1=f1,
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
