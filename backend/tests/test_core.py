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
