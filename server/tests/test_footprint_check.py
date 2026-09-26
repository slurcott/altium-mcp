"""Offline tests for footprint_check (IC footprint vs datasheet) and pcblib_file.

Synthetic footprints only. Real-library validation (2026-09-26): 38 PcbLibs /
332 footprints / 1964 pads parsed; a legacy SOIC-8 and a 64-QFN checked against
JEDEC/package dimensions; pre-fix 1206/1210 caps flagged as 50000 mil off origin.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import footprint_check as C

MIL = 1 / 0.0254   # mils per mm

SOIC8 = {"package": "dual", "style": "gullwing", "pins": 8, "pitch": 1.27,
         "span": [5.80, 6.20], "lead_length": [0.40, 1.27], "lead_width": [0.31, 0.51]}


def fp_from(pads_mm, origin=(0.0, 0.0), silk_pin1=True):
    """Parsed-footprint dict (mils, like pcblib_file) from mm pads [(name, x, y, w, h)]."""
    pads = [{"name": n, "x": (x + origin[0]) * MIL, "y": (y + origin[1]) * MIL,
             "w": w * MIL, "h": h * MIL, "rotation": 0.0, "shape": "rect"} for n, x, y, w, h in pads_mm]
    tracks = []
    if silk_pin1:
        p1 = next(p for p in pads if p["name"] == "1")
        tracks.append({"layer": "TopOverlay", "x1": p1["x"] - 40, "y1": p1["y"] + 20,
                       "x2": p1["x"] - 30, "y2": p1["y"] + 20, "width": 5})
    return {"name": "T", "pads": pads, "tracks": tracks, "arcs": []}


def good_soic(pitch=1.27, swap=None, mirror=False):
    """An IPC-nominal SOIC-8: pads 2.17 x 0.57 at +-2.64 mm."""
    pads = []
    for i in range(4):
        y = 1.5 * pitch - i * pitch
        pads.append((str(i + 1), -2.64, y, 2.17, 0.57))
        pads.append((str(8 - i), 2.64, y, 2.17, 0.57))
    if mirror:
        pads = [(n, -x, y, w, h) for n, x, y, w, h in pads]
    if swap:
        a, b = swap
        pads = [((b if n == a else a if n == b else n), x, y, w, h) for n, x, y, w, h in pads]
    return pads


class PackageModeTest(unittest.TestCase):
    def test_good_soic_passes(self):
        r = C.check(fp_from(good_soic()), SOIC8)
        self.assertEqual(r["verdict"], "PASS", r)
        self.assertEqual(r["info"]["orientation"], {"rotation": 0, "mirror": False})

    def test_rotated_footprint_still_passes(self):
        rot = [(n, -y, x, h, w) for n, x, y, w, h in good_soic()]     # 90 degrees
        r = C.check(fp_from(rot), SOIC8)
        self.assertEqual(r["verdict"], "PASS", r)
        self.assertEqual(r["info"]["orientation"]["rotation"], 90)

    def test_mirrored_pinout_fails(self):
        r = C.check(fp_from(good_soic(mirror=True)), SOIC8)
        self.assertEqual(r["verdict"], "FAIL")
        self.assertTrue(any("MIRRORED" in f for f in r["failures"]), r)

    def test_wrong_pitch_fails(self):
        r = C.check(fp_from(good_soic(pitch=1.0)), SOIC8)
        self.assertEqual(r["verdict"], "FAIL")
        self.assertTrue(any("pitch" in f for f in r["failures"]), r)

    def test_swapped_pin_numbers_fail(self):
        r = C.check(fp_from(good_soic(swap=("2", "7"))), SOIC8)
        self.assertEqual(r["verdict"], "FAIL")

    def test_missing_pin_fails(self):
        pads = [p for p in good_soic() if p[0] != "5"]
        r = C.check(fp_from(pads), SOIC8)
        self.assertTrue(any("missing" in f and "'5'" in f for f in r["failures"]), r)

    def test_short_pads_miss_leads(self):
        # pads pulled far outward: leads (toe at 3.0 mm, 0.84 mm long) mostly off the pads
        pads = [(n, x * 1.6, y, 1.0, h) for n, x, y, w, h in good_soic()]
        r = C.check(fp_from(pads), SOIC8)
        self.assertTrue(any("does not land" in f for f in r["failures"]), r)

    def test_origin_offset_warns(self):
        r = C.check(fp_from(good_soic(), origin=(1270.0, 0)), SOIC8)
        self.assertTrue(any("Offset Component Origin" in w for w in r["warnings"]), r)

    def test_no_pin1_silk_warns(self):
        r = C.check(fp_from(good_soic(), silk_pin1=False), SOIC8)
        self.assertTrue(any("pin 1" in w for w in r["warnings"]), r)

    def test_epad_missing_and_small(self):
        spec = dict(SOIC8, epad=[2.4, 3.1], epad_name="9")
        r = C.check(fp_from(good_soic()), spec)
        self.assertTrue(any("missing" in f and "'9'" in f for f in r["failures"]), r)
        small = good_soic() + [("9", 0.0, 0.0, 1.0, 1.0)]
        r = C.check(fp_from(small), spec)
        self.assertTrue(any("exposed pad" in f for f in r["failures"]), r)

    def test_quad_numbering(self):
        exp, leads = C.expected_from_package({"package": "quad", "style": "nolead", "pins": 16,
                                              "pitch": 0.5, "span": [2.9, 3.1],
                                              "lead_length": [0.3, 0.5], "lead_width": [0.18, 0.3]})
        by = {p["name"]: p for p in exp}
        self.assertLess(by["1"]["x"], 0)                          # pin 1 left side, top
        self.assertGreater(by["1"]["y"], by["4"]["y"])
        self.assertLess(by["5"]["y"], 0)                          # 5-8 bottom, left -> right
        self.assertLess(by["5"]["x"], by["8"]["x"])
        self.assertGreater(by["9"]["x"], 0)                       # 9-12 right, bottom -> top
        self.assertGreater(by["13"]["y"], 0)                      # 13-16 top, right -> left
        self.assertGreater(by["13"]["x"], by["16"]["x"])
        self.assertEqual(len(leads), 16)


class LandPatternModeTest(unittest.TestCase):
    LP = {"land_pattern": [{"name": "1", "x": -1.0, "y": 0.5, "w": 0.6, "h": 0.3},
                           {"name": "2", "x": -1.0, "y": -0.5, "w": 0.6, "h": 0.3},
                           {"name": "3", "x": 1.0, "y": -0.5, "w": 0.6, "h": 0.3},
                           {"name": "4", "x": 1.0, "y": 0.5, "w": 0.6, "h": 0.3},
                           {"name": "5", "x": 0.0, "y": 0.0, "w": 0.8, "h": 1.2}]}

    def pads(self, shift=0.0):
        return [(p["name"], p["x"] + (shift if p["name"] == "3" else 0), p["y"], p["w"], p["h"])
                for p in self.LP["land_pattern"]]

    def test_exact_pattern_passes(self):
        r = C.check(fp_from(self.pads()), self.LP)
        self.assertEqual(r["verdict"], "PASS", r)

    def test_shifted_pad_fails(self):
        r = C.check(fp_from(self.pads(shift=0.2)), self.LP)
        self.assertEqual(r["verdict"], "FAIL")
        self.assertTrue(any("land pattern" in f for f in r["failures"]), r)


class SliverTest(unittest.TestCase):
    def test_min_gap(self):
        pads = [{"name": "1", "x": 0, "y": 0, "w": 0.3, "h": 1}, {"name": "2", "x": 0.4, "y": 0, "w": 0.3, "h": 1}]
        self.assertAlmostEqual(C._min_gap(pads), 0.1)


if __name__ == "__main__":
    unittest.main()
