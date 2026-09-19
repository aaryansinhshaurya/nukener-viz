import uuid
from sqlalchemy.orm import Session
from app.models.review import Review


def get_latest_reviews_map(db: Session, entity_ids: list[uuid.UUID]) -> dict[uuid.UUID, Review]:
    """Reviews are append-only, so the 'current' verdict for an entity is
    just its most recent row. Returns {entity_id: latest Review}, only for
    entities that have at least one review."""
    if not entity_ids:
        return {}

    reviews = (
        db.query(Review)
        .filter(Review.entity_id.in_(entity_ids))
        .order_by(Review.created_at.desc(), Review.id.desc())
        .all()
    )

    latest: dict[uuid.UUID, Review] = {}
    for r in reviews:
        if r.entity_id not in latest:  # first time we see it = most recent, since ordered desc
            latest[r.entity_id] = r
    return latest
