"""Put library-swapped schematic parts back on their wires, and prove it (B24).

Replacing a part with a library part whose symbol has a different pin span,
origin or direction leaves its pins off the old wire ends. Proven on the OV4F
B_1 boards 2026-09-25 (FE 58 + TMC 42 placements, netlist IDENTICAL):

  plan  : for every part whose Comment changed since a pre-swap copy of the
          sheets (the baseline), work out
            ROT  - quarter turns so pin 1->2 points the old way (then re-plan
                   from the saved sheet, since every pin moves),
            MOVE - shift so pin 1 lands on the old pin-1 spot,
            WIRE - a straight wire from the new pin 2 to the old pin-2 spot.
          Anything else (not 2 pins, a direction that cannot be matched, a
          mirrored part that is still wrong after one turn) is MANUAL.
  check : per sheet, every pin's net membership (the set of pins it shares a
          net with) must equal the baseline's.

Everything except SCRIPT runs without Altium and is unit-tested.
"""
from pathlib import Path

import schdoc_file as S

SPEC_PATH = r"C:\Users\Public\altium_mcp\realign_spec.txt"

# Reads SPEC lines ROT|sheet|des|orientation, MOVE|sheet|des|dx|dy,
# WIRE|sheet|x1|y1|x2|y2 (mils). Proven 2026-09-25 as dev/realign_swap.pas.
SCRIPT = r"""var
    Spec   : TStringList;
    F      : TStringList;
    Line   : String;
    Rest   : String;
    Path   : String;
    Doc    : IServerDocument;
    Sch    : ISch_Document;
    Iter   : ISch_Iterator;
    C      : ISch_Component;
    X      : ISch_Component;
    W      : ISch_Wire;
    I      : Integer;
    P      : Integer;
    NRot   : Integer;
    NMove  : Integer;
    NWire  : Integer;
    NMiss  : Integer;
begin
    NRot := 0; NMove := 0; NWire := 0; NMiss := 0;
    Spec := TStringList.Create;
    F := TStringList.Create;
    Spec.LoadFromFile('{SPEC}');
    for I := 0 to Spec.Count - 1 do
    begin
        Line := Spec[I];
        if Line = '' then Continue;
        F.Clear;
        Rest := Line;
        while Rest <> '' do
        begin
            P := Pos('|', Rest);
            if P = 0 then begin F.Add(Rest); Rest := ''; end
            else begin F.Add(Copy(Rest, 1, P - 1)); Rest := Copy(Rest, P + 1, Length(Rest)); end;
        end;
        Path := F[1];
        Doc := Client.OpenDocument('SCH', Path);
        if Doc = nil then begin SandboxLog('no doc ' + Path); NMiss := NMiss + 1; Continue; end;
        Sch := SchServer.GetSchDocumentByPath(Path);
        if Sch = nil then begin SandboxLog('not loaded ' + Path); NMiss := NMiss + 1; Continue; end;
        SchServer.ProcessControl.PreProcess(Sch, '');
        if F[0] = 'WIRE' then
        begin
            W := SchServer.SchObjectFactory(eWire, eCreate_GlobalCopy);
            W.Location := Point(MilsToCoord(StrToInt(F[2])), MilsToCoord(StrToInt(F[3])));
            W.InsertVertex := 1;
            W.SetState_Vertex(1, Point(MilsToCoord(StrToInt(F[2])), MilsToCoord(StrToInt(F[3]))));
            W.InsertVertex := 2;
            W.SetState_Vertex(2, Point(MilsToCoord(StrToInt(F[4])), MilsToCoord(StrToInt(F[5]))));
            Sch.RegisterSchObjectInContainer(W);
            SchServer.RobotManager.SendMessage(Sch.I_ObjectAddress, c_BroadCast, SCHM_PrimitiveRegistration, W.I_ObjectAddress);
            NWire := NWire + 1;
        end
        else
        begin
            C := nil;
            Iter := Sch.SchIterator_Create;
            Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
            X := Iter.FirstSchObject;
            while X <> nil do
            begin
                if X.Designator.Text = F[2] then C := X;
                X := Iter.NextSchObject;
            end;
            Sch.SchIterator_Destroy(Iter);
            if C = nil then begin SandboxLog('missing ' + F[2]); NMiss := NMiss + 1; end
            else
            begin
                SchServer.RobotManager.SendMessage(C.I_ObjectAddress, c_BroadCast, SCHM_BeginModify, c_NoEventData);
                if F[0] = 'ROT' then begin C.Orientation := StrToInt(F[3]); NRot := NRot + 1; end
                else if F[0] = 'MOVE' then
                begin
                    C.MoveByXY(MilsToCoord(StrToInt(F[3])), MilsToCoord(StrToInt(F[4])));
                    NMove := NMove + 1;
                end;
                SchServer.RobotManager.SendMessage(C.I_ObjectAddress, c_BroadCast, SCHM_EndModify, c_NoEventData);
            end;
        end;
        SchServer.ProcessControl.PostProcess(Sch, '');
        Sch.GraphicallyInvalidate;
    end;
    Spec.Free;
    F.Free;
    ResultText := 'rotated ' + IntToStr(NRot) + ', moved ' + IntToStr(NMove) + ', wires ' + IntToStr(NWire) + ', missing ' + IntToStr(NMiss);
end;"""


def render_script(spec=SPEC_PATH):
    if "'" in spec:
        raise ValueError("spec path must not contain a quote")
    return SCRIPT.replace("{SPEC}", spec)


def write_spec(lines, path=SPEC_PATH):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(lines) + "\n", encoding="cp1252")
    return path


def sheets(prj):
    """Schematic sheets of a .PrjPcb, as absolute paths."""
    prj = Path(prj)
    return [prj.parent / l.split("=", 1)[1].strip() for l in prj.read_text(encoding="latin-1").splitlines()
            if l.startswith("DocumentPath=") and l.strip().lower().endswith(".schdoc")]


def baseline_file(base_dir, name):
    """The baseline copy of sheet `name` (History copies like X.~(3).SchDoc count)."""
    stem = name.split(".SchDoc")[0]
    hits = [p for p in Path(base_dir).rglob("*.SchDoc")
            if p.name.split(".~")[0].split(".SchDoc")[0] == stem]
    return hits[0] if hits else None


def _pins(c):
    return {p["designator"]: (round(p["hot_x"]), round(p["hot_y"])) for p in c.get("pins", [])}


def _unit(v):
    x, y = v
    return ((x > 0) - (x < 0), (y > 0) - (y < 0))


def _turns(vn, vo):
    """Quarter turns CCW that bring direction vn onto vo, or None."""
    v, k = vn, 0
    while _unit(v) != _unit(vo) and k < 4:
        v, k = (-v[1], v[0]), k + 1
    return None if k == 4 else k


def plan_components(sheet, old, new, only=(), rotated=(), done=()):
    """Plan lines + manual notes for one sheet from component dicts.

    old/new: lists of component dicts (schdoc_file.query shape: designator,
    comment, orientation, mirrored, pins[{designator, hot_x, hot_y}]).
    rotated: designators already turned in an earlier pass - if one still
    points the wrong way it becomes MANUAL instead of turning again.
    done: designators whose pins already have their baseline connectivity -
    skipped, so re-running on a fixed sheet plans nothing (no duplicate wires)."""
    lines, manual, n = [], [], 0
    old = {c["designator"]: c for c in old}
    for c in new:
        d = c["designator"]
        o = old.get(d)
        if (only and d not in only) or d in done:
            continue
        if o is None or (o.get("comment") or "") == (c.get("comment") or ""):
            continue
        op, np_ = _pins(o), _pins(c)
        if set(op) != {"1", "2"} or set(np_) != {"1", "2"}:
            manual.append((str(sheet), d, "pins old %s new %s" % (sorted(op), sorted(np_))))
            continue
        if op == np_:
            continue
        vo = (op["2"][0] - op["1"][0], op["2"][1] - op["1"][1])
        vn = (np_["2"][0] - np_["1"][0], np_["2"][1] - np_["1"][1])
        if _unit(vo) != _unit(vn):
            k = _turns(vn, vo)
            if k is None:
                manual.append((str(sheet), d, "direction cannot be matched old %s new %s" % (vo, vn)))
                continue
            if d in rotated:
                manual.append((str(sheet), d, "still wrong after a turn (mirrored=%s)" % bool(c.get("mirrored"))))
                continue
            # a mirrored part turns the other way for the same Orientation step
            step = -k if c.get("mirrored") else k
            lines.append(f"ROT|{sheet}|{d}|{(int(c.get('orientation') or 0) + step) % 4}")
            n += 1
            continue
        dx, dy = op["1"][0] - np_["1"][0], op["1"][1] - np_["1"][1]
        if dx or dy:
            lines.append(f"MOVE|{sheet}|{d}|{dx}|{dy}")
        p2 = (np_["2"][0] + dx, np_["2"][1] + dy)
        if p2 != op["2"]:
            lines.append(f"WIRE|{sheet}|{p2[0]}|{p2[1]}|{op['2'][0]}|{op['2'][1]}")
        n += 1
    return lines, manual, n


def plan(base_dir, prj, only=(), rotated=()):
    """(lines, manual, parts) for every sheet of the project."""
    lines, manual, n = [], [], 0
    for sh in sheets(prj):
        b = baseline_file(base_dir, sh.name)
        if b is None:
            continue
        mo, mn = _membership(S.netlist(str(b))), _membership(S.netlist(str(sh)))
        new = S.query(str(sh), "component")
        done = {c["designator"] for c in new
                if all(mo.get(f"{c['designator']}.{q}") == mn.get(f"{c['designator']}.{q}")
                       for q in ("1", "2"))}
        l, m, k = plan_components(sh, S.query(str(b), "component"), new, only, rotated, done)
        lines += l
        manual += m
        n += k
    return lines, manual, n


def _membership(nets):
    """{pin: frozenset of every pin on its net}. nets: [{pins: [...]}]."""
    r = {}
    for net in nets:
        m = frozenset(net["pins"])
        for p in net["pins"]:
            r[p] = m
    return r


def membership_diff(old_nets, new_nets):
    """[(pin, was, now)] where a pin's net membership changed."""
    mo, mn = _membership(old_nets), _membership(new_nets)
    return [(p, sorted(mo.get(p, [])), sorted(mn.get(p, [])))
            for p in sorted(set(mo) | set(mn)) if mo.get(p) != mn.get(p)]


def check(base_dir, prj):
    """{"sheets": [{sheet, pins, differences, first}], "identical": bool}."""
    out, bad = [], 0
    for sh in sheets(prj):
        b = baseline_file(base_dir, sh.name)
        if b is None:
            out.append({"sheet": sh.name, "error": "no baseline"})
            bad += 1
            continue
        new = S.netlist(str(sh))
        diffs = membership_diff(S.netlist(str(b)), new)
        out.append({"sheet": sh.name, "pins": sum(len(n["pins"]) for n in new),
                    "differences": len(diffs),
                    "first": [{"pin": p, "was": a, "now": c} for p, a, c in diffs[:12]]})
        bad += len(diffs)
    return {"sheets": out, "identical": bad == 0}
