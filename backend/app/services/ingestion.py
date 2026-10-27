"""Parse and validate chunk-level entity predictions."""

import csv
import ast
import io
import json
import re
from typing import List

from app.schemas.document import ChunkIn
from app.services.offset_matching import enrich, filter_uploadable


class IngestionError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(";\n".join(errors))


def parse_dataset(file_bytes: bytes, filename: str) -> List[ChunkIn]:
    """Choose the dataset parser from the uploaded filename."""
    name = filename.strip().lower()
    if name.endswith(".json"):
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


def align_chunks(chunks: List[ChunkIn]) -> tuple[List[ChunkIn], int]:
    """Apply the CLI offset matcher, then omit predictions with no valid highlight."""
    rows = [chunk.model_dump() for chunk in chunks]
    for row in rows:
        text = row["chunk_text"]
        occupied = []
        for entity in row["entities"]:
            start, end = entity.get("start_char"), entity.get("end_char")
            if start is None and end is None:
                continue
            if (type(start) is not int or type(end) is not int
                    or not 0 <= start < end <= len(text)
                    or text[start:end] != entity["entity_text"]
                    or any(start < used_end and used_start < end for used_start, used_end in occupied)):
                entity["start_char"] = entity["end_char"] = None
            else:
                occupied.append((start, end))
    try:
        enrich(rows)
        skipped = filter_uploadable(rows)
        return [ChunkIn(**row) for row in rows], skipped
    except (ValueError, TypeError) as exc:
        raise IngestionError([f"Could not align entity offsets: {exc}"]) from exc


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
            if chunk.chunk_text[start:end] != entity.entity_text:
                errors.append(f"Chunk {chunk.chunk_id}: offsets [{start}:{end}] do not match {entity.entity_text!r}")
            if any(start < used_end and used_start < end for used_start, used_end in spans):
                errors.append(f"Chunk {chunk.chunk_id}: entity spans overlap")
            spans.append((start, end))
        for entity in chunk.entities:
            if entity.start_char is not None:
                continue
            found = None
            for flags in (0, re.IGNORECASE):
                for match in re.finditer(re.escape(entity.entity_text), chunk.chunk_text, flags):
                    start, end = match.span()
                    if end - start != len(entity.entity_text):
                        continue
                    if all(end <= used_start or start >= used_end for used_start, used_end in spans):
                        found = (start, end)
                        break
                if found is not None:
                    break
            if found is not None:
                entity.start_char, entity.end_char = found
                entity.entity_text = chunk.chunk_text[found[0]:found[1]]
                spans.append(found)
    if errors:
        raise IngestionError(errors)
