"""Parse and validate chunk-level entity predictions."""

import csv
import ast
import io
import json
from typing import List

from app.schemas.document import ChunkIn


class IngestionError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(";\n".join(errors))


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
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IngestionError([f"Invalid JSON file: {exc}"]) from exc
    if not isinstance(data, list):
        raise IngestionError(["JSON file must be a list of chunk objects"])
    chunks, errors = [], []
    for number, item in enumerate(data, start=1):
        try:
            if not isinstance(item, dict):
                raise ValueError("expected a chunk object")
            chunks.append(ChunkIn(**{**item, "entities": _parse_entities(item.get("entities", []), f"Item {number}")}))
        except Exception as exc:
            errors.append(f"Item {number}: {exc}")
    if errors:
        raise IngestionError(errors)
    return chunks


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
            if chunk.chunk_text[start:end] != entity.entity_text:
                errors.append(f"Chunk {chunk.chunk_id}: offsets [{start}:{end}] do not match {entity.entity_text!r}")
            if any(start < used_end and used_start < end for used_start, used_end in spans):
                errors.append(f"Chunk {chunk.chunk_id}: entity spans overlap")
            spans.append((start, end))
    if errors:
        raise IngestionError(errors)
