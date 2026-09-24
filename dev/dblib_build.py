"""Build an Altium database library (.DbLib + Access .accdb) from the standard
passive library, for Altium 365's Library Importer. No Altium needed.

    python dev/dblib_build.py --out <folder> --schlib RES-2.SchLib --pcblib "RESC0402(1005)_L.PcbLib" \
        --package 0402 [--enable trial|full]

Why: the Component Editor batch grid takes one pasted row per "Add Component"
click - fine for a trial, error-prone for hundreds. A DbLib lets the Library
Importer create every component (parameters, one shared symbol and footprint,
and part choices from the Manufacturer n / Manufacturer Part Number n columns)
in one run.

The .DbLib layout is copied from a working Altium-written DbLib (Access 2007
backend, bracketed system fields [Description] [Library Ref] [Library Path]
[Footprint Ref] [Footprint Path]; every other column maps to the parameter of
the same name). The .accdb is written through the ACE OLE DB provider from
PowerShell (ADOX), which Office/Access Database Engine installs.
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "server"))
import library_gen as L  # noqa: E402

COLUMNS = ["Part Number", "Name", "Symbol Name", "Library Ref", "Library Path", "Footprint Ref", "Footprint Path",
           "Description", "Comment", "Value", "Case/Package", "Mounting Technology", "Pins",
           "Power", "RoHS Compliant", "Tolerance", "Voltage Rating",
           "Max Operating Temperature", "Min Operating Temperature",
           "Manufacturer 1", "Manufacturer Part Number 1",
           "Manufacturer 2", "Manufacturer Part Number 2",
           "Manufacturer 3", "Manufacturer Part Number 3"]
SYSTEM = {"Description": "[Description]", "Library Ref": "[Library Ref]",
          "Library Path": "[Library Path]", "Footprint Ref": "[Footprint Ref]",
          "Footprint Path": "[Footprint Path]"}
TRIAL_VALUES = ("10Ω", "1kΩ", "100kΩ")


def resistor_records(package, schlib, pcblib, symbol_ref, footprint_ref):
    recs = []
    for r in L.resistor_rows(package):
        if r["value"] == "0Ω" or r["name"] in L.ALREADY_IN_WORKSPACE:
            continue
        recs.append({
            # Name / Symbol Name: what the Library Importer maps by default. Symbol Name is
            # a template parameter it refuses to leave empty (2026-09-24 trial); one shared
            # value keeps a single symbol for the whole batch.
            "Part Number": r["name"], "Name": r["name"], "Symbol Name": symbol_ref,
            # file names only: models are found through the DbLib's LibrarySearchPath
            # (absolute paths were not followed by the Library Importer, 2026-09-24)
            "Library Ref": symbol_ref, "Library Path": Path(schlib).name,
            "Footprint Ref": footprint_ref, "Footprint Path": Path(pcblib).name,
            "Description": r["description"], "Comment": r["value"].replace("Ω", ""),
            "Value": r["value"].replace("Ω", ""), "Case/Package": package,
            "Mounting Technology": "SMT", "Pins": "2", "Power": r["power"],
            "RoHS Compliant": "Yes", "Tolerance": "1%", "Voltage Rating": r["voltage"],
            "Max Operating Temperature": r["tmax"], "Min Operating Temperature": r["tmin"],
            "Manufacturer 1": L.MFR_NAME[r["mfr1"]], "Manufacturer Part Number 1": r["mpn1"],
            "Manufacturer 2": L.MFR_NAME[r["mfr2"]], "Manufacturer Part Number 2": r["mpn2"],
            "Manufacturer 3": L.MFR_NAME[r["mfr3"]], "Manufacturer Part Number 3": r["mpn3"],
        })
    return recs


PS_BUILD = r'''
param([string]$Db, [string]$Json)
$ErrorActionPreference = "Stop"
if (Test-Path $Db) { Remove-Item $Db -Force }
$conn = "Provider=Microsoft.ACE.OLEDB.16.0;Data Source=$Db"
$cat = New-Object -ComObject ADOX.Catalog
[void]$cat.Create($conn)
$cn = $cat.ActiveConnection
$spec = Get-Content -Raw -Encoding UTF8 $Json | ConvertFrom-Json
foreach ($t in $spec.tables) {
    $cols = ($spec.columns | ForEach-Object { "[" + $_ + "] TEXT(255)" }) -join ", "
    [void]$cn.Execute("CREATE TABLE [" + $t.name + "] (" + $cols + ")")
    $rs = New-Object -ComObject ADODB.Recordset
    $rs.Open("[" + $t.name + "]", $cn, 1, 3, 2)
    foreach ($row in $t.rows) {
        $rs.AddNew()
        foreach ($c in $spec.columns) { $rs.Fields.Item($c).Value = [string]$row.$c }
        $rs.Update()
    }
    $rs.Close()
    Write-Output ("{0}: {1} rows" -f $t.name, $t.rows.Count)
}
$cn.Close()
'''


def dblib_text(db_name, tables, enabled, search_path=""):
    out = ["[OutputDatabaseLinkFile]", "Version=1.1", "[DatabaseLinks]",
           f"ConnectionString=Provider=Microsoft.ACE.OLEDB.12.0;Data Source={db_name};Persist Security Info=False",
           "AddMode=2", "RemoveMode=1", "UpdateMode=2", "ViewMode=0", "LeftQuote=[", "RightQuote=]",
           "QuoteTableNames=1", "UseTableSchemaName=0", "DefaultColumnType=VARCHAR(255)",
           "LibraryDatabaseType=Microsoft Access 2007", f"LibraryDatabasePath={db_name}",
           "DatabasePathRelative=1", "TopPanelCollapsed=0", f"LibrarySearchPath={search_path}",
           "OrcadMultiValueDelimiter=,", "SearchSubDirectories=0", "SchemaName=",
           f"LastFocusedTable={tables[0]}"]
    for i, t in enumerate(tables, 1):
        out += [f"[Table{i}]", "SchemaName=", f"TableName={t}",
                f"Enabled={'True' if t in enabled else 'False'}", "UserWhere=0", "UserWhereText="]
    n = 0
    for t in tables:
        for col in COLUMNS:
            n += 1
            param = SYSTEM.get(col, col)
            out += [f"[FieldMap{n}]",
                    f"Options=FieldName={t}.{col}|TableNameOnly={t}|FieldNameOnly={col}|FieldType=1|"
                    f"ParameterName={param}|VisibleOnAdd={'True' if col == 'Value' else 'False'}|"
                    "AddMode=0|RemoveMode=0|UpdateMode=0"]
    return "\r\n".join(out) + "\r\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--schlib", required=True, help="symbol library file (copied into --out)")
    ap.add_argument("--pcblib", required=True, help="footprint library file (copied into --out)")
    ap.add_argument("--symbol-ref", default="RES-2")
    ap.add_argument("--footprint-ref", default="RESC0402(1005)_L")
    ap.add_argument("--package", default="0402")
    ap.add_argument("--enable", choices=("trial", "full"), default="trial")
    ap.add_argument("--tag", default="", help="suffix for the output file names (avoid files open in Altium)")
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    sch = out / Path(args.schlib).name
    pcb = out / Path(args.pcblib).name
    if Path(args.schlib).resolve() != sch.resolve():
        shutil.copy2(args.schlib, sch)
    if Path(args.pcblib).resolve() != pcb.resolve():
        shutil.copy2(args.pcblib, pcb)

    recs = resistor_records(args.package, sch, pcb, args.symbol_ref, args.footprint_ref)
    trial = [r for r in recs if r["Value"] + "Ω" in TRIAL_VALUES]
    full_name, trial_name = f"Resistors_{args.package}", f"Trial_Resistors_{args.package}"
    spec = {"columns": COLUMNS, "tables": [{"name": trial_name, "rows": trial},
                                           {"name": full_name, "rows": recs}]}
    js = out / "dblib_spec.json"
    js.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    ps = out / "dblib_build.ps1"
    ps.write_text(PS_BUILD, encoding="utf-8")
    db = out / f"Standard_Passives_{args.package}{args.tag}.accdb"
    res = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps),
                          "-Db", str(db), "-Json", str(js)], capture_output=True, text=True)
    print(res.stdout.strip(), res.stderr.strip())
    if res.returncode != 0:
        return 1
    enabled = {trial_name} if args.enable == "trial" else {full_name}
    dbl = out / f"Standard_Passives_{args.package}{args.tag}.DbLib"
    dbl.write_text(dblib_text(db.name, [trial_name, full_name], enabled,
                              search_path=str(out.resolve())), encoding="utf-8")
    js.unlink()
    ps.unlink()
    print(f"wrote {dbl.name} (enabled: {', '.join(sorted(enabled))}), {db.name}: "
          f"{len(trial)} trial + {len(recs)} full rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
