"""Public result/input contract tests; no network or model dependencies."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from _workflow import InputError, emit, fingerprint, interval, load_input


class WorkflowTests(unittest.TestCase):
    def test_invalid_measurements_are_rejected(self):
        for value in [True, float("nan"), float("inf"), 10 ** 400, [2, 1], [0, 1], [1], "3"]:
            with self.subTest(value=value), self.assertRaises(InputError):
                interval(value, "height", positive=True)

    def test_signed_angles_and_exact_values_are_supported(self):
        self.assertEqual(interval([-10, 2], "pitch"), (-10.0, 2.0))
        self.assertEqual(interval(3, "height", positive=True), (3.0, 3.0))

    def test_nonfinite_json_including_numeric_overflow_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "request.json"
            for text in ['{"x": NaN}', '{"x": Infinity}', '{"x": [1e999]}', '[]']:
                path.write_text(text)
                with self.subTest(text=text), self.assertRaises(InputError):
                    load_input(path)

    def test_status_exit_codes_and_persisted_stdout_agree(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "result.json"
            for status, code in [("ok", 0), ("partial", 0), ("needs_input", 2), ("invalid", 2), ("unavailable", 3)]:
                stream = io.StringIO()
                with contextlib.redirect_stdout(stream):
                    actual = emit({"workflow": "test", "status": status, "result": {"n": 2}}, path)
                self.assertEqual(actual, code)
                self.assertEqual(path.read_text(), stream.getvalue())
                self.assertEqual(json.loads(path.read_text())["schema_version"], 1)
                self.assertEqual(list(path.parent.iterdir()), [path])

    def test_fingerprint_tracks_content_not_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            a, b = Path(directory) / "a", Path(directory) / "b"
            a.write_bytes(b"abc")
            b.write_bytes(b"abc")
            self.assertEqual(fingerprint(a)["sha256"], fingerprint(b)["sha256"])
            b.write_bytes(b"abd")
            self.assertNotEqual(fingerprint(a)["sha256"], fingerprint(b)["sha256"])
            with self.assertRaises(InputError):
                fingerprint(Path(directory) / "missing")


if __name__ == "__main__":
    unittest.main()
