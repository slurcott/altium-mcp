"""Parse and filter the line dumps written by the pcb_query and compiled-netlist
bridge commands. Pure Python - no Altium - so it is tested offline.

The Pascal side does the least it can: walk the objects and write one
pipe-delimited line each, using only API names already proven by other
commands. Filtering, windowing and summarising all happen here.
"""
import schdoc_file

# Kind name (tool vocabulary) -> dump tag written by PcbQueryFromSpec
PCB_KINDS = {
    "track": "TRK", "arc": "ARC", "pad": "PAD", "via": "VIA", "fill": "FIL",
    "region": "REG", "polygon": "PLY", "text": "TXT", "component": "CMP",
    "connection": "CON",
}
_TAG_TO_KIND = {v: k for k, v in PCB_KINDS.items()}

# Field names per tag, in the order PcbQueryFromSpec writes them. A field named
# "text" is always last and takes the rest of the line, so it may contain '|'.
_FIELDS = {
    "TRK": ["layer", "net", "x1", "y1", "x2", "y2", "width"],
    "ARC": ["layer", "net", "cx", "cy", "radius", "start_angle", "end_angle", "width"],
    "PAD": ["layer", "net", "name", "x", "y", "x_size", "y_size", "hole", "component"],
    "VIA": ["net", "x", "y", "size", "hole", "low_layer", "high_layer"],
    "FIL": ["layer", "net", "x1", "y1", "x2", "y2"],
    "REG": ["layer", "net", "region_kind", "x1", "y1", "x2", "y2"],
    "PLY": ["layer", "net", "x1", "y1", "x2", "y2"],
    "TXT": ["layer", "x", "y", "text"],
    "CMP": ["layer", "designator", "x", "y", "rotation"],
    "CON": ["net"],
}
_NUMERIC = {"x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "radius", "start_angle",
            "end_angle", "width", "x_size", "y_size", "hole", "size", "rotation"}


def _num(s):
    try:
        return round(float(s.replace(",", ".")), 3)
    except ValueError:
        return None


def parse_pcb_dump(text):
    """Returns (header, objects). header has file, origin_x, origin_y (absolute
    mils); every object's coordinates are mils relative to the board origin."""
    header, objects = {}, []
    for line in text.splitlines():
        if not line.strip():
            continue
        tag, _, rest = line.partition("|")
        if tag == "BOARD":
            ox, oy, path = (rest.split("|", 2) + ["", "", ""])[:3]
            header = {"file": path, "origin_x": _num(ox), "origin_y": _num(oy)}
            continue
        if tag == "BAD":
            objects.append({"kind": "unreadable", "object_id": rest})
            continue
        fields = _FIELDS.get(tag)
        if fields is None:
            continue
        parts = rest.split("|", len(fields) - 1)
        obj = {"kind": _TAG_TO_KIND[tag]}
        for name, val in zip(fields, parts):
            if name in _NUMERIC:
                obj[name] = _num(val)
            elif name in ("net", "component"):
                obj[name] = val or None
            else:
                obj[name] = val
        objects.append(obj)
    return header, objects


def _bbox(o):
    if "x1" in o:
        xs, ys = (o["x1"], o["x2"]), (o["y1"], o["y2"])
    elif "cx" in o:
        r = o.get("radius") or 0
        return o["cx"] - r, o["cy"] - r, o["cx"] + r, o["cy"] + r
    elif "x" in o:
        xs, ys = (o["x"],), (o["y"],)
    else:
        return None
    if None in xs or None in ys:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def filter_pcb(objects, layers=None, net=None, net_how="exact", window=None):
    """layers: comma list, case-insensitive exact (a via matches if either span
    layer matches). net: pattern with net_how exact|list|prefix|contains.
    window: (x1, y1, x2, y2) mils from origin; keeps objects whose bbox touches it."""
    want_layers = None
    if layers:
        want_layers = {s.strip().lower() for s in layers.split(",") if s.strip()}
    if window is not None:
        wx1, wy1, wx2, wy2 = window
        wx1, wx2 = min(wx1, wx2), max(wx1, wx2)
        wy1, wy2 = min(wy1, wy2), max(wy1, wy2)
    out = []
    for o in objects:
        if want_layers is not None:
            ls = {o.get("layer", ""), o.get("low_layer", ""), o.get("high_layer", "")}
            if not {x.lower() for x in ls if x} & want_layers:
                continue
        if net is not None and not schdoc_file._match(o.get("net") or "", net_how, net):
            continue
        if window is not None:
            b = _bbox(o)
            if b is None or b[2] < wx1 or b[0] > wx2 or b[3] < wy1 or b[1] > wy2:
                continue
        out.append(o)
    return out


def summarise_pcb(objects):
    """Counts by kind and layer, the nets present on each layer, and airline
    (unrouted connection) counts per net - what the hand-written board-audit
    scripts computed."""
    by_kind, nets_by_layer, airlines = {}, {}, {}
    for o in objects:
        k = o["kind"]
        layer = o.get("layer") or ("{}-{}".format(o.get("low_layer"), o.get("high_layer"))
                                   if k == "via" else "")
        by_kind.setdefault(k, {}).setdefault(layer, 0)
        by_kind[k][layer] += 1
        if k == "connection":
            airlines[o.get("net") or "(no net)"] = airlines.get(o.get("net") or "(no net)", 0) + 1
        elif o.get("net") and k in ("track", "arc", "fill", "region", "polygon"):
            nets_by_layer.setdefault(layer, set()).add(o["net"])
    return {
        "counts": {k: dict(sorted(v.items())) for k, v in sorted(by_kind.items())},
        "nets_by_layer": {l: sorted(n) for l, n in sorted(nets_by_layer.items())},
        "airlines_by_net": dict(sorted(airlines.items())),
        "unrouted_connections": sum(airlines.values()),
    }


def parse_compiled_netlist(text):
    """Returns (project_path, nets, violations). nets is
    [{"name", "pins": ["U1.3", ...]}] in compiler order, ready for
    schdoc_file.filter_nets."""
    project, nets, violations = None, [], []
    for line in text.splitlines():
        tag, _, rest = line.partition("|")
        if tag == "PRJ":
            project = rest
        elif tag == "N":
            nets.append({"name": rest or None, "pins": []})
        elif tag == "P" and nets:
            des, _, pin = rest.partition("|")
            nets[-1]["pins"].append(f"{des}.{pin}")
        elif tag == "V":
            violations.append(rest)
    for n in nets:
        n["pins"].sort(key=_pin_sort_key)
    return project, nets, violations


def _pin_sort_key(p):
    des, _, pin = p.rpartition(".")
    return des, (0, int(pin), "") if pin.isdigit() else (1, 0, pin)
