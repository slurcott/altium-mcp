"""Offline tests for footprint_spec (B23: create_footprints_batch origin)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import footprint_spec as F

# Mirrors the B23 scratch experiment: T_A_RAW centred on the 50000 origin,
# T_C_REL written origin-relative (lands 50000 mil away).
SPEC = """FOOTPRINT|T_A_RAW|raw
PAD|1|49941|50000|0|Top Layer|0|0|0|0|0|50|60|1
PAD|2|50059|50000|0|Top Layer|0|0|0|0|0|50|60|1
TRACK|49900|50100|50100|50100|5|Top Overlay
FOOTPRINT|T_C_REL|relative
PAD|1|-59|0|0|Top Layer|0|0|0|0|0|50|60|1
PAD|2|59|0|0|Top Layer|0|0|0|0|0|50|60|1
FOOTPRINT|NO_PADS|graphics only
TRACK|0|0|10|10|5|Top Overlay
"""


class PadCentreTest(unittest.TestCase):
    def test_centres(self):
        c = F.pad_centres(SPEC)
        self.assertEqual(c["T_A_RAW"], (50000.0, 50000.0))
        self.assertEqual(c["T_C_REL"], (0.0, 0.0))
        self.assertNotIn("NO_PADS", c)

    def test_off_origin_flags_raw_spec(self):
        # spec coords are origin-relative: the 50000-based footprint is the wrong one
        bad = F.off_origin(F.pad_centres(SPEC))
        self.assertEqual([b["name"] for b in bad], ["T_A_RAW"])
        self.assertEqual((bad[0]["dx"], bad[0]["dy"]), (50000.0, 50000.0))

    def test_tolerance(self):
        self.assertEqual(F.off_origin({"X": (0.5, -0.5)}), [])
        self.assertEqual(len(F.off_origin({"X": (2, 0)})), 1)


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.lib = os.path.join(self.d, "a.PcbLib")
        open(self.lib, "w").close()

    def _spec(self, fplib):
        p = os.path.join(self.d, "spec.txt")
        with open(p, "w", encoding="cp1252") as f:
            f.write("FPLIB|%s\nFOOTPRINT|X|\n" % fplib)
        return p

    def test_already_resolved_unchanged(self):
        real = os.path.realpath(self.lib)
        p = self._spec(real)
        run, a, b = F.resolve_fplib(p)
        self.assertEqual(run, p)
        self.assertEqual(os.path.normcase(a), os.path.normcase(b))

    def test_unresolved_rewritten(self):
        # a '..' segment stands in for the junction: same file, different spelling
        odd = os.path.join(self.d, "sub", "..", "a.PcbLib")
        os.mkdir(os.path.join(self.d, "sub"))
        p = self._spec(odd)
        run, a, b = F.resolve_fplib(p)
        self.assertNotEqual(run, p)
        with open(run, encoding="cp1252") as f:
            first = f.readline().strip()
        self.assertEqual(first, "FPLIB|" + os.path.realpath(self.lib))
        os.remove(run)

    def test_no_fplib(self):
        p = os.path.join(self.d, "s2.txt")
        with open(p, "w") as f:
            f.write("FOOTPRINT|X|\nPAD|1|0|0\n")
        self.assertEqual(F.resolve_fplib(p), (p, None, None))


if __name__ == "__main__":
    unittest.main()
