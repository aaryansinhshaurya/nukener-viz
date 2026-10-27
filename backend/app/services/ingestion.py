"""Parse and validate chunk-level entity predictions."""

import csv
import ast
import io
import json
import re
from typing import List

from app.schemas.document import ChunkIn
from app.services.offset_matching import build, free, tier_canonical, tier_literal


MATH_NOTATION = re.compile(
    r"\\[A-Za-z]+|[\$%°º˚×✕⋅·∙•±∓∼≈≃≤≥<>=≠→∞√∝⟨⟩〈〉₀-₉⁰-⁹−]|"
    r"[α-ωΑ-Ω]|\d\s*[-−]\s*\d"
)


class IngestionError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(";\n".join(errors))


def parse_dataset(file_bytes: bytes, filename: str) -> List[ChunkIn]:
    """Choose the dataset parser from the uploaded filename."""
    name = filename.strip().lower()
    # Windows short filenames may expose a .json file as .JSO.
    if name.endswith((".json", ".jso")):
        return parse_json(file_bytes)
    if name.endswith(".jsonl"):
        return parse_jsonl(file_bytes)
    if name.endswith(".csv"):
        return parse_csv(file_bytes)
    raise IngestionError(["Unsupported file type — upload a .csv, .json, or jsonl file"])


def _parse_entities(raw, row_label: str) -> list[dict]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "[]")
        except json.JSONDecodeError:
            try:
                raw = ast.literal_eval(raw)
            except (ValueError, SyntaxError) as exc:
                raise ValueError(f"{row_label}: invalid entities list: {exc}") from exc
    if not isinstance(raw, list):
        raise ValueError(f"{row_label}: entities must be an array")
    return raw


def parse_csv(file_bytes: bytes) -> List[ChunkIn]:
    try:
        reader = csv.DictReader(io.StringIO(file_bytes.decode("utf-8-sig")))
        required = {"source", "document_id", "cleaned_title", "chunk_id", "chunk_text", "entities", "triplets"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise IngestionError([f"CSV is missing columns: {', '.join(sorted(missing))}"])
        chunks, errors = [], []
        for number, row in enumerate(reader, start=2):
            try:
                if None in row:
                    raise ValueError("more values than CSV columns")
                data = {key: row[key] for key in ("source", "document_id", "cleaned_title", "chunk_id", "chunk_text")}
                data["entities"] = _parse_entities(row["entities"], f"Row {number}")
                chunks.append(ChunkIn(**data))
            except Exception as exc:
                errors.append(f"Row {number}: {exc}")
        if errors:
            raise IngestionError(errors)
        return chunks
    except UnicodeDecodeError as exc:
        raise IngestionError([f"CSV must be UTF-8: {exc}"]) from exc


def parse_json(file_bytes: bytes) -> List[ChunkIn]:
    try:
        data = json.loads(file_bytes.decode("utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise IngestionError([f"Invalid JSON file: {exc}"]) from exc
    except json.JSONDecodeError as exc:
        # Some prediction exports contain one JSON object per line but use .json.
        if len([line for line in file_bytes.splitlines() if line.strip()]) > 1:
            return parse_jsonl(file_bytes)
        raise IngestionError([f"Invalid JSON file: {exc}"]) from exc
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise IngestionError(["JSON file must contain chunk objects"])
    return _parse_json_items(enumerate(data, start=1), "Item")


def parse_jsonl(file_bytes: bytes) -> List[ChunkIn]:
    try:
        lines = file_bytes.decode("utf-8-sig").splitlines()
    except UnicodeDecodeError as exc:
        raise IngestionError([f"JSONL must be UTF-8: {exc}"]) from exc
    items, errors = [], []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            items.append((number, json.loads(line)))
        except json.JSONDecodeError as exc:
            errors.append(f"Line {number}: invalid JSON: {exc.msg}")
    if errors:
        raise IngestionError(errors)
    return _parse_json_items(items, "Line")


def _parse_json_items(items, label: str) -> List[ChunkIn]:
    chunks, errors = [], []
    for number, item in items:
        try:
            if not isinstance(item, dict):
                raise ValueError("expected a chunk object")
            chunks.append(ChunkIn(**{**item, "entities": _parse_entities(item.get("entities", []), f"{label} {number}")}))
        except Exception as exc:
            errors.append(f"{label} {number}: {exc}")
    if errors:
        raise IngestionError(errors)
    return chunks


def _math_equivalent(source_text: str, entity_text: str) -> bool:
    """Allow notation variants without changing the uploaded entity text."""
    return (bool(MATH_NOTATION.search(source_text) or MATH_NOTATION.search(entity_text))
            and build(source_text, False).string() == build(entity_text, False).string())


def align_chunks(chunks: List[ChunkIn]) -> tuple[List[ChunkIn], int]:
    """Keep every uploaded entity; highlight exact or math-equivalent spans."""
    without_offsets = 0
    for chunk in chunks:
        occupied = []
        pending = []
        for entity in chunk.entities:
            start, end = entity.start_char, entity.end_char
            if (type(start) is int and type(end) is int
                    and 0 <= start < end <= len(chunk.chunk_text)
                    and (chunk.chunk_text[start:end] == entity.entity_text
                         or _math_equivalent(chunk.chunk_text[start:end], entity.entity_text))
                    and free(start, end, occupied)):
                occupied.append((start, end))
            else:
                entity.start_char = entity.end_char = None
                pending.append(entity)

        math_ctx = None
        # Prefer longer names so a short name cannot take a longer name's span.
        for entity in sorted(pending, key=lambda item: -len(item.entity_text)):
            match = next((span for span in tier_literal(chunk.chunk_text, entity.entity_text, 0)
                          if free(*span, occupied)), None)
            if match is None:
                if math_ctx is None:
                    math_ctx = {"strict": build(chunk.chunk_text, False)}
                math_ctx["name_strict"] = build(entity.entity_text, False)
                match = next((span for span in tier_canonical(math_ctx, entity.entity_text, False, True)
                              if free(*span, occupied)
                              and _math_equivalent(chunk.chunk_text[span[0]:span[1]], entity.entity_text)), None)
            if match is None:
                without_offsets += 1
                continue
            entity.start_char, entity.end_char = match
            occupied.append(match)
    return chunks, without_offsets


def validate_chunks(chunks: List[ChunkIn]) -> None:
    errors = []
    if not chunks:
        raise IngestionError(["Dataset contains no chunks"])
    metadata = {}
    seen = set()
    for chunk in chunks:
        if not chunk.document_id or not chunk.chunk_id:
            errors.append("document_id and chunk_id cannot be empty")
        previous = metadata.setdefault(chunk.document_id, (chunk.source, chunk.cleaned_title))
        if previous != (chunk.source, chunk.cleaned_title):
            errors.append(f"Document {chunk.document_id}: source and cleaned_title differ between rows")
        key = (chunk.document_id, chunk.chunk_id)
        if key in seen:
            errors.append(f"Duplicate chunk_id {chunk.chunk_id} in document {chunk.document_id}")
        seen.add(key)
        spans = []
        for entity in chunk.entities:
            start, end = entity.start_char, entity.end_char
            if start is None:
                continue
            if (chunk.chunk_text[start:end] != entity.entity_text
                    and not _math_equivalent(chunk.chunk_text[start:end], entity.entity_text)):
                errors.append(f"Chunk {chunk.chunk_id}: offsets [{start}:{end}] do not match {entity.entity_text!r}")
            if any(start < used_end and used_start < end for used_start, used_end in spans):
                errors.append(f"Chunk {chunk.chunk_id}: entity spans overlap")
            spans.append((start, end))
    if errors:
        raise IngestionError(errors)
