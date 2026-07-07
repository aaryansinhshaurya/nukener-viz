import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.db import get_db
from app.models.project import ProjectMember, ProjectRole
from app.models.document import Document, Sentence, Entity, EntitySource
from app.models.review import Review, ReviewVerdict
from app.models.user import User
from app.schemas.review import ReviewCreate, ReviewOut, MissedEntityCreate, MissedEntityOut
from app.auth.dependencies import require_project_role

router = APIRouter(prefix="/projects/{project_id}", tags=["reviews"])

REVIEW_ROLES = [ProjectRole.OWNER, ProjectRole.REVIEWER]
ANY_ROLE = [ProjectRole.OWNER, ProjectRole.REVIEWER, ProjectRole.VIEWER]


def _get_entity_in_project(db: Session, project_id: uuid.UUID, entity_id: uuid.UUID) -> Entity:
    """Confirms the entity actually belongs to a sentence/document under
    this project — not just that the entity_id exists somewhere in the DB.
    Prevents one project's members from touching another project's data
    by guessing/reusing an entity_id."""
    entity = (
        db.query(Entity)
        .join(Sentence, Entity.sentence_id == Sentence.id)
        .join(Document, Sentence.document_id == Document.id)
        .filter(Entity.id == entity_id, Document.project_id == project_id)
        .first()
    )
    if entity is None:
        raise HTTPException(status_code=404, detail="Entity not found in this project")
    return entity


def _to_review_out(db: Session, review: Review) -> ReviewOut:
    reviewer = db.get(User, review.reviewer_id)
    return ReviewOut(
        id=review.id,
        verdict=review.verdict,
        reviewer_id=review.reviewer_id,
        reviewer_name=reviewer.name if reviewer else "Unknown",
        note=review.note,
        created_at=review.created_at,
    )


@router.post("/entities/{entity_id}/review", response_model=ReviewOut, status_code=status.HTTP_201_CREATED)
def submit_review(
    project_id: uuid.UUID,
    entity_id: uuid.UUID,
    payload: ReviewCreate,
    membership: ProjectMember = Depends(require_project_role(REVIEW_ROLES)),
    db: Session = Depends(get_db),
):
    """Mark a model-predicted entity as True Positive or False Positive.
    Submitting again later (e.g. changing your mind) just adds a new row —
    the old verdict is preserved, not overwritten."""
    entity = _get_entity_in_project(db, project_id, entity_id)

    if entity.source == EntitySource.HUMAN:
        raise HTTPException(
            status_code=400,
            detail="This entity was added by a reviewer as a missed detection and is already recorded as FN.",
        )

    if payload.verdict not in (ReviewVerdict.TP, ReviewVerdict.FP):
        raise HTTPException(status_code=400, detail="verdict must be TP or FP for a model-predicted entity")

    review = Review(
        entity_id=entity.id,
        reviewer_id=membership.user_id,
        verdict=payload.verdict,
        note=payload.note,
    )
    db.add(review)
    db.commit()
    db.refresh(review)

    return _to_review_out(db, review)


@router.get("/entities/{entity_id}/reviews", response_model=list[ReviewOut])
def list_entity_reviews(
    project_id: uuid.UUID,
    entity_id: uuid.UUID,
    _membership: ProjectMember = Depends(require_project_role(ANY_ROLE)),
    db: Session = Depends(get_db),
):
    """Full review history for one entity, most recent first — this is
    what backs 'hover to see who reviewed this and when', plus the
    complete audit trail if a verdict was changed."""
    _get_entity_in_project(db, project_id, entity_id)  # 404s if it's not actually in this project

    reviews = (
        db.query(Review)
        .filter(Review.entity_id == entity_id)
        .order_by(Review.created_at.desc())
        .all()
    )
    return [_to_review_out(db, r) for r in reviews]


@router.post(
    "/documents/{doc_id_external}/sentences/{sentence_id_external}/missed-entity",
    response_model=MissedEntityOut,
    status_code=status.HTTP_201_CREATED,
)
def report_missed_entity(
    project_id: uuid.UUID,
    doc_id_external: str,
    sentence_id_external: str,
    payload: MissedEntityCreate,
    membership: ProjectMember = Depends(require_project_role(REVIEW_ROLES)),
    db: Session = Depends(get_db),
):
    """The click-and-drag flow: a reviewer selects a span of plain text
    the model missed entirely, and reports it as a False Negative. This
    creates a brand-new entity (source=human) plus its FN review, in one
    step — a human adding this span already *is* the verdict."""
    sentence = (
        db.query(Sentence)
        .join(Document, Sentence.document_id == Document.id)
        .filter(
            Document.project_id == project_id,
            Document.doc_id_external == doc_id_external,
            Sentence.sentence_id_external == sentence_id_external,
        )
        .first()
    )
    if sentence is None:
        raise HTTPException(status_code=404, detail="Sentence not found in this project/document")

    if payload.start_char < 0 or payload.end_char <= payload.start_char or payload.end_char > len(sentence.text):
        raise HTTPException(status_code=422, detail="start_char/end_char are out of range for this sentence")

    actual_text = sentence.text[payload.start_char : payload.end_char]
    if actual_text != payload.text:
        raise HTTPException(
            status_code=422,
            detail=f"Offsets [{payload.start_char}:{payload.end_char}] point to '{actual_text}', not '{payload.text}'",
        )

    overlap = (
        db.query(Entity)
        .filter(
            Entity.sentence_id == sentence.id,
            Entity.start_char < payload.end_char,
            Entity.end_char > payload.start_char,
        )
        .first()
    )
    if overlap is not None:
        raise HTTPException(
            status_code=400,
            detail=f"This span overlaps an existing entity ('{overlap.text}') — review that one instead of adding a new span",
        )

    entity = Entity(
        sentence_id=sentence.id,
        text=payload.text,
        label=payload.label,
        start_char=payload.start_char,
        end_char=payload.end_char,
        source=EntitySource.HUMAN,
    )
    db.add(entity)
    db.flush()

    review = Review(
        entity_id=entity.id,
        reviewer_id=membership.user_id,
        verdict=ReviewVerdict.FN,
        note=payload.note,
    )
    db.add(review)
    db.commit()
    db.refresh(entity)
    db.refresh(review)

    return MissedEntityOut(
        id=entity.id,
        text=entity.text,
        label=entity.label,
        start_char=entity.start_char,
        end_char=entity.end_char,
        source=entity.source,
        review=_to_review_out(db, review),
    )
