"""Read footprints from a saved .PcbLib - no Altium.

A PcbLib is an OLE compound file with one storage per footprint; its 'Data'
stream starts with the footprint name and then holds the primitives as binary
records: a type byte, then length-prefixed sub-records. Layout follows the
open-source Altium importers (KiCad pcbnew/plugins/altium). Coordinates are in
1/10000 mil; everything returned here is in mils.

Only what a footprint check needs is decoded: pads fully (name, position, sizes,
shape, hole, rotation, layer, plated); tracks/arcs by layer (for courtyard and
silk extents). Unknown record types are skipped by length.
"""
import struct

from schdoc_file import read_ole_storage_streams

UNIT = 10000.0          # internal units per mil

TYPE_ARC, TYPE_PAD, TYPE_VIA, TYPE_TRACK, TYPE_TEXT, TYPE_FILL = 1, 2, 3, 4, 5, 6
TYPE_REGION, TYPE_BODY = 11, 12
# sub-record count per primitive type (each sub-record = uint32 length + bytes)
SUBRECORDS = {TYPE_ARC: 1, TYPE_PAD: 6, TYPE_VIA: 1, TYPE_TRACK: 1, TYPE_TEXT: 2,
              TYPE_FILL: 1, TYPE_REGION: 1, TYPE_BODY: 1}
SHAPES = {1: "round", 2: "rect", 3: "octagonal", 9: "roundrect"}
LAYERS = {1: "Top", 32: "Bottom", 33: "TopOverlay", 34: "BottomOverlay", 35: "TopPaste",
          36: "BottomPaste", 37: "TopSolder", 38: "BottomSolder", 74: "MultiLayer"}


def _pascal(b):
    n = b[0] if b else 0
    return b[1:1 + n].decode("latin-1")


def _pad(subs):
    """Decode a pad from its 6 sub-records."""
    name = _pascal(subs[0])
    m = subs[4]
    # main block: layer(1) flags(2) ids(10) | x y (int32) @13 | top/mid/bottom sizes @21..44 |
    # hole @45 | shapes top/mid/bot @49..51 | rotation (double) @52 | plated @60
    # (offsets verified on a real TI QFN module footprint, 2026-09-26)
    if len(m) < 61:
        return None
    x, y = struct.unpack_from("<ii", m, 13)
    tx, ty, mx, my, bx, by = struct.unpack_from("<6i", m, 21)
    hole = struct.unpack_from("<i", m, 45)[0]
    top_shape, mid_shape, bot_shape = m[49], m[50], m[51]
    rot = struct.unpack_from("<d", m, 52)[0]
    plated = bool(m[60])
    shape = SHAPES.get(top_shape, str(top_shape))
    corner = 0
    # Rounded rectangles live in the optional size-and-shape sub-record (651 bytes):
    # per-layer alternate shapes from byte 532 (9 = rounded rectangle, top layer first)
    # and corner radius % from byte 564 (verified on Altium's own _L chip footprints:
    # shape 9, 25 %). The main block then only says 'round'.
    SHAPE_OFF, RADIUS_OFF = 532, 564
    if len(subs) > 5 and len(subs[5]) > RADIUS_OFF:
        s6 = subs[5]
        if s6[SHAPE_OFF] == 9:
            shape = "roundrect"
            corner = s6[RADIUS_OFF]
    return {"name": name, "x": x / UNIT, "y": y / UNIT,
            "w": tx / UNIT, "h": ty / UNIT, "rotation": round(rot, 3),
            "shape": shape, "corner_pct": corner, "hole": hole / UNIT, "plated": plated,
            "layer": LAYERS.get(m[0], str(m[0])),
            "mid": (mx / UNIT, my / UNIT), "bottom": (bx / UNIT, by / UNIT)}


def _track(b):
    layer = b[0]
    x1, y1, x2, y2, w = struct.unpack_from("<5i", b, 13)      # same 13-byte header as pads
    return {"layer": LAYERS.get(layer, str(layer)), "x1": x1 / UNIT, "y1": y1 / UNIT,
            "x2": x2 / UNIT, "y2": y2 / UNIT, "width": w / UNIT}


def _arc(b):
    layer = b[0]
    cx, cy, r = struct.unpack_from("<3i", b, 13)
    a1, a2 = struct.unpack_from("<2d", b, 25)
    w = struct.unpack_from("<i", b, 41)[0]
    return {"layer": LAYERS.get(layer, str(layer)), "cx": cx / UNIT, "cy": cy / UNIT,
            "r": r / UNIT, "start": a1, "end": a2, "width": w / UNIT}


def parse_footprint(data):
    """Primitives of one footprint's Data stream."""
    pos = 0
    (n,) = struct.unpack_from("<I", data, pos)
    pos += 4
    name = _pascal(data[pos:pos + n])
    pos += n
    out = {"name": name, "pads": [], "tracks": [], "arcs": [], "unknown": 0}
    while pos < len(data):
        t = data[pos]
        pos += 1
        count = SUBRECORDS.get(t)
        if count is None:
            out["unknown"] += 1
            break                        # can't size an unknown record safely
        subs = []
        for _ in range(count):
            if pos + 4 > len(data):
                break
            (ln,) = struct.unpack_from("<I", data, pos)
            pos += 4
            subs.append(data[pos:pos + ln])
            pos += ln
        try:
            if t == TYPE_PAD:
                p = _pad(subs)
                if p:
                    out["pads"].append(p)
            elif t == TYPE_TRACK and subs:
                out["tracks"].append(_track(subs[0]))
            elif t == TYPE_ARC and subs:
                out["arcs"].append(_arc(subs[0]))
        except struct.error:
            out["unknown"] += 1
    return out


def read_pcblib(path):
    """{footprint storage name: parsed footprint} for every footprint in a .PcbLib."""
    out = {}
    for storage, data in read_ole_storage_streams(path, "Data").items():
        if not data:
            continue
        try:
            out[storage] = parse_footprint(data)
        except (struct.error, IndexError):
            out[storage] = {"name": storage, "error": "unparsed"}
    return out
