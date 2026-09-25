"""Realign library-swapped parts onto the old wiring, and prove the netlist is unchanged.

  python realign.py plan  <baseline_dir> <project.PrjPcb> <spec_out.txt>
  python realign.py check <baseline_dir> <project.PrjPcb>

plan : for every part whose Comment changed since the baseline, move it so pin 1 lands on the
       old pin-1 spot; if pin 2 then misses the old pin-2 spot, add a straight wire between them.
       Writes MOVE|sheet|des|dx|dy and WIRE|sheet|x1|y1|x2|y2 lines (mils) for the Altium script,
       and lists parts it cannot do safely (no pins, direction changed) as MANUAL.
check: per sheet, every pin's net membership (the set of pins it connects to) must equal the
       baseline's. Prints every difference; exit code 1 if any.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import schdoc_file as S


import os
ONLY = set(filter(None, os.environ.get("REALIGN_ONLY", "").split(",")))


def sheets(prj):
    prj = Path(prj)
    return [prj.parent / l.split("=", 1)[1].strip() for l in open(prj, encoding="latin-1")
            if l.startswith("DocumentPath=") and l.strip().lower().endswith(".schdoc")]


def baseline_file(base_dir, name):
    hits = [p for p in Path(base_dir).rglob("*.SchDoc") if p.name.split(".~")[0].split(".SchDoc")[0] == name.split(".SchDoc")[0]]
    return hits[0] if hits else None


def pins(c):
    return {p["designator"]: (round(p["hot_x"]), round(p["hot_y"])) for p in c.get("pins", [])}


def unit(v):
    x, y = v
    return ((x > 0) - (x < 0), (y > 0) - (y < 0))


def plan(base_dir, prj, out):
    lines, manual, n = [], [], 0
    for sh in sheets(prj):
        b = baseline_file(base_dir, sh.name)
        if b is None:
            continue
        old = {c["designator"]: c for c in S.query(str(b), "component")}
        for c in S.query(str(sh), "component"):
            d = c["designator"]
            o = old.get(d)
            if ONLY and d not in ONLY:
                continue
            if o is None or (o.get("comment") or "") == (c.get("comment") or ""):
                continue
            op, np_ = pins(o), pins(c)
            if set(op) != {"1", "2"} or set(np_) != {"1", "2"}:
                manual.append((sh.name, d, "pins old %s new %s" % (sorted(op), sorted(np_))))
                continue
            if op == np_:
                continue
            vo = (op["2"][0] - op["1"][0], op["2"][1] - op["1"][1])
            vn = (np_["2"][0] - np_["1"][0], np_["2"][1] - np_["1"][1])
            if unit(vo) != unit(vn):
                if c.get("mirrored"):
                    manual.append((sh.name, d, "mirrored part, direction changed"))
                    continue
                # quarter turns (CCW, Altium orientation +1) that bring the new pin 1->2
                # direction onto the old one; applied in pass 1, then re-plan from the saved sheet
                v, k = vn, 0
                while unit(v) != unit(vo) and k < 4:
                    v, k = (-v[1], v[0]), k + 1
                if k == 4:
                    manual.append((sh.name, d, "direction cannot be matched old %s new %s" % (vo, vn)))
                    continue
                lines.append(f"ROT|{sh}|{d}|{(int(c['orientation']) + k) % 4}")
                n += 1
                continue
            dx, dy = op["1"][0] - np_["1"][0], op["1"][1] - np_["1"][1]
            if dx or dy:
                lines.append(f"MOVE|{sh}|{d}|{dx}|{dy}")
            p2 = (np_["2"][0] + dx, np_["2"][1] + dy)
            if p2 != op["2"]:
                lines.append(f"WIRE|{sh}|{p2[0]}|{p2[1]}|{op['2'][0]}|{op['2'][1]}")
            n += 1
    Path(out).write_text("\n".join(lines) + "\n", encoding="cp1252")
    print(f"planned {n} parts, {sum(l.startswith('MOVE') for l in lines)} moves, "
          f"{sum(l.startswith('WIRE') for l in lines)} wires -> {out}")
    for m in manual:
        print("MANUAL", *m)
    return manual


def membership(path):
    r = {}
    for net in S.netlist(str(path)):
        s = frozenset(net["pins"])
        for p in net["pins"]:
            r[p] = s
    return r


def check(base_dir, prj):
    bad = 0
    for sh in sheets(prj):
        b = baseline_file(base_dir, sh.name)
        if b is None:
            print("no baseline for", sh.name)
            bad += 1
            continue
        mo, mn = membership(b), membership(sh)
        diffs = []
        for p in sorted(set(mo) | set(mn)):
            if mo.get(p) != mn.get(p):
                diffs.append((p, sorted(mo.get(p, [])), sorted(mn.get(p, []))))
        print(f"{sh.name:40} pins {len(mn):4}  differences {len(diffs)}")
        for p, a, c in diffs[:12]:
            print(f"    {p}: was {a}\n         now {c}")
        bad += len(diffs)
    print("RESULT:", "IDENTICAL" if bad == 0 else f"{bad} DIFFERENCES")
    return bad


if __name__ == "__main__":
    if sys.argv[1] == "plan":
        plan(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        sys.exit(1 if check(sys.argv[2], sys.argv[3]) else 0)
