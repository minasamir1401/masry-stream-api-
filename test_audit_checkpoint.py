import json
from pathlib import Path
import tempfile
import unittest

from audit_catalog import checkpoint_rows


class AuditCheckpointTests(unittest.TestCase):
    def test_interrupted_last_write_preserves_completed_results_and_allows_resume(self):
        with tempfile.TemporaryDirectory(prefix="anime-audit-checkpoint-") as directory:
            path = Path(directory) / "results.jsonl"
            first = {"anime_id": 1535, "episode": 4, "title": "أنمي"}
            complete = (json.dumps(first, ensure_ascii=False) + "\n").encode()
            path.write_bytes(complete + b'{"anime_id":21459,"episode":')
            self.assertEqual(checkpoint_rows(path), [first])
            self.assertEqual(path.read_bytes(), complete)
            second = {"anime_id": 21459, "episode": 12}
            with path.open("ab") as handle:
                handle.write((json.dumps(second) + "\n").encode())
            self.assertEqual(checkpoint_rows(path), [first, second])

    def test_corruption_in_a_completed_line_is_not_silently_discarded(self):
        with tempfile.TemporaryDirectory(prefix="anime-audit-checkpoint-") as directory:
            path = Path(directory) / "results.jsonl"
            path.write_bytes(b'{"anime_id":1535}\ninvalid\n')
            with self.assertRaises(ValueError):
                checkpoint_rows(path)


if __name__ == "__main__":
    unittest.main()
