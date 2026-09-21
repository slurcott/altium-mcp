# altium-mcp — working notes for Claude

The code is in two layers:

- `server/main.py` is the MCP tools, written in Python.
- `server/AltiumScript/*.pas` is the production DelphiScript. `Altium_API.pas`
  dispatches each request to it.

`run_altium_script` is the escape hatch: a blank sandbox for one-off scripts.
`server/altium_guard.py` sits in front of every launch.

## Safety

- **Never run anything against Altium without the user's OK.** Another session
  may be driving it, and the user may be screen-sharing a design review. Check
  `altium_health` (or `python dev/run_sandbox.py --check`) first; it only reads
  the process table.
- **The main checkout is what Altium loads.** Do development in a git worktree on
  a branch, and never switch branches in the main checkout while Altium is up.
- Read `~/.claude/skills/altium-script/GOTCHAS.md` before writing any DelphiScript.
- Offline tests run with `python -m unittest server/tests/test_altium_guard.py`. Add a
  test for every lint rule and every guard.

## The improvement loop — this tool is meant to get better with use

Every problem hit while *using* Altium is evidence about the tool. Capture it,
even when the session's actual job is design work.

1. **Before writing a sandbox script**, check whether a tool or generic command
   already does it (the MCP tool list, `dev/GENERIC_COMMANDS.md`). About 16 of the
   77 fixture scripts from 2026-09-20 duplicated existing tools.
2. **When something goes wrong, turn it into a guard, not only a memory:**
   - **A wedge caused by a name:** add it to `DENYLIST` in `altium_guard.py`,
     with the remedy, plus a test.
   - **A silent-logic trap** (it compiles and does the wrong thing): add a warning
     in `lint_script`, plus a test.
   - **A mechanics lesson:** add it to GOTCHAS.md.
   - **A tool bug:** fix it and add a regression test. If it needs Altium, log it
     as `needs Altium`.
3. **When the same kind of script has been written three times**, it is a missing
   command. Run `python dev/mine_history.py` (it reads the run archive; add
   `--dir <scratchpad>` for loose `.pas` files), then add or extend a generic
   command per `dev/GENERIC_COMMANDS.md`.
4. **Log every item in `dev/TOOL_BACKLOG.md`** with evidence. Move it to Done with
   the commit hash when fixed. At the end of an Altium-heavy session, spend
   five minutes mining the archive and updating the backlog.
5. **A lint refusal for a name that turns out to be real** is fixed by running
   it once with `allow_new_api`. It is then recorded in `server/verified_api.txt`
   automatically. Do not weaken the linter.

## Upstream

`origin` is coffeenmusic/altium-mcp, and we have read access only. Push to `fork`
(slurcott/altium-mcp). Contribute small, focused PRs from fork branches:
- one concern per PR
- offline tests included
- no client paths or project names in code, tests or docs

Run bodies and history stay in `C:\Users\Public\altium_mcp`, never in the repo.
