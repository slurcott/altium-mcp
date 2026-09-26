"""Offline tests for swap_realign (B24)."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import swap_realign as R


def part(des, comment, p1, p2, orientation=0, mirrored=False, extra_pins=()):
    pins = [{"designator": "1", "hot_x": p1[0], "hot_y": p1[1]},
            {"designator": "2", "hot_x": p2[0], "hot_y": p2[1]}]
    pins += [{"designator": d, "hot_x": 0, "hot_y": 0} for d in extra_pins]
    return {"designator": des, "comment": comment, "orientation": orientation,
            "mirrored": mirrored, "pins": pins}


class PlanTest(unittest.TestCase):
    def plan(self, old, new, **kw):
        return R.plan_components("S.SchDoc", [old], [new], **kw)

    def test_unchanged_comment_ignored(self):
        o = part("R1", "10k", (0, 0), (0, 400))
        self.assertEqual(self.plan(o, part("R1", "10k", (100, 0), (100, 300))), ([], [], 0))

    def test_same_pins_nothing_to_do(self):
        o = part("R1", "10k", (0, 0), (0, 400))
        self.assertEqual(self.plan(o, part("R1", "RES 0603 10kΩ 1%", (0, 0), (0, 400))), ([], [], 0))

    def test_move_and_bridge(self):
        # old span 400 (pins at 0 and 400), new symbol span 200 and shifted by 100
        o = part("R1", "10k", (1000, 1000), (1000, 1400))
        n = part("R1", "RES 0603 10kΩ 1%", (1100, 1000), (1100, 1200))
        lines, manual, k = self.plan(o, n)
        self.assertEqual(lines, ["MOVE|S.SchDoc|R1|-100|0", "WIRE|S.SchDoc|1000|1200|1000|1400"])
        self.assertEqual((manual, k), ([], 1))

    def test_rotate_first(self):
        o = part("C1", "100n", (0, 0), (0, 200))          # pin 1 -> 2 points up
        n = part("C1", "CAP", (0, 0), (200, 0), orientation=0)   # points right
        lines, _, _ = self.plan(o, n)
        self.assertEqual(lines, ["ROT|S.SchDoc|C1|1"])    # one CCW quarter turn

    def test_mirrored_turns_the_other_way(self):
        o = part("C1", "100n", (0, 0), (0, 200))
        n = part("C1", "CAP", (0, 0), (200, 0), orientation=0, mirrored=True)
        lines, _, _ = self.plan(o, n)
        self.assertEqual(lines, ["ROT|S.SchDoc|C1|3"])

    def test_already_rotated_goes_manual(self):
        o = part("C1", "100n", (0, 0), (0, 200))
        n = part("C1", "CAP", (0, 0), (200, 0), mirrored=True)
        lines, manual, _ = self.plan(o, n, rotated={"C1"})
        self.assertEqual(lines, [])
        self.assertIn("still wrong", manual[0][2])

    def test_not_two_pins_is_manual(self):
        o = part("Q1", "FET", (0, 0), (0, 200), extra_pins=("3",))
        n = part("Q1", "FET2", (0, 0), (0, 300), extra_pins=("3",))
        lines, manual, _ = self.plan(o, n)
        self.assertEqual(lines, [])
        self.assertEqual(manual[0][1], "Q1")

    def test_done_parts_skipped(self):
        # already realigned (pin 2 bridged by a wire): connectivity matches -> nothing to plan
        o = part("R1", "10k", (1000, 1000), (1000, 1400))
        n = part("R1", "RES 0603 10kΩ 1%", (1000, 1000), (1000, 1200))
        self.assertEqual(len(self.plan(o, n)[0]), 1)                 # would re-add the wire
        self.assertEqual(self.plan(o, n, done={"R1"}), ([], [], 0))

    def test_only_filter(self):
        o = part("R1", "10k", (0, 0), (0, 400))
        n = part("R1", "RES", (100, 0), (100, 400))
        self.assertEqual(self.plan(o, n, only={"R2"}), ([], [], 0))


class MembershipTest(unittest.TestCase):
    def test_identical(self):
        nets = [{"pins": ["R1.1", "C1.1"]}, {"pins": ["R1.2", "U1.3"]}]
        self.assertEqual(R.membership_diff(nets, list(reversed(nets))), [])

    def test_short_detected(self):
        old = [{"pins": ["R1.1", "C1.1"]}, {"pins": ["R1.2", "U1.3"]}]
        new = [{"pins": ["R1.1", "C1.1", "R1.2", "U1.3"]}]
        pins = [p for p, _, _ in R.membership_diff(old, new)]
        self.assertEqual(pins, ["C1.1", "R1.1", "R1.2", "U1.3"])

    def test_open_detected(self):
        old = [{"pins": ["R1.2", "U1.3"]}]
        new = [{"pins": ["U1.3"]}]
        self.assertEqual([p for p, _, _ in R.membership_diff(old, new)], ["R1.2", "U1.3"])


class FilesTest(unittest.TestCase):
    def test_sheets_and_baseline(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "b" / "History").mkdir(parents=True)
            (d / "b" / "History" / "Power.~(3).SchDoc").write_text("")
            prj = d / "x.PrjPcb"
            prj.write_text("[Document1]\nDocumentPath=Power.SchDoc\n[Document2]\nDocumentPath=x.PcbDoc\n",
                           encoding="latin-1")
            self.assertEqual([p.name for p in R.sheets(prj)], ["Power.SchDoc"])
            self.assertEqual(R.baseline_file(d / "b", "Power.SchDoc").name, "Power.~(3).SchDoc")
            self.assertIsNone(R.baseline_file(d / "b", "Other.SchDoc"))

    def test_render_script(self):
        s = R.render_script(r"C:\x\spec.txt")
        self.assertIn(r"Spec.LoadFromFile('C:\x\spec.txt')", s)
        self.assertNotIn("{SPEC}", s)
        with self.assertRaises(ValueError):
            R.render_script("C:\\it's.txt")


if __name__ == "__main__":
    unittest.main()
