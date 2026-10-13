import csv
import json
import tempfile
import unittest
from pathlib import Path

from add_entity_offsets import enrich


class OffsetScriptTests(unittest.TestCase):
    def write_csv(self, path: Path, rows: list[dict]) -> None:
        fields = ["source", "document_id", "cleaned_title", "chunk_id", "chunk_text", "entities", "triplets"]
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def test_repeated_mentions_preserve_other_fields(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            source, output = Path(directory) / "source.csv", Path(directory) / "output.csv"
            entities = [
                {"entity_text": "SPARC", "entity_class": "Fusion Device", "entity_confidence_score": 0.99},
                {"entity_text": "SPARC", "entity_class": "Fusion Device", "entity_confidence_score": 0.81},
            ]
            triplets = '[{"subject":"SPARC"}]'
            self.write_csv(source, [{"source": "test", "document_id": "d1", "cleaned_title": "T",
                                     "chunk_id": "c1", "chunk_text": "SPARC and SPARC",
                                     "entities": json.dumps(entities), "triplets": triplets}])
            self.assertEqual(enrich(source, output), (1, 2))
            with output.open(encoding="utf-8-sig", newline="") as stream:
                row = next(csv.DictReader(stream))
            result = json.loads(row["entities"])
            self.assertEqual([(e["start_char"], e["end_char"]) for e in result], [(0, 5), (10, 15)])
            self.assertEqual([e["entity_confidence_score"] for e in result], [0.99, 0.81])
            self.assertEqual(row["triplets"], triplets)

    def test_failure_does_not_write_output(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            source, output = Path(directory) / "source.csv", Path(directory) / "output.csv"
            self.write_csv(source, [{"source": "test", "document_id": "d1", "cleaned_title": "T",
                                     "chunk_id": "c1", "chunk_text": "SPARC",
                                     "entities": '[{"entity_text":"missing"}]', "triplets": "[]"}])
            with self.assertRaisesRegex(ValueError, "row 2"):
                enrich(source, output)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
