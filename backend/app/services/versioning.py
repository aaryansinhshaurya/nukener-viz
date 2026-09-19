import uuid
import hashlib
import json
from datetime import datetime, timezone
from sqlalchemy.orm import Session, selectinload
from app.models.document import Document, Sentence, Entity, EntitySource
from app.models.review import Review, ReviewVerdict
from app.models.version import ProjectVersion


def _serialize_project(db: Session, project_id: uuid.UUID) -> dict:
    """Walks the full Document -> Sentence -> Entity -> Review tree for a
    project and serializes it to plain JSON. This blob — not a diff — is
    what's stored per version, so revert is always 'replace current rows
    with this blob' rather than replaying a chain of patches."""
    documents = (
        db.query(Document)
        .options(selectinload(Document.sentences).selectinload(Sentence.entities).selectinload(Entity.reviews))
        .filter(Document.project_id == project_id)
        .all()
    )
    doc_list = []
    for d in sorted(documents, key=lambda item: item.doc_id_external):
        sent_list = []
        for s in sorted(d.sentences, key=lambda item: item.sentence_id_external):
            ent_list = []
            for e in sorted(s.entities, key=lambda item: (item.start_char, item.end_char, str(item.id))):
                reviews = [
                    {
                        "id": str(r.id),
                        "reviewer_id": str(r.reviewer_id),
                        "verdict": r.verdict.value,
                        "note": r.note,
                        "created_at": r.created_at.isoformat(),
                    }
                    for r in sorted(e.reviews, key=lambda item: (item.created_at, str(item.id)))
                ]
                ent_list.append({
                    "id": str(e.id),
                    "text": e.text,
                    "label": e.label,
                    "start_char": e.start_char,
                    "end_char": e.end_char,
                    "source": e.source.value,
                    "reviews": reviews,
                })
            sent_list.append({
                "id": str(s.id),
                "sentence_id_external": s.sentence_id_external,
                "text": s.text,
                "entities": ent_list,
            })
        doc_list.append({
            "id": str(d.id),
            "doc_id_external": d.doc_id_external,
            "filename": d.filename,
            "source": d.source,
            "cleaned_title": d.cleaned_title,
            "sentences": sent_list,
        })
    return {"documents": doc_list}


def create_version(db: Session, project_id: uuid.UUID, user_id: uuid.UUID, label: str | None) -> ProjectVersion:
    snapshot = _serialize_project(db, project_id)
    version = ProjectVersion(
        project_id=project_id,
        label=label or f"Version {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        created_by=user_id,
        snapshot=snapshot,
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    return version


def list_versions(db: Session, project_id: uuid.UUID) -> list[ProjectVersion]:
    return (
        db.query(ProjectVersion)
        .filter(ProjectVersion.project_id == project_id)
        .order_by(ProjectVersion.created_at.desc())
        .all()
    )


def compare_snapshots(before: dict, after: dict) -> dict:
    """Compare current verdicts by stable entity ID; keep the response small."""
    def index(snapshot: dict) -> dict[str, dict]:
        result = {}
        for document in snapshot.get("documents", []):
            for sentence in document.get("sentences", []):
                for entity in sentence.get("entities", []):
                    if entity.get("source") != "model":
                        continue
                    reviews = entity.get("reviews", [])
                    latest = max(reviews, key=lambda r: (r["created_at"], r["id"])) if reviews else None
                    result[entity["id"]] = {
                        "document_id": document["doc_id_external"],
                        "sentence_id": sentence["sentence_id_external"],
                        "text": entity["text"], "label": entity["label"],
                        "verdict": latest["verdict"] if latest else None,
                    }
        return result

    old, new = index(before), index(after)
    changes = []
    for entity_id in sorted(old.keys() | new.keys()):
        previous, current = old.get(entity_id), new.get(entity_id)
        if previous == current:
            continue
        item = current or previous
        changes.append({"entity_id": entity_id, **{k: item[k] for k in ("document_id", "sentence_id", "text", "label")},
                        "before": previous["verdict"] if previous else None,
                        "after": current["verdict"] if current else None,
                        "kind": "added" if previous is None else "removed" if current is None else "changed"})
    return {"before_entities": len(old), "after_entities": len(new),
            "added": sum(c["kind"] == "added" for c in changes),
            "removed": sum(c["kind"] == "removed" for c in changes),
            "changed": sum(c["kind"] == "changed" for c in changes),
            "changes": changes}


def snapshot_hash(snapshot: dict) -> str:
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode("utf-8")).hexdigest()


class RevertCounts:
    def __init__(self):
        self.documents = 0
        self.sentences = 0
        self.entities = 0
        self.reviews = 0


def revert_to_version(db: Session, project_id: uuid.UUID, version: ProjectVersion) -> RevertCounts:
    """Deletes every current Document/Sentence/Entity/Review row for the
    project and rebuilds them exactly from the snapshot, reusing the
    original UUIDs so anything that cached an id (e.g. an open browser
    tab) doesn't silently point at nothing. This is a full replace, not a
    merge — the snapshot becomes the new source of truth."""
    counts = RevertCounts()

    current_docs = db.query(Document).filter(Document.project_id == project_id).all()
    for d in current_docs:
        db.delete(d)  # cascades to sentences -> entities -> reviews
    db.flush()

    for doc_blob in version.snapshot.get("documents", []):
        document = Document(
            id=uuid.UUID(doc_blob["id"]),
            project_id=project_id,
            doc_id_external=doc_blob["doc_id_external"],
            filename=doc_blob.get("filename"),
            source=doc_blob.get("source"),
            cleaned_title=doc_blob.get("cleaned_title"),
        )
        db.add(document)
        db.flush()
        counts.documents += 1

        for sent_blob in doc_blob.get("sentences", []):
            sentence = Sentence(
                id=uuid.UUID(sent_blob["id"]),
                document_id=document.id,
                sentence_id_external=sent_blob["sentence_id_external"],
                text=sent_blob["text"],
            )
            db.add(sentence)
            db.flush()
            counts.sentences += 1

            for ent_blob in sent_blob.get("entities", []):
                entity = Entity(
                    id=uuid.UUID(ent_blob["id"]),
                    sentence_id=sentence.id,
                    text=ent_blob["text"],
                    label=ent_blob["label"],
                    start_char=ent_blob["start_char"],
                    end_char=ent_blob["end_char"],
                    source=EntitySource(ent_blob["source"]),
                )
                db.add(entity)
                db.flush()
                counts.entities += 1

                for rev_blob in ent_blob.get("reviews", []):
                    review = Review(
                        id=uuid.UUID(rev_blob["id"]),
                        entity_id=entity.id,
                        reviewer_id=uuid.UUID(rev_blob["reviewer_id"]),
                        verdict=ReviewVerdict(rev_blob["verdict"]),
                        note=rev_blob.get("note"),
                        created_at=datetime.fromisoformat(rev_blob["created_at"]),
                    )
                    db.add(review)
                    counts.reviews += 1

    db.commit()
    return counts
