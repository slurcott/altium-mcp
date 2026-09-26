"""Check an IC footprint against its datasheet - offline.

The expected geometry comes from a small package spec written from the
datasheet (all dimensions in mm), in one of two forms:

  land_pattern - the datasheet's recommended land pattern, pad by pad:
      {"land_pattern": [{"name": "1", "x": -1.4, "y": 1.0, "w": 0.8, "h": 0.3}, ...]}
      Best for modules, odd packages, and whenever the datasheet gives one.

  package - the package drawing, for standard dual-row / quad packages:
      {"package": "dual" | "quad", "style": "gullwing" | "nolead",
       "pins": 8, "pitch": 1.27,
       "span": [5.8, 6.2],        # toe-to-toe across the package (E / D), min-max
       "lead_length": [0.4, 1.27], # L (gull-wing foot or no-lead terminal), min-max
       "lead_width": [0.31, 0.51], # b, min-max
       "span_y": [...]            # quad only, if the other side differs
       "epad": [w, h]             # exposed/thermal pad, optional; "epad_name": "9"
      }
      Pin 1 is top-left; numbering runs counter-clockwise (JEDEC).

Hard failures: pin set/numbering, pitch, a lead that doesn't land on its pad,
mirrored pinout, exposed pad missing or much smaller than the datasheet's.
Advisories: IPC-7351B nominal pad size, solder-mask slivers, origin not at the
pad centre, no pin-1 mark in the silkscreen.
"""
import math
from itertools import product

MIL = 0.0254           # mm per mil
TOL_POS = 0.05         # mm - pad centre must match within this
TOL_SIZE = 0.10        # mm - advisory pad size tolerance
# IPC-7351B nominal density fillets (toe, heel, side) in mm
FILLETS = {"gullwing": (0.35, 0.35, 0.03), "gullwing_fine": (0.35, 0.35, 0.01),
           "nolead": (0.30, 0.00, -0.04)}


# ------------------------------------------------------------------ footprint side
def footprint_pads(fp):
    """Pads of a parsed footprint (pcblib_file) as mm dicts with rotation folded in."""
    out = []
    for p in fp["pads"]:
        w, h = p["w"] * MIL, p["h"] * MIL
        if round(p.get("rotation", 0)) % 180 == 90:
            w, h = h, w
        out.append({"name": p["name"], "x": p["x"] * MIL, "y": p["y"] * MIL, "w": w, "h": h,
                    "shape": p.get("shape")})
    return out


def _signal_pads(pads):
    """One representative pad per name: the largest (split/paste sub-pads share a name)."""
    best = {}
    for p in pads:
        if p["name"] not in best or p["w"] * p["h"] > best[p["name"]]["w"] * best[p["name"]]["h"]:
            best[p["name"]] = p
    return best


# ------------------------------------------------------------------ expected side
def expected_from_package(spec):
    """Expected pads (mm) + lead rectangles from a package spec."""
    n, e = spec["pins"], spec["pitch"]
    style = spec.get("style", "gullwing")
    if style == "gullwing" and e <= 0.625:
        style_f = "gullwing_fine"
    else:
        style_f = style
    jt, jh, js = FILLETS[style_f]
    L, b = spec["lead_length"], spec["lead_width"]

    def row(span):
        emin, emax = span
        z = emax + 2 * jt                    # outer extent of the land pattern
        g = emin - 2 * L[1] - 2 * jh         # inner gap
        pad_len = (z - g) / 2
        centre = (z + g) / 4                 # pad centre distance from package centre
        pad_w = b[1] + 2 * js
        # lead foot: from toe (span/2) inward by L, nominal values
        toe = (emin + emax) / 4
        lead = (toe - (L[0] + L[1]) / 2, toe)
        return centre, pad_len, pad_w, lead

    pads, leads = [], []
    if spec["package"] == "dual":
        per = n // 2
        c, pl, pw, lead = row(spec["span"])
        y0 = (per - 1) * e / 2
        for i in range(per):              # left column, top -> bottom
            y = y0 - i * e
            pads.append({"name": str(i + 1), "x": -c, "y": y, "w": pl, "h": pw})
            leads.append({"name": str(i + 1), "p0": (-lead[1], y), "p1": (-lead[0], y)})
        for i in range(per):              # right column, bottom -> top
            y = -y0 + i * e
            pads.append({"name": str(per + i + 1), "x": c, "y": y, "w": pl, "h": pw})
            leads.append({"name": str(per + i + 1), "p0": (lead[0], y), "p1": (lead[1], y)})
    elif spec["package"] == "quad":
        per = n // 4
        cx, plx, pwx, leadx = row(spec["span"])
        cy, ply, pwy, leady = row(spec.get("span_y", spec["span"]))
        s0 = (per - 1) * e / 2
        k = 1
        for i in range(per):              # left, top -> bottom
            y = s0 - i * e
            pads.append({"name": str(k), "x": -cx, "y": y, "w": plx, "h": pwx})
            leads.append({"name": str(k), "p0": (-leadx[1], y), "p1": (-leadx[0], y)}); k += 1
        for i in range(per):              # bottom, left -> right
            x = -s0 + i * e
            pads.append({"name": str(k), "x": x, "y": -cy, "w": pwy, "h": ply})
            leads.append({"name": str(k), "p0": (x, -leady[1]), "p1": (x, -leady[0])}); k += 1
        for i in range(per):              # right, bottom -> top
            y = -s0 + i * e
            pads.append({"name": str(k), "x": cx, "y": y, "w": plx, "h": pwx})
            leads.append({"name": str(k), "p0": (leadx[0], y), "p1": (leadx[1], y)}); k += 1
        for i in range(per):              # top, right -> left
            x = s0 - i * e
            pads.append({"name": str(k), "x": x, "y": cy, "w": pwy, "h": ply})
            leads.append({"name": str(k), "p0": (x, leady[0]), "p1": (x, leady[1])}); k += 1
    else:
        raise ValueError("package must be 'dual' or 'quad' (use land_pattern for anything else)")
    if spec.get("epad"):
        w, h = spec["epad"]
        pads.append({"name": str(spec.get("epad_name", n + 1)), "x": 0.0, "y": 0.0, "w": w, "h": h,
                     "epad": True})
    return pads, leads


def expected_pads(spec):
    if "land_pattern" in spec:
        return [dict(p) for p in spec["land_pattern"]], []
    return expected_from_package(spec)


# ------------------------------------------------------------------ matching
TRANSFORMS = [(rot, mir) for rot, mir in product((0, 90, 180, 270), (False, True))]


def _tf(x, y, rot, mirror):
    if mirror:
        x = -x
    r = math.radians(rot)
    return (round(x * math.cos(r) - y * math.sin(r), 6), round(x * math.sin(r) + y * math.cos(r), 6))


def best_transform(expected, actual):
    """Rotation/mirror of the expected pattern that best lands on the footprint, by pad name."""
    # For each rotation/mirror, the best-fit shift (mean of matched-pad differences) is
    # where the datasheet's package centre lands in footprint coordinates - for an
    # asymmetric part that is NOT the pad centroid. "centre" = that shift.
    act = _signal_pads(actual)
    best = None
    for rot, mir in TRANSFORMS:
        pairs = []
        for p in expected:
            a = act.get(p["name"])
            if a is not None:
                pairs.append((a, _tf(p["x"], p["y"], rot, mir)))
        if not pairs:
            continue
        ox = sum(a["x"] - x for a, (x, y) in pairs) / len(pairs)
        oy = sum(a["y"] - y for a, (x, y) in pairs) / len(pairs)
        err, hits = 0.0, 0
        for a, (x, y) in pairs:
            d = math.hypot(a["x"] - ox - x, a["y"] - oy - y)
            err += d
            hits += d <= TOL_POS
        key = (-hits, err)
        if best is None or key < best[0]:
            best = (key, rot, mir, (ox, oy))
    if best is None:
        return {"rotation": 0, "mirror": False, "centre": (0.0, 0.0)}
    return {"rotation": best[1], "mirror": best[2], "centre": best[3]}


# ------------------------------------------------------------------ the check
def check(fp, spec, silk_marker_radius=1.5):
    """Check a parsed footprint (pcblib_file.parse_footprint) against a package spec.

    Returns {"verdict": PASS|FAIL, "failures": [...], "warnings": [...], "info": {...}}.
    """
    fails, warns, info = [], [], {}
    actual = footprint_pads(fp)
    expected, leads = expected_pads(spec)
    act = _signal_pads(actual)
    exp_names = [p["name"] for p in expected]
    missing = sorted(set(exp_names) - set(act), key=_natural)
    extra = sorted(set(act) - set(exp_names), key=_natural)
    if missing:
        fails.append(f"pads missing for pins {missing}")
    if extra:
        warns.append(f"pads with no datasheet pin: {extra} (mounting/thermal? name them deliberately)")
    info["pads"] = len(actual)
    info["pins_expected"] = len(exp_names)

    t = best_transform(expected, actual)
    info["orientation"] = {"rotation": t["rotation"], "mirror": t["mirror"]}
    if t["mirror"]:
        fails.append("pinout is MIRRORED relative to the datasheet (top view vs bottom view?)")
    cx, cy = t["centre"]
    info["origin_offset_mm"] = (round(cx, 3), round(cy, 3))
    if math.hypot(cx, cy) > 0.05:
        warns.append(f"footprint origin is not at the datasheet package centre: the package centre sits at "
                     f"({cx:.3f}, {cy:.3f}) mm - Altium release flags 'Offset Component Origin'")

    pos_bad, size_warn = [], []
    for p in expected:
        a = act.get(p["name"])
        if a is None:
            continue
        x, y = _tf(p["x"], p["y"], t["rotation"], t["mirror"])
        d = math.hypot(a["x"] - cx - x, a["y"] - cy - y)
        w, h = (p["w"], p["h"]) if t["rotation"] % 180 == 0 else (p["h"], p["w"])
        if d > TOL_POS:
            pos_bad.append(f"{p['name']} off by {d:.3f} mm")
        if p.get("epad"):
            if a["w"] * a["h"] < 0.7 * w * h:
                fails.append(f"exposed pad {p['name']} is {a['w']:.2f}x{a['h']:.2f} mm, datasheet "
                             f"{w:.2f}x{h:.2f} mm (<70 % area)")
            continue
        if abs(a["w"] - w) > TOL_SIZE or abs(a["h"] - h) > TOL_SIZE:
            size_warn.append(f"{p['name']} {a['w']:.2f}x{a['h']:.2f} vs {w:.2f}x{h:.2f}")
    if pos_bad:
        more = f" (+{len(pos_bad) - 8} more)" if len(pos_bad) > 8 else ""
        if "land_pattern" not in spec:
            # package mode: centres come from the IPC formula, not the datasheet - advisory;
            # the hard test is that every lead lands on its pad (below)
            warns.append(f"pad centres differ from IPC-7351B nominal: {pos_bad[:8]}{more}")
        else:
            fails.append(f"pad positions do not match the datasheet land pattern: {pos_bad[:8]}{more}")
    if size_warn:
        src = "the datasheet land pattern" if "land_pattern" in spec else "IPC-7351B nominal"
        warns.append(f"pad sizes differ from {src} by > {TOL_SIZE} mm: {size_warn[:8]}"
                     + (f" (+{len(size_warn) - 8} more)" if len(size_warn) > 8 else ""))

    # every lead's foot (nominal terminal) must land on its pad (package mode):
    # FAIL if it misses sideways or < 75 % of its length is on the pad; WARN if partly off
    lead_miss, lead_part = [], []
    for ld in leads:
        a = act.get(ld["name"])
        if a is None:
            continue
        (x0, y0) = _tf(ld["p0"][0], ld["p0"][1], t["rotation"], t["mirror"])
        (x1, y1) = _tf(ld["p1"][0], ld["p1"][1], t["rotation"], t["mirror"])
        ax, ay = a["x"] - cx, a["y"] - cy
        if abs(y1 - y0) < 1e-9:          # lead runs along x
            lo, hi, lat = min(x0, x1), max(x0, x1), y0
            plo, phi, pc, half = ax - a["w"] / 2, ax + a["w"] / 2, ay, a["h"] / 2
        else:                            # lead runs along y
            lo, hi, lat = min(y0, y1), max(y0, y1), x0
            plo, phi, pc, half = ay - a["h"] / 2, ay + a["h"] / 2, ax, a["w"] / 2
        overlap = max(0.0, min(hi, phi) - max(lo, plo))
        cover = overlap / (hi - lo) if hi > lo else 1.0
        if abs(lat - pc) > half or cover < 0.75:
            lead_miss.append(f"{ld['name']} ({cover:.0%} on pad)")
        elif cover < 0.999:
            lead_part.append(f"{ld['name']} ({cover:.0%})")
    if lead_miss:
        fails.append(f"lead foot does not land on its pad: {lead_miss[:10]}")
    if lead_part:
        warns.append(f"lead foot only partly on its pad (nominal terminal): {lead_part[:8]}"
                     + (f" (+{len(lead_part) - 8} more)" if len(lead_part) > 8 else ""))

    # pitch, from the pads themselves (package mode)
    if "pitch" in spec:
        gaps = _row_pitches(list(act.values()))
        if gaps and any(abs(g - spec["pitch"]) > 0.02 for g in gaps):
            fails.append(f"pad pitch {sorted(set(round(g, 3) for g in gaps))} mm vs datasheet {spec['pitch']} mm")

    # solder-mask slivers: pad-to-pad copper gap
    min_gap = _min_gap(actual)
    info["min_pad_gap_mm"] = round(min_gap, 3) if min_gap is not None else None
    if min_gap is not None and min_gap < 0.15:
        warns.append(f"smallest pad-to-pad gap {min_gap:.3f} mm - below ~0.15 mm there is no solder-mask "
                     "web between pads (bridging risk; check the fab's minimum mask web)")

    # pin-1 marker: silkscreen near pad 1
    p1 = act.get("1")
    if p1 is not None:
        near = 0
        for tr in fp.get("tracks", []):
            if tr["layer"] != "TopOverlay":
                continue
            for (x, y) in ((tr["x1"], tr["y1"]), (tr["x2"], tr["y2"])):
                if math.hypot(x * MIL - p1["x"], y * MIL - p1["y"]) <= silk_marker_radius:
                    near += 1
        for arc in fp.get("arcs", []):
            if arc["layer"] == "TopOverlay" and math.hypot(arc["cx"] * MIL - p1["x"], arc["cy"] * MIL - p1["y"]) <= silk_marker_radius:
                near += 1
        info["silk_near_pin1"] = near
        if near == 0:
            warns.append("no silkscreen within 1.5 mm of pin 1 - is there a pin-1 marker?")

    if fp.get("regions"):
        warns.append(f"footprint has {len(fp['regions'])} copper region(s) beyond its pads - the pad check "
                     "does not include them; judge the land from the overlay (regions drawn in red)")
    return {"verdict": "FAIL" if fails else "PASS", "failures": fails, "warnings": warns, "info": info}


def render_overlay(fp, spec, out_png, px_per_mm=120, title=None):
    """PNG: footprint copper (red fill) under the datasheet pattern (green outline), aligned
    exactly as check() aligns them. For eyeballing a drawing read against the drawing.
    Needs Pillow (the MCP server venv has it)."""
    from PIL import Image, ImageDraw
    actual = footprint_pads(fp)
    expected, _ = expected_pads(spec)
    # align exactly as check() does: one (largest) pad per name on BOTH sides - pairing
    # every same-named sub-pad with the footprint's largest skews the fit
    t = best_transform(list(_signal_pads(expected).values()), actual)
    cx, cy = t["centre"]
    exp = []
    for p in expected:
        x, y = _tf(p["x"], p["y"], t["rotation"], t["mirror"])
        w, h = (p["w"], p["h"]) if t["rotation"] % 180 == 0 else (p["h"], p["w"])
        exp.append({"name": p["name"], "x": x, "y": y, "w": w, "h": h})
    act = [dict(p, x=p["x"] - cx, y=p["y"] - cy) for p in actual]
    allp = exp + act
    for reg in fp.get("regions", []):
        for x, y in reg["points"]:
            allp.append({"x": x * MIL - cx, "y": y * MIL - cy, "w": 0, "h": 0})
    minx = min(p["x"] - p["w"] / 2 for p in allp) - 0.6
    maxx = max(p["x"] + p["w"] / 2 for p in allp) + 0.6
    miny = min(p["y"] - p["h"] / 2 for p in allp) - 0.6
    maxy = max(p["y"] + p["h"] / 2 for p in allp) + 0.9
    W, H = int((maxx - minx) * px_per_mm), int((maxy - miny) * px_per_mm)
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img, "RGBA")

    def box(p):
        return ((p["x"] - p["w"] / 2 - minx) * px_per_mm, (maxy - (p["y"] + p["h"] / 2)) * px_per_mm,
                (p["x"] + p["w"] / 2 - minx) * px_per_mm, (maxy - (p["y"] - p["h"] / 2)) * px_per_mm)

    for p in act:
        d.rectangle(box(p), fill=(220, 40, 40, 110))
    for reg in fp.get("regions", []):         # copper regions (mils, footprint coordinates)
        pts = [((x * MIL - cx - minx) * px_per_mm, (maxy - (y * MIL - cy)) * px_per_mm) for x, y in reg["points"]]
        if len(pts) >= 3:
            d.polygon(pts, fill=(220, 40, 40, 110))
    for p in exp:
        d.rectangle(box(p), outline=(0, 150, 0, 255), width=3)
        bx = box(p)
        d.text(((bx[0] + bx[2]) / 2 - 4, (bx[1] + bx[3]) / 2 - 6), p["name"], fill=(0, 0, 0))
    ox, oy = (0 - minx) * px_per_mm, (maxy - 0) * px_per_mm
    d.line((ox - 12, oy, ox + 12, oy), fill=(0, 0, 255), width=2)
    d.line((ox, oy - 12, ox, oy + 12), fill=(0, 0, 255), width=2)
    # 1 mm scale bar
    d.line((10, H - 12, 10 + px_per_mm, H - 12), fill=(0, 0, 0), width=3)
    d.text((14, H - 30), "1 mm", fill=(0, 0, 0))
    d.text((10, 6), (title or fp.get("name", "")) + "   green = datasheet, red = footprint, blue + = package centre",
           fill=(0, 0, 0))
    img.save(out_png)
    return str(out_png)


def _natural(s):
    return (0, int(s)) if s.isdigit() else (1, s)


def _row_pitches(pads):
    """Centre spacings between neighbouring equal-size pads that share a row or column."""
    out = []
    for axis, other in (("y", "x"), ("x", "y")):
        rows = {}
        for p in pads:
            rows.setdefault((round(p[other], 2), round(p["w"], 2), round(p["h"], 2)), []).append(p[axis])
        for vals in rows.values():
            if len(vals) < 3:          # a row needs 3+ pads; 2 = the two sides of the package
                continue
            vals = sorted(vals)
            out += [b - a for a, b in zip(vals, vals[1:]) if b - a > 1e-3]
    return out


def _min_gap(pads):
    g = None
    for i, a in enumerate(pads):
        for b in pads[i + 1:]:
            if a["name"] == b["name"]:
                continue
            dx = abs(a["x"] - b["x"]) - (a["w"] + b["w"]) / 2
            dy = abs(a["y"] - b["y"]) - (a["h"] + b["h"]) / 2
            d = math.hypot(max(dx, 0), max(dy, 0)) if (dx > 0 or dy > 0) else min(dx, dy)
            g = d if g is None or d < g else g
    return g
