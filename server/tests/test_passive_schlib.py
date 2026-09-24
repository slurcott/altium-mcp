"""Offline tests for passive_schlib (the build_passive_schlib tool's non-Altium parts).

    python -m unittest server/tests/test_passive_schlib.py -v
"""
import sys
import tempfile
import unittest
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
import passive_schlib as PS  # noqa: E402

FIXTURE = SERVER / "tests" / "fixtures" / "std_0402_trial.SchLib"   # built by SCRIPT, 3 parts


class Spec(unittest.TestCase):
    def test_spec_line(self):
        line = PS.spec_line("RES 0402 1kΩ 1%", "desc", "1k", [("Value", "1k"), ("Tolerance", "1%")])
        self.assertEqual(line, "RES 0402 1kΩ 1%|desc|1k|Value=1k|Tolerance=1%")

    def test_separator_in_field_refused(self):
        with self.assertRaises(ValueError):
            PS.spec_line("a|b", "", "", [])
        with self.assertRaises(ValueError):
            PS.spec_line("a", "", "", [("x=y", "1")])

    def test_spec_file_keeps_unicode(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "spec.txt"
            PS.write_spec(["RES 0402 1kΩ 1%|x|1k|Max Operating Temperature=155°C"], p)
            raw = p.read_bytes()
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))           # BOM for TStringList
            self.assertIn("155°C", raw.decode("utf-8-sig"))

    def test_render_script_fills_paths(self):
        s = PS.render_script(r"C:\x\lib.SchLib", "RESC0402(1005)_L")
        self.assertIn(r"'C:\x\lib.SchLib'", s)
        self.assertEqual(s.count("'RESC0402(1005)_L'"), 2)
        for placeholder in ("{SCHLIB}", "{FOOTPRINT}", "{SPEC}"):
            self.assertNotIn(placeholder, s)
        with self.assertRaises(ValueError):
            PS.render_script("C:\\it's.SchLib", "X")


class Verify(unittest.TestCase):
    def test_names_from_built_fixture(self):
        self.assertEqual(sorted(PS.schlib_component_names(FIXTURE)),
                         sorted(["RES 0402 10Ω 1%", "RES 0402 1kΩ 1%", "RES 0402 100kΩ 1%"]))

    def test_verify_ok_and_missing(self):
        good = PS.verify(FIXTURE, ["RES 0402 10Ω 1%", "RES 0402 1kΩ 1%", "RES 0402 100kΩ 1%"],
                         "RESC0402(1005)_L", r"RMCF0402FT\w+")
        self.assertTrue(good["ok"])
        self.assertEqual(good["mpn_hits"], 3)
        bad = PS.verify(FIXTURE, ["RES 0402 10Ω 1%", "RES 0402 4.7kΩ 1%"], "RESC0402(1005)_L")
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["missing"], ["RES 0402 4.7kΩ 1%"])

    def test_prepare_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "a.SchLib"
            PS.prepare(FIXTURE, out)
            with self.assertRaises(FileExistsError):
                PS.prepare(FIXTURE, out)


if __name__ == "__main__":
    unittest.main()



class LongNameTest(unittest.TestCase):
    def test_verify_long_names_truncated_by_ole(self):
        # "CAP 0805 100nF 50V X7R flex-term" is 32 chars; OLE storage names keep 31
        names = ["CAP 0805 100nF 50V X7R", "CAP 0805 100nF 50V X7R flex-term", "CAP 0805 1µF 50V X7R",
                 "CAP 0805 2.2µF 50V X7R", "CAP 0805 4.7µF 50V X7R", "CAP 0805 10µF 10V X7R"]
        r = PS.verify(SERVER / "tests" / "fixtures" / "std_cap_0805_longname.SchLib", names,
                      "CAPC0805(2012)145_L")
        self.assertTrue(r["ok"], r)
