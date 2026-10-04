"""Parse a saved Altium DRC report ("Design Rule Check - <board>.drc", text) - offline, no Altium.

Altium writes it to "Project Outputs for <project>/" when Tools > Design Rule Check runs with
"Create Report File" ticked. Returns per-rule counts and the violation lines, so a review can read the
DRC result without scripting the live board (backlog B33; live violation reading wedged once, B32).
"""
import os
import re
import time


def parse(path, rule_filter=None, max_lines=60):
    text = open(path, encoding="utf-8", errors="replace").read()
    rules, cur = [], None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("Processing Rule :"):
            cur = {"rule": s[len("Processing Rule :"):].strip(), "count": 0, "violations": []}
            rules.append(cur)
        elif s.startswith("Rule Violations :") and cur is not None:
            cur["count"] = int(re.sub(r"\D", "", s) or 0)
        elif s.startswith("Violation between") and cur is not None:
            cur["violations"].append(s[len("Violation between "):])
    total = re.search(r"Violations Detected\s*:\s*(\d+)", text)
    out = []
    for r in rules:
        if r["count"] == 0:
            continue
        if rule_filter and rule_filter.lower() not in r["rule"].lower():
            continue
        out.append({"rule": r["rule"], "count": r["count"], "violations": r["violations"][:max_lines],
                    "truncated": max(0, len(r["violations"]) - max_lines)})
    return {"report": path, "written": time.ctime(os.path.getmtime(path)),
            "total": int(total.group(1)) if total else sum(r["count"] for r in rules),
            "rules_with_violations": out}


def find_report(project_or_board):
    """Locate the newest .drc report next to a .PrjPcb / .PcbDoc (in any 'Project Outputs for *' folder)."""
    d = os.path.dirname(os.path.abspath(project_or_board))
    cands = []
    for root, _, files in os.walk(d):
        if "Project Outputs for" not in root and root != d:
            continue
        cands += [os.path.join(root, f) for f in files if f.lower().endswith(".drc")]
    return max(cands, key=os.path.getmtime) if cands else None
