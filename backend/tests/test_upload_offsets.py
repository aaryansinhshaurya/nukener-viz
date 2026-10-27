import csv
import io
import json
import unittest

from app.services.ingestion import align_chunks, parse_csv, parse_json, parse_dataset, validate_chunks


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
        aligned, skipped = align_chunks(chunks)
        validate_chunks(aligned)

        self.assertEqual(skipped, 1)
        self.assertEqual(len(aligned[0].entities), 1)
        entity = aligned[0].entities[0]
        self.assertEqual(entity.entity_text, "SPARC")
        self.assertEqual((entity.start_char, entity.end_char), (0, 5))
        self.assertEqual(entity.entity_confidence_score, 0.99)

    def test_json_without_offsets_is_aligned_before_validation(self):
        chunks = parse_json(json.dumps([self.row]).encode("utf-8"))
        self.assert_upload_result(chunks)

    def test_josn_filename_uses_json_parser(self):
        chunks = parse_dataset(json.dumps([self.row]).encode("utf-8"), "predictions.JOSN")
        self.assert_upload_result(chunks)

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
