"""Offline tests for backlog B38-B42 (2026-10-08): copper write tool pieces, pad owner in pcb_query, honest
islands and gaps, polygon nets, modal-dialog pre-check."""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import altium_dump  # noqa: E402
import altium_guard as g  # noqa: E402
import pcb_copper as c  # noqa: E402
import pcb_nets  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SANDBOX_SRC = (ROOT / "server" / "SandboxScript" / "Sandbox.pas").read_text(encoding="utf-8")
CORPUS = g.Corpus.from_repo(ROOT)

PLAN = {"tracks": [{"layer": "Mid Layer 1", "x1": -2500, "y1": 300, "x2": -2450, "y2": 250, "w": 10, "net": "A"},
                   {"layer": "Mid Layer 2", "x1": -650, "y1": 250, "x2": -650, "y2": 1950.5, "w": 10, "net": "A"},
                   {"layer": "Top Layer", "x1": 0, "y1": 0, "x2": 100, "y2": 0, "w": 75, "net": "B"}],
        "vias": [{"x": -650, "y": 250, "d": 20, "hole": 10, "net": "A"},
                 {"x": 500, "y": 500, "d": 50, "hole": 28, "net": ""}]}


class Table(unittest.TestCase):
    def test_grouped_by_net_in_hundredths_of_a_mil(self):
        lines = c.table_lines(PLAN)
        self.assertEqual(lines[0], "N|A")
        self.assertIn("T|1|-250000|30000|-245000|25000|1000", lines)
        self.assertIn("T|2|-65000|25000|-65000|195050|1000", lines)
        self.assertIn("V|-65000|25000|2000|1000", lines)
        self.assertEqual(lines.index("N|B") + 1, lines.index("T|T|0|0|10000|0|7500"))
        self.assertEqual(lines[-2:], ["N|", "V|50000|50000|5000|2800"])        # no-net objects last, own group

    def test_plan_checks(self):
        self.assertEqual(c.check_plan(PLAN), [])
        bad = {"tracks": [{"layer": "Inner 9", "x1": 0, "y1": 0, "x2": 0, "y2": 0, "w": 0, "net": "A|B"}],
               "vias": [{"x": 0, "y": 0, "d": 10, "hole": 10, "net": "A"}]}
        msgs = " ".join(c.check_plan(bad))
        for part in ("layer", "width", "zero length", "pad d > hole", "cannot be written"):
            self.assertIn(part, msgs)
        self.assertIn("no tracks and no vias", c.check_plan({"tracks": [], "vias": []})[0])


class Script(unittest.TestCase):
    def lint(self, dry):
        body = c.write_script(r"C:\b\Board.PcbDoc", r"C:\x\table.txt", dry)
        return body, g.lint_script(body, CORPUS, SANDBOX_SRC, allow_new_api=c.NEW_API)

    def test_write_script_passes_the_linter(self):
        body, rep = self.lint(False)
        self.assertEqual(rep["errors"], [], rep["errors"])
        self.assertIn("PCBObjectFactory(eTrackObject", body)
        self.assertIn("Trk.Net := Cur", body)

    def test_dry_run_creates_nothing(self):
        body, rep = self.lint(True)
        self.assertEqual(rep["errors"], [], rep["errors"])
        for word in ("PCBObjectFactory", "AddPCBObject", "PreProcess", "PostProcess"):
            self.assertNotIn(word, body)
        self.assertIn("would add tracks", body)

    def test_nothing_is_created_inside_the_net_lookup_loop(self):
        body = c.write_script("b", "t", False)
        loop = body[body.index("Net := Iter.FirstPCBObject"):body.index("Board.BoardIterator_Destroy(Iter)")]
        self.assertNotIn("PCBObjectFactory", loop)
        self.assertNotIn("AddPCBObject", loop)

    def test_quotes_in_paths_are_doubled(self):
        self.assertIn("S1 := 'C:\\it''s\\b.PcbDoc'", c.write_script("C:\\it's\\b.PcbDoc", "t", True))

    def test_result_line(self):
        self.assertEqual(c.parse_result("added tracks 425 vias 126 nets-not-found 0 "),
                         {"dry_run": False, "tracks": 425, "vias": 126, "nets_not_found": 0, "missing_nets": []})
        r = c.parse_result("would add tracks 3 vias 1 nets-not-found 2 FOO BAR ")
        self.assertTrue(r["dry_run"]); self.assertEqual(r["missing_nets"], ["FOO", "BAR"])
        self.assertIsNone(c.parse_result("ERROR wrong board focused: x"))


def board(tracks=(), vias=(), pads=()):
    return {"tracks": list(tracks), "vias": list(vias), "pads": list(pads), "arcs": [], "regions": [], "fills": []}


class Verify(unittest.TestCase):
    def test_all_present_reversed_ends_allowed(self):
        B = board([dict(layer="Mid1", net="A", x1=-2450, y1=250, x2=-2500, y2=300, w=10),
                   dict(layer="Mid2", net="A", x1=-650, y1=250, x2=-650, y2=1950.5, w=10),
                   dict(layer="Top", net="B", x1=0, y1=0, x2=100, y2=0, w=75)],
                  [dict(net="A", x=-650, y=250, d=20, hole=10), dict(net=None, x=500, y=500, d=50, hole=28)])
        r = c.verify(B, PLAN, pcb_nets.long_layer)
        self.assertTrue(r["ok"], r)

    def test_missing_and_wrong_net_are_reported(self):
        B = board([dict(layer="Mid1", net="X", x1=-2500, y1=300, x2=-2450, y2=250, w=10)],
                  [dict(net="A", x=-650, y=250, d=20, hole=10)])
        r = c.verify(B, PLAN, pcb_nets.long_layer)
        self.assertFalse(r["ok"])
        self.assertEqual((r["missing_tracks"], r["missing_vias"], r["wrong_net_total"]), (2, 1, 1))
        self.assertEqual(r["wrong_net"][0]["file"], "X")

    def test_same_place_on_another_layer_is_missing(self):
        B = board([dict(layer="Top", net="A", x1=-2500, y1=300, x2=-2450, y2=250, w=10)])
        self.assertEqual(c.verify(B, {"tracks": PLAN["tracks"][:1], "vias": []}, pcb_nets.long_layer)["missing_tracks"], 1)


class ReaderFixes(unittest.TestCase):
    def pad(self, **kw):
        d = dict(comp="J1", name="1", net="P", x=0, y=0, w=65, h=65, rot=0, layer="MultiLayer", hole=43, shape="round")
        d.update(kw); return d

    def test_track_through_a_pad_joins_it(self):                       # B40: 75 mil track over pins 1, 3, 5
        B = board([dict(layer="Top", net="P", x1=-100, y1=0, x2=100, y2=0, w=75)],
                  pads=[self.pad(name="1", x=-100), self.pad(name="3", x=0), self.pad(name="5", x=100)])
        self.assertEqual(len(pcb_nets.islands(B, "P")), 1)

    def test_track_beside_a_pad_does_not_join_it(self):
        B = board([dict(layer="Top", net="P", x1=-100, y1=60, x2=100, y2=60, w=10)],
                  pads=[self.pad(name="1", x=-100, y=60), self.pad(name="3", x=0, y=0)])
        self.assertEqual(len(pcb_nets.islands(B, "P")), 2)

    def test_round_pad_gap_is_measured_to_the_circle(self):            # B40: was -3.7 with the bounding box
        pd = self.pad()
        self.assertAlmostEqual(pcb_nets.pad_edge_dist(61, 69, pd), (61 ** 2 + 69 ** 2) ** 0.5 - 32.5, places=3)
        self.assertAlmostEqual(pcb_nets.pad_edge_dist(61, 69, self.pad(shape="rect")), (28.5 ** 2 + 36.5 ** 2) ** 0.5, places=3)

    def test_oblong_round_pad_is_a_stadium(self):
        pd = self.pad(w=100, h=40)
        self.assertAlmostEqual(pcb_nets.pad_edge_dist(80, 0, pd), 30, places=3)
        self.assertAlmostEqual(pcb_nets.pad_edge_dist(0, 50, pd), 30, places=3)
        self.assertEqual(pcb_nets.pad_edge_dist(40, 10, pd), 0)

    def test_long_layer_names(self):
        self.assertEqual([pcb_nets.long_layer(n) for n in ("Top", "Mid1", "Mid2", "Bottom", "Mechanical 1")],
                         ["Top Layer", "Mid Layer 1", "Mid Layer 2", "Bottom Layer", "Mechanical 1"])


class PadOwner(unittest.TestCase):                                      # B39
    def test_component_field_is_parsed_and_optional(self):
        text = ("BOARD|7500|1000|C:\\b.PcbDoc\n"
                "PAD|Multi Layer|PWR_RTN|2|-5700|400|65|65|43.307|J_A\n"
                "PAD|Multi Layer||SO1|-6000|350|126|126|126|\n"
                "PAD|Top Layer|N1|1|10|20|30|26|0\n")
        _, objs = altium_dump.parse_pcb_dump(text)
        self.assertEqual([o.get("component") for o in objs], ["J_A", None, None])
        self.assertEqual(objs[0]["hole"], 43.307)


class ModalPreCheck(unittest.TestCase):                                 # B42
    def test_preflight_refuses_and_names_the_dialog(self):
        with mock.patch.object(g, "read_wedge", return_value=None), \
             mock.patch.object(g, "altium_modal_state", return_value={"blocked": True, "dialogs": ["Replace Component"]}):
            ok, why = g.preflight(pids=[100])
        self.assertFalse(ok)
        self.assertIn("Replace Component", why)
        self.assertIn("NOT marked wedged", why)

    def test_preflight_passes_when_nothing_is_open(self):
        with mock.patch.object(g, "read_wedge", return_value=None), \
             mock.patch.object(g, "altium_modal_state", return_value={"blocked": False, "dialogs": []}):
            self.assertTrue(g.preflight(pids=[100])[0])

    def test_state_is_quiet_without_altium(self):
        self.assertEqual(g.altium_modal_state(pids=[]), {"blocked": False, "dialogs": []})


if __name__ == "__main__":
    unittest.main()
