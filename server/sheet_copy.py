"""Copy a (released) schematic sheet into another project, adapted - the offline parts.

Proven by hand 2026-09-26 (FE B_1 "CAN Interface.SchDoc" -> OV4F keypad): copy the file on
disk (never touch the source), rename its ports to the target project's names and IO
types, reset designators so annotation renumbers only the copied parts, save. The one
trap found: resetting EVERY designator also hits named parts (TP_TXCAN -> TP_TXCAN?);
only plain auto-style designators - LETTERS then a NUMBER (C14, U3, TP12) - are reset;
anything else (TP_5v0, R_S3, TP_TXCAN) is a deliberate name and is kept.

Everything except SCRIPT runs without Altium and is unit-tested.
"""
import re
import shutil
from pathlib import Path

IOTYPES = {"unspecified": 0, "output": 1, "input": 2, "bidirectional": 3}


def reset_designator(des):
    """'C14' -> 'C?'; only letters-then-number designators; named parts (TP_5v0) -> None."""
    m = re.match(r"^([A-Za-z]+)(\d+)$", des)
    return (m.group(1) + "?") if m else None


def plan(src, dst, port_map):
    """Validate inputs; returns (spec lines for SCRIPT). port_map: {old: new or {name, io}}."""
    src, dst = Path(src), Path(dst)
    if not src.exists():
        raise FileNotFoundError(src)
    if dst.exists():
        raise FileExistsError(f"{dst} exists - pick a new name (never overwrite a sheet)")
    if src.resolve() == dst.resolve():
        raise ValueError("source and destination are the same file")
    lines = []
    for old, new in (port_map or {}).items():
        if isinstance(new, dict):
            name, io = new["name"], new.get("io")
        else:
            name, io = new, None
        if "|" in old or "|" in name:
            raise ValueError("port names must not contain '|'")
        code = -1 if io is None else IOTYPES[io.lower()] if isinstance(io, str) else int(io)
        lines.append(f"PORT|{old}|{name}|{code}")
    return lines


def copy_file(src, dst):
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return str(dst)


# Reads SPEC lines PORT|old|new|iotype(-1 = keep). {DOC} and {SPEC} filled by render_script.
SCRIPT = r"""var
    Doc   : IServerDocument;
    Sch   : ISch_Document;
    It    : ISch_Iterator;
    P     : ISch_Port;
    C     : ISch_Component;
    Spec  : TStringList;
    F     : TStringList;
    Line  : String;
    Rest  : String;
    Pre   : String;
    I     : Integer;
    K     : Integer;
    NP    : Integer;
    ND    : Integer;
begin
    Doc := Client.OpenDocument('SCH', '{DOC}');
    if Doc = nil then begin ResultText := 'no doc'; Exit; end;
    Sch := SchServer.GetSchDocumentByPath('{DOC}');
    if Sch = nil then begin ResultText := 'not loaded'; Exit; end;
    Spec := TStringList.Create;
    F := TStringList.Create;
    Spec.LoadFromFile('{SPEC}');
    NP := 0; ND := 0;
    SchServer.ProcessControl.PreProcess(Sch, '');
    It := Sch.SchIterator_Create;
    It.AddFilter_ObjectSet(MkSet(ePort));
    P := It.FirstSchObject;
    while P <> nil do
    begin
        for I := 0 to Spec.Count - 1 do
        begin
            Line := Spec[I];
            F.Clear;
            Rest := Line;
            while Rest <> '' do
            begin
                K := Pos('|', Rest);
                if K = 0 then begin F.Add(Rest); Rest := ''; end
                else begin F.Add(Copy(Rest, 1, K - 1)); Rest := Copy(Rest, K + 1, Length(Rest)); end;
            end;
            if (F.Count = 4) and (F[0] = 'PORT') and (P.Name = F[1]) then
            begin
                SchServer.RobotManager.SendMessage(P.I_ObjectAddress, c_BroadCast, SCHM_BeginModify, c_NoEventData);
                P.Name := F[2];
                if StrToInt(F[3]) >= 0 then P.IOType := StrToInt(F[3]);
                SchServer.RobotManager.SendMessage(P.I_ObjectAddress, c_BroadCast, SCHM_EndModify, c_NoEventData);
                NP := NP + 1;
                Break;
            end;
        end;
        P := It.NextSchObject;
    end;
    Sch.SchIterator_Destroy(It);
    if {RESET} = 1 then
    begin
        It := Sch.SchIterator_Create;
        It.AddFilter_ObjectSet(MkSet(eSchComponent));
        C := It.FirstSchObject;
        while C <> nil do
        begin
            Pre := C.Designator.Text;
            K := Length(Pre);
            while (K > 0) and (Pre[K] >= '0') and (Pre[K] <= '9') do K := K - 1;
            I := 1;
            while (I <= K) and (((Pre[I] >= 'A') and (Pre[I] <= 'Z')) or ((Pre[I] >= 'a') and (Pre[I] <= 'z'))) do I := I + 1;
            if (K > 0) and (K < Length(Pre)) and (I > K) then
            begin
                SchServer.RobotManager.SendMessage(C.I_ObjectAddress, c_BroadCast, SCHM_BeginModify, c_NoEventData);
                C.Designator.Text := Copy(Pre, 1, K) + '?';
                SchServer.RobotManager.SendMessage(C.I_ObjectAddress, c_BroadCast, SCHM_EndModify, c_NoEventData);
                ND := ND + 1;
            end;
            C := It.NextSchObject;
        end;
        Sch.SchIterator_Destroy(It);
    end;
    SchServer.ProcessControl.PostProcess(Sch, '');
    Sch.GraphicallyInvalidate;
    Spec.Free;
    F.Free;
    ResultText := 'ports renamed ' + IntToStr(NP) + ', designators reset ' + IntToStr(ND);
end;"""


def render_script(doc, spec, reset=True):
    for v in (doc, spec):
        if "'" in str(v):
            raise ValueError("paths must not contain a quote")
    return (SCRIPT.replace("{DOC}", str(doc)).replace("{SPEC}", str(spec))
            .replace("{RESET}", "1" if reset else "0"))
