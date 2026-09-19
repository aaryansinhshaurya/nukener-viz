import uuid
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from sqlalchemy.orm import Session, selectinload
from app.db import get_db
from app.models.project import ProjectMember, ProjectRole
from app.models.document import Document, Sentence, Entity
from app.models.user import User
from app.schemas.document import UploadSummary, DocumentSummaryOut, DocumentDetailOut, SentenceOut, EntityOut
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
    """Owner-only. Parses a CSV or JSON file into Document -> Sentence ->
    Entity rows. Supported once per project — versioning (save/revert)
    checkpoints the review state on top of this data, it doesn't replace
    the underlying dataset with a new upload."""
    existing = db.query(Document).filter(Document.project_id == project_id).first()
    if existing:
        raise HTTPException(
            status_code=400,
            detail=(
                "This project already has a dataset uploaded. Re-uploading a "
                "different CSV/JSON onto an existing project isn't supported — "
                "start a new project instead."
            ),
        )

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
