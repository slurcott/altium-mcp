"""Read a saved .SchDoc straight from disk - no Altium involved, so nothing can wedge.

A .SchDoc is an OLE compound file whose "FileHeader" stream holds one record per
object, each a `|KEY=value|...` string. Children point at their parent through
OWNERINDEX (index into the record list, header excluded). Record types used here:

    1 component   2 pin   17 power port   18 port   25 net label
    27 wire       29 junction   34 designator   41 parameter

Coordinates in the file are in units of 10 mil, with *_FRAC holding 1/100000 of
a unit; everything returned here is converted to mils.

Limitation: this reads what is SAVED. Unsaved edits in Altium are invisible to
it - save first (save_doc), or ask Altium.
"""
import struct
from pathlib import Path

REC_COMPONENT, REC_PIN, REC_POWER, REC_PORT = 1, 2, 17, 18
REC_NETLABEL, REC_WIRE, REC_JUNCTION, REC_DESIGNATOR, REC_PARAMETER = 25, 27, 29, 34, 41

ENDOFCHAIN = 0xFFFFFFFE


# ----------------------------------------------------------------------------- OLE
def read_ole_stream(path, name):
    """Return the bytes of one top-level stream of an OLE compound file."""
    b = Path(path).read_bytes()
    if b[:8] != bytes.fromhex("D0CF11E0A1B11AE1"):
        raise ValueError(f"{path} is not an OLE compound file")
    sector_size = 1 << struct.unpack_from("<H", b, 30)[0]
    mini_size = 1 << struct.unpack_from("<H", b, 32)[0]
    n_fat, dir_start = struct.unpack_from("<II", b, 44)
    mini_cutoff, minifat_start, _n_minifat, difat_start, n_difat = struct.unpack_from("<IIIII", b, 56)

    def sector(i):
        off = (i + 1) * sector_size
        return b[off:off + sector_size]

    # FAT sector list: 109 entries in the header, then the DIFAT chain
    fat_sectors = [s for s in struct.unpack_from("<109I", b, 76) if s < ENDOFCHAIN][:n_fat]
    d = difat_start
    for _ in range(n_difat):
        entries = struct.unpack_from(f"<{sector_size // 4}I", sector(d))
        fat_sectors += [s for s in entries[:-1] if s < ENDOFCHAIN]
        d = entries[-1]
    fat = []
    for s in fat_sectors[:n_fat]:
        fat += struct.unpack_from(f"<{sector_size // 4}I", sector(s))

    def chain(start):
        out, s, seen = [], start, set()
        while s < ENDOFCHAIN and s not in seen:
            seen.add(s)
            out.append(s)
            s = fat[s]
        return out

    directory = b"".join(sector(s) for s in chain(dir_start))
    entries = []
    for off in range(0, len(directory), 128):
        e = directory[off:off + 128]
        n = struct.unpack_from("<H", e, 64)[0]
        entries.append({
            "name": e[:max(n - 2, 0)].decode("utf-16le", errors="replace"),
            "type": e[66],
            "start": struct.unpack_from("<I", e, 116)[0],
            "size": struct.unpack_from("<Q", e, 120)[0] & 0xFFFFFFFF,
        })
    root = entries[0]
    target = next((e for e in entries if e["type"] == 2 and e["name"] == name), None)
    if target is None:
        raise KeyError(f"no stream named {name!r} in {path}")

    if target["size"] >= mini_cutoff:
        data = b"".join(sector(s) for s in chain(target["start"]))
        return data[:target["size"]]

    # small stream: lives in the mini stream, indexed by the mini FAT
    minifat = []
    for s in chain(minifat_start):
        minifat += struct.unpack_from(f"<{sector_size // 4}I", sector(s))
    ministream = b"".join(sector(s) for s in chain(root["start"]))
    out, s = [], target["start"]
    while s < ENDOFCHAIN:
        out.append(ministream[s * mini_size:(s + 1) * mini_size])
        s = minifat[s]
    return b"".join(out)[:target["size"]]


# ------------------------------------------------------------------------- records
def read_records(path):
    """All object records of a .SchDoc, in file order, header excluded.

    Each record is a dict of its |KEY=value| fields (keys upper-cased) plus
    '_index' (the OWNERINDEX that children use to point at it).
    """
    data = read_ole_stream(path, "FileHeader")
    records, pos = [], 0
    while pos + 4 <= len(data):
        (word,) = struct.unpack_from("<I", data, pos)
        length, kind = word & 0x00FFFFFF, word >> 24
        body = data[pos + 4:pos + 4 + length]
        pos += 4 + length
        if kind != 0:
            continue    # binary record; not used on a SchDoc
        text = body.rstrip(b"\0").decode("latin1")
        rec = {}
        for field in text.strip("|").split("|"):
            if "=" in field:
                k, v = field.split("=", 1)
                rec[k.upper()] = v
        records.append(rec)
    if records and "HEADER" in records[0]:
        records = records[1:]
    for i, r in enumerate(records):
        r["_index"] = i
    return records


def _coord(rec, key):
    """A coordinate field in mils (file units are 10 mil, plus a 1e-5 fraction)."""
    whole = int(rec.get(key, "0") or 0)
    frac = int(rec.get(key + "_FRAC", "0") or 0)
    return round((whole + frac / 100000.0) * 10, 3)


def _rtype(rec):
    try:
        return int(rec.get("RECORD", "-1"))
    except ValueError:
        return -1


# -------------------------------------------------------------------------- sheet
class Sheet:
    """A parsed .SchDoc: components with their pins, labels, ports, wires."""

    def __init__(self, path):
        self.path = str(path)
        self.records = read_records(path)
        by_owner = {}
        for r in self.records:
            if "OWNERINDEX" in r:
                by_owner.setdefault(int(r["OWNERINDEX"]), []).append(r)

        self.components = []
        for r in self.records:
            if _rtype(r) != REC_COMPONENT:
                continue
            kids = by_owner.get(r["_index"], [])
            des = next((k.get("TEXT", "") for k in kids if _rtype(k) == REC_DESIGNATOR), "")
            params = {k.get("NAME", ""): k.get("TEXT", "") for k in kids
                      if _rtype(k) == REC_PARAMETER and k.get("NAME")}
            part = r.get("CURRENTPARTID", "1")
            mode = r.get("DISPLAYMODE", "0") or "0"
            pins = []
            for k in kids:
                if _rtype(k) != REC_PIN:
                    continue
                # multi-part components store every part's pins; keep this part's
                if k.get("OWNERPARTID", part) not in (part, "-1", "0"):
                    continue
                # a symbol with alternate display modes stores each mode's pins, at
                # different positions; only the displayed mode's pins are on the sheet
                if (k.get("OWNERPARTDISPLAYMODE", "0") or "0") != mode:
                    continue
                pins.append(self._pin(k))
            self.components.append({
                "designator": des,
                "libref": r.get("LIBREFERENCE", ""),
                "description": r.get("COMPONENTDESCRIPTION", ""),
                "comment": params.get("Comment", ""),
                "x": _coord(r, "LOCATION.X"), "y": _coord(r, "LOCATION.Y"),
                "orientation": int(r.get("ORIENTATION", "0") or 0),
                "mirrored": r.get("ISMIRRORED", "F") == "T",
                "part": part,
                "pins": pins,
                "parameters": params,
            })

        self.netlabels = [self._text_obj(r) for r in self.records if _rtype(r) == REC_NETLABEL]
        self.power = [dict(self._text_obj(r), style=int(r.get("STYLE", "0") or 0))
                      for r in self.records if _rtype(r) == REC_POWER]
        self.ports = [dict(self._text_obj(r), text=r.get("NAME", ""), width=_coord(r, "WIDTH"))
                      for r in self.records if _rtype(r) == REC_PORT]
        self.junctions = [(_coord(r, "LOCATION.X"), _coord(r, "LOCATION.Y"))
                          for r in self.records if _rtype(r) == REC_JUNCTION]
        self.wires = []
        for r in self.records:
            if _rtype(r) != REC_WIRE:
                continue
            n = int(r.get("LOCATIONCOUNT", "0") or 0)
            self.wires.append([(_coord(r, f"X{i}"), _coord(r, f"Y{i}")) for i in range(1, n + 1)])

    @staticmethod
    def _text_obj(r):
        return {"text": r.get("TEXT", ""), "x": _coord(r, "LOCATION.X"),
                "y": _coord(r, "LOCATION.Y"), "orientation": int(r.get("ORIENTATION", "0") or 0)}

    @staticmethod
    def _pin(k):
        x, y = _coord(k, "LOCATION.X"), _coord(k, "LOCATION.Y")
        length = _coord(k, "PINLENGTH")
        orient = int(k.get("PINCONGLOMERATE", "0") or 0) & 3
        # Location is the body end; the electrical (hot) end is PinLength further out
        dx, dy = {0: (length, 0), 1: (0, length), 2: (-length, 0), 3: (0, -length)}[orient]
        return {"designator": k.get("DESIGNATOR", ""), "name": k.get("NAME", ""),
                "x": x, "y": y, "hot_x": round(x + dx, 3), "hot_y": round(y + dy, 3),
                "orientation": orient}


# -------------------------------------------------------------------------- query
def _match(value, how, pattern):
    if pattern is None:
        return True
    v = value or ""
    if how == "list":
        if isinstance(pattern, str):     # MCP passes lists as "A,B,C"
            pattern = [x.strip() for x in pattern.split(",")]
        return v in pattern
    if how == "prefix":
        return v.startswith(pattern)
    if how == "contains":
        return pattern in v
    return v == pattern


def query(path, kind="component", match_field=None, how="exact", pattern=None, include_pins=True):
    """Objects of one kind from a saved .SchDoc, optionally filtered on one field."""
    sheet = Sheet(path)
    pools = {"component": sheet.components, "netlabel": sheet.netlabels,
             "power": sheet.power, "port": sheet.ports}
    if kind == "pin":
        items = [dict(p, component=c["designator"]) for c in sheet.components for p in c["pins"]]
    elif kind == "wire":
        items = [{"vertices": w} for w in sheet.wires]
    elif kind == "junction":
        items = [{"x": x, "y": y} for x, y in sheet.junctions]
    elif kind in pools:
        items = pools[kind]
    else:
        raise ValueError(f"unknown kind {kind!r}")
    if match_field:
        items = [i for i in items if _match(str(i.get(match_field, "")), how, pattern)]
    if kind == "component" and not include_pins:
        items = [{k: v for k, v in i.items() if k != "pins"} for i in items]
    return items


# ------------------------------------------------------------------------ netlist
def _netlist_builder():
    """dev/netlist.py's connectivity rules (wires, junctions, pin hot ends, labels,
    power ports, ports), which were checked against a hand-drawn reference sheet."""
    import importlib.util
    path = Path(__file__).resolve().parents[1] / "dev" / "netlist.py"
    spec = importlib.util.spec_from_file_location("altium_netlist", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.build


def sheet_records(sheet):
    """A Sheet in the record shape dev/netlist.py consumes (integer mils)."""
    r = lambda v: int(round(v))
    recs = {"WIRE": [], "JUNC": [], "PIN": [], "PWR": [], "PORT": [], "NLBL": [], "COMP": []}
    for w in sheet.wires:
        for (x1, y1), (x2, y2) in zip(w, w[1:]):
            recs["WIRE"].append([r(x1), r(y1), r(x2), r(y2)])
    recs["JUNC"] = [[r(x), r(y)] for x, y in sheet.junctions]
    for c in sheet.components:
        recs["COMP"].append([c["designator"], c["libref"], "", c["comment"]])
        for p in c["pins"]:
            recs["PIN"].append([c["designator"], p["designator"], r(p["hot_x"]), r(p["hot_y"])])
    recs["PWR"] = [[r(o["x"]), r(o["y"]), o["text"], o.get("style", 0)] for o in sheet.power]
    recs["PORT"] = [[r(o["x"]), r(o["y"]), o["text"], 0, 0, r(o.get("width", 0))] for o in sheet.ports]
    recs["NLBL"] = [[r(o["x"]), r(o["y"]), o["text"]] for o in sheet.netlabels]
    return recs


def netlist(path, net=None, net_how="exact", has_designator=None, designator_how="list",
            only_pins_of=None, single_pin_only=False):
    """Nets of a saved .SchDoc, from the file - never Altium's cached compile.

    Returns [{"name": str|None, "count": n, "pins": ["U1.3", ...]}], named nets
    first. Filters mirror the 11 netlist scripts of the 2026-09-20 fixture day:
    net name, nets containing a designator, only pins of given parts, and
    single-pin nets (a label or pin that attached to nothing).
    """
    built = _netlist_builder()(sheet_records(Sheet(path)))
    nets = [{"name": n, "pins": pins} for n, pins in sorted(built["named"].items())]
    nets += [{"name": None, "pins": pins} for pins in built["anonymous"]]

    def des_of(pin):
        return pin.rsplit(".", 1)[0]

    out = []
    for n in nets:
        if net is not None and not _match(n["name"] or "", net_how, net):
            continue
        if has_designator is not None and not any(
                _match(des_of(p), designator_how, has_designator) for p in n["pins"]):
            continue
        if single_pin_only and len(n["pins"]) != 1:
            continue
        pins = n["pins"]
        if only_pins_of is not None:
            pins = [p for p in pins if _match(des_of(p), "list", only_pins_of)]
        out.append({"name": n["name"], "count": len(n["pins"]), "pins": pins})
    return out
