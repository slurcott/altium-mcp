"""Find what the toolset is missing, from the scripts people actually had to write.

    python dev/mine_history.py                    # the run archive (every sandbox run)
    python dev/mine_history.py --dir <folder>     # a folder of .pas bodies, e.g. a scratchpad
    python dev/mine_history.py --since 2026-09-20 # archive entries from a date on

Every run_altium_script / dev/run_sandbox.py run is archived by
server/altium_guard.py to C:\\Users\\Public\\altium_mcp\\history. This reads the
archive and reports:

  * CLUSTERS - scripts that touch the same API. Three or more near-identical
    scripts are a generic command that does not exist yet; that is the signal to
    add one (see dev/GENERIC_COMMANDS.md) and log it in dev/TOOL_BACKLOG.md.
  * WEDGES - runs that died or wrote no log, with the last step reached. Each
    is either a missing lint rule, a GOTCHAS entry, or a tool bug.
  * LINT REFUSALS - the most common reasons. A refusal that keeps recurring for
    a name that turns out to be real means the corpus needs that name.

It never touches Altium.
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "server"))
import altium_guard as g  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Too common to say anything about what a script is for
NOISE = {
    "add", "text", "count", "free", "create", "sandboxlog", "inttostr", "strtoint",
    "x", "y", "location", "firstschobject", "nextschobject", "schiterator_create",
    "schiterator_destroy", "addfilter_objectset", "mkset", "coordtomils", "milstocoord",
    "getdocumentbypath", "opendocument", "showdocument", "getcurrentschdocument",
    "copy", "pos", "length", "uppercase", "lowercase", "booltostr", "tstringlist",
    "resulttext", "savetofile", "loadfromfile", "trim", "floattostr", "format",
}


def fingerprint(body):
    """The API a script touches: member names plus e* object-kind constants."""
    _, rest, _ = g.split_var_block(body)
    names = set()
    for name, recv, _ in g._references(g.tokenize(rest)):
        if recv is not None or re.match(r"e[A-Z_]", name, re.I) and name.startswith("e"):
            names.add(name)
    return names - NOISE


def jaccard(a, b):
    return len(a & b) / len(a | b) if a | b else 0.0


def cluster(items, threshold=0.55):
    """Greedy single-pass clustering on API fingerprints."""
    clusters = []
    for label, fp in items:
        if not fp:
            continue
        for c in clusters:
            if jaccard(fp, c["core"]) >= threshold:
                c["members"].append(label)
                c["core"] = c["core"] & fp if len(c["core"] & fp) >= 3 else c["core"]
                break
        else:
            clusters.append({"core": set(fp), "members": [label]})
    return sorted(clusters, key=lambda c: -len(c["members"]))


def load_archive(since=None):
    runs = []
    for f in sorted(g.HISTORY_DIR.glob("*.json")) if g.HISTORY_DIR.exists() else []:
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if since and r.get("when", "") < since:
            continue
        r["_file"] = f.name
        runs.append(r)
    return runs


def main(argv):
    since = argv[argv.index("--since") + 1] if "--since" in argv else None
    if "--dir" in argv:
        folder = Path(argv[argv.index("--dir") + 1])
        runs = [{"_file": p.name, "outcome": "unknown",
                 "body": p.read_text(encoding="utf-8", errors="replace")}
                for p in sorted(folder.glob("*.pas"))]
        print(f"{len(runs)} scripts from {folder}")
    else:
        runs = load_archive(since)
        print(f"{len(runs)} archived runs in {g.HISTORY_DIR}" + (f" since {since}" if since else ""))
        print("outcomes:", dict(Counter(r.get("outcome") for r in runs)))

    # --- repeated work -> missing tools ---------------------------------------
    items = [(r["_file"], fingerprint(r.get("body", ""))) for r in runs if r.get("body")]
    print("\n== CLUSTERS (3+ scripts touching the same API = a missing generic command)")
    shown = 0
    for c in cluster(items):
        if len(c["members"]) < 3:
            continue
        shown += 1
        print(f"\n[{len(c['members'])} scripts] shared API: {', '.join(sorted(c['core'])[:14])}")
        print("   ", ", ".join(c["members"][:20]) + (" ..." if len(c["members"]) > 20 else ""))
    if not shown:
        print("  none - no repeated pattern yet")

    # --- failures -> lint rules, GOTCHAS entries, tool bugs --------------------
    wedges = [r for r in runs if r.get("outcome") in ("died", "no_log", "timeout")]
    if wedges:
        print("\n== WEDGES (each one: add a lint rule, a GOTCHAS entry, or fix a tool)")
        for r in wedges:
            last = (r.get("steps") or ["<no log>"])[-1]
            print(f"  {r.get('when')}  {r.get('outcome'):8}  {r.get('command') or ''}"
                  f"  last step: {last}")

    refusals = Counter()
    for r in runs:
        for e in r.get("lint_errors", []):
            refusals[re.sub(r"^line \d+: ", "", e)[:110]] += 1
    if refusals:
        print("\n== LINT REFUSALS (recurring + actually real => add the name to the corpus)")
        for msg, n in refusals.most_common(10):
            print(f"  {n:3}x  {msg}")

    warns = Counter(w[:90] for r in runs for w in r.get("lint_warnings", []))
    if warns:
        print("\n== LINT WARNINGS that still ran")
        for msg, n in warns.most_common(5):
            print(f"  {n:3}x  {msg}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
