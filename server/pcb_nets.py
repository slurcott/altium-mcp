"""Offline reader for a SAVED Altium .PcbDoc: components, pads, tracks, arcs, vias, regions, fills with net names.

Coordinates are mils relative to the board origin (what Altium's status bar shows). Read-only; never
touches Altium, so it is safe while the board is open (it sees the last SAVE, not unsaved edits).

Depends on the binary-record decoders in the Altium MCP server (Lurcott Labs fork of
coffeenmusic/altium-mcp, file server/pcblib_file.py). Point ALTIUM_MCP_SERVER at that folder if it is
not in the default location below.

Record offsets used here were checked against Altium Designer 26 files (2026-10-03): pad net @3 and
component @7 of the main block, track/via/arc geometry @13.
"""
import math
import os
import struct
import sys
import time

MCP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, MCP)
import pcblib_file as p  # noqa: E402

U = p.UNIT
LAYER = {1: "Top", 2: "Mid1", 3: "Mid2", 32: "Bottom", 39: "Mid1", 40: "Mid2", 74: "Multi"}
COPPER = ("Top", "Mid1", "Mid2", "Bottom")
DEFAULT_PCB = None


def _records(data, nsub):
    q = 0
    while q < len(data):
        t = data[q]; q += 1
        subs = []
        for _ in range(nsub(t)):
            (ln,) = struct.unpack_from("<I", data, q); q += 4
            subs.append(data[q:q + ln]); q += ln
        yield t, subs


def load(path=None):
    path = path or DEFAULT_PCB
    st = p.read_ole_storage_streams(path, "Data")
    board = p._text_records(st["Board6"])[0]
    OX, OY = p._mil(board.get("ORIGINX", 0)), p._mil(board.get("ORIGINY", 0))
    comps = p._text_records(st["Components6"])
    nets = [n.get("NAME") for n in p._text_records(st["Nets6"])]
    net = lambda i: nets[i] if i < len(nets) else None
    cname = lambda i: comps[i].get("SOURCEDESIGNATOR") if i < len(comps) else None
    B = {"path": path, "saved": time.ctime(os.path.getmtime(path)), "origin": (OX, OY), "nets": nets,
         "comps": {}, "pads": [], "tracks": [], "vias": [], "arcs": [], "regions": [], "fills": []}
    for c in comps:
        B["comps"][c.get("SOURCEDESIGNATOR")] = dict(x=p._mil(c.get("X", 0)) - OX, y=p._mil(c.get("Y", 0)) - OY,
                                                     rot=float(c.get("ROTATION", 0) or 0), layer=c.get("LAYER"),
                                                     pat=c.get("PATTERN"))
    for t, subs in _records(st["Pads6"], lambda t: p.SUBRECORDS.get(t, 6)):
        if t != p.TYPE_PAD or len(subs) < 5:
            continue
        ni, ci = struct.unpack_from("<H", subs[4], 3)[0], struct.unpack_from("<H", subs[4], 7)[0]
        q = p._pad(subs)
        B["pads"].append(dict(comp=cname(ci), name=q["name"], net=net(ni), x=q["x"] - OX, y=q["y"] - OY,
                              w=q["w"], h=q["h"], rot=q["rotation"], layer=q["layer"], hole=q["hole"]))
    for t, subs in _records(st.get("Tracks6", b""), lambda t: 1):
        b = subs[0]
        x1, y1, x2, y2, w = struct.unpack_from("<5i", b, 13)
        B["tracks"].append(dict(layer=LAYER.get(b[0], b[0]), net=net(struct.unpack_from("<H", b, 3)[0]),
                                x1=x1 / U - OX, y1=y1 / U - OY, x2=x2 / U - OX, y2=y2 / U - OY, w=w / U))
    for t, subs in _records(st.get("Vias6", b""), lambda t: 1):
        b = subs[0]
        x, y, d, h = struct.unpack_from("<4i", b, 13)
        B["vias"].append(dict(net=net(struct.unpack_from("<H", b, 3)[0]), x=x / U - OX, y=y / U - OY, d=d / U, hole=h / U))
    for t, subs in _records(st.get("Arcs6", b""), lambda t: 1):
        b = subs[0]
        cx, cy, r = struct.unpack_from("<3i", b, 13)
        a1, a2 = struct.unpack_from("<2d", b, 25)
        B["arcs"].append(dict(layer=LAYER.get(b[0], b[0]), net=net(struct.unpack_from("<H", b, 3)[0]),
                              cx=cx / U - OX, cy=cy / U - OY, r=r / U, a1=a1, a2=a2,
                              w=struct.unpack_from("<i", b, 41)[0] / U))
    for t, subs in _records(st.get("Regions6", b""), lambda t: 1):
        b = subs[0]
        if len(b) < 26 or LAYER.get(b[0]) not in COPPER:
            continue
        (tl,) = struct.unpack_from("<I", b, 18)
        j = 22 + tl
        (nv,) = struct.unpack_from("<I", b, j)
        pts = [(vx / U - OX, vy / U - OY) for vx, vy in
               (struct.unpack_from("<2d", b, j + 4 + 16 * k) for k in range(nv))]
        B["regions"].append(dict(layer=LAYER[b[0]], net=net(struct.unpack_from("<H", b, 3)[0]),
                                 comp=cname(struct.unpack_from("<H", b, 7)[0]), pts=pts))
    return B


def seg_dist(px, py, x1, y1, x2, y2):
    """Distance from a point to a line segment."""
    dx, dy = x2 - x1, y2 - y1
    L = dx * dx + dy * dy
    t = 0 if L == 0 else max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / L))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def pad_edge_dist(px, py, pd):
    """Distance from a point to a pad's edge (rectangle approximation, right-angle rotations)."""
    w, h = (pd["w"], pd["h"]) if round(pd["rot"]) % 180 == 0 else (pd["h"], pd["w"])
    return math.hypot(max(abs(px - pd["x"]) - w / 2, 0), max(abs(py - pd["y"]) - h / 2, 0))


# ---- routed-net checks (from the OV4F keypad repo tools/layout_checks, 2026-10-04; backlog B34) ----

def islands(B, net):
    """Union-find over the net's copper; returns the list of pad groups."""
    nodes = []
    for t in B["tracks"]:
        if t["net"] == net and t["layer"] in COPPER:
            nodes.append(("t", t, {t["layer"]}))
    for a in B["arcs"]:
        if a["net"] == net and a["layer"] in COPPER:
            ends = [(a["cx"] + a["r"] * math.cos(math.radians(g)), a["cy"] + a["r"] * math.sin(math.radians(g)))
                    for g in (a["a1"], a["a2"])]
            nodes.append(("t", dict(x1=ends[0][0], y1=ends[0][1], x2=ends[1][0], y2=ends[1][1]), {a["layer"]}))
    for v in B["vias"]:
        if v["net"] == net:
            nodes.append(("v", v, set(COPPER)))
    for q in B["pads"]:
        if q["net"] == net:
            nodes.append(("p", q, set(COPPER) if q["layer"] == "MultiLayer" else {q["layer"]}))

    def ends(o):
        return [(o["x1"], o["y1"]), (o["x2"], o["y2"])]

    def touch(A, Bn):
        (ka, a, la), (kb, b, lb) = A, Bn
        if not la & lb:
            return False
        if ka == "t" and kb == "t":
            return any(seg_dist(x, y, b["x1"], b["y1"], b["x2"], b["y2"]) < 0.8 for x, y in ends(a)) or \
                   any(seg_dist(x, y, a["x1"], a["y1"], a["x2"], a["y2"]) < 0.8 for x, y in ends(b))
        if ka == "t" or kb == "t":
            t, (k, o) = (a, (kb, b)) if ka == "t" else (b, (ka, a))
            if k == "v":
                return seg_dist(o["x"], o["y"], t["x1"], t["y1"], t["x2"], t["y2"]) < o["d"] / 2
            return any(pad_edge_dist(x, y, o) < 1.0 for x, y in ends(t))
        if {ka, kb} == {"v", "p"}:
            v, pd = (a, b) if ka == "v" else (b, a)
            return pad_edge_dist(v["x"], v["y"], pd) < v["d"] / 2
        return False

    parent = list(range(len(nodes)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            if touch(nodes[i], nodes[j]):
                parent[find(i)] = find(j)
    groups = {}
    for i, (k, o, _) in enumerate(nodes):
        if k == "p":
            groups.setdefault(find(i), []).append(f"{o['comp']}.{o['name']}")
    return list(groups.values())



def padnet_diff(before, after):
    """Pad-by-pad net comparison of two saved PcbDocs: parts added/removed and every changed pad."""
    A, B = load(before), load(after)

    def pn(X):
        out = {}
        for q in X["pads"]:
            if q["comp"]:
                out.setdefault((q["comp"], q["name"]), set()).add(q["net"])
        return out
    na, nb = pn(A), pn(B)
    diff = sorted(k for k in set(na) | set(nb) if na.get(k) != nb.get(k))
    return {"before": before, "after": after, "components": [len(A["comps"]), len(B["comps"])],
            "added": sorted(set(B["comps"]) - set(A["comps"])), "removed": sorted(set(A["comps"]) - set(B["comps"])),
            "pad_net_changes": [{"pad": f"{k[0]}.{k[1]}", "before": sorted(map(str, na.get(k, []))),
                                 "after": sorted(map(str, nb.get(k, [])))} for k in diff]}


def net_check(path, names, clear=8.0, max_items=200):
    """Connectivity + clearance of the listed nets on a saved board (see keypad tools/layout_checks/net_check.py)."""
    B = load(path)
    nets, mine = [], []
    for net in names:
        segs = [t for t in B["tracks"] if t["net"] == net and t["layer"] in COPPER]
        mine += segs
        g = islands(B, net)
        nets.append({"net": net, "connected": len(g) == 1, "islands": g,
                     "length_mil": round(sum(math.hypot(t["x2"] - t["x1"], t["y2"] - t["y1"]) for t in segs)),
                     "vias": sum(1 for v in B["vias"] if v["net"] == net),
                     "layers": sorted({t["layer"] for t in segs}), "widths": sorted({round(t["w"], 1) for t in segs})})
    others_t = [t for t in B["tracks"] if t["layer"] in COPPER and t["net"] not in names]
    others_v = [v for v in B["vias"] if v["net"] not in names]
    others_p = [q for q in B["pads"] if q["net"] not in names and q["comp"]]
    bad = set()
    for t in mine:
        hw = t["w"] / 2
        n = max(1, int(math.hypot(t["x2"] - t["x1"], t["y2"] - t["y1"]) / 2))
        pts = [(t["x1"] + (t["x2"] - t["x1"]) * k / n, t["y1"] + (t["y2"] - t["y1"]) * k / n) for k in range(n + 1)]
        for o in others_t:
            if o["layer"] == t["layer"]:
                d = min(seg_dist(x, y, o["x1"], o["y1"], o["x2"], o["y2"]) for x, y in pts) - hw - o["w"] / 2
                if d < clear:
                    bad.add((round(d, 1), t["net"], t["layer"], "track", o["net"], round(o["x1"]), round(o["y1"])))
        for v in others_v:
            d = min(math.hypot(x - v["x"], y - v["y"]) for x, y in pts) - hw - v["d"] / 2
            if d < clear:
                bad.add((round(d, 1), t["net"], t["layer"], "via", v["net"], round(v["x"]), round(v["y"])))
        for q in others_p:
            if q["layer"] in ("MultiLayer", t["layer"]):
                d = min(pad_edge_dist(x, y, q) for x, y in pts) - hw
                if d < clear:
                    bad.add((round(d, 1), t["net"], t["layer"], f"pad {q['comp']}.{q['name']}", q["net"],
                             round(q["x"]), round(q["y"])))
    near = [dict(zip(("gap_mil", "net", "layer", "other", "other_net", "x", "y"), b)) for b in sorted(bad)]
    return {"board": path, "saved": B["saved"], "nets": nets, "clearance_below": clear,
            "near_misses": near[:max_items], "near_miss_total": len(near)}
