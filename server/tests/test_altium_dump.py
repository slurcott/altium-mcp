"""Offline tests for altium_dump - the parsers behind pcb_query and the
compiled-netlist source of netlist_query. None of these touch Altium.

    python -m unittest server/tests/test_altium_dump.py -v

The dump lines mirror what PcbQueryFromSpec / CompiledNetlistFromSpec write.
"""
import sys
import unittest
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))

import altium_dump as d  # noqa: E402
import schdoc_file  # noqa: E402

PCB_DUMP = "\r\n".join([
    r"BOARD|1000|2000|C:\x\board.PcbDoc",
    "TRK|Top Layer|GND|0|0|100|0|10",
    "TRK|Mid Layer 1|3v3|50|50|50|400|8",
    "TRK|Mid Layer 1|SWDIO|10|10|20|10|6",
    "ARC|Top Overlay||-1082,93|0,28|50|0|360|5",   # comma decimal (locale)
    "PAD|Top Layer|GND|1|300|300|60|40|0",
    "VIA|GND|500|500|24|12|Top Layer|Bottom Layer",
    "PLY|Mid Layer 2|GND|0|0|1000|800",
    "TXT|Top Overlay|10|20|R1|odd text",            # '|' inside text survives
    "CMP|Top Layer|U8|-1082.9|0.3|90",
    "CON|5v0",
    "CON|NetR19_2",
    "CON|NetR19_2",
    "BAD|12",
    "",
])


class PcbDump(unittest.TestCase):
    def setUp(self):
        self.header, self.objs = d.parse_pcb_dump(PCB_DUMP)

    def test_header(self):
        self.assertEqual(self.header["file"], r"C:\x\board.PcbDoc")
        self.assertEqual((self.header["origin_x"], self.header["origin_y"]), (1000, 2000))

    def test_every_line_parsed(self):
        self.assertEqual(len(self.objs), 13)
        self.assertEqual(self.objs[-1], {"kind": "unreadable", "object_id": "12"})

    def test_comma_decimal_and_empty_net(self):
        arc = next(o for o in self.objs if o["kind"] == "arc")
        self.assertAlmostEqual(arc["cx"], -1082.93)
        self.assertIsNone(arc["net"])

    def test_text_keeps_pipes(self):
        txt = next(o for o in self.objs if o["kind"] == "text")
        self.assertEqual(txt["text"], "R1|odd text")

    def test_every_tag_has_a_kind(self):
        self.assertEqual(set(d._FIELDS), set(d.PCB_KINDS.values()))


class PcbFilter(unittest.TestCase):
    def setUp(self):
        _, self.objs = d.parse_pcb_dump(PCB_DUMP)

    def test_layer_case_insensitive(self):
        got = d.filter_pcb(self.objs, layers="mid layer 1")
        self.assertEqual([o["net"] for o in got], ["3v3", "SWDIO"])

    def test_via_matches_span_layer(self):
        got = d.filter_pcb(self.objs, layers="Bottom Layer")
        self.assertEqual([o["kind"] for o in got], ["via"])

    def test_net_prefix(self):
        got = d.filter_pcb(self.objs, net="Net", net_how="prefix")
        self.assertEqual(len(got), 2)

    def test_window_hits_bbox_not_just_endpoints(self):
        # the Mid1 3v3 track runs x=50, y 50..400 - window crosses its middle
        got = d.filter_pcb(self.objs, layers="Mid Layer 1", window=(40, 200, 60, 210))
        self.assertEqual([o["net"] for o in got], ["3v3"])

    def test_window_arc_uses_radius(self):
        got = d.filter_pcb(self.objs, window=(-1040, -10, -1030, 10))
        self.assertEqual([o["kind"] for o in got], ["arc"])

    def test_window_drops_objects_without_geometry(self):
        got = d.filter_pcb(self.objs, window=(-1e6, -1e6, 1e6, 1e6))
        self.assertNotIn("connection", {o["kind"] for o in got})


class PcbSummary(unittest.TestCase):
    def test_board_audit_numbers(self):
        _, objs = d.parse_pcb_dump(PCB_DUMP)
        s = d.summarise_pcb(objs)
        self.assertEqual(s["counts"]["track"], {"Mid Layer 1": 2, "Top Layer": 1})
        self.assertEqual(s["nets_by_layer"]["Mid Layer 1"], ["3v3", "SWDIO"])
        self.assertEqual(s["airlines_by_net"], {"5v0": 1, "NetR19_2": 2})
        self.assertEqual(s["unrouted_connections"], 3)
        self.assertEqual(s["counts"]["via"], {"Top Layer-Bottom Layer": 1})


NETLIST_DUMP = "\r\n".join([
    r"PRJ|C:\x\FE.PrjPcb",
    "N|Vmon", "P|U1|13", "P|C26|1", "P|TP_Vmon|1", "P|R25|2",
    "N|TMC_RESET", "P|J1|8", "P|U1|21",
    "N|NetR19_2", "P|R19|2",
    "N|PADS", "P|U2|10", "P|U2|9", "P|U2|A1",
    "V|Net NetR19_2 has only one pin (Pin R19-2)",
    "V|Off grid Pin U7-3 at 1234,5678",
])


class CompiledNetlist(unittest.TestCase):
    def setUp(self):
        self.prj, self.nets, self.viol = d.parse_compiled_netlist(NETLIST_DUMP)

    def test_parse(self):
        self.assertEqual(self.prj, r"C:\x\FE.PrjPcb")
        self.assertEqual(len(self.nets), 4)
        self.assertEqual(len(self.viol), 2)

    def test_pins_sorted_naturally(self):
        self.assertEqual(self.nets[0]["pins"], ["C26.1", "R25.2", "TP_Vmon.1", "U1.13"])
        self.assertEqual(self.nets[3]["pins"], ["U2.9", "U2.10", "U2.A1"])

    def test_shared_filters_apply(self):
        got = schdoc_file.filter_nets(self.nets, has_designator="U1")
        self.assertEqual([n["name"] for n in got], ["Vmon", "TMC_RESET"])
        got = schdoc_file.filter_nets(self.nets, single_pin_only=True)
        self.assertEqual([n["name"] for n in got], ["NetR19_2"])
        got = schdoc_file.filter_nets(self.nets, net="Vmon", only_pins_of="U1")
        self.assertEqual(got, [{"name": "Vmon", "count": 4, "pins": ["U1.13"]}])


class BridgeWiring(unittest.TestCase):
    """The Python tools, the Pascal dispatcher and the focus skip-list must agree."""

    def test_commands_dispatched_and_self_focusing(self):
        api = (SERVER / "AltiumScript" / "Altium_API.pas").read_text(encoding="utf-8")
        other = (SERVER / "AltiumScript" / "other_utils.pas").read_text(encoding="utf-8")
        for cmd in ("pcb_query", "compiled_netlist"):
            self.assertIn(f"'{cmd}':", api)
            self.assertIn(f"(CommandName = '{cmd}')", other)


if __name__ == "__main__":
    unittest.main()
