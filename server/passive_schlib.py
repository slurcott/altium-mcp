"""Standard-passive SchLib builder for Altium 365's Library Importer.

Proven 2026-09-24 on 145 0402 resistors (3-part trial + 142-part batch):
one run of SCRIPT turns a template SchLib holding one symbol (e.g. RES-2
downloaded from the workspace) into one component per spec row - name,
description, comment, parameters, Manufacturer n / Manufacturer Part Number n
pairs and a PCBLIB footprint link. The Library Importer (File > Import
Library, with the SchLib AND the footprint's PcbLib added as sources) then
creates every component with its Part Choices, sharing ONE symbol and ONE
footprint for the whole batch ("same geometry" warnings = deduplication).

Why not the other routes (all tried the same day):
  * Component Editor batch grid: works, but one pasted row per Add Component
    click; the Datasheets column appears only when a part has one (column
    shift); plain File > Save is local only (Save to Server = Ctrl+Alt+S).
  * DbLib + Library Importer: components and Part Choices import, but the
    importer never loads the symbol/footprint from a DbLib ("Symbol name
    cannot be empty"), with absolute paths or a LibrarySearchPath.

Everything here except SCRIPT runs without Altium and is unit-tested.
"""
import re
import shutil
import struct
from pathlib import Path

SPEC_PATH = r"C:\Users\Public\altium_mcp\schlib_spec.txt"

# The proven sandbox body. {SCHLIB} and {FOOTPRINT} are filled by render_script;
# rows come from SPEC_PATH: Name|Description|Comment|Param=Value|...
SCRIPT = r"""var
    Doc    : IServerDocument;
    Lib    : ISch_Lib;
    It     : ISch_Iterator;
    Src    : ISch_Component;
    C      : ISch_Component;
    CI     : ISch_Iterator;
    Param  : ISch_Parameter;
    Impl   : ISch_Implementation;
    Spec   : TStringList;
    Line   : String;
    Rest   : String;
    Fld    : String;
    PName  : String;
    PVal   : String;
    I      : Integer;
    K      : Integer;
    P      : Integer;
    NImpl  : Integer;
    Found  : Boolean;
    NComp  : Integer;
begin
    Doc := Client.OpenDocument('SchLib', '{SCHLIB}');
    if Doc = nil then begin ResultText := 'could not open SchLib'; Exit; end;
    Client.ShowDocument(Doc);
    Lib := SchServer.GetCurrentSchDocument;
    if Lib = nil then begin ResultText := 'no current sch doc'; Exit; end;
    It := Lib.SchLibIterator_Create;
    It.AddFilter_ObjectSet(MkSet(eSchComponent));
    Src := It.FirstSchObject;
    Lib.SchIterator_Destroy(It);
    if Src = nil then begin ResultText := 'no source component'; Exit; end;
    SandboxLog('source ' + Src.LibReference);
    Spec := TStringList.Create;
    Spec.LoadFromFile('{SPEC}');
    for I := 0 to Spec.Count - 1 do
    begin
        Line := Spec[I];
        if Line = '' then Continue;
        if I < Spec.Count - 1 then C := Src.Replicate else C := Src;
        Rest := Line;
        K := 0;
        while Rest <> '' do
        begin
            P := Pos('|', Rest);
            if P = 0 then begin Fld := Rest; Rest := ''; end
            else begin Fld := Copy(Rest, 1, P - 1); Rest := Copy(Rest, P + 1, Length(Rest)); end;
            if K = 0 then C.LibReference := Fld
            else if K = 1 then C.ComponentDescription := Fld
            else if K = 2 then C.Comment.Text := Fld
            else
            begin
                P := Pos('=', Fld);
                PName := Copy(Fld, 1, P - 1);
                PVal := Copy(Fld, P + 1, Length(Fld));
                Found := False;
                CI := C.SchIterator_Create;
                CI.AddFilter_ObjectSet(MkSet(eParameter));
                Param := CI.FirstSchObject;
                while Param <> nil do
                begin
                    if UpperCase(Param.Name) = UpperCase(PName) then begin Param.Text := PVal; Found := True; end;
                    Param := CI.NextSchObject;
                end;
                C.SchIterator_Destroy(CI);
                if not Found then
                begin
                    Param := SchServer.SchObjectFactory(eParameter, eCreate_Default);
                    Param.Name := PName;
                    Param.Text := PVal;
                    Param.ParamType := eParameterType_String;
                    Param.ReadOnlyState := eReadOnly_None;
                    Param.IsHidden := True;
                    C.AddSchObject(Param);
                    SchServer.RobotManager.SendMessage(C.I_ObjectAddress, c_BroadCast, SCHM_PrimitiveRegistration, Param.I_ObjectAddress);
                end;
            end;
            K := K + 1;
        end;
        NImpl := 0;
        CI := C.SchIterator_Create;
        CI.AddFilter_ObjectSet(MkSet(eImplementation));
        Impl := CI.FirstSchObject;
        while Impl <> nil do
        begin
            if UpperCase(Impl.ModelType) = 'PCBLIB' then
            begin
                Impl.ModelName := '{FOOTPRINT}';
                Impl.IsCurrent := True;
                NImpl := NImpl + 1;
            end;
            Impl := CI.NextSchObject;
        end;
        C.SchIterator_Destroy(CI);
        if NImpl = 0 then
        begin
            Impl := C.AddSchImplementation;
            Impl.ModelName := '{FOOTPRINT}';
            Impl.ModelType := 'PCBLIB';
            Impl.IsCurrent := True;
            Impl.UseComponentLibrary := True;
        end;
        if I < Spec.Count - 1 then
        begin
            Lib.AddSchComponent(C);
            SchServer.RobotManager.SendMessage(nil, c_BroadCast, SCHM_PrimitiveRegistration, C.I_ObjectAddress);
        end;
        if (I mod 20) = 0 then SandboxLog('row ' + IntToStr(I));
    end;
    Spec.Free;
    Lib.GraphicallyInvalidate;
    NComp := 0;
    It := Lib.SchLibIterator_Create;
    It.AddFilter_ObjectSet(MkSet(eSchComponent));
    C := It.FirstSchObject;
    while C <> nil do begin NComp := NComp + 1; C := It.NextSchObject; end;
    Lib.SchIterator_Destroy(It);
    ResultText := 'components in lib: ' + IntToStr(NComp);
end;"""


def spec_line(name, description, comment, params):
    """One spec row. '|' and '=' inside values would break the Pascal parser."""
    fields = [name, description, comment] + [f"{k}={v}" for k, v in params]
    for f in fields:
        if "|" in f:
            raise ValueError(f"'|' not allowed in a spec field: {f!r}")
    for k, v in params:
        if "=" in k:
            raise ValueError(f"'=' not allowed in a parameter name: {k!r}")
    return "|".join(fields)


def write_spec(lines, path=SPEC_PATH):
    # UTF-8 with BOM: TStringList.LoadFromFile keeps the ohm/degree signs (verified)
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8-sig", newline="\r\n")


def render_script(schlib, footprint, spec=SPEC_PATH):
    for v in (schlib, footprint, spec):
        if "'" in str(v):
            raise ValueError(f"quote not allowed in {v!r}")
    return (SCRIPT.replace("{SCHLIB}", str(schlib)).replace("{FOOTPRINT}", footprint)
            .replace("{SPEC}", str(spec)))


def prepare(template_schlib, out_schlib):
    """Copy the template (one symbol) to the output SchLib the script edits."""
    out = Path(out_schlib)
    if out.exists():
        raise FileExistsError(f"{out} exists - pick a new name (it may be open in Altium)")
    shutil.copy2(template_schlib, out)
    return out


def schlib_component_names(path):
    """Component names in a saved .SchLib: each component is an OLE storage."""
    b = Path(path).read_bytes()
    ssz = 1 << struct.unpack_from("<H", b, 30)[0]
    dir_start = struct.unpack_from("<I", b, 48)[0]
    nfat = struct.unpack_from("<I", b, 44)[0]
    fat = []
    for s in list(struct.unpack_from("<109I", b, 76))[:nfat]:
        fat += struct.unpack_from(f"<{ssz // 4}I", b, 512 + s * ssz)
    chain, s = [], dir_start
    while s < 0xFFFFFFF0:
        chain.append(s)
        s = fat[s]
    d = b"".join(b[512 + s * ssz:512 + (s + 1) * ssz] for s in chain)
    names = []
    for i in range(0, len(d), 128):
        n = struct.unpack_from("<H", d, i + 64)[0]
        if d[i + 66] == 1 and n > 2:                    # storage = component
            names.append(d[i:i + n - 2].decode("utf-16-le"))
    return names


def verify(path, expected_names, footprint, mpn_pattern=None):
    """Offline check of a built SchLib: every expected component present, none
    extra, footprint referenced once per component (and part numbers, if a
    regex is given)."""
    names = schlib_component_names(path)
    b = Path(path).read_bytes()
    fp_hits = len(re.findall(re.escape(footprint.encode()), b))
    out = {"components": len(names),
           "missing": sorted(set(expected_names) - set(names)),
           "unexpected": sorted(set(names) - set(expected_names)),
           "footprint_links": fp_hits}
    if mpn_pattern:
        out["mpn_hits"] = len(re.findall(mpn_pattern.encode(), b))
    out["ok"] = (not out["missing"] and not out["unexpected"]
                 and fp_hits >= len(expected_names))
    return out
