import csv
import io
import json
import unittest
import uuid
from types import SimpleNamespace

from app.models.document import EntitySource
from app.models.review import ReviewVerdict
from app.services.ingestion import IngestionError, parse_csv, parse_json, validate_offsets_against_text
from app.services.metrics import _compute_metrics_for_entities
from app.services.tokens import new_token, token_digest
from app.services.versioning import compare_snapshots, snapshot_hash


class IngestionTests(unittest.TestCase):
    def test_text_only_csv_entity_gets_offsets(self):
        sentence = "Energy Project Permitting The U.S. Department of the Interior recently announced a change."
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["document_id", "filename", "source", "cleaned_title", "sentence_id", "sentence", "entities"])
        writer.writerow(["D0002", "", "The Fusion Report", "Energy Policy Outlook", "D0002-S020", sentence,
                         json.dumps([{"text": "U.S. Department of the Interior",
                                      "label": "Organization and Company [Government / Policy / Funding / Regulatory Agency]"}])])

        rows, skipped = parse_csv(output.getvalue().encode())
        validate_offsets_against_text(rows)
        self.assertEqual(skipped, 0)
        self.assertEqual(len(rows[0].entities), 1)
        entity = rows[0].entities[0]
        self.assertEqual(sentence[entity.start_char:entity.end_char], entity.text)

    def test_repeated_text_uses_distinct_spans(self):
        data = [{"document_id": "D1", "sentence_id": "S1", "sentence": "Alpha and Alpha",
                 "entities": [{"text": "Alpha", "label": "ORG"}, {"text": "Alpha", "label": "ORG"}]}]
        rows, skipped = parse_json(json.dumps(data).encode())
        validate_offsets_against_text(rows)
        self.assertEqual(skipped, 0)
        self.assertEqual([entity.start_char for entity in rows[0].entities], [0, 10])

    def test_unmatched_text_is_skipped_without_losing_valid_entities(self):
        data = [{"document_id": "D1", "sentence_id": "S1", "sentence": "Alpha beta",
                 "entities": [{"text": "SPARC", "label": "DEVICE"}, {"text": "Alpha", "label": "ORG"}]}]
        details = []
        rows, skipped = parse_json(json.dumps(data).encode(), details)
        validate_offsets_against_text(rows)
        self.assertEqual(skipped, 1)
        self.assertIn("SPARC", details[0])
        self.assertEqual([entity.text for entity in rows[0].entities], ["Alpha"])

    def test_csv_unmatched_text_skips_only_the_invalid_prediction(self):
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["document_id", "sentence_id", "sentence", "entities"])
        writer.writerow(["D1", "S1", "Alpha beta", json.dumps([
            {"text": "SPARC", "label": "DEVICE"}, {"text": "beta", "label": "ORG"},
        ])])
        details = []
        rows, skipped = parse_csv(output.getvalue().encode(), details)
        validate_offsets_against_text(rows)
        self.assertEqual(skipped, 1)
        self.assertIn("Row 2", details[0])
        self.assertEqual([entity.text for entity in rows[0].entities], ["beta"])

    def test_invalid_explicit_offsets_still_fail_import(self):
        data = [{"document_id": "D1", "sentence_id": "S1", "sentence": "Alpha beta",
                 "entities": [{"text": "Gamma", "label": "ORG", "start_char": 0, "end_char": 5}]}]
        rows, _ = parse_json(json.dumps(data).encode())
        with self.assertRaises(IngestionError):
            validate_offsets_against_text(rows)

    def test_new_columns_accept_blank_optional_metadata(self):
        csv_data = (
            'document_id,filename,source,cleaned_title,sentence_id,sentence,entities\n'
            'D1,,,Title,S1,Alpha beta,"[{""text"":""Alpha"",""label"":""ORG"",""start_char"":0,""end_char"":5}]"\n'
        ).encode()
        rows, skipped = parse_csv(csv_data)
        validate_offsets_against_text(rows)
        self.assertEqual(skipped, 0)
        self.assertIsNone(rows[0].filename)
        self.assertIsNone(rows[0].source)
        self.assertEqual(rows[0].cleaned_title, "Title")

    def test_repeated_document_metadata_must_match(self):
        data = [
            {"document_id": "D1", "source": "A", "sentence_id": "S1", "sentence": "One", "entities": []},
            {"document_id": "D1", "source": "B", "sentence_id": "S2", "sentence": "Two", "entities": []},
        ]
        rows, _ = parse_json(json.dumps(data).encode())
        with self.assertRaises(IngestionError):
            validate_offsets_against_text(rows)


class PrecisionTests(unittest.TestCase):
    def test_only_reviewed_predictions_affect_precision(self):
        ids = [uuid.uuid4() for _ in range(4)]
        entities = [SimpleNamespace(id=ids[i], source=EntitySource.MODEL) for i in range(3)]
        entities.append(SimpleNamespace(id=ids[3], source=EntitySource.HUMAN))
        reviews = {ids[0]: SimpleNamespace(verdict=ReviewVerdict.TP),
                   ids[1]: SimpleNamespace(verdict=ReviewVerdict.FP),
                   ids[3]: SimpleNamespace(verdict=ReviewVerdict.FN)}
        result = _compute_metrics_for_entities(entities, reviews)
        self.assertEqual((result.tp, result.fp, result.total_model_entities), (1, 1, 3))
        self.assertEqual(result.precision, 0.5)
        self.assertEqual(result.percent_reviewed, 66.67)


class VersionTests(unittest.TestCase):
    def test_comparison_reports_verdict_change(self):
        def snapshot(verdict):
            return {"documents": [{"doc_id_external": "D1", "sentences": [{
                "sentence_id_external": "S1", "entities": [{"id": "e1", "source": "model",
                "text": "Alpha", "label": "ORG", "reviews": [] if verdict is None else [{
                    "id": "r1", "created_at": "2026-01-01T00:00:00+00:00", "verdict": verdict}]}]}]}]}
        result = compare_snapshots(snapshot("TP"), snapshot("FP"))
        self.assertEqual(result["changed"], 1)
        self.assertEqual(result["changes"][0]["after"], "FP")
        self.assertEqual(snapshot_hash(snapshot("TP")), snapshot_hash(snapshot("TP")))


class TokenTests(unittest.TestCase):
    def test_token_digest_is_stable_without_storing_raw_value(self):
        raw, digest = new_token()
        self.assertEqual(digest, token_digest(raw))
        self.assertNotEqual(raw, digest)


if __name__ == "__main__":
    unittest.main()
