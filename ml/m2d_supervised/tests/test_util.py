import tempfile
import unittest
from pathlib import Path

from m2d_supervised.util import atomic_json, object_sha256, read_json, seed_for


class UtilTest(unittest.TestCase):
    def test_atomic_json_replaces_complete_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "state.json"
            atomic_json(path, {"value": 1})
            atomic_json(path, {"value": 2, "complete": True})
            self.assertEqual(read_json(path), {"value": 2, "complete": True})
            self.assertFalse(path.with_name("state.json.tmp").exists())

    def test_hash_and_seed_are_order_stable(self) -> None:
        self.assertEqual(object_sha256({"b": 2, "a": 1}), object_sha256({"a": 1, "b": 2}))
        self.assertEqual(seed_for(42, "sample"), seed_for(42, "sample"))
        self.assertNotEqual(seed_for(42, "sample"), seed_for(43, "sample"))


if __name__ == "__main__":
    unittest.main()
