import ast
import csv
import io
import json
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


def parse_csv(file_bytes: bytes) -> tuple[List[SentenceIn], int]:

    text = file_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))

    required_columns = {
        "doc_id",
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

    skipped_entities = 0

    for i, row in enumerate(reader, start=2):

        row_label = f"Row {i} (sentence_id={row.get('sentence_id')})"

        try:

            entities_raw = _parse_entities_field(
                row["entities"],
                row_label,
            )

            entities: list[EntityIn] = []

            for e in entities_raw:

                start = e.get("start_char")
                end = e.get("end_char")

                # Ignore entities with missing offsets — but the caller
                # gets the count back and must surface it, rather than
                # this silently vanishing into a server-side print().
                if start in (None, "", "null") or end in (None, "", "null"):
                    skipped_entities += 1
                    continue

                entities.append(EntityIn(**e))

            sentence = SentenceIn(
                doc_id=row["doc_id"].strip(),
                sentence_id=row["sentence_id"].strip(),
                sentence=row["sentence"],
                entities=entities,
            )

            sentences.append(sentence)

        except Exception as exc:
            errors.append(f"{row_label}: {exc}")

    if errors:
        raise IngestionError(errors)

    return sentences, skipped_entities


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

    skipped_entities = 0

    for i, item in enumerate(data, start=1):

        sid = item.get("sentence_id") if isinstance(item, dict) else "?"

        row_label = f"Item {i} (sentence_id={sid})"

        try:

            cleaned_entities = []

            for e in item.get("entities", []):

                start = e.get("start_char")
                end = e.get("end_char")

                if start in (None, "", "null") or end in (None, "", "null"):
                    skipped_entities += 1
                    continue

                cleaned_entities.append(e)

            item["entities"] = cleaned_entities

            sentences.append(SentenceIn(**item))

        except Exception as exc:
            errors.append(f"{row_label}: {exc}")

    if errors:
        raise IngestionError(errors)

    return sentences, skipped_entities


def validate_offsets_against_text(sentences: List[SentenceIn]) -> None:
    """
    Verify that entity.text exactly matches
    sentence[start_char:end_char].
    """

    errors: list[str] = []

    for s in sentences:

        for e in s.entities:

            actual = s.sentence[e.start_char:e.end_char]

            if actual != e.text:

                errors.append(
                    f"Sentence {s.sentence_id}: "
                    f"entity '{e.text}' with offsets "
                    f"[{e.start_char}:{e.end_char}] "
                    f"actually points to '{actual}'"
                )

    if errors:
        raise IngestionError(errors)