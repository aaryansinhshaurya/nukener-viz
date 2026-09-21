import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional
from sqlalchemy.orm import Session
from app.models.document import Document, Sentence, Entity, EntitySource
from app.models.review import ReviewVerdict
from app.services.reviews import get_latest_reviews_map


@dataclass
class ClassMetricsResult:
    label: str
    tp: int
    fp: int
    total_model_entities: int
    reviewed_model_entities: int
    percent_reviewed: float
    precision: Optional[float]


@dataclass
class MetricsResult:
    tp: int
    fp: int
    total_model_entities: int
    reviewed_model_entities: int
    percent_reviewed: float
    precision: Optional[float]
    class_metrics: list[ClassMetricsResult]


def _compute_counts(entities: list[Entity], latest_reviews: dict) -> dict:
    """Return precision and coverage counts for one collection of predictions."""
    tp = sum(
        1 for entity in entities
        if (review := latest_reviews.get(entity.id)) is not None
        and review.verdict == ReviewVerdict.TP
    )
    fp = sum(
        1 for entity in entities
        if (review := latest_reviews.get(entity.id)) is not None
        and review.verdict == ReviewVerdict.FP
    )
    total_model = len(entities)
    reviewed_model = tp + fp

    return {
        "tp": tp,
        "fp": fp,
        "total_model_entities": total_model,
        "reviewed_model_entities": reviewed_model,
        "percent_reviewed": (
            100.0 if total_model == 0
            else round(100.0 * reviewed_model / total_model, 2)
        ),
        "precision": round(tp / reviewed_model, 4) if reviewed_model > 0 else None,
    }


def _compute_metrics_for_entities(entities: list[Entity], latest_reviews: dict) -> MetricsResult:
    """Compute overall and per-class metrics for model predictions."""
    model_entities = [e for e in entities if e.source == EntitySource.MODEL]
    entities_by_label = defaultdict(list)
    for entity in model_entities:
        entities_by_label[entity.label].append(entity)

    class_metrics = [
        ClassMetricsResult(label=label, **_compute_counts(class_entities, latest_reviews))
        for label, class_entities in sorted(
            entities_by_label.items(), key=lambda item: item[0].casefold()
        )
    ]

    return MetricsResult(
        **_compute_counts(model_entities, latest_reviews),
        class_metrics=class_metrics,
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
