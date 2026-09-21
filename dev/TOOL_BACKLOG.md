# Tool backlog

The living list of what the toolset is missing or gets wrong. **Any session that
hits a problem adds an entry here**. That includes sessions doing design work that
only *use* the tools. The protocol is in [`../CLAUDE.md`](../CLAUDE.md).

Entry format: **ID. Title** (status, date found, found by). Then the evidence (what
actually happened, not a guess), and the fix (what would stop it happening again).
Status is one of `open`, `in progress`, `needs Altium`, `done <commit>`, or `wontfix <reason>`.

Evidence sources: `python dev/mine_history.py` (clusters, wedges, lint refusals from
the run archive), `~/.claude/skills/altium-script/GOTCHAS.md`, and the session itself.

---

## Open

**B3. `sch_query` and `netlist_query`** (open, 2026-09-21, fixture scripts)
- Evidence: the miner finds an 11-script netlist cluster and a component+pin query
  cluster. Together that is 31 of 77 fixture scripts.
- Fix: `GENERIC_COMMANDS.md` §2–3. The netlist is built from the saved file,
  not `DM_Compile`, which can serve a cached netlist.

**B4. `sch_edit` and `sch_place`** (open, 2026-09-21, fixture scripts)
- Evidence: the miner finds 8 edit/delete and 8 place/save scripts. `placepico`
  and `placeenc` used `.Location`, which left 17 parts invisible.
- Fix: `GENERIC_COMMANDS.md` §4–5.

**B5. Pin hot-end is wrong for mirrored parts** (needs Altium, 2026-09-21, audit)
- Evidence: the header of `dev/check_connectivity.pas`. It is the prime suspect for
  net labels landing one pin pitch off, which silently grounded three Pico pins.
- Fix: a bench experiment on one mirrored part (compare `Pin.Location` and
  `Orientation` before and after `Mirror`), then correct `GetPinHotEnd`. This
  blocks `sch_label_pins`.

**B6. `get_pcb_layer_stackup` uses `LayersInStackCount`** (needs Altium, 2026-09-21, audit)
- Evidence: `pcb_utils.pas:1076`. GOTCHAS says this name wedged the sandbox, and
  the linter denylists it.
- Fix: run the tool once on a PCB. If it wedges, replace it with a stack
  iteration. If it works, the GOTCHAS entry needs a note on the context in which
  the name failed.

**B7. The fixture session hand-wrote work that existing tools already do** (open, 2026-09-21, fixture scripts)
- Evidence: 7 footprint builders and 3 footprint dumps, which
  `create_footprints_batch` and `get_footprint_primitives` cover, and 3 symbol
  scripts. It probably ran without the MCP connected (`dev/run_sandbox.py` exists
  for exactly that case).
- Fix: confirm that the altium MCP is configured for the project directory
  the design sessions run in. Make `dev/run_sandbox.py --tools` print the
  command list, so a CLI-only session knows what exists.

**B8. Two sandboxes with different scratch-variable sets** (open, 2026-09-21, audit)
- Evidence: `dev/sandbox/Sandbox.pas` declares `S4-5, I4-5, Obj6-7, List2,
  TargetDoc, ...`, while `server/SandboxScript/Sandbox.pas` does not. The dev
  scripts wedge the server sandbox; the linter now catches this.
- Fix: give `dev/sandbox_runner.py` the same guard, lint and `USER VARS`
  injection, so dev scripts declare what they use and both sandboxes can
  share one variable set.

**B9. Bridge timeout is fixed at 120 s and marks a wedge** (open, 2026-09-21, design)
- Evidence: long legitimate commands (`run_output_jobs`, large batches) would
  be marked wedged.
- Fix: a timeout per command in `execute_command`, and a "slow, not wedged"
  check (is Altium's CPU busy? is a dialog up?) before marking.

**B10. Linter: names in `Run`/`SandboxLog` scope count as reserved** (open, 2026-09-21, design)
- Evidence: a user var named `Msg` is refused because it is `SandboxLog`'s parameter.
- Fix: this is harmless but over-strict. Only globals and `Run` locals should collide.

**B11. Post the upstream issue about `-REditScript:Stop`** (open, 2026-09-21, user)
- Evidence: the draft sits untracked at `ISSUE-DRAFT-editscript-stop.md` in the
  main checkout.
- Fix: post it. Mention that the class-only dialog filter also matches other
  applications' dialogs (fixed on `tool-hardening`).

## Done

- **D1.** Preflight, cross-session lock, wedge marker, and `altium_health` (74452d8).
- **D2.** Sandbox linter, `allow_new_api`, and `verified_api.txt` learning (74452d8).
- **D3.** Dialog dismissal limited to X2 windows; OK is pressed on single-button boxes (f8701b6).
- **D4.** Run archive and `dev/mine_history.py`.
- **D5.** (B1) All 12 production modals now return `ERROR:` instead. An unknown command answers at once
  rather than timing out after 120 s. A command whose document cannot be focused now fails with the
  reason, where before it silently ran against whatever was focused. A regression test forbids
  `ShowMessage` in `server/AltiumScript`. Verified live 2026-09-21: an unknown command failed cleanly
  in 1.2 s with no dialog, and get_all_designators still round-trips (99dae94).
- **D6.** (B2) `save_doc(doc_path)`: saves an open .PcbLib, .PcbDoc, .SchLib or .SchDoc by the rule for its
  kind, refuses (never hangs) if the document still reads clean, and reports success only when the file
  changed on disk. The Sch rule is a *neutral touch*: a value changed and put back inside
  BeginModify/EndModify, which marks the document modified without changing its content. Proven
  2026-09-21 on scratch copies of all four kinds, from clean through dirty to saved, and live through
  the production bridge (431bf01).
