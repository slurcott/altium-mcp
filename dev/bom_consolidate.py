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


def load_sheets(paths, board, known):
    """Components on saved .SchDoc sheets that the BOM export does not list
    (added since the export). Their part number comes from the MPN parameter,
    else the Comment. Repeated (multi-channel) sheets count once - the BOM
    export is the source of quantities."""
    import schdoc_file
    lines = []
    for path in paths:
        for c in schdoc_file.query(path, "component", include_pins=False):
            d = c["designator"]
            # a repeated (multi-channel) sheet's C3 appears in the BOM as C3_HBU, C3_HBV...
            if d in known or d.endswith("?") or any(k.startswith(d + "_") for k in known):
                continue
            prm = {k.lower(): v for k, v in (c.get("parameters") or {}).items()}
            mpn = prm.get("manufacturer part number") or prm.get("part number") or c.get("comment") or ""
            lines.append({"board": board, "name": c.get("comment") or "",
                          "description": c.get("description") or "", "designators": [d],
                          "quantity": 1, "manufacturer": prm.get("manufacturer", ""),
                          "mpn": mpn.strip(), "supplier": "", "spn": "", "unit_price": None,
                          "lifecycle": "", "from_sheet": Path(path).name})
    return lines


def attributes(line):
    """Decoded attributes: the part number wins; the description fills gaps."""
    by_mpn = P.decode_mpn(line["mpn"])
    by_text = P.decode_description(f'{line["description"]} {line["name"]}')
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
    return "general"


CLASS_II = {"X8R", "X7R", "X7S", "X6S", "X5R", "Y5V", "Z5U"}


def tolerance_matters(a, bulk_cap_min):
    """Bulk-decoupling policy: a class-II MLCC at or above bulk_cap_min farads
    has no tolerance requirement (positions that need one are specials)."""
    return not (bulk_cap_min is not None and a["type"] == "C" and a.get("value")
                and a["value"] >= bulk_cap_min and a.get("dielectric") in CLASS_II)


def covers(cand, member, bulk_cap_min=None):
    """Can `cand` be fitted wherever `member` is fitted? (None = unknown)."""
    c, m = cand["attrs"], member["attrs"]
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


def consolidate(lines, prefer_supplier="DigiKey", resistor_std_tol=None, bulk_cap_min=None):
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
                            "not_in_bom_export": bool(ln.get("from_sheet"))})
        if rec["unit_price"] is None:
            rec["unit_price"] = ln["unit_price"]
        if ln["supplier"]:
            rec["suppliers"].add(ln["supplier"])
        for mm in mismatches(a, text):
            if mm not in rec["flags"]:
                rec["flags"].append(mm)
        if a["type"] == "R" and a["value"] == 0:
            rec["flags"].append("0 ohm jumper - check this is intended")

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
        if resistor_std_tol is not None:
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
    if cand and cand_power is not None and cand_power + 1e-9 < need_power:
        cand = None
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
                    help="LABEL=a.SchDoc;b.SchDoc - add parts missing from that board's BOM export")
    ap.add_argument("--prefer-supplier", default="DigiKey")
    ap.add_argument("--resistor-standard", type=float, metavar="PCT",
                    help="library tolerance for general resistors, e.g. 1")
    ap.add_argument("--bulk-cap-min", type=float, metavar="FARADS",
                    help="class-II MLCCs at or above this ignore tolerance, e.g. 1e-6")
    ap.add_argument("--json", help="write the full result here")
    args = ap.parse_args(argv)
    lines = []
    for spec in args.bom:
        label, _, path = spec.partition("=")
        lines += load_bom(path, label)
    for spec in args.sheets:
        label, _, paths = spec.partition("=")
        known = {d for ln in lines if ln["board"] == label for d in ln["designators"]}
        lines += load_sheets([p for p in paths.split(";") if p], label, known)
    res = consolidate(lines, args.prefer_supplier, args.resistor_standard, args.bulk_cap_min)
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
