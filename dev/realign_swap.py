"""Command-line wrapper for server/swap_realign.py (the MCP tool is realign_swapped_parts).

  python realign_swap.py plan  <baseline_dir> <project.PrjPcb> <spec_out.txt>
  python realign_swap.py check <baseline_dir> <project.PrjPcb>
  python realign_swap.py script <spec_path>      # print the Altium script for a spec

Env REALIGN_ONLY=R1,R2 limits the plan to those designators.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import swap_realign as R

if __name__ == "__main__":
    only = set(filter(None, os.environ.get("REALIGN_ONLY", "").split(",")))
    if sys.argv[1] == "plan":
        lines, manual, n = R.plan(sys.argv[2], sys.argv[3], only)
        R.write_spec(lines, sys.argv[4])
        print(f"planned {n} parts, {len(lines)} lines -> {sys.argv[4]}")
        for m in manual:
            print("MANUAL", *m)
    elif sys.argv[1] == "check":
        c = R.check(sys.argv[2], sys.argv[3])
        print(json.dumps(c, indent=1))
        print("RESULT:", "IDENTICAL" if c["identical"] else "DIFFERENCES")
        sys.exit(0 if c["identical"] else 1)
    else:
        print(R.render_script(sys.argv[2]))
