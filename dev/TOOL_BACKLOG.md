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

**B3b. `sch_query` for SchLib symbols** (open, 2026-09-21)
- SchLib pins are binary records, so a SchLib query goes through Altium (or a
  binary pin decoder). SchDoc queries are done (D7).

**B4. `sch_edit` and `sch_place`** (open, 2026-09-21, fixture scripts)
- Evidence: the miner finds 8 edit/delete and 8 place/save scripts. `placepico`
  and `placeenc` used `.Location`, which left 17 parts invisible.
- Fix: `GENERIC_COMMANDS.md` §4–5.

**B5. Pin hot-end is wrong for mirrored parts - IN ALTIUM'S IN-MEMORY API ONLY** (narrowed 2026-09-21)
- Evidence: the header of `dev/check_connectivity.pas`, and net labels landing one pin
  pitch off, which silently grounded three Pico pins.
- **Narrowed 2026-09-21, offline:** in the SAVED .SchDoc, mirrored parts' pin records
  are already transformed. The plain hot-end math puts 36 of 37 mirrored-part pins on
  wiring, and flipping X puts 0 there. So the file geometry is right, and the bug is
  in the in-memory path (`GetPinHotEnd` on live `ISch_Pin` objects).
- Fix: `sch_label_pins` computes pin positions from the saved file
  (`save_doc`, then `schdoc_file`), which avoids the bug entirely. Fixing
  `GetPinHotEnd` still needs one Altium experiment, but no longer blocks labelling.

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

**B14. BOM consolidation + part-number decoding** (in progress, 2026-09-23, user request)
- Evidence: generic library picks leave one requirement bought under several MPNs
  (OV4F B_1: 54 passive MPNs for 44 requirements) and BOM names that contradict
  the MPN actually bought (X7R name / X5R part; "1%" name / 5% part).
- Built (tool-dev, offline-tested): `server/passives.py` decodes 12 R/MLCC series;
  `dev/bom_consolidate.py` reads Altium BOM .xlsx (+ saved sheets for parts newer
  than the export), groups by type/package/value, recommends upgrade-only merges,
  specs a new part when none covers, flags name-vs-MPN mismatches.
- Next: DigiKey API lookup to fill specs/alternates; expose as an MCP tool;
  CM substitution checker using the same `covers()` rule; `sch_edit` (B4) to apply swaps.

**B15. ERC violations come back as category names only** (open, 2026-09-23, live run)
- Evidence: first live `netlist_query` on a .PrjPcb returned 13 violations as
  "Nets with no driving source", "Nets with possible connection problems"... -
  `DM_LongDescriptorString` is the violation CLASS, not which net/pin.
- Fix: find the per-violation detail member (probe with `allow_new_api` in the
  sandbox: e.g. `DM_DescriptorString`, `DM_ErrorLevel`, the violating object) and
  emit net/designator per violation.

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
- **D7.** (B3) `sch_query` and `netlist_query` read a saved .SchDoc from the file, never Altium
  (`server/schdoc_file.py`). The netlist reuses `dev/netlist.py`'s connectivity rules, so it is never the
  cached `DM_Compile`. Checked on the fixture sheet:
  - all 349 on-sheet pins are placed in nets
  - 296 of 349 pin hot ends land on wiring
  - the separated grounds (FE_SGND 9, TMC_SGND 20, PWR_RTN 37, LINK_GND 6) come out as distinct nets
  - there is no stray GND net

  Found on the way: symbols with alternate display modes store every mode's pins, at different
  positions, so only the displayed mode's pins are kept (these had been 24 phantom pins).
- **D8.** (B12) `netlist_query` on a .PrjPcb: compiled multi-sheet netlist + ERC violations
  (7f5b4a5). Verified live 2026-09-23 on FE B_1: 65 nets / 113 components in 3 s, matching the
  113 components the file reader counts on the saved sheets.
- **D9.** (B13) `pcb_query` (7f5b4a5). Verified live 2026-09-23 on the FE board: 3355 objects,
  0 unreadable, 217 vias, 5 airlines; a window around U8 returned U8 at (-1098.7, 5.0) mil rot 90
  with its pads - matching the hand-scripted placement check.
- **D10.** `build_passive_schlib`: standard passives -> import-ready SchLib -> Library Importer
  (server/passive_schlib.py, offline tests). Proven live 2026-09-24: 145 0402 resistors
  imported with Part Choices, 1 shared symbol + 1 footprint. Full workflow and the routes
  that do NOT work (DbLib, batch grid limits): dev/LIBRARY_IMPORT.md.

- **B16 (2026-09-25) Same PcbLib opened twice.** `create_footprints_batch` (FPLIB) and
  `get_footprint_primitives(library_path=...)` each opened `Std_Cap_1206_1210.PcbLib`; it showed
  twice under Free Documents - likely the `C:\Users\Steve` junction vs the real
  `C:\Users\SteveLurcott` path. Risk: edit one copy, save the other. Fix: resolve paths
  (os.path.realpath) before handing them to Altium, and reuse an open document whose resolved
  path matches before calling Client.OpenDocument.

- **B17 (2026-09-25) Bulk lifecycle change (retire superseded parts).** DelphiScript has no
  workspace lifecycle API (only read-only LifeCycle fields and ILibraryUpdatePartOptions.
  NewLifeCycleState). Retiring 99 superseded generics was manual Explorer Ctrl/Shift-click +
  Batch state change ("Make n Obsolete"). Investigate the Nexar GraphQL API for an A365
  lifecycle mutation; prove on ONE part first. Selection logic already exists (retire lists in
  lurcott-library passives/retired/, rule: same size+value, >= power/voltage).

- **B18 (2026-09-25) Private library data in local history.** dev/library_gen.py (Lurcott Labs
  part choices) was committed on tool-dev from 8497f73 and moved to the private lurcott-library
  repo. None of it was ever pushed (fork/* branches predate it), but slurcott/altium-mcp is
  PUBLIC: before pushing tool-dev, squash/rewrite so those commits don't go up, or accept it.

- **B19 (2026-09-25) pcb_query reports phantom airlines.** FE B_1: `pcb_query(summary)` said
  `unrouted_connections: 2` on NetR19_2 (I2C SCL) before AND after a close/reopen, while Altium's
  Reports > Board Information said 247/247 routed, 0 remaining, DRC Un-Routed Net 0, and the net
  highlight showed no ratsnest. The eConnectionObject endpoints (x1/y1/x2/y2: (390,-378) ->
  (1392,-153) -> (1708,-564) mils from origin) touched no object of that net. Until fixed, treat a
  non-zero airline count as "ask for Board Information", never as a defect. Fix idea: only count
  connections whose endpoints land on a same-net pad/via/track, or read the routing-completion
  figure Board Information uses.
