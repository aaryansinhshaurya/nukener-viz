import csv
import io
import json
import unittest
import uuid
from types import SimpleNamespace

from fastapi import HTTPException
from app.models.document import EntitySource
from app.routers.documents import _add_missing_entities
from app.services.ingestion import IngestionError, align_chunks, parse_csv, parse_json, parse_dataset, validate_chunks


class UploadOffsetTests(unittest.TestCase):
    def setUp(self):
        self.row = {
            "source": "test",
            "document_id": "D1",
            "cleaned_title": "Test document",
            "chunk_id": "D1_0001",
            "chunk_text": "SPARC is a compact tokamak.",
            "entities": [
                {
                    "entity_text": "SPARC",
                    "entity_class": "Fusion Device",
                    "entity_confidence_score": 0.99,
                },
                {
                    "entity_text": "stellarator",
                    "entity_class": "Fusion Technique",
                    "entity_confidence_score": 0.80,
                },
            ],
            "triplets": [],
        }

    def assert_upload_result(self, chunks):
        aligned, without_offsets = align_chunks(chunks)
        validate_chunks(aligned)

        self.assertEqual(without_offsets, 1)
        self.assertEqual(len(aligned[0].entities), 2)
        entity = aligned[0].entities[0]
        self.assertEqual(entity.entity_text, "SPARC")
        self.assertEqual((entity.start_char, entity.end_char), (0, 5))
        self.assertEqual(entity.entity_confidence_score, 0.99)
        unmatched = aligned[0].entities[1]
        self.assertEqual(unmatched.entity_text, "stellarator")
        self.assertIsNone(unmatched.start_char)
        self.assertIsNone(unmatched.end_char)

    def test_json_without_offsets_is_aligned_before_validation(self):
        chunks = parse_json(json.dumps([self.row]).encode("utf-8"))
        self.assert_upload_result(chunks)

    def test_jsonl_filename_uses_jsonl_parser(self):
        chunks = parse_dataset((json.dumps(self.row) + "\n").encode("utf-8"), "predictions.JSONL")
        self.assert_upload_result(chunks)

    def test_json_filename_accepts_newline_delimited_records(self):
        second = {**self.row, "chunk_id": "D1_0002"}
        payload = "\n".join(json.dumps(item) for item in (self.row, second)).encode("utf-8")
        chunks = parse_dataset(payload, "predictions.json")
        self.assertEqual([chunk.chunk_id for chunk in chunks], ["D1_0001", "D1_0002"])
        validate_chunks(align_chunks(chunks)[0])

    def test_json_filename_accepts_single_chunk_object(self):
        chunks = parse_dataset(json.dumps(self.row).encode("utf-8"), "predictions.json")
        self.assert_upload_result(chunks)

    def test_windows_short_json_extension_is_accepted(self):
        chunks = parse_dataset(json.dumps([self.row]).encode("utf-8"), "_INNOV~3.JSO")
        self.assert_upload_result(chunks)

    def test_uploaded_entity_text_is_never_rewritten_or_inferred_from_triplets(self):
        row = {**self.row, "entities": [
            {"entity_text": "sparc", "entity_class": "Fusion Device"},
        ], "triplets": [{"subject": "Imaginary entity", "predicate": "mentions", "object": "SPARC"}]}
        chunks = parse_json(json.dumps([row]).encode("utf-8"))
        aligned, without_offsets = align_chunks(chunks)
        validate_chunks(aligned)
        self.assertEqual(without_offsets, 1)
        self.assertEqual(len(aligned[0].entities), 1)
        self.assertEqual(aligned[0].entities[0].entity_text, "sparc")
        self.assertIsNone(aligned[0].entities[0].start_char)

    def test_only_entities_array_entries_are_loaded(self):
        row = {**self.row, "chunk_text": "ITER and DIFFER are mentioned here.",
               "entities": [
                   {"entity_text": "ITER", "entity_class": "Fusion Device"},
                   {"entity_text": "DIFFER", "entity_class": "Research Lab"},
               ], "triplets": [{"subject": "Bosman", "predicate": "works at", "object": "DIFFER"}]}
        chunks, without_offsets = align_chunks(parse_json(json.dumps([row]).encode("utf-8")))
        validate_chunks(chunks)
        self.assertEqual(without_offsets, 0)
        self.assertEqual([(entity.entity_text, entity.start_char, entity.end_char)
                          for entity in chunks[0].entities],
                         [("ITER", 0, 4), ("DIFFER", 9, 15)])

    def test_equivalent_math_notation_keeps_uploaded_entity_text(self):
        row = {**self.row, "chunk_text": "The parameter β is measured.",
               "entities": [{"entity_text": "\\beta", "entity_class": "Fusion Metric"}]}
        chunks, without_offsets = align_chunks(parse_json(json.dumps([row]).encode("utf-8")))
        validate_chunks(chunks)
        entity = chunks[0].entities[0]
        self.assertEqual(without_offsets, 0)
        self.assertEqual(entity.entity_text, "\\beta")
        self.assertEqual(chunks[0].chunk_text[entity.start_char:entity.end_char], "β")

    def test_reupload_rejects_old_predictions_not_in_entities_array(self):
        chunks, without_offsets = align_chunks(parse_json(json.dumps([self.row]).encode("utf-8")))
        old_entity = SimpleNamespace(source=EntitySource.MODEL, start_char=0, end_char=5,
                                     text="sparc", label="Fusion Device")
        stored_chunk = SimpleNamespace(chunk_id_external="D1_0001", text=self.row["chunk_text"],
                                       entities=[old_entity])
        document = SimpleNamespace(doc_id_external="D1", chunks=[stored_chunk])
        db = SimpleNamespace(add=lambda entity: self.fail("Should not add entities"), commit=lambda: None)
        with self.assertRaises(HTTPException) as raised:
            _add_missing_entities(db, [document], chunks, without_offsets)
        self.assertEqual(raised.exception.status_code, 409)

    def test_reupload_keeps_duplicate_unlocated_entities(self):
        row = {**self.row, "entities": [
            {"entity_text": "stellarator", "entity_class": "Fusion Technique"},
            {"entity_text": "stellarator", "entity_class": "Fusion Technique"},
        ]}
        chunks, without_offsets = align_chunks(parse_json(json.dumps([row]).encode("utf-8")))
        old_entity = SimpleNamespace(source=EntitySource.MODEL, start_char=None, end_char=None,
                                     text="stellarator", label="Fusion Technique")
        stored_chunk = SimpleNamespace(id=uuid.uuid4(), chunk_id_external="D1_0001", text=row["chunk_text"],
                                       entities=[old_entity])
        document = SimpleNamespace(doc_id_external="D1", chunks=[stored_chunk])
        added = []
        db = SimpleNamespace(add=added.append, commit=lambda: None)
        summary = _add_missing_entities(db, [document], chunks, without_offsets)
        self.assertEqual(summary.entities_created, 1)
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0].text, "stellarator")

    def test_unsupported_extension_names_jsonl(self):
        with self.assertRaises(IngestionError) as raised:
            parse_dataset(b"", "predictions.txt")
        self.assertEqual(raised.exception.errors, [
            "Unsupported file type — upload a .csv, .json, or jsonl file"
        ])

    def test_csv_without_offsets_is_aligned_before_validation(self):
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=self.row.keys())
        writer.writeheader()
        writer.writerow({
            **self.row,
            "entities": json.dumps(self.row["entities"]),
            "triplets": json.dumps(self.row["triplets"]),
        })
        chunks = parse_csv(output.getvalue().encode("utf-8"))
        self.assert_upload_result(chunks)


if __name__ == "__main__":
    unittest.main()
