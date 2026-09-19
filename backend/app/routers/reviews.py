import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.db import get_db
from app.models.project import ProjectMember, ProjectRole
from app.models.document import Document, Sentence, Entity, EntitySource
from app.models.review import Review, ReviewVerdict
from app.models.user import User
from app.schemas.review import ReviewCreate, ReviewOut
from app.auth.dependencies import require_project_role
from app.services.locking import get_active_lock, lock_manager

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
async def submit_review(
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
    sentence = db.get(Sentence, entity.sentence_id)
    lock = get_active_lock(db, sentence.document_id)
    if lock is None or lock.locked_by != membership.user_id:
        raise HTTPException(status_code=409, detail="Lock this document before reviewing it")

    if entity.source != EntitySource.MODEL:
        raise HTTPException(
            status_code=400,
            detail="Only model predictions can receive TP or FP reviews.",
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
    await lock_manager.broadcast(project_id, {"type": "review", "document_id": str(sentence.document_id)})
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
        .order_by(Review.created_at.desc(), Review.id.desc())
        .all()
    )
    return [_to_review_out(db, r) for r in reviews]


