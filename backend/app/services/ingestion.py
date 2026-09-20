import ast
import csv
import io
import json
import re
from typing import List

from app.schemas.document import SentenceIn, EntityIn


class IngestionError(Exception):
    """Raised when the uploaded file fails validation."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(";\n".join(errors))


def _parse_entities_field(raw: str, row_label: str) -> list[dict]:
    """
    The entities column may contain either:

    JSON:
    [{"text":"John",...}]

    or Python literal:
    [{'text':'John',...}]
    """

    raw = raw.strip()

    if not raw:
        return []

    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        pass

    try:
        return ast.literal_eval(raw)
    except (ValueError, SyntaxError) as exc:
        raise ValueError(f"{row_label}: could not parse entities field ({exc})")


def _prepare_entities(raw_entities: list[dict], sentence: str) -> list[EntityIn]:
    """Locate text-only predictions, keeping explicit offsets when provided."""
    if not isinstance(raw_entities, list):
        raise ValueError("entities must be a list")

    used_spans: list[tuple[int, int]] = []
    for entity in raw_entities:
        if not isinstance(entity, dict):
            raise ValueError("each entity must be an object")
        if entity.get("start_char") not in (None, "", "null") and entity.get("end_char") not in (None, "", "null"):
            used_spans.append((int(entity["start_char"]), int(entity["end_char"])))

    prepared: list[EntityIn] = []
    for entity in raw_entities:
        start = entity.get("start_char")
        end = entity.get("end_char")
        missing_start = start in (None, "", "null")
        missing_end = end in (None, "", "null")
        if missing_start != missing_end:
            raise ValueError(f"entity '{entity.get('text', '')}' has only one character offset")

        if missing_start:
            text = entity.get("text")
            if not isinstance(text, str) or not text:
                raise ValueError("entities without offsets need nonempty text")

            span = None
            for flags in (0, re.IGNORECASE):
                for match in re.finditer(re.escape(text), sentence, flags):
                    candidate = match.span()
                    if all(candidate[1] <= used[0] or candidate[0] >= used[1] for used in used_spans):
                        span = candidate
                        break
                if span is not None:
                    break
            if span is None:
                raise ValueError(f"entity '{text}' was not found in the sentence; provide valid offsets")

            start, end = span
            entity = {**entity, "text": sentence[start:end], "start_char": start, "end_char": end}
            used_spans.append(span)

        prepared.append(EntityIn(**entity))
    return prepared


def parse_csv(file_bytes: bytes) -> tuple[List[SentenceIn], int]:

    text = file_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))

    required_columns = {
        "document_id",
        "sentence_id",
        "sentence",
        "entities",
    }

    missing = required_columns - set(reader.fieldnames or [])

    if missing:
        raise IngestionError(
            [f"CSV is missing required column(s): {', '.join(sorted(missing))}"]
        )

    sentences: list[SentenceIn] = []
    errors: list[str] = []

    for i, row in enumerate(reader, start=2):

        row_label = f"Row {i} (sentence_id={row.get('sentence_id')})"

        try:

            entities_raw = _parse_entities_field(
                row["entities"],
                row_label,
            )

            entities = _prepare_entities(entities_raw, row["sentence"])

            sentence = SentenceIn(
                document_id=row["document_id"].strip(),
                filename=(row.get("filename") or "").strip() or None,
                source=(row.get("source") or "").strip() or None,
                cleaned_title=(row.get("cleaned_title") or "").strip() or None,
                sentence_id=row["sentence_id"].strip(),
                sentence=row["sentence"],
                entities=entities,
            )

            sentences.append(sentence)

        except Exception as exc:
            errors.append(f"{row_label}: {exc}")

    if errors:
        raise IngestionError(errors)

    return sentences, 0


def parse_json(file_bytes: bytes) -> tuple[List[SentenceIn], int]:

    try:
        data = json.loads(file_bytes.decode("utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise IngestionError([f"Invalid JSON file: {exc}"])

    if not isinstance(data, list):
        raise IngestionError(
            ["JSON file must be a list of sentence objects"]
        )

    sentences: list[SentenceIn] = []
    errors: list[str] = []

    for i, item in enumerate(data, start=1):

        sid = item.get("sentence_id") if isinstance(item, dict) else "?"

        row_label = f"Item {i} (sentence_id={sid})"

        try:
            if not isinstance(item, dict):
                raise ValueError("each item must be an object")
            entities = _prepare_entities(item.get("entities", []), item["sentence"])
            sentences.append(SentenceIn(**{**item, "entities": entities}))

        except Exception as exc:
            errors.append(f"{row_label}: {exc}")

    if errors:
        raise IngestionError(errors)

    return sentences, 0


def validate_offsets_against_text(sentences: List[SentenceIn]) -> None:
    """
    Verify that entity.text matches sentence[start_char:end_char]. If the
    only difference is casing, normalize entity.text to the exact span so
    stored rows and API responses use the sentence's real text.
    """

    errors: list[str] = []
    metadata: dict[str, tuple[str | None, str | None, str | None]] = {}
    sentence_ids: set[tuple[str, str]] = set()

    for s in sentences:
        if not s.document_id or not s.sentence_id:
            errors.append("document_id and sentence_id cannot be empty")
        values = (s.filename, s.source, s.cleaned_title)
        previous = metadata.setdefault(s.document_id, values)
        if previous != values:
            errors.append(f"Document {s.document_id}: filename, source, and cleaned_title differ between rows")
        key = (s.document_id, s.sentence_id)
        if key in sentence_ids:
            errors.append(f"Duplicate sentence_id {s.sentence_id} in document {s.document_id}")
        sentence_ids.add(key)

        for e in s.entities:

            actual = s.sentence[e.start_char:e.end_char]

            if actual == e.text:
                continue

            if actual.casefold() == e.text.casefold():
                e.text = actual
                continue

            errors.append(
                f"Sentence {s.sentence_id}: "
                f"entity '{e.text}' with offsets "
                f"[{e.start_char}:{e.end_char}] "
                f"actually points to '{actual}'"
            )

    if errors:
        raise IngestionError(errors)
