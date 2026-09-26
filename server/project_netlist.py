"""Whole-project netlist from the saved .SchDoc files - no Altium, no compile.

Follows the hierarchy: sheet symbols -> child sheets (a sheet placed by several
symbols becomes several channels, named $Component_$RoomName like Altium's
default, e.g. Q4_M3), sheet entries <-> child ports, power ports global, net
labels local to their sheet instance. Built 2026-09-26 for the OV4F keypad
review, where it found the CAN TX pin fault and verified each fix.

Net identifier scope (PrjPcb HierarchyMode):
  0 Automatic      -> hierarchical if any sheet symbol exists, else flat if any
                      port exists, else global
  1 Flat           -> ports global, labels local
  2 Hierarchical   -> ports <-> parent entries, labels local
  3 Strict Hier.   -> treated as hierarchical (power ports still global here)
  4 Global         -> ports and labels global

Limits: repeated sheet symbols with the SAME designator (e.g. 13 x "M") are
numbered M1..Mn in file order - connectivity is exact, but Altium may number
the rooms in a different order. Repeat() statements are not expanded.
"""
import importlib.util
from collections import defaultdict
from pathlib import Path

import schdoc_file as S

MODE_NAMES = {0: "automatic", 1: "flat", 2: "hierarchical", 3: "strict_hierarchical", 4: "global"}


def _netlist_mod():
    path = Path(__file__).resolve().parents[1] / "dev" / "netlist.py"
    spec = importlib.util.spec_from_file_location("altium_netlist", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------ file parsing
def project_sheets(prj):
    """(sheet paths that exist, HierarchyMode int) from a .PrjPcb."""
    prj = Path(prj)
    text = prj.read_text(encoding="latin-1")
    paths, mode = [], 0
    for line in text.splitlines():
        if line.startswith("DocumentPath=") and line.strip().lower().endswith(".schdoc"):
            p = prj.parent / line.split("=", 1)[1].strip()
            if p.exists():
                paths.append(p)
        elif line.startswith("HierarchyMode="):
            try:
                mode = int(line.split("=", 1)[1])
            except ValueError:
                pass
    return paths, mode


def sheet_symbols(records):
    """Sheet symbols on one sheet: [{idx, designator, file, entries: [(x, y, name)]}] (mils)."""
    syms = {}
    for r in records:
        if r.get("RECORD") == "15":
            syms[r["_index"]] = {"idx": r["_index"], "designator": "", "file": "",
                                 "x": int(r.get("LOCATION.X", 0)) * 10,
                                 "y": int(r.get("LOCATION.Y", 0)) * 10,
                                 "w": int(r.get("XSIZE", 0)) * 10,
                                 "h": int(r.get("YSIZE", 0)) * 10, "entries": []}
    for r in records:
        owner = syms.get(int(r.get("OWNERINDEX", -1) or -1))
        if owner is None:
            continue
        t = r.get("RECORD")
        if t == "32":
            owner["designator"] = r.get("TEXT", "")
        elif t == "33":
            owner["file"] = r.get("TEXT", "")
        elif t == "16":
            # DISTANCEFROMTOP is in 100-mil units (verified: 87/87 + 26/26 entries land on wire ends)
            d = int(r.get("DISTANCEFROMTOP", 0)) * 100
            side = int(r.get("SIDE", 0))
            x, y, w, h = owner["x"], owner["y"], owner["w"], owner["h"]
            pt = {0: (x, y - d), 1: (x + w, y - d), 2: (x + d, y), 3: (x + d, y - h)}.get(side, (x, y - d))
            owner["entries"].append((pt[0], pt[1], r.get("NAME", "")))
    return list(syms.values())


def entry_key(sym_idx, name):
    return f"{sym_idx}|{name}"


def parse_sheet(path, netlist_mod=None):
    """{'clusters': [...], 'symbols': [...]} for one saved sheet."""
    nl = netlist_mod or _netlist_mod()
    sheet = S.Sheet(str(path))
    syms = sheet_symbols(sheet.records)
    recs = S.sheet_records(sheet)
    recs["ENTRY"] = [[x, y, entry_key(s["idx"], name)] for s in syms for x, y, name in s["entries"]]
    return {"clusters": nl.clusters(recs), "symbols": syms}


# ------------------------------------------------------------------ hierarchy merge
class _DSU:
    def __init__(self):
        self.p = {}

    def find(self, a):
        self.p.setdefault(a, a)
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def _stem(name):
    n = name.replace("/", "\\").split("\\")[-1]
    return n[:-7].lower() if n.lower().endswith(".schdoc") else n.lower()


def merge(sheets, mode=0):
    """Merge parsed sheets into one project netlist.

    sheets: {sheet file name: {"clusters": [{"names": [(prio, text, kind)], "pins": set}],
                               "symbols": [{"idx", "designator", "file", "entries": [(x, y, name)]}]}}
    Returns {"mode", "top", "instances", "nets": [{"name", "names", "pins"}], "warnings"}.
    """
    by_stem = {_stem(k): k for k in sheets}
    referenced = {_stem(s["file"]) for d in sheets.values() for s in d["symbols"]}
    tops = [k for k in sheets if _stem(k) not in referenced]
    warnings = []
    any_symbols = any(d["symbols"] for d in sheets.values())
    any_ports = any(n[2] == "port" for d in sheets.values() for c in d["clusters"] for n in c["names"])
    eff = mode
    if mode == 0:
        eff = 2 if any_symbols else (1 if any_ports else 4)
    hier = eff in (2, 3)

    # instantiate
    instances = []          # {id, sheet, room, parent, sym_idx}

    def walk(sheet, room, parent, sym_idx, depth):
        if depth > 20:
            warnings.append(f"hierarchy deeper than 20 at {sheet} - cycle?")
            return
        iid = len(instances)
        instances.append({"id": iid, "sheet": sheet, "room": room, "parent": parent, "sym_idx": sym_idx})
        syms = sheets[sheet]["symbols"]
        counts = defaultdict(int)
        for s in syms:
            counts[s["designator"]] += 1
        seen = defaultdict(int)
        for s in syms:
            child = by_stem.get(_stem(s["file"]))
            if child is None:
                warnings.append(f"{sheet}: sheet symbol {s['designator']} -> '{s['file']}' not in project")
                continue
            seen[s["designator"]] += 1
            r = s["designator"] if counts[s["designator"]] == 1 else f"{s['designator']}{seen[s['designator']]}"
            walk(child, r, iid, s["idx"], depth + 1)

    for t in tops:
        walk(t, "", None, None, 0)

    # a sheet placed more than once is a channel: its parts get _Room suffixes
    uses = defaultdict(int)
    for inst in instances:
        uses[inst["sheet"]] += 1

    # Altium net names are case-insensitive (fHv == fHV), and a net label or
    # port named like a power net joins that (global) power net - both proven
    # against the IPC-2581 netlists of the released OV4F B_1 boards.
    power_names = {n[1].upper() for d in sheets.values() for c in d["clusters"]
                   for n in c["names"] if n[2] == "power"}
    dsu = _DSU()
    for inst in instances:
        iid, sheet = inst["id"], inst["sheet"]
        for ci, c in enumerate(sheets[sheet]["clusters"]):
            node = ("c", iid, ci)
            dsu.find(node)
            for _prio, text, kind in c["names"]:
                key = text.upper()
                if kind == "power" or (kind in ("label", "port") and key in power_names):
                    dsu.union(node, ("power", key))
                elif kind == "label":
                    dsu.union(node, ("label", key) if eff == 4 else ("label", iid, key))
                elif kind == "port":
                    if eff in (1, 4):
                        dsu.union(node, ("port", key))
                    else:
                        dsu.union(node, ("port", iid, key))       # same-named ports on a sheet connect
                elif kind == "entry":
                    dsu.union(node, ("entry", iid, text))

    if hier:
        for inst in instances:
            if inst["parent"] is None:
                continue
            child_ports = {n[1].upper(): n[1] for c in sheets[inst["sheet"]]["clusters"]
                           for n in c["names"] if n[2] == "port"}
            parent_sheet = instances[inst["parent"]]["sheet"]
            sym = next(s for s in sheets[parent_sheet]["symbols"] if s["idx"] == inst["sym_idx"])
            names = {name for _x, _y, name in sym["entries"]}
            for name in names:
                key = name.upper()
                if key in child_ports:
                    target = ("power", key) if key in power_names else ("port", inst["id"], key)
                    dsu.union(("entry", inst["parent"], entry_key(sym["idx"], name)), target)
                else:
                    warnings.append(f"{parent_sheet}: entry '{name}' on {sym['designator'] or '?'} has no port in {inst['sheet']}")
            upper_names = {n.upper() for n in names}
            for key in sorted(set(child_ports) - upper_names):
                warnings.append(f"{inst['sheet']}: port '{child_ports[key]}' has no entry on its sheet symbol in {parent_sheet}")

    nets = defaultdict(lambda: {"names": set(), "power": set(), "pins": set(), "depth": 99})
    depth_of = {}
    for inst in instances:
        d, p = 0, inst["parent"]
        while p is not None:
            d, p = d + 1, instances[p]["parent"]
        depth_of[inst["id"]] = d
    for inst in instances:
        iid, sheet = inst["id"], inst["sheet"]
        suffix = f"_{inst['room']}" if uses[sheet] > 1 and inst["room"] else ""
        for ci, c in enumerate(sheets[sheet]["clusters"]):
            n = nets[dsu.find(("c", iid, ci))]
            for p in c["pins"]:
                des, pin = p.rsplit(".", 1)
                n["pins"].add(f"{des}{suffix}.{pin}")
            for _prio, text, kind in c["names"]:
                if kind == "entry":
                    continue
                if kind == "power":
                    n["power"].add(text)
                elif depth_of[iid] <= n["depth"]:
                    if depth_of[iid] < n["depth"]:
                        n["names"] = set()
                    n["depth"] = depth_of[iid]
                    n["names"].add(text)
    out = []
    for n in nets.values():
        if not n["pins"]:
            continue
        pick = sorted(n["power"]) or sorted(n["names"])
        out.append({"name": pick[0] if pick else None,
                    "names": sorted(n["power"] | n["names"]),
                    "pins": sorted(n["pins"])})
    out.sort(key=lambda n: (n["name"] is None, n["name"] or "", n["pins"]))
    return {"mode": MODE_NAMES.get(eff, str(eff)), "top": tops,
            "instances": [{"sheet": i["sheet"], "room": i["room"],
                           "parent": instances[i["parent"]]["sheet"] if i["parent"] is not None else None}
                          for i in instances],
            "nets": out, "warnings": warnings}


def build(prj):
    """Project netlist straight from a .PrjPcb and its saved sheets."""
    paths, mode = project_sheets(prj)
    nl = _netlist_mod()
    sheets = {p.name: parse_sheet(p, nl) for p in paths}
    return merge(sheets, mode)


def membership(nets):
    """{pin: frozenset of every pin on its net}."""
    r = {}
    for n in nets:
        m = frozenset(n["pins"])
        for p in n["pins"]:
            r[p] = m
    return r


def diff(old_nets, new_nets):
    """Pins whose net membership changed: [{pin, was, now}] (sorted pin lists)."""
    mo, mn = membership(old_nets), membership(new_nets)
    return [{"pin": p, "was": sorted(mo.get(p, [])), "now": sorted(mn.get(p, []))}
            for p in sorted(set(mo) | set(mn)) if mo.get(p) != mn.get(p)]


def ipc2581_nets(path):
    """Nets of a released board from its IPC-2581 file (.cvg/.xml, or a release .zip holding one).

    Every PinRef under an element carrying net="..." belongs to that net. Pins with
    no net are absent (the PCB side omits unconnected pins)."""
    import xml.etree.ElementTree as ET
    import zipfile
    path = Path(path)
    if path.suffix.lower() == ".zip":
        z = zipfile.ZipFile(path)
        names = [n for n in z.namelist() if n.lower().endswith((".cvg", ".xml")) and "2581" in n]
        names = names or [n for n in z.namelist() if n.lower().endswith(".cvg")]
        if not names:
            raise ValueError(f"no IPC-2581 file in {path}")
        data = z.read(names[0])
    else:
        data = path.read_bytes()
    root = ET.fromstring(data)
    nets = defaultdict(set)
    stack = [(root, None)]
    while stack:
        el, net = stack.pop()
        net = el.attrib.get("net", net)
        if el.tag.split("}")[-1] == "PinRef" and net and net != "No Net":
            nets[net].add(f'{el.attrib.get("componentRef")}.{el.attrib.get("pin")}')
        stack.extend((c, net) for c in el)
    return [{"name": k, "pins": sorted(v)} for k, v in sorted(nets.items())]


def compare(nets, other):
    """Compare two netlists on the pins both contain (a PCB omits unconnected pins).

    Returns {"differences": [...], "only_in_first", "only_in_second"}."""
    a = {p for n in nets for p in n["pins"]}
    b = {p for n in other for p in n["pins"]}
    common = a & b
    fa = [{"pins": [p for p in n["pins"] if p in common]} for n in nets]
    fb = [{"pins": [p for p in n["pins"] if p in common]} for n in other]
    return {"differences": diff([n for n in fb if len(n["pins"]) > 1], [n for n in fa if len(n["pins"]) > 1]),
            "only_in_first": sorted(a - b), "only_in_second": sorted(b - a)}
