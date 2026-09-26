"""Offline tests for project_netlist (whole-project netlist across the hierarchy).

Synthetic sheets only. Real-board validation (2026-09-26): 0 grouping
differences against the IPC-2581 netlists of two released boards, and the
keypad hierarchy (18 instances incl. 13 channels) resolves with no warnings.
"""
import importlib.util
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import project_netlist as P

_spec = importlib.util.spec_from_file_location(
    "altium_netlist", Path(__file__).resolve().parents[2] / "dev" / "netlist.py")
NL = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(NL)


def cl(pins=(), **names):
    """A cluster: cl(["R1.1"], power="GND", label="X", port="Y", entry="7|Y")."""
    prio = {"power": 0, "port": 1, "label": 2, "entry": 3}
    ns = []
    for kind, v in names.items():
        for text in (v if isinstance(v, (list, tuple)) else [v]):
            ns.append((prio[kind], text, kind))
    return {"names": sorted(ns), "pins": set(pins)}


def sym(idx, des, file, *entries):
    return {"idx": idx, "designator": des, "file": file, "entries": [(0, 0, e) for e in entries]}


def by_pin(result):
    return {p: n for n in result["nets"] for p in n["pins"]}


class HierarchyTest(unittest.TestCase):
    def setUp(self):
        # Top: connector J1.1 -> entry IN on child symbol A; J1.2 on GND power
        # Child "Ch.SchDoc": port IN -> R1.1, R1.2 on GND power, label LOCAL on R2.1
        self.sheets = {
            "Top.SchDoc": {"clusters": [cl(["J1.1"], entry="10|IN"), cl(["J1.2"], power="GND")],
                           "symbols": [sym(10, "A", "Ch.SchDoc", "IN")]},
            "Ch.SchDoc": {"clusters": [cl(["R1.1"], port="IN"), cl(["R1.2"], power="GND"),
                                       cl(["R2.1"], label="LOCAL")],
                          "symbols": []},
        }

    def test_entry_port_and_power(self):
        r = P.merge(self.sheets, mode=0)
        self.assertEqual(r["mode"], "hierarchical")
        self.assertEqual(r["top"], ["Top.SchDoc"])
        pins = by_pin(r)
        self.assertEqual(pins["J1.1"]["pins"], ["J1.1", "R1.1"])
        self.assertEqual(pins["J1.2"]["pins"], ["J1.2", "R1.2"])
        self.assertEqual(pins["J1.2"]["name"], "GND")
        self.assertEqual(r["warnings"], [])

    def test_channels_get_room_suffix_and_stay_separate(self):
        self.sheets["Top.SchDoc"]["clusters"].append(cl(["J1.3"], entry="11|IN"))
        self.sheets["Top.SchDoc"]["symbols"].append(sym(11, "A", "Ch.SchDoc", "IN"))   # same designator twice
        r = P.merge(self.sheets)
        pins = by_pin(r)
        self.assertEqual(pins["J1.1"]["pins"], ["J1.1", "R1_A1.1"])
        self.assertEqual(pins["J1.3"]["pins"], ["J1.3", "R1_A2.1"])
        # labels are local per instance: two separate LOCAL nets
        self.assertNotEqual(pins["R2_A1.1"]["pins"], pins["R2_A2.1"]["pins"])
        # power is global across channels
        self.assertEqual(pins["R1_A1.2"]["pins"], ["J1.2", "R1_A1.2", "R1_A2.2"])

    def test_missing_port_warns(self):
        self.sheets["Top.SchDoc"]["symbols"][0]["entries"].append((0, 0, "OUT"))
        r = P.merge(self.sheets)
        self.assertTrue(any("entry 'OUT'" in w for w in r["warnings"]))

    def test_names_case_insensitive(self):
        self.sheets["Ch.SchDoc"]["clusters"].append(cl(["R3.1"], label="local"))
        pins = by_pin(P.merge(self.sheets))
        self.assertEqual(pins["R2.1"]["pins"], ["R2.1", "R3.1"])

    def test_label_named_like_power_joins_power(self):
        self.sheets["Ch.SchDoc"]["clusters"].append(cl(["R4.1"], label="GND"))
        pins = by_pin(P.merge(self.sheets))
        self.assertIn("R4.1", pins["J1.2"]["pins"])

    def test_flat_mode_ports_global(self):
        sheets = {"A.SchDoc": {"clusters": [cl(["R1.1"], port="X")], "symbols": []},
                  "B.SchDoc": {"clusters": [cl(["R2.1"], port="X")], "symbols": []}}
        r = P.merge(sheets, mode=0)
        self.assertEqual(r["mode"], "flat")
        self.assertEqual(by_pin(r)["R1.1"]["pins"], ["R1.1", "R2.1"])

    def test_diff(self):
        old = [{"pins": ["A.1", "B.1"]}, {"pins": ["C.1"]}]
        new = [{"pins": ["A.1"]}, {"pins": ["B.1", "C.1"]}]
        self.assertEqual([d["pin"] for d in P.diff(old, new)], ["A.1", "B.1", "C.1"])
        self.assertEqual(P.diff(old, old), [])


class ClustersTest(unittest.TestCase):
    """dev/netlist.clusters(): the per-sheet layer project_netlist builds on."""

    def test_vertical_port_attaches_at_far_end(self):
        # vertical port (style 4) at (100,0), width 300 -> its far end (100,300) meets the wire
        recs = {"WIRE": [[100, 300, 100, 500]], "PIN": [["R1", "1", 100, 500]],
                "PORT": [[100, 0, "V", 0, 4, 300]]}
        c = [c for c in NL.clusters(recs) if c["pins"]]
        self.assertEqual(c[0]["names"], [(1, "V", "port")])

    def test_port_abutting_entry_without_wire(self):
        recs = {"PORT": [[0, 0, "P", 0, 0, 200]], "ENTRY": [[200, 0, "9|G"]]}
        named = [c["names"] for c in NL.clusters(recs)]
        self.assertIn([(1, "P", "port"), (3, "9|G", "entry")], named)

    def test_build_unchanged_ignores_entries(self):
        recs = {"WIRE": [[0, 0, 100, 0]], "PIN": [["R1", "1", 0, 0], ["R2", "1", 100, 0]],
                "NLBL": [[50, 0, "N"]]}
        self.assertEqual(NL.build(recs)["named"], {"N": ["R1.1", "R2.1"]})


if __name__ == "__main__":
    unittest.main()
