"""Minimal stdlib .xlsx reader: dumps every sheet's rows as JSON lists."""
import json
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def col_index(ref):
    letters = re.match(r"[A-Z]+", ref).group(0)
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_xlsx(path):
    z = zipfile.ZipFile(path)
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS):
            shared.append("".join(t.text or "" for t in si.iter("{%s}t" % NS["m"])))
    sheets = {}
    for name in sorted(n for n in z.namelist() if n.startswith("xl/worksheets/sheet")):
        rows = []
        for row in ET.fromstring(z.read(name)).iter("{%s}row" % NS["m"]):
            cells = {}
            for c in row.findall("m:c", NS):
                v = c.find("m:v", NS)
                t = c.get("t")
                if t == "s" and v is not None:
                    val = shared[int(v.text)]
                elif t == "inlineStr":
                    val = "".join(x.text or "" for x in c.iter("{%s}t" % NS["m"]))
                else:
                    val = v.text if v is not None else None
                cells[col_index(c.get("r"))] = val
            if cells:
                rows.append([cells.get(i) for i in range(max(cells) + 1)])
        sheets[name] = rows
    return sheets


if __name__ == "__main__":  # pragma: no cover
    for p in sys.argv[1:]:
        for sheet, rows in read_xlsx(p).items():
            print("=====", p, sheet, len(rows))
            for r in rows:
                if any(x not in (None, "") for x in r):
                    print(json.dumps(r, ensure_ascii=False))
