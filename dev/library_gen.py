"""Generate the standard passive library as tables for Altium 365 Component
Editor batch mode (paste from Excel). No Altium needed.

    python dev/library_gen.py --out <folder>

Writes one CSV per batch (one symbol + one footprint per batch, one row per
value): resistors_0402.csv, resistors_0603.csv, resistors_0805.csv,
capacitors.csv, placeholders.csv, plus library_summary.json.

Rules (the library policy):
  * Resistors: Stackpole RMCF, 1 %, AEC-Q200, E24 from 1 ohm to 1 Mohm, plus a
    0 ohm jumper per size. Alternates: Yageo AC (AEC-Q200) and Vishay CRCW e3
    (AEC-Q200), both 1 %. Part numbers are built from each series' naming rule
    (server/passives.py) and every one round-trips through the decoder.
  * Capacitors: a curated list (CAPACITORS below) - MLCC availability does not
    follow a naming rule, so each part number is individually verified.
  * Placeholders: one "unassigned" part per type and size, for values not yet
    in the library; bom_consolidate resolves or flags them.

COLUMNS maps each output column to a field. It is the only thing to change when
the workspace's batch-mode columns turn out to be named differently.
"""
import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import passives as P  # noqa: E402

# Output column -> internal field. Rename the left side to match the workspace.
COLUMNS = [
    ("Name", "name"),
    ("Description", "description"),
    ("Comment", "comment"),
    ("Value", "value"),
    ("Case/Package", "package"),
    ("Mounting Technology", "mount"),
    ("Pins", "pins"),
    ("Case Code (Metric)", "metric"),
    ("Tolerance", "tolerance"),
    ("Power", "power"),
    ("Voltage Rating", "voltage"),
    ("Capacitor Type", "captype"),
    ("Dielectric", "dielectric"),
    ("Temperature Coefficient", "tcr"),
    ("Min Operating Temperature", "tmin"),
    ("Max Operating Temperature", "tmax"),
    ("Ratings", "ratings"),
    ("Symbol", "symbol"),
    ("Footprint", "footprint"),
    ("Manufacturer 1", "mfr1"),
    ("Manufacturer Part Number 1", "mpn1"),
    ("Manufacturer 2", "mfr2"),
    ("Manufacturer Part Number 2", "mpn2"),
    ("Manufacturer 3", "mfr3"),
    ("Manufacturer Part Number 3", "mpn3"),
    ("Library Status", "status"),
    ("Verified", "verified"),
    ("Notes", "note"),
]

METRIC = {"0402": "1005", "0603": "1608", "0805": "2012", "1206": "3216", "1210": "3225"}
# RMCF datasheet (Stackpole): power @70 C, max working voltage, max body height (mm)
RMCF = {"0402": (0.0625, 50, 0.40), "0603": (0.1, 75, 0.55), "0805": (0.125, 150, 0.65),
        "1206": (0.25, 200, 0.70)}
JUMPER_AMPS = {"0402": 1, "0603": 1, "0805": 2, "1206": 2}

# Standard footprints: IPC least density (_L), Steve 2026-09-24 - already in the
# workspace and proven on the B_1 boards; board space is tight. Resistor names
# confirmed in the workspace (RESC0603(1608)_L = item PCC-007-0006-1; 4 duplicates
# exist - use that one). Capacitor _L names and RESC0805 still to confirm by
# searching the workspace; None = held back.
RES_FOOTPRINT = {"0402": "RESC0402(1005)_L", "0603": "RESC0603(1608)_L", "0805": "RESC0805(2012)_L",
                 "1206": None}
# Cap footprints: tallest _L variant per size (pads identical; height covers every part).
# Items PCC-006-0002/0011/0012. 1206/1210 built 2026-09-25 (IPC-7351B least, calibrated to
# the 0805 _L: Z rounded up, pad length 0.76 mm) - no standalone item existed in the workspace.
CAP_FOOTPRINT = {"0402": "CAPC0402(1005)60_L", "0603": "CAPC0603(1608)100_L",
                 "0805": "CAPC0805(2012)145_L", "1206": "CAPC1206(3216)190_L",
                 "1210": "CAPC1210(3225)270_L"}
RES_SYMBOL = "SYM-007-0001-2"      # RES-2, the Resistor template symbol (batch grid 2026-09-24)
CAP_SYMBOL = "SYM-006-0000-2"      # workspace generic non-polarised capacitor (confirmed)


def fmt_watts(w):
    """Workspace style: 63mW, 100mW, 125mW, 250mW, 1W."""
    return f"{int(w * 1000 + 0.5)}mW" if w < 1 else f"{w:g}W"   # 62.5 -> 63, as Altium writes it

# Curated MLCCs: (package, value text, farads, volts, dielectric, tol %, mpn, mfr,
# verified-where). Every part here decodes to these attributes (tested) and was
# found at a distributor. Tolerance is don't-care for class II >= 1 uF (policy).
K, TDK, MUR = "KEMET", "TDK", "Murata"
CAPACITORS = [
    # C0G - small signal, timing, filters
    ("0402", "10pF", 10e-12, 50, "C0G", 5, "C0402C100J5GACAUTO", K, "DigiKey"),
    ("0402", "22pF", 22e-12, 50, "C0G", 5, "C0402C220J5GACAUTO", K, "DigiKey"),
    ("0402", "100pF", 100e-12, 200, "C0G", 5, "C0402C101J2GACAUTO", K, "DigiKey"),
    ("0402", "1nF", 1e-9, 50, "C0G", 5, "C0402C102J5GACAUTO", K, "DigiKey"),
    ("0603", "6.8pF", 6.8e-12, 50, "C0G", None, "C0603C689D5GACAUTO", K, "in OV4F design"),
    ("0603", "9pF", 9e-12, 100, "C0G", None, "CGA3E2NP02A090D080AA", TDK, "in OV4F design"),
    ("0603", "10pF", 10e-12, 50, "C0G", 5, "C0603C100J5GACAUTO", K, "DigiKey"),
    ("0603", "22pF", 22e-12, 50, "C0G", 5, "C0603C220J5GACAUTO", K, "distributor listing"),
    ("0603", "100pF", 100e-12, 100, "C0G", 5, "C0603C101J1GACAUTO", K, "TME"),
    ("0603", "1nF", 1e-9, 50, "C0G", 5, "C0603C102J5GACAUTO", K, "TME"),
    ("0603", "100pF", 100e-12, 250, "C0G", 5, "C0603C101JAGACAUTO", K, "in OV4F design"),
    # X7R - decoupling and bulk
    ("0402", "1nF", 1e-9, 50, "X7R", 10, "C0402C102K5RACAUTO", K, "DigiKey"),
    ("0402", "10nF", 10e-9, 50, "X7R", 10, "C0402C103K5RACAUTO", K, "DigiKey"),
    ("0402", "100nF", 100e-9, 16, "X7R", 10, "C0402C104K4RACAUTO", K, "DigiKey"),
    ("0603", "1nF", 1e-9, 50, "X7R", 10, "C0603C102K5RACAUTO", K, "distributor listing"),
    ("0603", "10nF", 10e-9, 50, "X7R", 10, "C0603C103K5RACAUTO", K, "DigiKey"),
    ("0603", "47nF", 47e-9, 50, "X7R", 10, "C0603C473K5RACAUTO", K, "DigiKey"),
    ("0603", "100nF", 100e-9, 50, "X7R", 10, "CGA3E2X7R1H104K080AA", TDK, "in OV4F design",
     K, "C0603C104K5RACAUTO"),
    ("0603", "470nF", 470e-9, 16, "X7R", 10, "C0603C474K4RACAUTO", K, "Arrow"),
    ("0603", "470nF", 470e-9, 25, "X7R", 10, "C0603C474K3RACTU", K, "in OV4F design"),
    ("0603", "1µF", 1e-6, 16, "X7R", 10, "C0603C105K4RACAUTO", K, "Octopart"),
    ("0603", "10µF", 10e-6, 10, "X5R", 20, "C0603C106M8PACTU", K, "in OV4F design"),  # X5R: 85 C
    ("0805", "100nF", 100e-9, 50, "X7R", 10, "C0805C104K5RACAUTO", K, "DigiKey"),
    ("0805", "100nF", 100e-9, 50, "X7R", 5, "C0805X104J5RACTU", K, "in OV4F design"),  # flex term
    ("0805", "1µF", 1e-6, 50, "X7R", 10, "C0805C105K5RACAUTO", K, "DigiKey"),
    ("0805", "2.2µF", 2.2e-6, 50, "X7R", 10, "C0805C225K5RACAUTO", K, "DigiKey"),
    ("0805", "4.7µF", 4.7e-6, 50, "X7R", 10, "CGA4J1X7R1H475K125AE", TDK, "TTI"),
    ("0805", "10µF", 10e-6, 10, "X7R", 10, "C0805C106K8RACAUTO", K, "Mouser"),
    ("1206", "1µF", 1e-6, 50, "X7R", 10, "C1206C105K5RACAUTO", K, "DigiKey"),
    ("1206", "2.2µF", 2.2e-6, 50, "X7R", 10, "C1206C225K5RACAUTO", K, "TTI"),
    ("1206", "22µF", 22e-6, 10, "X7R", 20, "GRM31CR71A226ME15L", MUR, "in OV4F design"),
    ("1210", "10µF", 10e-6, 16, "X7R", 10, "C1210C106K4RACAUTO", K, "DigiKey"),
    ("1210", "10µF", 10e-6, 75, "X7R", 20, "CGA6P1X7R1N106M250AC", TDK, "DigiKey"),
    ("1210", "22µF", 22e-6, 16, "X7R", 10, "C1210C226K4RACAUTO", K, "DigiKey",
     MUR, "GCM32ER71C226ME19K"),
]


# Deliberate exceptions to the library rules, recorded so nobody "fixes" them later.
CAP_NOTES = {
    "C0603C106M8PACTU": "ACCEPTED X5R (85 C) - 0603 kept for board space (Steve 2026-09-24); "
                        "no 0603 10 uF X7R alternative. Do not swap to 0805 without a layout change.",
    "C0805X104J5RACTU": "Flexible termination - use where board flex / cracking matters.",
    "C0603C101JAGACAUTO": "250 V C0G - chosen where the voltage margin matters.",
}


def fmt_ohms(v, sign="Ω"):
    if v == 0:
        return "0" + sign
    for unit, s in (("M", 1e6), ("k", 1e3)):
        if v >= s:
            return f"{v / s:g}{unit}{sign}"
    return f"{v:g}{sign}"


def fmt_farads(f):
    for unit, s in (("µF", 1e-6), ("nF", 1e-9), ("pF", 1e-12)):
        if f >= s * 0.999:
            return f"{f / s:g}{unit}"
    return f"{f:g}F"


def tcr(ohms):
    # RMCF: +/-100 ppm 10 ohm - 1 Mohm, +/-200 ppm below 10 ohm (datasheet)
    return "±100ppm/°C" if 10 <= ohms <= 1e6 else "±200ppm/°C"


def resistor_rows(package):
    power, volts, _ = RMCF[package]
    rows = []
    for v in P.e24_values(1.0, 1e6):
        val = fmt_ohms(v)
        mpns = (P.rmcf_1pct(package, v), P.yageo_ac_1pct(package, v), P.vishay_crcw_1pct(package, v))
        for m in mpns:                       # every constructed part number must decode back
            d = P.decode_mpn(m)
            assert d and abs(d["value"] - v) < 1e-6 * max(v, 1) and d["tolerance"] == 1 \
                and d["package"] == package, (m, d)
        rows.append({
            "name": f"RES {package} {val} 1%",
            "description": f"{val} {fmt_watts(power)} 1% {package} ({METRIC[package]} Metric) SMD thick film AEC-Q200",
            "comment": val, "value": val, "package": package, "metric": METRIC[package],
            "tolerance": "1%", "power": fmt_watts(power), "voltage": f"{volts}V", "dielectric": "",
            "mount": "SMT", "pins": "2", "tmin": "-55°C", "tmax": "155°C",
            "tcr": tcr(v), "ratings": "AEC-Q200", "symbol": RES_SYMBOL,
            "footprint": fp_status(RES_FOOTPRINT[package])[0],
            "mfr1": "Stackpole Electronics", "mpn1": mpns[0],
            "mfr2": "YAGEO", "mpn2": mpns[1], "mfr3": "Vishay Dale", "mpn3": mpns[2],
            "status": fp_status(RES_FOOTPRINT[package])[1], "verified": "series rule; sample confirmed",
        })
    rows.append({
        "name": f"RES {package} 0Ω jumper", "description":
            f"0Ω jumper {package} {JUMPER_AMPS[package]}A thick film AEC-Q200",
        "comment": "0Ω", "value": "0Ω", "package": package, "metric": METRIC[package],
        "tolerance": "jumper", "power": "", "voltage": "", "dielectric": "", "tcr": "",
        "ratings": "AEC-Q200", "symbol": RES_SYMBOL, "footprint": fp_status(RES_FOOTPRINT[package])[0],
        "mount": "SMT", "pins": "2",
        "mfr1": "Stackpole Electronics", "mpn1": P.rmcf_jumper(package),
        "mfr2": "", "mpn2": "", "mfr3": "", "mpn3": "",
        "status": fp_status(RES_FOOTPRINT[package])[1], "verified": "distributor",
    })
    return rows


def capacitor_rows():
    rows = []
    for pkg, val, f, volts, diel, tol, mpn, mfr, where, *alt in CAPACITORS:
        d = P.decode_mpn(mpn)
        assert d and d["package"] == pkg and abs(d["value"] - f) < 1e-3 * f \
            and d["voltage"] == volts and d["dielectric"] == diel, (mpn, d)
        class2 = diel != "C0G"
        rows.append({
            "name": f"CAP {pkg} {val} {volts:g}V {diel}" + (" flex-term" if d.get("flex") else ""),
            "description": f"CAP MLCC {val} {volts:g}V {diel} {pkg}"
                           + (" AEC-Q200" if d["aec"] else ""),
            "comment": val, "value": val, "package": pkg, "metric": METRIC[pkg],
            "tolerance": "don't care (class II)" if class2 else (f"{tol:g}%" if tol else "see part"),
            "mount": "SMT", "pins": "2", "captype": "Ceramic",
            "tmin": "-55°C", "tmax": {"X5R": "85°C", "X6S": "105°C", "X7S": "125°C"}.get(diel, "125°C"),
            "power": "", "voltage": f"{volts:g}V", "dielectric": diel, "tcr": "",
            "ratings": "AEC-Q200" if d["aec"] else "", "symbol": CAP_SYMBOL,
            "footprint": fp_status(CAP_FOOTPRINT[pkg])[0], "mfr1": mfr, "mpn1": mpn,
            "mfr2": alt[0] if alt else "", "mpn2": alt[1] if alt else "", "mfr3": "", "mpn3": "",
            "status": fp_status(CAP_FOOTPRINT[pkg])[1], "verified": where, "note": CAP_NOTES.get(mpn, ""),
        })
    return rows


def placeholder_rows():
    rows = []
    for kind, sym, fps in (("RES", RES_SYMBOL, RES_FOOTPRINT), ("CAP", CAP_SYMBOL, CAP_FOOTPRINT)):
        for pkg in fps:
            rows.append({
                "name": f"{kind} {pkg} (unassigned)",
                "description": f"{'Resistor' if kind == 'RES' else 'Capacitor'} {pkg} - value not in "
                               "library yet; set Value (and Voltage/Dielectric for caps)",
                "comment": "=Value", "value": "", "package": pkg, "metric": METRIC[pkg],
                "tolerance": "", "power": "", "voltage": "", "dielectric": "", "tcr": "",
                "ratings": "", "symbol": sym, "footprint": fp_status(fps[pkg])[0], "mount": "SMT", "pins": "2",
                "mfr1": "", "mpn1": "", "mfr2": "", "mpn2": "", "mfr3": "", "mpn3": "",
                "status": "PLACEHOLDER" if fps[pkg] else HOLD, "verified": "",
            })
    return rows


HOLD = "HOLD - footprint not confirmed yet"


def fp_status(fp):
    return (fp or "", "STANDARD" if fp else HOLD)


def write_csv(path, rows):
    # utf-8-sig: Excel opens it with the ohm and micro signs intact
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([c for c, _ in COLUMNS])
        for r in rows:
            w.writerow([r.get(k, "") for _, k in COLUMNS])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--sizes", default="0402,0603,0805")
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    summary = {}
    for pkg in args.sizes.split(","):
        rows = resistor_rows(pkg)
        write_csv(out / f"resistors_{pkg}.csv", rows)
        summary[f"resistors_{pkg}"] = len(rows)
    caps = capacitor_rows()
    if caps:
        write_csv(out / "capacitors.csv", caps)
    summary["capacitors"] = len(caps)
    ph = placeholder_rows()
    write_csv(out / "placeholders.csv", ph)
    summary["placeholders"] = len(ph)
    summary.update(write_batch(out))
    (out / "library_summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(summary))
    return summary



# ---- Altium 365 Component Editor batch grid (seen 2026-09-24) -------------------
# Exact column order of the batch grid; Part Choices are Manufacturer/Part Number
# pairs; models are referenced by ITEM ID, not name. Paste the data rows (no
# header) with the first cell on FolderPath.
FOOTPRINT_ITEM = {("R", "0603"): "PCC-007-0006-1",   # RESC0603(1608)_L (4 duplicates exist)
                  ("R", "0402"): None}               # RESC0402(1005)_L item ID - to find
MFR_NAME = {"Stackpole Electronics": "Stackpole Electronics", "YAGEO": "Yageo Group",
            "Vishay Dale": "Vishay"}
# No "Datasheets" column: the grid only shows it when a selected part has one.
BATCH_COLUMNS = ["FolderPath", "Item ID", "Name", "Type", "Description",
                 "PCBLIB (default)", "SCHLIB", "Case/Package", "Max Operating Temperature",
                 "Min Operating Temperature", "Mounting Technology", "Pins", "Power",
                 "RoHS Compliant", "Tolerance", "Value", "Voltage Rating",
                 "Part Choice 1 Manufacturer", "Part Choice 1 Part Number",
                 "Part Choice 2 Manufacturer", "Part Choice 2 Part Number",
                 "Part Choice 3 Manufacturer", "Part Choice 3 Part Number"]


TSV_SEP, TSV_EOL = chr(9), chr(10)
# Library parts already created in the workspace - never batch them again.
ALREADY_IN_WORKSPACE = {"RES 0603 12kΩ 1%"}         # CMP-009-00177, 2026-09-24 (TMC R20)
# Whole batches already released to the workspace - skipped entirely.
BATCHES_DONE = {"0603", "0402", "0805"}   # 0603: batch-grid paste; 0402: SchLib + Library Importer (2026-09-24)
RES_FOLDER = "Components" + chr(92) + "Resistors"


def batch_rows(package, folder=RES_FOLDER):
    fp = FOOTPRINT_ITEM.get(("R", package))
    if not fp:
        return None
    out = []
    for r in resistor_rows(package):
        if r["value"] == "0Ω":
            continue                                   # jumpers: separate, reviewed by hand
        if r["name"] in ALREADY_IN_WORKSPACE:
            continue
        out.append([folder, "", r["name"], "Resistors", r["description"], fp, RES_SYMBOL,
                    package, r["tmax"], r["tmin"], "SMT", "2", r["power"], "Yes", "1%",
                    r["value"].replace("Ω", ""), r["voltage"],
                    MFR_NAME[r["mfr1"]], r["mpn1"], MFR_NAME[r["mfr2"]], r["mpn2"],
                    MFR_NAME[r["mfr3"]], r["mpn3"]])
    return out


def write_batch(out_dir):
    written = {}
    for pkg in ("0402", "0603", "0805"):
        if pkg in BATCHES_DONE:
            continue
        rows = batch_rows(pkg)
        if rows is None:
            continue
        path = Path(out_dir) / f"batch_resistors_{pkg}.tsv"
        # tab-separated: pastes into grids as cells; header kept on line 1 for checking
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            f.write(TSV_SEP.join(BATCH_COLUMNS) + TSV_EOL)
            for row in rows:
                f.write(TSV_SEP.join(row) + TSV_EOL)
        written[path.name] = len(rows)
    return written


if __name__ == "__main__":
    main()
