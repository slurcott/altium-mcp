"""Run a script body in the SERVER sandbox from the command line - the same path
as the run_altium_script MCP tool, for sessions where the MCP is not connected.

    python dev/run_sandbox.py <body.pas> [timeout_seconds] [--allow Name,Name] [--no-lint]
    python dev/run_sandbox.py --check          # health only, changes nothing
    python dev/run_sandbox.py --lint <body.pas> # lint only, never touches Altium

It shares every guard with the MCP tool (server/altium_guard.py): the script is
linted before Altium sees it, the run is refused when Altium is missing,
duplicated, marked wedged or busy with another session, and a run that writes
no log marks Altium wedged so the NEXT attempt refuses instead of spawning
another instance.

Note this is NOT dev/sandbox_runner.py, which drives the separate dev/sandbox
project with its larger scratch-variable set.
"""
import json
import sys
import time
from pathlib import Path

# Altium returns descriptions containing ohm/degree symbols; a cp1252 console
# raises UnicodeEncodeError on print and loses the whole result. Force UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO = Path(__file__).resolve().parents[1]
SERVER = REPO / "server"
sys.path.insert(0, str(SERVER))
import altium_guard as g  # noqa: E402

SANDBOX_DIR = SERVER / "SandboxScript"
SANDBOX_PAS = SANDBOX_DIR / "Sandbox.pas"
SANDBOX_PRJ = SANDBOX_DIR / "Sandbox.PrjScr"
LOG = g.EXCHANGE_DIR / "sandbox_log.txt"
RESULT = g.EXCHANGE_DIR / "sandbox_result.json"


def altium_exe():
    return json.loads((SERVER / "config.json").read_text())["altium_exe_path"]


def lint(body, allow):
    corpus = g.Corpus.from_repo(REPO)
    return corpus, g.lint_script(body, corpus, SANDBOX_PAS.read_text(encoding="utf-8"), allow)


def print_lint(report):
    for e in report["errors"]:
        print("  LINT ERROR:", e)
    for w in report["warnings"]:
        print("  LINT WARNING:", w)


def main(argv):
    import subprocess

    if argv[:1] == ["--check"]:
        ok, msg = g.preflight()
        holder = g.altium_session._holder()
        if ok and holder:
            ok, msg = False, f"another session holds the Altium lock: {holder}"
        print(("HEALTHY: " if ok else "NOT READY: ") + msg)
        for t in (t for _, _, t in g.find_altium_dialogs()):
            print("  open Altium dialog:", t)
        return 0 if ok else 1

    lint_only = argv[:1] == ["--lint"]
    if lint_only:
        argv = argv[1:]
    no_lint = "--no-lint" in argv
    allow = []
    if "--allow" in argv:
        allow = argv[argv.index("--allow") + 1].split(",")
    positional = [a for i, a in enumerate(argv)
                  if not a.startswith("--") and (i == 0 or argv[i - 1] != "--allow")]
    if not positional:
        print(__doc__)
        return 2

    body = Path(positional[0]).read_text(encoding="utf-8")
    timeout = int(positional[1]) if len(positional) > 1 else 120

    corpus, report = lint(body, allow)
    if lint_only:
        print_lint(report)
        print("LINT CLEAN" if not report["errors"] else "LINT FAILED")
        return 0 if not report["errors"] else 1
    if not no_lint and report["errors"]:
        print("REFUSED BY THE LINTER - nothing was sent to Altium")
        print_lint(report)
        return 1
    print_lint({"errors": [], "warnings": report["warnings"]})

    ok, msg = g.preflight()
    if not ok:
        print("REFUSING TO RUN\n" + msg)
        return 1
    print(msg)

    injected = g.inject(SANDBOX_PAS.read_text(encoding="utf-8"), body)
    try:
        with g.altium_session("dev/run_sandbox.py"):
            SANDBOX_PAS.write_text(injected, encoding="utf-8")
            for f in (LOG, RESULT):
                try:
                    f.unlink()
                except OSError:
                    pass
            subprocess.Popen(f'"{altium_exe()}" -RScriptingSystem:RunScript('
                             f'ProjectName="{SANDBOX_PRJ}"^|ProcName="Sandbox>Run")', shell=True)
            start = time.time()
            while not RESULT.exists() and time.time() - start < timeout:
                time.sleep(0.5)
                if time.time() - start > 6:
                    for t in g.dismiss_altium_dialogs():
                        print("  dismissed Altium dialog:", t)
    except g.AltiumBusy as e:
        print("REFUSING TO RUN\n" + str(e))
        return 1

    steps = LOG.read_text(encoding="utf-8", errors="replace").splitlines() if LOG.exists() else []
    print(f"--- elapsed {time.time() - start:.1f}s, {len(steps)} steps ---")
    for s in steps:
        print("  LOG:", s)

    if RESULT.exists():
        if not no_lint:
            for n in corpus.record_verified(corpus.new_members(body)):
                print("  recorded as verified API:", n)
        print("--- RESULT ---")
        print(RESULT.read_text(encoding="utf-8", errors="replace").strip())
        return 0

    if steps:
        g.mark_wedged(f"sandbox script died after step: {steps[-1]}")
        print("\n--- SCRIPT DIED MID-RUN ---")
        print(f"last step reached: {steps[-1]}")
        print("The statement AFTER that is what crashed or paused the script.")
    else:
        g.mark_wedged("sandbox script wrote no log at all")
        print("\n--- NO LOG WRITTEN - the executor is almost certainly PAUSED IN THE DEBUGGER ---")
        print("A compile error the linter missed, or a stray breakpoint in Sandbox.pas.")
    print("\nAltium is now marked WEDGED; further runs refuse until it is restarted.")
    print("*** Do NOT simply re-run. *** -REditScript:Stop is unreliable here. Recover with:")
    print("    " + g.RESTART_HINT.replace("\n", "\n    ").replace("<altium_exe>", altium_exe()))
    print("Safe only if your last write was verified on disk (GOTCHAS.md section 0).")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
