import uuid
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from sqlalchemy.orm import Session, selectinload
from app.db import get_db
from app.models.project import ProjectMember, ProjectRole
from app.models.document import Document, Chunk, Entity, EntitySource
from app.models.user import User
from app.schemas.document import UploadSummary, DocumentSummaryOut, DocumentDetailOut, ChunkOut, EntityOut, ChunkIn
from app.schemas.review import ReviewOut
from app.auth.dependencies import require_project_role
from app.services.ingestion import parse_dataset, align_chunks, validate_chunks, IngestionError
from app.services.reviews import get_latest_reviews_map
from app.config import settings

router = APIRouter(prefix="/projects/{project_id}", tags=["documents"])
ANY_ROLE = [ProjectRole.OWNER, ProjectRole.REVIEWER, ProjectRole.VIEWER]


@router.post("/upload", response_model=UploadSummary, status_code=status.HTTP_201_CREATED)
def upload_dataset(
    project_id: uuid.UUID,
    file: UploadFile = File(...),
    _membership: ProjectMember = Depends(require_project_role([ProjectRole.OWNER])),
    db: Session = Depends(get_db),
):
    """Create a chunk dataset, or add missing predictions from the same one."""
    raw = file.file.read(settings.MAX_UPLOAD_BYTES + 1)
    if len(raw) > settings.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Upload exceeds the configured size limit")
    try:
        chunks = parse_dataset(raw, file.filename or "")
        chunks, entities_skipped = align_chunks(chunks)
        validate_chunks(chunks)
    except IngestionError as exc:
        raise HTTPException(status_code=422, detail={"errors": exc.errors}) from exc

    existing_documents = (
        db.query(Document)
        .options(selectinload(Document.chunks).selectinload(Chunk.entities))
        .filter(Document.project_id == project_id)
        .all()
    )
    if existing_documents:
        return _add_missing_entities(db, existing_documents, chunks, entities_skipped)

    doc_map: dict[str, Document] = {}
    entities_created = 0
    positions: dict[str, int] = {}
    for item in chunks:
        if item.document_id not in doc_map:
            document = Document(
                project_id=project_id, doc_id_external=item.document_id,
                source=item.source, cleaned_title=item.cleaned_title,
            )
            db.add(document)
            db.flush()
            doc_map[item.document_id] = document
        chunk = Chunk(
            document_id=doc_map[item.document_id].id,
            chunk_id_external=item.chunk_id, text=item.chunk_text,
            position=positions.get(item.document_id, 0),
        )
        positions[item.document_id] = chunk.position + 1
        db.add(chunk)
        db.flush()
        for prediction in item.entities:
            db.add(Entity(
                chunk_id=chunk.id, text=prediction.entity_text,
                label=prediction.entity_class,
                start_char=prediction.start_char, end_char=prediction.end_char,
                confidence_score=prediction.entity_confidence_score,
            ))
            entities_created += 1
    db.commit()
    return UploadSummary(
        documents_created=len(doc_map), chunks_created=len(chunks),
        entities_created=entities_created, entities_skipped=entities_skipped,
    )


def _add_missing_entities(db: Session, documents: list[Document], chunks: list[ChunkIn], entities_skipped: int) -> UploadSummary:
    """Preserve reviews on repeat upload of an identical chunk dataset."""
    existing = {
        (document.doc_id_external, chunk.chunk_id_external): chunk
        for document in documents for chunk in document.chunks
    }
    uploaded = {(chunk.document_id, chunk.chunk_id) for chunk in chunks}
    if uploaded != set(existing):
        raise HTTPException(status_code=409, detail="This project has different documents or chunks. Create a new project for a different dataset.")
    for item in chunks:
        stored = existing[(item.document_id, item.chunk_id)]
        if stored.text != item.chunk_text:
            raise HTTPException(status_code=409, detail=f"Chunk {item.chunk_id} differs from the existing dataset. No entities were added.")
    added = 0
    for item in chunks:
        stored = existing[(item.document_id, item.chunk_id)]
        known = {(e.start_char, e.end_char, e.text, e.label) for e in stored.entities if e.source == EntitySource.MODEL}
        for prediction in item.entities:
            key = (prediction.start_char, prediction.end_char, prediction.entity_text, prediction.entity_class)
            if key in known:
                continue
            db.add(Entity(
                chunk_id=stored.id, text=prediction.entity_text,
                label=prediction.entity_class,
                start_char=prediction.start_char, end_char=prediction.end_char,
                confidence_score=prediction.entity_confidence_score,
            ))
            known.add(key)
            added += 1
    db.commit()
    return UploadSummary(
        documents_created=0, chunks_created=0, entities_created=added,
        entities_skipped=entities_skipped,
    )


@router.get("/documents", response_model=list[DocumentSummaryOut])
def list_documents(
    project_id: uuid.UUID,
    _membership: ProjectMember = Depends(require_project_role(ANY_ROLE)),
    db: Session = Depends(get_db),
):
    documents = db.query(Document).filter(Document.project_id == project_id).all()
    return [
        DocumentSummaryOut(
            id=d.id, doc_id_external=d.doc_id_external,
            chunk_count=db.query(Chunk).filter(Chunk.document_id == d.id).count(),
            source=d.source, cleaned_title=d.cleaned_title,
        ) for d in documents
    ]


@router.get("/documents/{doc_id_external}", response_model=DocumentDetailOut)
def get_document(
    project_id: uuid.UUID,
    doc_id_external: str,
    _membership: ProjectMember = Depends(require_project_role(ANY_ROLE)),
    db: Session = Depends(get_db),
):
    document = (
        db.query(Document)
        .options(selectinload(Document.chunks).selectinload(Chunk.entities))
        .filter(Document.project_id == project_id, Document.doc_id_external == doc_id_external)
        .first()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    all_entity_ids = [e.id for chunk in document.chunks for e in chunk.entities]
    latest_reviews = get_latest_reviews_map(db, all_entity_ids)
    reviewer_ids = {r.reviewer_id for r in latest_reviews.values()}
    reviewer_names = {u.id: u.name for u in db.query(User).filter(User.id.in_(reviewer_ids)).all()} if reviewer_ids else {}
    chunks_out = []
    for chunk in sorted(document.chunks, key=lambda item: (item.position, item.chunk_id_external)):
        entities_out = []
        for entity in chunk.entities:
            review = latest_reviews.get(entity.id)
            review_out = ReviewOut(
                id=review.id, verdict=review.verdict, reviewer_id=review.reviewer_id,
                reviewer_name=reviewer_names.get(review.reviewer_id, "Unknown"),
                note=review.note, created_at=review.created_at,
            ) if review is not None else None
            entities_out.append(EntityOut(
                id=entity.id, text=entity.text, label=entity.label,
                start_char=entity.start_char, end_char=entity.end_char,
                confidence_score=entity.confidence_score, source=entity.source,
                current_review=review_out,
            ))
        chunks_out.append(ChunkOut(
            id=chunk.id, chunk_id_external=chunk.chunk_id_external,
            text=chunk.text, entities=entities_out,
        ))
    return DocumentDetailOut(
        id=document.id, doc_id_external=document.doc_id_external,
        source=document.source, cleaned_title=document.cleaned_title,
        chunks=chunks_out,
    )
