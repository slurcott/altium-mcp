"""Offline helpers for create_footprints_batch spec files (B23).

create_footprints_batch takes coordinates RELATIVE TO THE LIBRARY ORIGIN (proven live
2026-09-26: a spec written 50000-based - the origin's raw value - landed 50000 mil off,
read back from the saved .PcbLib, where pads are stored origin-relative). So a footprint
centred on its origin has pads centred on 0,0 in the spec. (The earlier belief that the
spec is raw came from Altium's own primitive dump, which reports absolute coordinates.)
These helpers:

  * resolve the FPLIB path (the C:\\Users\\Steve junction and the real path
    are the same file; opening it under both names gives two live copies), and
  * compute each footprint's pad-extent centre from the spec - it must be ~0,0.
"""
import os
import tempfile

TOL_MILS = 1.0


def _fields(line):
    return [f.strip() for f in line.rstrip("\r\n").split("|")]


def resolve_fplib(spec_path):
    """Return a spec path whose FPLIB line holds the fully resolved library path.

    Returns (path_to_use, original_fplib, resolved_fplib). If nothing changes
    the original spec path is returned; otherwise a temp copy is written.
    """
    with open(spec_path, encoding="cp1252") as f:
        lines = f.readlines()
    for i, line in enumerate(lines):
        fl = _fields(line)
        if fl and fl[0].upper() == "FPLIB" and len(fl) > 1 and fl[1]:
            real = os.path.realpath(fl[1])
            if os.path.normcase(real) == os.path.normcase(fl[1]):
                return spec_path, fl[1], real
            lines[i] = "FPLIB|" + real + "\n"
            fd, tmp = tempfile.mkstemp(suffix="_spec.txt")
            with os.fdopen(fd, "w", encoding="cp1252") as f:
                f.writelines(lines)
            return tmp, fl[1], real
        if fl and fl[0].upper() == "FOOTPRINT":
            break
    return spec_path, None, None


def pad_centres(spec_text):
    """{footprint: (cx, cy)} = centre of the pad-location extent, in mils."""
    out, name, xs, ys = {}, None, [], []

    def flush():
        if name is not None and xs:
            out[name] = ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)

    for line in spec_text.splitlines():
        fl = _fields(line)
        kind = fl[0].upper() if fl else ""
        if kind == "FOOTPRINT":
            flush()
            name, xs, ys = fl[1] if len(fl) > 1 else "", [], []
        elif kind == "PAD" and name is not None and len(fl) > 3:
            try:
                xs.append(float(fl[2]))
                ys.append(float(fl[3]))
            except ValueError:
                pass
    flush()
    return out


def off_origin(centres, origin_x=0.0, origin_y=0.0, tol=TOL_MILS):
    """Footprints whose pad centre is not on the footprint origin (spec coords are
    origin-relative, so the target is 0,0 - pass the origin only for raw specs).

    Returns a list of {name, dx, dy} (mils, centre minus origin). Footprints
    that are deliberately asymmetric (e.g. pin 1 at origin) will show here too;
    it is a warning, not an error.
    """
    bad = []
    for n, (cx, cy) in centres.items():
        dx, dy = cx - origin_x, cy - origin_y
        if abs(dx) > tol or abs(dy) > tol:
            bad.append({"name": n, "dx": round(dx, 3), "dy": round(dy, 3)})
    return bad
