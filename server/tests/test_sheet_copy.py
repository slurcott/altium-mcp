"""Offline tests for sheet_copy."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import sheet_copy as SC


class ResetTest(unittest.TestCase):
    def test_numbered_designators_reset(self):
        self.assertEqual(SC.reset_designator("C14"), "C?")
        self.assertEqual(SC.reset_designator("U3"), "U?")
        self.assertEqual(SC.reset_designator("TP12"), "TP?")

    def test_named_parts_untouched(self):
        self.assertIsNone(SC.reset_designator("TP_TXCAN"))
        self.assertIsNone(SC.reset_designator("TP_5v0"))            # named, ends in a digit
        self.assertIsNone(SC.reset_designator("R_S3"))
        self.assertIsNone(SC.reset_designator("Gnd"))
        self.assertIsNone(SC.reset_designator("12"))                # no prefix: leave it


class PlanTest(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.src = self.d / "a.SchDoc"
        self.src.write_bytes(b"x")

    def test_port_lines(self):
        lines = SC.plan(self.src, self.d / "b.SchDoc",
                        {"CN_P": "CANH", "TxCAN": {"name": "CanTx", "io": "input"}})
        self.assertEqual(lines, ["PORT|CN_P|CANH|-1", "PORT|TxCAN|CanTx|2"])

    def test_refuses_overwrite_and_same_file(self):
        (self.d / "b.SchDoc").write_bytes(b"y")
        with self.assertRaises(FileExistsError):
            SC.plan(self.src, self.d / "b.SchDoc", {})
        with self.assertRaises(ValueError):
            SC.plan(self.src, self.d / "c.SchDoc", {"A|B": "C"})

    def test_script_rendered(self):
        s = SC.render_script(r"C:\x\b.SchDoc", r"C:\x\spec.txt", reset=False)
        self.assertIn(r"'C:\x\b.SchDoc'", s)
        self.assertIn("if 0 = 1 then", s)
        self.assertNotIn("{", s.split("begin", 1)[1].replace("{RESET}", ""))


if __name__ == "__main__":
    unittest.main()
