import uuid
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from sqlalchemy.orm import Session, selectinload
from app.db import get_db
from app.models.project import ProjectMember, ProjectRole
from app.models.document import Document, Sentence, Entity, EntitySource
from app.models.user import User
from app.schemas.document import UploadSummary, DocumentSummaryOut, DocumentDetailOut, SentenceOut, EntityOut, SentenceIn
from app.schemas.review import ReviewOut
from app.auth.dependencies import require_project_role
from app.services.ingestion import parse_csv, parse_json, validate_offsets_against_text, IngestionError
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
    """Create a dataset, or add missing predictions from the same dataset."""

    raw = file.file.read(settings.MAX_UPLOAD_BYTES + 1)
    if len(raw) > settings.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Upload exceeds the configured size limit")
    filename = (file.filename or "").lower()

    try:
        if filename.endswith(".json"):
            sentences, skipped_entities = parse_json(raw)
        elif filename.endswith(".csv"):
            sentences, skipped_entities = parse_csv(raw)
        else:
            raise IngestionError(["Unsupported file type — upload a .csv or .json file"])

        validate_offsets_against_text(sentences)
    except IngestionError as exc:
        raise HTTPException(status_code=422, detail={"errors": exc.errors})

    existing_documents = (
        db.query(Document)
        .options(selectinload(Document.sentences).selectinload(Sentence.entities))
        .filter(Document.project_id == project_id)
        .all()
    )
    if existing_documents:
        return _add_missing_entities(db, existing_documents, sentences)

    doc_map: dict[str, Document] = {}
    sentences_created = 0
    entities_created = 0

    for s in sentences:
        if s.document_id not in doc_map:
            document = Document(project_id=project_id, doc_id_external=s.document_id,
                                filename=s.filename, source=s.source, cleaned_title=s.cleaned_title)
            db.add(document)
            db.flush()  # assigns document.id for the sentence FK below
            doc_map[s.document_id] = document

        document = doc_map[s.document_id]
        sentence_row = Sentence(
            document_id=document.id,
            sentence_id_external=s.sentence_id,
            text=s.sentence,
        )
        db.add(sentence_row)
        db.flush()
        sentences_created += 1

        for e in s.entities:
            db.add(
                Entity(
                    sentence_id=sentence_row.id,
                    text=e.text,
                    label=e.label,
                    start_char=e.start_char,
                    end_char=e.end_char,
                )
            )
            entities_created += 1

    db.commit()

    return UploadSummary(
        documents_created=len(doc_map),
        sentences_created=sentences_created,
        entities_created=entities_created,
        entities_skipped_missing_offsets=skipped_entities,
    )


def _add_missing_entities(db: Session, documents: list[Document], sentences: list[SentenceIn]) -> UploadSummary:
    """Repair an old import while preserving its reviews and document IDs."""
    existing = {
        (document.doc_id_external, sentence.sentence_id_external): sentence
        for document in documents for sentence in document.sentences
    }
    uploaded = {(sentence.document_id, sentence.sentence_id) for sentence in sentences}
    if uploaded != set(existing):
        raise HTTPException(
            status_code=409,
            detail="This project has a different set of documents or sentences. Re-upload the original dataset to add missing entities.",
        )

    for sentence in sentences:
        stored = existing[(sentence.document_id, sentence.sentence_id)]
        if stored.text != sentence.sentence:
            raise HTTPException(
                status_code=409,
                detail=f"Sentence {sentence.sentence_id} differs from the existing dataset. No entities were added.",
            )

    added = 0
    for sentence in sentences:
        stored = existing[(sentence.document_id, sentence.sentence_id)]
        known = {
            (entity.start_char, entity.end_char, entity.label)
            for entity in stored.entities if entity.source == EntitySource.MODEL
        }
        for entity in sentence.entities:
            key = (entity.start_char, entity.end_char, entity.label)
            if key in known:
                continue
            db.add(Entity(
                sentence_id=stored.id, text=entity.text, label=entity.label,
                start_char=entity.start_char, end_char=entity.end_char,
            ))
            known.add(key)
            added += 1
    db.commit()
    return UploadSummary(documents_created=0, sentences_created=0, entities_created=added)


@router.get("/documents", response_model=list[DocumentSummaryOut])
def list_documents(
    project_id: uuid.UUID,
    _membership: ProjectMember = Depends(require_project_role(ANY_ROLE)),
    db: Session = Depends(get_db),
):
    documents = db.query(Document).filter(Document.project_id == project_id).all()
    result = []
    for d in documents:
        count = db.query(Sentence).filter(Sentence.document_id == d.id).count()
        result.append(DocumentSummaryOut(id=d.id, doc_id_external=d.doc_id_external,
                                         sentence_count=count, source=d.source))
    return result


@router.get("/documents/{doc_id_external}", response_model=DocumentDetailOut)
def get_document(
    project_id: uuid.UUID,
    doc_id_external: str,
    _membership: ProjectMember = Depends(require_project_role(ANY_ROLE)),
    db: Session = Depends(get_db),
):
    document = (
        db.query(Document)
        .options(selectinload(Document.sentences).selectinload(Sentence.entities))
        .filter(Document.project_id == project_id, Document.doc_id_external == doc_id_external)
        .first()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")

    # Attach each entity's current (latest) review, if any, so the
    # frontend can show TP/FP status and who reviewed it without a
    # second round-trip per entity.
    all_entity_ids = [e.id for s in document.sentences for e in s.entities]
    latest_reviews = get_latest_reviews_map(db, all_entity_ids)

    reviewer_ids = {r.reviewer_id for r in latest_reviews.values()}
    reviewer_names = {u.id: u.name for u in db.query(User).filter(User.id.in_(reviewer_ids)).all()} if reviewer_ids else {}

    sentences_out = []
    for s in document.sentences:
        entities_out = []
        for e in s.entities:
            review = latest_reviews.get(e.id)
            review_out = None
            if review is not None:
                review_out = ReviewOut(
                    id=review.id,
                    verdict=review.verdict,
                    reviewer_id=review.reviewer_id,
                    reviewer_name=reviewer_names.get(review.reviewer_id, "Unknown"),
                    note=review.note,
                    created_at=review.created_at,
                )
            entities_out.append(
                EntityOut(
                    id=e.id,
                    text=e.text,
                    label=e.label,
                    start_char=e.start_char,
                    end_char=e.end_char,
                    source=e.source,
                    current_review=review_out,
                )
            )
        sentences_out.append(
            SentenceOut(
                id=s.id,
                sentence_id_external=s.sentence_id_external,
                text=s.text,
                entities=entities_out,
            )
        )

    return DocumentDetailOut(id=document.id, doc_id_external=document.doc_id_external,
                             source=document.source, sentences=sentences_out)
