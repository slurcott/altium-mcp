"""BOM consolidation: find passives bought under several part numbers, and the
BOM data errors that hide them. Reads Altium BOM exports (.xlsx); no Altium.

    python dev/bom_consolidate.py --bom "FE=path/to/FE.xlsx" --bom "TMC=path/to/TMC.xlsx" \
        [--prefer-supplier DigiKey] [--json out.json]

Rules (upgrade-only - a substitute may be better, never worse):
  * Same requirement = same type + package + value. Precision resistors
    (tolerance < 1 %) and current shunts (< 1 ohm) are never pooled with
    general-purpose parts.
  * A part can stand in for the group when it meets every member's voltage,
    dielectric (C0G > X7R > X7S > X6S > X5R), tolerance and power, and is
    AEC-Q200 if any member is.
  * Unknown attributes are never assumed: the group is reported for a
    human to check.
"""
import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "server"))
sys.path.insert(0, str(HERE))

import passives as P  # noqa: E402

try:
    from xlsx_read import read_xlsx  # noqa: E402
except ImportError:  # pragma: no cover
    read_xlsx = None

COLUMNS = {"name": "Name", "description": "Description", "designator": "Designator",
           "quantity": "Quantity", "manufacturer": "Manufacturer 1",
           "mpn": "Manufacturer Part Number 1", "supplier": "Supplier 1",
           "spn": "Supplier Part Number 1", "price": "Supplier Unit Price 1",
           "lifecycle": "Manufacturer Lifecycle 1"}


def load_bom(path, board):
    rows = next(iter(read_xlsx(path).values()))
    head = rows[0]
    idx = {k: head.index(v) for k, v in COLUMNS.items() if v in head}
    lines = []
    for r in rows[1:]:
        get = lambda k: (r[idx[k]] if k in idx and idx[k] < len(r) else None) or ""  # noqa: E731
        if not get("designator"):
            continue
        price = get("price")
        lines.append({
            "board": board, "name": get("name").strip(), "description": get("description").strip(),
            "designators": [d.strip() for d in get("designator").split(",") if d.strip()],
            "quantity": int(float(get("quantity") or 0)), "manufacturer": get("manufacturer"),
            "mpn": get("mpn").strip(), "supplier": get("supplier"), "spn": get("spn"),
            "unit_price": float(price) if price else None, "lifecycle": get("lifecycle"),
            "from_sheet": None,
        })
    return lines


_SPEC_KEYS = ("value", "resistance", "capacitance", "case-eia", "case/package", "case code (imperial)",
              "tolerance", "voltage rating", "voltage", "dielectric", "power", "power rating", "ratings")


def _sheet_parts(paths):
    import schdoc_file
    for path in paths:
        for c in schdoc_file.query(path, "component", include_pins=False):
            if c["designator"].endswith("?"):
                continue
            prm = {k.lower(): v for k, v in (c.get("parameters") or {}).items()}
            specs = " ".join(f"{prm[k]}" for k in _SPEC_KEYS if prm.get(k))
            yield c, prm, specs, Path(path).name


def reconcile(bom_lines, paths, board):
    """The saved sheets decide WHAT is placed; the BOM export only supplies
    part number / supplier data for a part it already knew by the same name.

    Returns (lines, changes). A designator whose part differs from the export
    (or that the export never had) is marked changed; export designators no
    longer on any sheet are reported as removed. A repeated (multi-channel)
    sheet's C3 matches the export's C3_HBU, C3_HBV, ..."""
    by_des = {d: ln for ln in bom_lines for d in ln["designators"]}
    by_name = {}
    for ln in bom_lines:
        for k in (ln["name"], ln["mpn"]):
            if k:
                by_name.setdefault(k.upper(), ln)
    lines, changes, seen = [], [], set()
    for c, prm, specs, sheet in _sheet_parts(paths):
        d = c["designator"]
        comment = (c.get("comment") or "").strip()
        sheet_mpn = (prm.get("manufacturer part number") or prm.get("part number") or comment).strip()
        exp = [x for x in by_des if x == d or x.startswith(d + "_")]
        seen.update(exp)
        old = by_des[exp[0]] if exp else None
        same = old is not None and {comment.upper(), sheet_mpn.upper()} & {old["name"].upper(), old["mpn"].upper()}
        src = old if same else by_name.get(comment.upper()) or by_name.get(sheet_mpn.upper())
        des = exp if (exp and same) else ([d] if not exp else exp)
        ln = {"board": board, "designators": des, "quantity": len(des), "from_sheet": sheet,
              "name": comment, "description": f'{c.get("description") or ""} {specs}'.strip()}
        if src:
            ln.update({k: src[k] for k in ("manufacturer", "mpn", "supplier", "spn", "unit_price", "lifecycle")})
            ln["description"] = f'{ln["description"]} {src["description"]}'.strip()
        else:
            ln.update({"manufacturer": prm.get("manufacturer", ""), "mpn": sheet_mpn, "supplier": "",
                       "spn": "", "unit_price": None, "lifecycle": ""})
        if not same:
            changes.append({"board": board, "designator": d, "was": old["mpn"] if old else None,
                            "now": ln["mpn"], "sheet": sheet})
        ln["changed_since_export"] = not same
        lines.append(ln)
    for d, ln in sorted(by_des.items()):
        if d not in seen:
            changes.append({"board": board, "designator": d, "was": ln["mpn"], "now": None, "sheet": None})
    return lines, changes


def attributes(line):
    """Decoded attributes: the part number wins; the description fills gaps."""
    by_mpn = P.decode_mpn(line["mpn"])
    by_text = P.decode_description(f'{line["description"]} {line["name"]}')
    if by_mpn is None and _NOT_A_PLAIN_PASSIVE.search(f'{line["description"]} {line["name"]}'):
        return None, by_text, None
    if by_mpn is None:
        if by_text.get("type") in ("R", "C") and by_text.get("package") and by_text.get("value"):
            a = {"type": by_text["type"], "package": by_text["package"], "value": by_text["value"],
                 "voltage": by_text.get("voltage"), "dielectric": by_text.get("dielectric"),
                 "tolerance": by_text.get("tolerance"), "power": by_text.get("power"),
                 "aec": by_text.get("aec", False), "series": "(from description)"}
            return a, by_text, "description"
        return None, by_text, None
    a = dict(by_mpn)
    if a.get("aec") is False and by_text.get("aec"):
        a["aec"] = True
    if a["type"] == "R" and a.get("power") is None:
        a["power"] = by_text.get("power")
    return a, by_text, "part number"


# thermistors, varistors, fuses, electrolytics... read like plain R/C in a
# description but are not interchangeable chip passives
_NOT_A_PLAIN_PASSIVE = re.compile(
    r"thermistor|\bNTC\b|\bPTC\b|varistor|fuse|ferrite|electrolytic|tantalum|polymer", re.I)


def mismatches(a, text):
    """Where the name/description disagrees with the part actually bought."""
    out = []
    if not a or not text:
        return out
    if text.get("dielectric") and a.get("dielectric") and text["dielectric"] != a["dielectric"]:
        out.append(f'dielectric: text says {text["dielectric"]}, part is {a["dielectric"]}')
    if text.get("tolerance") and a.get("tolerance") and a["type"] == "R" \
            and abs(text["tolerance"] - a["tolerance"]) > 1e-9:
        out.append(f'tolerance: text says {text["tolerance"]:g}%, part is {a["tolerance"]:g}%')
    if text.get("voltage") and a.get("voltage") and abs(text["voltage"] - a["voltage"]) > 1e-9:
        out.append(f'voltage: text says {text["voltage"]:g} V, part is {a["voltage"]:g} V')
    if text.get("value") and a.get("value") is not None and text.get("type") == a["type"]:
        if abs(text["value"] - a["value"]) > 1e-3 * max(text["value"], 1e-15):
            out.append(f'value: text says {P.fmt_value(a["type"], text["value"])}, '
                       f'part is {P.fmt_value(a["type"], a["value"])}')
    return out


def pool(a):
    """Which pool a part belongs to - parts in different pools never merge."""
    if a["type"] == "R":
        if a["value"] is not None and a["value"] < 1:
            return "shunt"
        if a.get("tolerance") is not None and a["tolerance"] < 1:
            return "precision"
        if a.get("tech") == "thin":     # thin film is chosen for low TCR / drift
            return "precision"
    return "general"


CLASS_II = {"X8R", "X7R", "X7S", "X6S", "X5R", "Y5V", "Z5U"}


def tolerance_matters(a, bulk_cap_min):
    """Bulk-decoupling policy: a class-II MLCC at or above bulk_cap_min farads
    has no tolerance requirement (positions that need one are specials)."""
    return not (bulk_cap_min is not None and a["type"] == "C" and a.get("value")
                and a["value"] >= bulk_cap_min and a.get("dielectric") in CLASS_II)


def covers(cand, member, bulk_cap_min=None):
    """Can `cand` be fitted wherever `member` is fitted? (None = unknown)."""
    if cand.get("mpn") and cand.get("mpn") == member.get("mpn"):
        return True                     # the same part always stands in for itself
    c, m = cand["attrs"], member["attrs"]
    if m.get("flex") and not c.get("flex"):
        return False                    # flexible termination is a requirement
    checks = []
    if c["type"] == "C":
        checks.append(None if c.get("voltage") is None or m.get("voltage") is None
                      else c["voltage"] >= m["voltage"])
        rc, rm = P.DIELECTRIC_RANK.get(c.get("dielectric")), P.DIELECTRIC_RANK.get(m.get("dielectric"))
        checks.append(None if rc is None or rm is None else rc >= rm)
    else:
        if m.get("power") is not None:
            checks.append(None if c.get("power") is None else c["power"] >= m["power"])
    if tolerance_matters(m, bulk_cap_min):
        checks.append(None if c.get("tolerance") is None or m.get("tolerance") is None
                      else c["tolerance"] <= m["tolerance"])
    if m.get("aec"):
        checks.append(bool(c.get("aec")))
    if any(x is False for x in checks):
        return False
    return None if any(x is None for x in checks) else True


def load_library(folder):
    """Library entries from dev/library_gen.py output (every *.csv in folder).
    Returns records shaped like consolidate()'s parts: {mpn, manufacturer, attrs}."""
    import csv
    entries = []
    for path in sorted(Path(folder).glob("*.csv")):
        with open(path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                mpn = (row.get("Manufacturer Part Number 1") or "").strip()
                a = P.decode_mpn(mpn) if mpn else None
                if a:
                    entries.append({"mpn": mpn, "manufacturer": row.get("Manufacturer 1", ""),
                                    "name": row.get("Name", ""), "attrs": a})
    return entries


def match_library(g, members, library, bulk_cap_min):
    """Point a requirement group at the library entry that can stand in for every
    member (upgrade-only). Status: 'library' (already on it), 'to-library',
    or 'not-in-library' (the library needs an entry for this requirement)."""
    a0 = members[0]["attrs"]
    if g["pool"] != "general" or (a0["type"] == "R" and not a0.get("value")):
        return                                  # jumpers, shunts, precision: specials
    cands = [e for e in library if e["attrs"]["type"] == a0["type"]
             and e["attrs"]["package"] == a0["package"]
             and e["attrs"]["value"] is not None and a0["value"] is not None
             and abs(e["attrs"]["value"] - a0["value"]) <= 1e-6 * max(a0["value"], 1e-12)]
    sizes = {e["attrs"]["package"] for e in library if e["attrs"]["type"] == a0["type"]}
    if a0["package"] not in sizes:
        g["status"] = "special"
        g["reason"] = f'{a0["package"]} is not a library size - a special by choice'
        return
    fits = [e for e in cands if all(covers(e, m, bulk_cap_min) for m in members)]
    if not fits and cands:
        # partial: move whoever a library entry covers; the rest stay as specials
        best = max(cands, key=lambda e: sum(bool(covers(e, m, bulk_cap_min)) for m in members))
        moved = [m for m in members if covers(best, m, bulk_cap_min)]
        if moved:
            kept = [m for m in members if m not in moved]
            g["library"] = g["recommend"] = best["mpn"]
            g["status"] = "to-library-partial"
            g["reason"] = (f'move to library part {best["mpn"]}; keep '
                           f'{", ".join(m["mpn"] for m in kept)} as a special (needs '
                           f'{requirement(kept, bulk_cap_min)})')
            return
    if not fits:
        g["library"] = None
        g["status"] = "not-in-library"
        g["reason"] = ("library has no entry that covers " + requirement(members, bulk_cap_min)
                       if cands else "library has no entry for this value and size")
        return
    # the lowest-rated entry that still covers everyone (no needless upgrade)
    pick = sorted(fits, key=lambda e: (e["attrs"].get("voltage") or 0))[0]
    g["library"] = pick["mpn"]
    if all(m["mpn"] == pick["mpn"] for m in members):
        g["status"], g["reason"] = "library", "already on the library part"
    else:
        g["status"], g["recommend"] = "to-library", pick["mpn"]
        g["reason"] = f'move to library part {pick["mpn"]} ({pick["name"]})'


def consolidate(lines, prefer_supplier="DigiKey", resistor_std_tol=None, bulk_cap_min=None,
                library=None):
    """resistor_std_tol: library tolerance for general resistors (e.g. 1.0) -
    any general resistor left on a looser part gets a library-part proposal.
    bulk_cap_min: farads; class-II MLCCs at or above it ignore tolerance."""
    parts = {}          # mpn -> part record (merges the same MPN across boards)
    other = []
    for ln in lines:
        a, text, source = attributes(ln)
        key = ln["mpn"] or f'(no MPN) {ln["name"] or ln["description"]}'
        if a is None or a["type"] not in ("R", "C"):
            other.append(ln)
            continue
        rec = parts.setdefault(key, {"mpn": ln["mpn"], "manufacturer": ln["manufacturer"],
                                     "attrs": a, "source": source, "uses": [],
                                     "suppliers": set(), "unit_price": ln["unit_price"],
                                     "flags": []})
        rec["uses"].append({"board": ln["board"], "designators": ln["designators"],
                            "quantity": ln["quantity"], "name": ln["name"],
                            "changed_since_export": bool(ln.get("changed_since_export"))})
        if rec["unit_price"] is None:
            rec["unit_price"] = ln["unit_price"]
        if ln["supplier"]:
            rec["suppliers"].add(ln["supplier"])
        new_flags = mismatches(a, text)
        if a["type"] == "R" and a["value"] == 0 and "jumper" not in (a.get("series") or ""):
            new_flags.append("0 ohm jumper - check this is intended")
        for mm in new_flags:
            if mm not in rec["flags"]:
                rec["flags"].append(mm)

    groups = {}
    for rec in parts.values():
        a = rec["attrs"]
        groups.setdefault((a["type"], a["package"], a["value"], pool(a)), []).append(rec)

    out = []
    for (kind, pkg, value, pl), members in sorted(groups.items(), key=lambda kv: (
            kv[0][0], kv[0][1] or "", kv[0][2] or 0)):
        qty = sum(u["quantity"] for m in members for u in m["uses"])
        g = {"type": kind, "package": pkg, "value": P.fmt_value(kind, value), "pool": pl,
             "total_quantity": qty, "boards": sorted({u["board"] for m in members for u in m["uses"]}),
             "parts": [], "status": "single", "recommend": None, "reason": ""}
        for m in members:
            g["parts"].append({
                "mpn": m["mpn"], "manufacturer": m["manufacturer"], "series": m["attrs"].get("series"),
                "voltage": m["attrs"].get("voltage"), "dielectric": m["attrs"].get("dielectric"),
                "tolerance": m["attrs"].get("tolerance"), "power": m["attrs"].get("power"),
                "aec": m["attrs"].get("aec"), "decoded_from": m["source"],
                "suppliers": sorted(m["suppliers"]), "unit_price": m["unit_price"],
                "quantity": sum(u["quantity"] for u in m["uses"]),
                "uses": m["uses"], "flags": m["flags"]})
        if len(members) > 1:
            full = [m for m in members if all(covers(m, o, bulk_cap_min) for o in members if o is not m)]
            maybe = [m for m in members
                     if all(covers(m, o, bulk_cap_min) is not False for o in members if o is not m)]
            if full or maybe:
                pick = sorted(full or maybe, key=lambda m: (
                    not m["attrs"].get("aec"), prefer_supplier not in m["suppliers"],
                    m["unit_price"] if m["unit_price"] is not None else 9e9))[0]
                g["status"] = "merge" if full else "merge-check"
                g["recommend"] = pick["mpn"]
                g["reason"] = ("meets or beats every member" if full else
                               "beats every member on known attributes; some attributes unknown - check")
            else:
                g["status"] = "needs-new-part"
                g["reason"] = "no existing part covers all members: " + requirement(members, bulk_cap_min)
        if library:
            match_library(g, members, library, bulk_cap_min)
        elif resistor_std_tol is not None:
            apply_resistor_standard(g, members, resistor_std_tol)
        out.append(g)
    return {"groups": out, "other": other, "lines": len(lines)}


def apply_resistor_standard(g, members, std_tol):
    """Library standard: general resistors are bought at std_tol (1 %). If the
    group would end up on a looser part, propose the library part instead."""
    if g["type"] != "R" or g["pool"] != "general" or not members[0]["attrs"].get("value"):
        return                                   # jumpers, shunts, precision: exempt
    final = g["recommend"] or (members[0]["mpn"] if len(members) == 1 else None)
    if final is None:
        return
    fa = next(m["attrs"] for m in members if m["mpn"] == final)
    if fa.get("tolerance") is not None and fa["tolerance"] <= std_tol:
        return
    a = members[0]["attrs"]
    need_power = max([m["attrs"]["power"] for m in members if m["attrs"].get("power")] or [0])
    cand = P.rmcf_1pct(a["package"], a["value"]) if std_tol >= 1 else None
    cand_power = P.decode_mpn(cand)["power"] if cand else None
    if cand is None or (cand_power is not None and cand_power + 1e-9 < need_power):
        # outside the standard series (size or power): a special, chosen for its rating
        g["status"] = "special" if g["status"] == "single" else g["status"]
        g["reason"] = (g["reason"] + "; " if g["reason"] else "") +             "outside the standard resistor series (size or power) - left as a special"
        return
    g["status"] = "to-standard"
    g["recommend"] = cand
    g["reason"] = (f"library standard is {std_tol:g} %: replace {final} with a {std_tol:g} % AEC-Q200 part"
                   + (f" (candidate {cand} - confirm at distributor)" if cand else
                      " (no series candidate - choose by hand)"))


def requirement(members, bulk_cap_min=None):
    a = [m["attrs"] for m in members]
    bits = []
    vs = [x["voltage"] for x in a if x.get("voltage")]
    if vs:
        bits.append(f">= {max(vs):g} V")
    ds = [x["dielectric"] for x in a if x.get("dielectric")]
    if ds:
        bits.append(max(ds, key=lambda d: P.DIELECTRIC_RANK.get(d, 0)) + " or better")
    ts = [x["tolerance"] for x in a if x.get("tolerance") and tolerance_matters(x, bulk_cap_min)]
    if ts:
        bits.append(f"<= {min(ts):g} %")
    ps = [x["power"] for x in a if x.get("power")]
    if ps:
        bits.append(f">= {max(ps):g} W")
    if any(x.get("aec") for x in a):
        bits.append("AEC-Q200")
    return ", ".join(bits)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bom", action="append", required=True, help="LABEL=path.xlsx")
    ap.add_argument("--sheets", action="append", default=[],
                    help="LABEL=a.SchDoc;b.SchDoc - the saved sheets decide what is placed on that "
                         "board; its BOM export only supplies part-number/supplier data")
    ap.add_argument("--prefer-supplier", default="DigiKey")
    ap.add_argument("--resistor-standard", type=float, metavar="PCT",
                    help="library tolerance for general resistors, e.g. 1")
    ap.add_argument("--library", metavar="FOLDER",
                    help="library tables from dev/library_gen.py; groups are matched to them")
    ap.add_argument("--bulk-cap-min", type=float, metavar="FARADS",
                    help="class-II MLCCs at or above this ignore tolerance, e.g. 1e-6")
    ap.add_argument("--json", help="write the full result here")
    args = ap.parse_args(argv)
    lines = []
    for spec in args.bom:
        label, _, path = spec.partition("=")
        lines += load_bom(path, label)
    changes = []
    for spec in args.sheets:
        label, _, paths = spec.partition("=")
        board_lines = [ln for ln in lines if ln["board"] == label]
        lines = [ln for ln in lines if ln["board"] != label]
        rec, ch = reconcile(board_lines, [p for p in paths.split(";") if p], label)
        lines += rec
        changes += ch
    library = load_library(args.library) if args.library else None
    res = consolidate(lines, args.prefer_supplier, args.resistor_standard, args.bulk_cap_min, library)
    res["changed_since_export"] = changes
    if args.json:
        Path(args.json).write_text(json.dumps(res, indent=1, default=list), encoding="utf-8")
    for g in res["groups"]:
        if g["status"] != "single":
            print(f'{g["status"]:15} {g["type"]} {g["package"]} {g["value"]:8} x{g["total_quantity"]:<3} '
                  f'{"+".join(g["boards"]):7} -> {g["recommend"] or "-"}  '
                  f'[{", ".join(p["mpn"] for p in g["parts"])}]  {g["reason"]}')
    for g in res["groups"]:
        for p in g["parts"]:
            if p["flags"]:
                print(f'FLAG {p["mpn"]}: {"; ".join(p["flags"])}')
    return res


if __name__ == "__main__":
    main()
