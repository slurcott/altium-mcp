"""Write tracks and vias into a board from a table, and prove from the saved file that they landed (backlog B38).

The plan is JSON, coordinates in mils from the board origin:
    {"tracks": [{"layer": "Mid Layer 1", "x1":, "y1":, "x2":, "y2":, "w":, "net": "SDA"}, ...],
     "vias":   [{"x":, "y":, "d": 20, "hole": 10, "net": "SDA"}, ...]}
A net of "" or null leaves the object without a net.

Three pure pieces, used by the pcb_add_copper tool:
  table_lines(plan)  -> the plain table the Altium script reads (hundredths of a mil, one record per line)
  write_script(...)  -> the DelphiScript body (same statements proven by hand on 2026-10-08: 551 routed objects,
                        then 94 stitching vias). Nothing is created inside an iterator loop; the list variable is
                        the script's own (the sandbox scratch List1 wedges the engine).
  verify(B, plan)    -> every planned object present in a loaded board (pcb_nets.load), on its layer, with its net
"""
import json

LAYER_CODE = {"Top Layer": "T", "Bottom Layer": "B", "Mid Layer 1": "1", "Mid Layer 2": "2",
              "Mid Layer 3": "3", "Mid Layer 4": "4"}
CODE_CONST = {"T": "eTopLayer", "B": "eBottomLayer", "1": "eMidLayer1", "2": "eMidLayer2",
              "3": "eMidLayer3", "4": "eMidLayer4"}
# Names the script uses that the linter may not have seen in a completed run yet.
NEW_API = ["tstringlist", "loadfromfile", "count", "free", "create", "enetobject", "name", "net",
           "pcbobjectfactory", "etrackobject", "eviaobject", "enodimension", "ecreate_default",
           "x1", "y1", "x2", "y2", "x", "y", "width", "layer", "size", "holesize", "lowlayer", "highlayer",
           "etoplayer", "ebottomlayer", "emidlayer1", "emidlayer2", "emidlayer3", "emidlayer4",
           "addpcbobject", "pcbm_boardregisteration", "xorigin", "yorigin", "preprocess", "postprocess",
           "viewmanager_fullupdate"]


def load_plan(path):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return {"tracks": list(d.get("tracks", [])), "vias": list(d.get("vias", []))}


def check_plan(plan):
    """Return a list of problems that must stop the write (empty = usable)."""
    bad = []
    for i, t in enumerate(plan["tracks"]):
        if t.get("layer") not in LAYER_CODE:
            bad.append(f"track {i}: layer {t.get('layer')!r} is not one of {sorted(LAYER_CODE)}")
        if not float(t.get("w", 0)) > 0:
            bad.append(f"track {i}: width must be positive")
        if abs(t["x1"] - t["x2"]) < 1e-6 and abs(t["y1"] - t["y2"]) < 1e-6:
            bad.append(f"track {i}: zero length at ({t['x1']}, {t['y1']})")
    for i, v in enumerate(plan["vias"]):
        if not float(v.get("d", 0)) > float(v.get("hole", 0)) > 0:
            bad.append(f"via {i}: needs pad d > hole > 0")
    for o in plan["tracks"] + plan["vias"]:
        n = o.get("net") or ""
        if "|" in n or "'" in n or "\n" in n:
            bad.append(f"net name {n!r} cannot be written to the table")
            break
    if not plan["tracks"] and not plan["vias"]:
        bad.append("the plan holds no tracks and no vias")
    return bad[:20]


def _h(v):
    return str(int(round(float(v) * 100)))


def table_lines(plan):
    """N|net  T|layer code|x1|y1|x2|y2|width  V|x|y|pad|hole - grouped by net, hundredths of a mil."""
    nets = []
    for o in plan["tracks"] + plan["vias"]:
        n = o.get("net") or ""
        if n not in nets:
            nets.append(n)
    lines = []
    for n in nets:
        lines.append(f"N|{n}")
        for t in plan["tracks"]:
            if (t.get("net") or "") == n:
                lines.append("|".join(["T", LAYER_CODE[t["layer"]], _h(t["x1"]), _h(t["y1"]), _h(t["x2"]), _h(t["y2"]), _h(t["w"])]))
        for v in plan["vias"]:
            if (v.get("net") or "") == n:
                lines.append("|".join(["V", _h(v["x"]), _h(v["y"]), _h(v["d"]), _h(v["hole"])]))
    return lines


def _q(path):
    return str(path).replace("'", "''")


def write_script(board_path, table_path, dry_run=True):
    """DelphiScript body. Dry run: reads the table and looks up every net, creates nothing."""
    layer_chain = "\n".join(
        f"            {'if' if i == 0 else 'else if'} F2 = '{code}' then Trk.Layer := {const}"
        for i, (code, const) in enumerate(CODE_CONST.items())) + ";"
    create_track = f"""            Trk := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
            Trk.X1 := Board.XOrigin + MilsToCoord(StrToInt(F3) / 100);
            Trk.Y1 := Board.YOrigin + MilsToCoord(StrToInt(F4) / 100);
            Trk.X2 := Board.XOrigin + MilsToCoord(StrToInt(F5) / 100);
            Trk.Y2 := Board.YOrigin + MilsToCoord(StrToInt(F6) / 100);
            Trk.Width := MilsToCoord(StrToInt(F7) / 100);
{layer_chain}
            if Cur <> nil then Trk.Net := Cur;
            Board.AddPCBObject(Trk);
            PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Trk.I_ObjectAddress);
"""
    create_via = """            Via := PCBServer.PCBObjectFactory(eViaObject, eNoDimension, eCreate_Default);
            Via.X := Board.XOrigin + MilsToCoord(StrToInt(F2) / 100);
            Via.Y := Board.YOrigin + MilsToCoord(StrToInt(F3) / 100);
            Via.Size := MilsToCoord(StrToInt(F4) / 100);
            Via.HoleSize := MilsToCoord(StrToInt(F5) / 100);
            Via.LowLayer := eTopLayer;
            Via.HighLayer := eBottomLayer;
            if Cur <> nil then Via.Net := Cur;
            Board.AddPCBObject(Via);
            PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Via.I_ObjectAddress);
"""
    if dry_run:
        create_track = create_via = ""
    pre = "" if dry_run else "    PCBServer.PreProcess;\n"
    post = "" if dry_run else "    PCBServer.PostProcess;\n    Board.ViewManager_FullUpdate;\n"
    word = "would add" if dry_run else "added"
    return f"""var
    Board : IPCB_Board;
    Iter  : IPCB_BoardIterator;
    Net   : IPCB_Net;
    Cur   : IPCB_Net;
    Trk   : IPCB_Track;
    Via   : IPCB_Via;
    Lines : TStringList;
    K, Q, Idx, NT, NV, NoNet : Integer;
    Rec, Want, Fld, Kind, Missing, F2, F3, F4, F5, F6, F7 : String;
begin
    ResultText := 'start';
    S1 := '{_q(board_path)}';
    S2 := '{_q(table_path)}';
    Client.ShowDocument(Client.OpenDocument('PCB', S1));
    Board := PCBServer.GetCurrentPCBBoard;
    if Board = nil then begin ResultText := 'ERROR no current board'; Exit; end;
    if Board.FileName <> S1 then begin ResultText := 'ERROR wrong board focused: ' + Board.FileName; Exit; end;
    Lines := TStringList.Create;
    Lines.LoadFromFile(S2);
    NT := 0; NV := 0; NoNet := 0; Cur := nil; Missing := '';
    SandboxLog('table lines ' + IntToStr(Lines.Count));
{pre}    for K := 0 to Lines.Count - 1 do
    begin
        Rec := Lines[K];
        Idx := 0; Kind := ''; F2 := ''; F3 := ''; F4 := ''; F5 := ''; F6 := ''; F7 := '';
        while Length(Rec) > 0 do
        begin
            Q := Pos('|', Rec);
            if Q = 0 then begin Fld := Rec; Rec := ''; end
            else begin Fld := Copy(Rec, 1, Q - 1); Rec := Copy(Rec, Q + 1, 500); end;
            Idx := Idx + 1;
            if Idx = 1 then Kind := Fld
            else if Idx = 2 then F2 := Fld
            else if Idx = 3 then F3 := Fld
            else if Idx = 4 then F4 := Fld
            else if Idx = 5 then F5 := Fld
            else if Idx = 6 then F6 := Fld
            else if Idx = 7 then F7 := Fld;
        end;
        if Kind = 'N' then
        begin
            Want := F2;
            Cur := nil;
            if Want <> '' then
            begin
                Iter := Board.BoardIterator_Create;
                Iter.AddFilter_ObjectSet(MkSet(eNetObject));
                Iter.AddFilter_LayerSet(AllLayers);
                Iter.AddFilter_Method(eProcessAll);
                Net := Iter.FirstPCBObject;
                while Net <> nil do
                begin
                    if Net.Name = Want then Cur := Net;
                    Net := Iter.NextPCBObject;
                end;
                Board.BoardIterator_Destroy(Iter);
                if Cur = nil then begin NoNet := NoNet + 1; Missing := Missing + Want + ' '; end;
            end;
        end
        else if Kind = 'T' then
        begin
{create_track}            NT := NT + 1;
        end
        else if Kind = 'V' then
        begin
{create_via}            NV := NV + 1;
        end;
    end;
{post}    Lines.Free;
    ResultText := '{word} tracks ' + IntToStr(NT) + ' vias ' + IntToStr(NV) + ' nets-not-found ' + IntToStr(NoNet) + ' ' + Missing;
end;"""


def parse_result(text):
    """'added tracks 425 vias 126 nets-not-found 0 ' -> dict (None when the text is not a result line)."""
    import re
    m = re.match(r"(would add|added) tracks (\d+) vias (\d+) nets-not-found (\d+)\s*(.*)", (text or "").strip())
    if not m:
        return None
    return {"dry_run": m.group(1) == "would add", "tracks": int(m.group(2)), "vias": int(m.group(3)),
            "nets_not_found": int(m.group(4)), "missing_nets": m.group(5).split()}


def verify(B, plan, long_layer=lambda s: s, tol=0.5):
    """Compare a loaded board (pcb_nets.load) with the plan: what is missing, and which objects carry another net."""
    near = lambda a, b: abs(a - b) <= tol
    ft = [dict(t, layer=long_layer(t["layer"])) for t in B["tracks"]]
    miss_t, miss_v, wrong = [], [], []
    for t in plan["tracks"]:
        m = [f for f in ft if f["layer"] == t["layer"] and near(f["w"], float(t["w"])) and
             ((near(f["x1"], t["x1"]) and near(f["y1"], t["y1"]) and near(f["x2"], t["x2"]) and near(f["y2"], t["y2"])) or
              (near(f["x1"], t["x2"]) and near(f["y1"], t["y2"]) and near(f["x2"], t["x1"]) and near(f["y2"], t["y1"])))]
        if not m:
            miss_t.append(t)
        elif (t.get("net") or None) not in {f["net"] or None for f in m}:
            wrong.append({"track": [t["x1"], t["y1"], t["x2"], t["y2"]], "planned": t.get("net"), "file": m[0]["net"]})
    for v in plan["vias"]:
        m = [f for f in B["vias"] if near(f["x"], v["x"]) and near(f["y"], v["y"]) and near(f["d"], float(v["d"]))]
        if not m:
            miss_v.append(v)
        elif (v.get("net") or None) not in {f["net"] or None for f in m}:
            wrong.append({"via": [v["x"], v["y"]], "planned": v.get("net"), "file": m[0]["net"]})
    return {"planned_tracks": len(plan["tracks"]), "planned_vias": len(plan["vias"]),
            "missing_tracks": len(miss_t), "missing_vias": len(miss_v), "wrong_net": wrong[:20],
            "wrong_net_total": len(wrong), "ok": not (miss_t or miss_v or wrong),
            "first_missing": ([{"track": [t["x1"], t["y1"], t["x2"], t["y2"], t["layer"]]} for t in miss_t[:5]] +
                              [{"via": [v["x"], v["y"]]} for v in miss_v[:5]])}
