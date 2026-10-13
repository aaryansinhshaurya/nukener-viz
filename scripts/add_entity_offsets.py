"""Add chunk-relative character offsets to entity predictions in a CSV.

Usage:
    python scripts/add_entity_offsets.py predictions.csv predictions-with-offsets.csv

Offsets are zero-based and end_char is exclusive, matching Python slicing.
Repeated identical mentions are assigned to their next unused occurrence from
left to right. When a prediction cannot be located, the script fails without
writing a partial output file so the input can be corrected before upload.
"""

import argparse
import ast
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path


def locate_entities(entities: list, chunk_text: str, row_number: int) -> None:
    if not isinstance(entities, list):
        raise ValueError(f"row {row_number}: entities must be a JSON array")

    occupied: list[tuple[int, int]] = []
    for index, entity in enumerate(entities, start=1):
        if not isinstance(entity, dict):
            raise ValueError(f"row {row_number}, entity {index}: expected an object")
        name = entity.get("entity_text")
        if not isinstance(name, str) or not name:
            raise ValueError(f"row {row_number}, entity {index}: entity_text must be nonempty")
        start, end = entity.get("start_char"), entity.get("end_char")
        if (start is None) != (end is None):
            raise ValueError(f"row {row_number}, entity {index}: supply both offsets or neither")
        if start is not None:
            if (not isinstance(start, int) or isinstance(start, bool)
                    or not isinstance(end, int) or isinstance(end, bool)
                    or start < 0 or end <= start or chunk_text[start:end] != name):
                raise ValueError(f"row {row_number}, entity {index}: invalid existing offsets for {name!r}")
            if any(start < used_end and used_start < end for used_start, used_end in occupied):
                raise ValueError(f"row {row_number}, entity {index}: existing offsets overlap")
            occupied.append((start, end))

    for index, entity in enumerate(entities, start=1):
        if entity.get("start_char") is not None:
            continue
        name = entity["entity_text"]
        match = None
        for flags in (0, re.IGNORECASE):
            for candidate in re.finditer(re.escape(name), chunk_text, flags):
                start, end = candidate.span()
                if end - start != len(name):
                    continue
                if all(end <= used_start or start >= used_end for used_start, used_end in occupied):
                    match = (start, end)
                    break
            if match:
                break
        if match is None:
            raise ValueError(
                f"row {row_number}, entity {index}: {name!r} has no unused occurrence in chunk_text"
            )
        entity["start_char"], entity["end_char"] = match
        entity["entity_text"] = chunk_text[match[0]:match[1]]
        occupied.append(match)

    predicted = Counter(entity["entity_text"].casefold() for entity in entities)
    for name, count in predicted.items():
        occurrences = len(list(re.finditer(re.escape(name), chunk_text, re.IGNORECASE)))
        if occurrences > count:
            print(
                f"Warning: row {row_number}: {name!r} appears {occurrences} times "
                f"but has {count} prediction(s); assigned the earliest unused occurrence(s).",
                file=sys.stderr,
            )


def enrich(input_path: Path, output_path: Path) -> tuple[int, int]:
    if input_path.resolve() == output_path.resolve():
        raise ValueError("input and output paths must differ")
    with input_path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        columns = reader.fieldnames or []
        required = {"document_id", "chunk_id", "chunk_text", "entities"}
        missing = required - set(columns)
        if missing:
            raise ValueError(f"CSV is missing columns: {', '.join(sorted(missing))}")
        rows = []
        entity_count = 0
        for row_number, row in enumerate(reader, start=2):
            if None in row:
                raise ValueError(f"row {row_number}: more values than CSV columns")
            raw_entities = row["entities"] or "[]"
            try:
                entities = json.loads(raw_entities)
            except json.JSONDecodeError:
                try:
                    entities = ast.literal_eval(raw_entities)
                except (ValueError, SyntaxError) as exc:
                    raise ValueError(f"row {row_number}: invalid entities list: {exc}") from exc
            locate_entities(entities, row["chunk_text"] or "", row_number)
            entity_count += len(entities)
            row["entities"] = json.dumps(entities, ensure_ascii=False)
            rows.append(row)

    with output_path.open("w", encoding="utf-8-sig", newline="") as destination:
        writer = csv.DictWriter(destination, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows), entity_count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_csv", type=Path)
    parser.add_argument("output_csv", type=Path)
    args = parser.parse_args()
    try:
        rows, entities = enrich(args.input_csv, args.output_csv)
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote {rows} chunks and {entities} entities to {args.output_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
