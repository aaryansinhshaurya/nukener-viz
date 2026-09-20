import csv
import io
import json
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import HTTPException

from app.models.document import EntitySource
from app.routers.documents import _add_missing_entities
from app.services.ingestion import parse_csv, validate_offsets_against_text


SENTENCE = (
    "Energy Project Permitting The U.S. Department of the Interior recently announced "
    "it would accelerate the energy permitting process to 28 days maximum in response "
    "to Trump's declaration of a national energy emergency."
)
ENTITY_TEXT = "U.S. Department of the Interior"
LABEL = "Organization and Company [Government / Policy / Funding / Regulatory Agency]"


def sample_rows():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["document_id", "filename", "source", "cleaned_title", "sentence_id", "sentence", "entities"])
    writer.writerow([
        "D0002", "", "The Fusion Report",
        "2025 Energy Policy Outlook: Industry Uncertainty and Executive Orders",
        "D0002-S020", SENTENCE, json.dumps([{"text": ENTITY_TEXT, "label": LABEL}]),
    ])
    rows, _ = parse_csv(output.getvalue().encode("utf-8"))
    validate_offsets_against_text(rows)
    return rows


class DocumentRepairTests(unittest.TestCase):
    def setUp(self):
        self.rows = sample_rows()
        self.stored_sentence = SimpleNamespace(
            id=uuid.uuid4(), sentence_id_external="D0002-S020", text=SENTENCE, entities=[]
        )
        self.document = SimpleNamespace(doc_id_external="D0002", sentences=[self.stored_sentence])
        self.db = Mock()

    def test_reupload_adds_missing_entity_with_resolved_offsets(self):
        summary = _add_missing_entities(
            self.db, [self.document], self.rows, 1,
            ["Row 49 (sentence_id=D0006-S011): entity 'SPARC' was not found in the sentence"],
        )
        self.assertEqual((summary.documents_created, summary.sentences_created, summary.entities_created), (0, 0, 1))
        self.assertEqual(summary.entities_skipped_missing_offsets, 1)
        self.assertIn("SPARC", summary.skipped_entity_details[0])
        entity = self.db.add.call_args.args[0]
        self.assertEqual(entity.sentence_id, self.stored_sentence.id)
        self.assertEqual(SENTENCE[entity.start_char:entity.end_char], ENTITY_TEXT)
        self.assertEqual(entity.label, LABEL)
        self.db.commit.assert_called_once()

    def test_reupload_does_not_duplicate_existing_prediction(self):
        prediction = self.rows[0].entities[0]
        self.stored_sentence.entities.append(SimpleNamespace(
            start_char=prediction.start_char, end_char=prediction.end_char,
            label=LABEL, source=EntitySource.MODEL,
        ))
        summary = _add_missing_entities(self.db, [self.document], self.rows)
        self.assertEqual(summary.entities_created, 0)
        self.db.add.assert_not_called()

    def test_different_sentence_is_rejected_without_changes(self):
        self.stored_sentence.text = "Different text"
        with self.assertRaises(HTTPException) as raised:
            _add_missing_entities(self.db, [self.document], self.rows)
        self.assertEqual(raised.exception.status_code, 409)
        self.db.add.assert_not_called()
        self.db.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
