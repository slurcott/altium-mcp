"""Offline tests for passives.py (part-number decoding) and
dev/bom_consolidate.py (grouping + upgrade-only substitution). No Altium.

    python -m unittest server/tests/test_bom_consolidate.py -v

Every part number below came off a real BOM.
"""
import sys
import unittest
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
sys.path.insert(0, str(SERVER.parent / "dev"))

import passives as P  # noqa: E402
import bom_consolidate as B  # noqa: E402


def dec(mpn):
    d = P.decode_mpn(mpn)
    assert d is not None, mpn
    return d


class Decode(unittest.TestCase):
    CAPS = [  # mpn, package, value (F), volts, dielectric, tolerance %, aec
        ("06035C104KAZ2A", "0603", 100e-9, 50, "X7R", 10, False),
        ("12066C226KAT2A", "1206", 22e-6, 6.3, "X7R", 10, False),
        ("CC1206KKX5R5BB226", "1206", 22e-6, 6.3, "X5R", 10, False),
        ("C0603C105K4RACTU", "0603", 1e-6, 16, "X7R", 10, False),
        ("C0402C101K5RACAUTO", "0402", 100e-12, 50, "X7R", 10, True),
        ("C0603C689D5GACAUTO", "0603", 6.8e-12, 50, "C0G", None, True),
        ("CGA3E2X7R1H104K080AA", "0603", 100e-9, 50, "X7R", 10, True),
        ("CGA3E2NP02A090D080AA", "0603", 9e-12, 100, "C0G", None, True),
        ("GRM21BZ71H475ME15K", "0805", 4.7e-6, 50, "X7R", 20, False),
        ("GRM21BC81H475KE11K", "0805", 4.7e-6, 50, "X6S", 10, False),
        ("GCM32ER71C226ME19K", "1210", 22e-6, 16, "X7R", 20, True),
        ("UMK316B7105KLHT", "1206", 1e-6, 50, "X7R", 10, False),
        ("UMK212BB7225KG-T", "0805", 2.2e-6, 50, "X7R", 10, False),
        ("UMR325AC7106KM-P", "1210", 10e-6, 50, "X7S", 10, False),
    ]
    RES = [  # mpn, package, ohms, tolerance %
        ("RC0603FR-131KL", "0603", 1e3, 1),
        ("AC0603JR-0720KL", "0603", 20e3, 5),
        ("RC0402JR-100RL", "0402", 0, 5),          # 10" reel, 0 ohm - NOT 100 ohm
        ("RMCF0603JG4K70", "0603", 4.7e3, 5),
        ("RMCF0402JT4R70", "0402", 4.7, 5),
        ("CRCW060333K0JNEC", "0603", 33e3, 5),
        ("CRGCQ0805J47R", "0805", 47, 5),
        ("CRGP0805F1M0", "0805", 1e6, 1),
        ("CPF0603F9K09C1", "0603", 9.09e3, 1),
        ("MCR03FZPJ472", "0603", 4.7e3, 5),
        ("MCR03FZPFX2492", "0603", 24.9e3, 1),
        ("RK73B1JLTD104J", "0603", 100e3, 5),
        ("RN732ATTD9092F100", "0805", 90.9e3, 1),
    ]

    def test_caps(self):
        for mpn, pkg, val, v, diel, tol, aec in self.CAPS:
            with self.subTest(mpn=mpn):
                d = dec(mpn)
                self.assertEqual((d["type"], d["package"], d["voltage"], d["dielectric"],
                                  d["tolerance"], d["aec"]), ("C", pkg, v, diel, tol, aec))
                self.assertAlmostEqual(d["value"] / val, 1, places=6)

    def test_resistors(self):
        for mpn, pkg, ohms, tol in self.RES:
            with self.subTest(mpn=mpn):
                d = dec(mpn)
                self.assertEqual((d["type"], d["package"], d["tolerance"]), ("R", pkg, tol))
                self.assertAlmostEqual(d["value"], ohms, places=6)

    def test_unknown_series_is_none_not_a_guess(self):
        for mpn in ("EMZR350ARA561MJA0G", "KRL2012E-C-R003-F-T5", "", None):
            self.assertIsNone(P.decode_mpn(mpn))

    def test_eia_multiplier_digits(self):
        self.assertEqual(P.code3("104"), 100000)
        self.assertAlmostEqual(P.code3("689"), 6.8)
        self.assertAlmostEqual(P.code3("109"), 1.0)
        self.assertAlmostEqual(P.code3("4R7"), 4.7)


def line(board, des, mpn, desc="", name="", supplier="DigiKey", price=0.1):
    return {"board": board, "name": name, "description": desc, "designators": des.split(","),
            "quantity": len(des.split(",")), "manufacturer": "", "mpn": mpn, "supplier": supplier,
            "spn": "", "unit_price": price, "lifecycle": "", "from_sheet": None}


def group(res, pkg, value):
    return next(g for g in res["groups"] if g["package"] == pkg and g["value"] == value)


class Consolidate(unittest.TestCase):
    def test_upgrade_wins_across_boards(self):
        res = B.consolidate([line("FE", "C1,C4", "06035C104KAZ2A"),
                             line("FE", "C17", "CGA3E2X7R1H104K080AA"),
                             line("TMC", "C14,C18", "06035C104KAZ2A")])
        g = group(res, "0603", "100nF")
        self.assertEqual((g["status"], g["recommend"], g["total_quantity"]),
                         ("merge", "CGA3E2X7R1H104K080AA", 5))
        self.assertEqual(g["boards"], ["FE", "TMC"])

    def test_never_downgrade_dielectric_or_voltage(self):
        # 50 V X7S vs 25 V X7R: each is worse on one axis -> no merge
        res = B.consolidate([line("T", "C9", "UMR325AC7106KM-P"), line("T", "C15", "12103C106KAT2A")])
        g = group(res, "1210", "10uF")
        self.assertEqual(g["status"], "needs-new-part")
        self.assertIn(">= 50 V", g["reason"])
        self.assertIn("X7R or better", g["reason"])

    def test_aec_member_forces_aec_substitute(self):
        res = B.consolidate([line("F", "C15", "GRM21BZ71H475ME15K"),
                             line("F", "C32", "CGA4J3X7R1C475K125AB")])
        g = group(res, "0805", "4.7uF")
        self.assertEqual(g["status"], "needs-new-part")
        self.assertIn("AEC-Q200", g["reason"])

    def test_precision_and_shunt_stay_out_of_general_pool(self):
        res = B.consolidate([line("F", "R5", "RC0603FR-131KL"),
                             line("F", "R16", "RG1608N-102-W-T5",
                                  desc="Automotive Metal Thin Film Chip Resistor, 0603, 1kΩ, 0.05%, 10ppm/°C, 0.1W")])
        pools = sorted(g["pool"] for g in res["groups"] if g["value"] == "1k")
        self.assertEqual(pools, ["general", "precision"])

    def test_text_that_disagrees_with_part_is_flagged(self):
        res = B.consolidate([line("F", "C6", "CC1206KKX5R5BB226", desc="CAP CER 22UF 6.3V X7R 1206"),
                             line("T", "R2", "RMCF0603JG4K70",
                                  desc="Chip Resistor, 4.7 KOhm, +/- 1%, 0.1 W, 0603")])
        flags = {p["mpn"]: p["flags"] for g in res["groups"] for p in g["parts"]}
        self.assertTrue(any("X5R" in f for f in flags["CC1206KKX5R5BB226"]))
        self.assertTrue(any("5%" in f for f in flags["RMCF0603JG4K70"]))

    def test_unknown_attribute_is_merge_check_not_merge(self):
        res = B.consolidate([line("F", "C33", "C0603C689D5GACAUTO"),
                             line("F", "C40", "CGA3E2C0G1H689D080AA")])
        g = group(res, "0603", "6.8pF")
        self.assertNotEqual(g["status"], "merge")

    def test_non_passives_are_set_aside(self):
        res = B.consolidate([line("F", "U1", "PIC32CM1216JH01032-E/PT", desc="MCU")])
        self.assertEqual(len(res["other"]), 1)
        self.assertEqual(res["groups"], [])


class LibraryPolicies(unittest.TestCase):
    def test_rmcf_part_number_builder(self):
        cases = {("0603", 4700): "RMCF0603FT4K70", ("0603", 10e3): "RMCF0603FT10K0",
                 ("0603", 33): "RMCF0603FT33R0", ("0603", 120): "RMCF0603FT120R",
                 ("0603", 100e3): "RMCF0603FT100K", ("0603", 24.9e3): "RMCF0603FT24K9",
                 ("0402", 4.7): "RMCF0402FT4R70", ("0805", 1e6): "RMCF0805FT1M00"}
        for (pkg, ohms), mpn in cases.items():
            self.assertEqual(P.rmcf_1pct(pkg, ohms), mpn)
            self.assertAlmostEqual(P.decode_mpn(mpn)["value"], ohms, places=6)
        self.assertIsNone(P.rmcf_1pct("0603", 0))          # jumper
        self.assertIsNone(P.rmcf_1pct("1225", 47))         # outside the series

    def test_bulk_cap_tolerance_ignored(self):
        lines = [line("F", "C6", "CC1206KKX5R5BB226"), line("T", "C16", "GRM31CR71A226ME15L")]
        self.assertEqual(group(B.consolidate(lines), "1206", "22uF")["status"], "needs-new-part")
        g = group(B.consolidate(lines, bulk_cap_min=1e-6), "1206", "22uF")
        self.assertEqual((g["status"], g["recommend"]), ("merge", "GRM31CR71A226ME15L"))

    def test_bulk_rule_leaves_small_caps_strict(self):
        # 100 nF is below the bulk threshold: tolerance still counts
        lines = [line("F", "C1", "C0805X104J5RACTU"), line("F", "C2", "06035C104KAZ2A")]
        res = B.consolidate(lines, bulk_cap_min=1e-6)
        self.assertTrue(all(g["status"] == "single" for g in res["groups"]))  # different packages

    def test_resistor_standard_moves_5pct_to_1pct(self):
        res = B.consolidate([line("T", "R2,R3", "RMCF0603JG4K70"), line("F", "R10", "MCR03FZPJ472")],
                            resistor_std_tol=1)
        g = group(res, "0603", "4.7k")
        self.assertEqual((g["status"], g["recommend"]), ("to-standard", "RMCF0603FT4K70"))

    def test_resistor_standard_exemptions(self):
        res = B.consolidate([line("F", "R1", "RC0402JR-100RL"),                 # 0 ohm jumper
                             line("F", "R5", "RC0603FR-131KL")],                # already 1 %
                            resistor_std_tol=1)
        self.assertTrue(all(g["status"] == "single" for g in res["groups"]))

    def test_resistor_standard_respects_power(self):
        # 0.33 W part: RMCF0805 is only 0.125 W, so it stays a special
        res = B.consolidate([line("F", "R26", "CRGCQ0805J47R", desc="47 ohm 0.33W 5% 0805")],
                            resistor_std_tol=1)
        g = group(res, "0805", "47R")
        self.assertEqual(g["status"], "special")       # chosen for its rating - left alone
        self.assertIsNone(g["recommend"])


class SheetsAreTheTruth(unittest.TestCase):
    """Regression: a BOM export older than the sheets must not decide what is
    placed (OV4F FE R21 was 47 ohm in the export, a 0 ohm jumper on the sheet)."""

    def run_reconcile(self, bom, comps):
        from unittest import mock
        fake = [({"designator": d, "comment": cm, "description": ""}, {}, "", "s.SchDoc") for d, cm in comps]
        with mock.patch.object(B, "_sheet_parts", return_value=fake):
            return B.reconcile(bom, ["s.SchDoc"], "FE")

    def test_changed_part_comes_from_the_sheet(self):
        bom = [dict(line("FE", "R21", "CRGCQ0805J47R", name="CRGCQ0805J47R"))]
        lines, changes = self.run_reconcile(bom, [("R21", "HCJ0805ZT0R00")])
        self.assertEqual(lines[0]["mpn"], "HCJ0805ZT0R00")
        self.assertEqual(changes, [{"board": "FE", "designator": "R21", "was": "CRGCQ0805J47R",
                                    "now": "HCJ0805ZT0R00", "sheet": "s.SchDoc"}])

    def test_unchanged_generic_name_keeps_export_part_number(self):
        bom = [line("FE", "R11,R12", "CRCW060333K0JNEC", name="33K 5% 0603(1608)")]
        lines, changes = self.run_reconcile(bom, [("R11", "33K 5% 0603(1608)"), ("R12", "33K 5% 0603(1608)")])
        self.assertEqual({ln["mpn"] for ln in lines}, {"CRCW060333K0JNEC"})
        self.assertEqual(changes, [])

    def test_removed_and_multichannel(self):
        bom = [line("FE", "R9", "RMCF0603JG120R"), line("FE", "C3_HBU,C3_HBV,C3_HBW", "C0402H102J5GACT500")]
        lines, changes = self.run_reconcile(bom, [("C3", "C0402H102J5GACT500")])
        self.assertEqual(lines[0]["designators"], ["C3_HBU", "C3_HBV", "C3_HBW"])
        self.assertEqual([(c["designator"], c["now"]) for c in changes], [("R9", None)])

    def test_new_decoders(self):
        d = dec("AC0603FR-7W10KL")          # Yageo double-power on 7" reel
        self.assertEqual((d["value"], d["tolerance"], d["power"], d["aec"]), (10e3, 1, 0.2, True))
        self.assertEqual(dec("HCJ0805ZT0R00")["value"], 0.0)
        d = dec("RNCP0603FTD2K49")
        self.assertEqual((d["value"], d["tolerance"], d["tech"]), (2490.0, 1, "thin"))


if __name__ == "__main__":
    unittest.main()
