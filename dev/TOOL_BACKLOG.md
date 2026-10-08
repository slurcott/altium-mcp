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

- **B43 DONE 2026-10-08: WPF prompts, and the first LIVE dialog click.** Altium's "Unsaved Changes" prompt is a
  WPF window (class `HwndWrapper[...]`, no child windows): `find_altium_dialogs` did not list it and BM_CLICK has
  nothing to press. Now listed with class `WPF` while a main window is disabled; `click_dialog_button` accepts
  only Cancel / Close on it (WM_CLOSE) and refuses every other caption even with allow_other. LIVE:
  `altium_dialogs` listed the prompt, `altium_dialog_click(hwnd, "Cancel")` closed it, `altium_health` then ok.
  Reading WPF button names needs UI Automation (no comtypes / pywinauto in the venv; an MSAA attempt through
  pywin32 failed on property access) - open as an idea, not needed for recovery. Still unproven live: a click
  on a classic Delphi box (TXPBitBtn OK of the script Error box). 3 tests (WpfPrompt).

- **B38-B42 DONE 2026-10-08** (commit f043809; 19 offline tests in tests/test_pcb_copper.py).
  B38 `pcb_add_copper` (server/pcb_copper.py + tool): LIVE on a scratch board - dry run, write of 3 tracks +
  2 vias on three layers incl. a no-net via and a fractional coordinate, save, read-back all present on the
  right nets, repeat refused. B39 pad `component` in `pcb_query`: LIVE (J_A pads and a free hole).
  B40 island through a pad + round-pad gap, B41 polygon list / poured-region net / pad shape / `long_layer()`:
  checked on a real 4-layer board file. B42 modal pre-check in `preflight` and in the no-log branch: reads
  "not blocked" live; the blocked case is covered by a mocked test and one live check with the user.
  Original entries:
  **B38. `pcb_add_copper`: write tracks and vias from a table** (open, 2026-10-08)
  - Evidence: a 4-layer board was routed by an offline router and written with a hand-pasted sandbox script
    (551 objects, then 94 stitching vias): read a table file with the script's own `TStringList`, find each net
    by name with a read-only iterator, create with `PCBServer.PCBObjectFactory`, assign the net, no edits inside
    iterators. Promote that script to a tool with a dry-run flag and a built-in save + read-back.

  **B39. `pcb_query` pads without their component** (open, 2026-10-08)
  - A pad record has name, net, position, size - not the designator it belongs to. Every placement and mirror
    check had to match pads by net and pin name or fall back to the offline reader.

  **B40. `pcb_net_check`: false break and pessimistic gaps** (open, 2026-10-08)
  - A track passing through a same-net pad without an end point on it is counted as a separate island.
  - Near-miss gap uses the pad's half-diagonal for round pads.

  **B41. offline board reader: polygon net and layer names** (open, 2026-10-08)
  - `pcb_nets.load()` returns pour regions with net None, and short layer names (`Top`, `Mid1`) where every
    other tool uses Altium's (`Top Layer`, `Mid Layer 1`).

  **B42. modal-dialog preflight for `run_altium_script`** (open, 2026-10-08)
  - A script launched while the user had the Replace Component window open never started; the guard marked a
    wedge. `altium_dialogs` reported nothing afterwards. Detect a modal Altium form before launching and say
    "close the open dialog", instead of marking the engine wedged.


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

- **B20 (2026-09-25) get_pcb_layer_stackup drops CORE dielectrics.** On TMC B_1 (6 layer) it reported
  total 19.5 mil with "No Dielectric" under an inner Gnd layer; the FE 4-layer Layer Stack Manager
  shows Core-043 52 mil between layers 2 and 3 (real total 63.1 mil). The tool only attaches the
  dielectric that follows a copper layer as prepreg and skips cores - it led to a wrong "stackup is
  0.020 in" claim. Fix: iterate the stack's dielectric objects (cores included) and sum all heights;
  add a total that matches the Layer Stack Manager.

- **B21 (2026-09-25) bom_consolidate doesn't recognise library parts by name.** A placed workspace
  library part (Comment "RES 0603 4.7kOhm 1%") is decoded from its description as its own "part"
  with no MPN, so it shows as a second part number in its group and never as status `library`.
  Fix: when --library is given, map the library table's Name -> Manufacturer Part Number 1 (and
  the other part choices) before grouping. Also: Explorer/BOM paths > 260 chars fail in xlsx_read
  (copy to a short path first) - consider \?\ prefixes.

- **B22 (2026-09-25) build_passive_schlib produced EMPTY symbols.** `Src.Replicate` copies the
  component record but NOT its child pins/graphics for the CAP-NP-2 template (and pins for RES-2), so
  every row except the last (the edited original) imported with an empty symbol. The Library Importer
  then de-duplicated all empties into one empty SYM item per file. verify() only checked component
  names + footprint links. Found when swapped FE caps had no pins. Workspace fixed by batch-repointing
  SCHLIB to SYM-006-0000-2 / SYM-007-0001-2. Fix: copy child primitives explicitly (iterate the source's
  pins/graphics and AddSchObject a Replicate of each), and make verify() count pins + graphics per
  component (fail if any component has 0 pins).
  **Fixed offline 2026-09-25 (needs one live scratch run).** Offline evidence (new
  schdoc_file.read_ole_storage_streams + passive_schlib.component_record_counts): CAP-NP-2 replicas
  kept 0 of 2 pins and 0 of 4 graphics; RES-2 replicas kept 2 of 4 pin records and lost the zigzag -
  the "proven" 0402 trial was damaged too (workspace already repointed). SCRIPT now strips what
  Replicate copied and adds Obj.Replicate of every Src pin/graphic (C.RemoveSchObject is NOT yet
  verified in this Altium). verify() now fails on any 0-pin component or on more than one
  (pins, graphics) shape across the lib. Live test: build a 3-row cap + 3-row res scratch lib, run
  verify (expect ok, one shape), open one component in Altium to eyeball.
  BLOCKER until then: the linter refuses the script (`.RemoveSchObject` never run). Probe it first in
  a short run_altium_script on a scratch SchLib with allow_new_api=["RemoveSchObject"]; once a run
  completes it lands in verified_api.txt and build_passive_schlib runs normally.
- **B23 (2026-09-25) create_footprints_batch double-applied the library origin.** Spec coords given
  as raw library mils (50000-based, as get_footprint_primitives dumps them) landed at 100000 for
  CAPC1206(3216)190_L (50 in off origin -> "Offset Component Origin" at release). Fix: make the spec
  origin-relative (subtract Board.XOrigin/YOrigin on dump, add on create) and document it; add a
  post-create check that pads are centred on the origin.
  **Investigated + guarded 2026-09-25.** Scratch test (~/.ov4fb/b23): create places RAW coords exactly
  as given (50000-based pads land centred on the 50000 origin; a 0-based spec lands 50000 mil off), so
  dump and create already agree - the "double origin" hypothesis is wrong and the frame was NOT
  changed (would break every existing spec). The 1206 offset is unexplained; prime suspect is the B16
  double-open (same PcbLib under the C:/Users/Steve junction and the real path). Guards added:
  footprint_spec.py resolves the FPLIB path (realpath) before Altium sees it; the script reports the
  library origin; the tool returns off_origin (pads not centred on origin). Tests test_footprint_spec.
  Still to do: live-confirm off_origin on a scratch lib next time Altium is free.
- **B24 (2026-09-25) Library swap needs pin realignment.** Replacing parts whose old symbols have a
  different pin span/origin leaves pins off the wires. Proven procedure (FE 58 + TMC 42 placements,
  netlist IDENTICAL): ~/.ov4fb/realign.py (plan: ROT / MOVE pin1->old pin1 / WIRE bridge pin2; check:
  per-pin net membership vs a pre-swap copy) + realign.pas. Mirrored parts rotate the opposite way.
  Promote into the tool (MCP command + tests).
  **Done 2026-09-25 (offline-tested; apply path not yet run as the MCP tool).** server/swap_realign.py
  + MCP tool realign_swapped_parts (apply=False: plan + check only; apply=True: ROT pass, save,
  re-plan, MOVE/WIRE pass, save, check). New: IDEMPOTENT - a part is skipped once its pins have their
  baseline connectivity (the old script re-proposed every bridge wire on a fixed sheet); a part still
  wrong after one turn goes MANUAL instead of spinning. Smoke: released FE/TMC/fixture plan 0 lines,
  check IDENTICAL. dev/realign_swap.py is now a CLI over the module; realign_swap.pas removed.
- **B25 (2026-09-26) Whole-project netlist, offline. DONE.** server/project_netlist.py + MCP tool
  project_netlist: follows sheet symbols -> child sheets (channels named $Component_$RoomName),
  entries <-> ports, power global, labels local, names case-insensitive, label/port named like a
  power net joins it. compare_to a snapshot .PrjPcb or an IPC-2581 release file/zip. Validated: 0
  grouping differences vs the IPC-2581 of two released boards; a 6-sheet hierarchical project with 13
  channels resolves with no warnings. Fixes found on the way (dev/netlist.py): vertical ports
  (style 4-7) attach along y; ports abutting sheet entries with no wire; pinless clusters kept
  (clusters()); schdoc_file passes port iotype/style. build() output unchanged on 31 real sheets.
  Open: Repeat() not expanded; duplicate symbol designators numbered in file order.
- **B26 (2026-09-26) IC footprint vs datasheet checker - core DONE (offline).** server/pcblib_file.py reads
  saved .PcbLib footprints without Altium (pads: pos/size/shape incl. roundrect %/rotation/hole; tracks/arcs;
  offsets verified on a TI QFN module + Altium _L chip footprints; 38 libs/332 fps/1964 pads parse).
  server/footprint_check.py: spec = datasheet land pattern (hard position check) or package dims
  (dual/quad, JEDEC CCW numbering, IPC-7351B nominal as advisory, lead-on-pad coverage as the hard
  check). FAIL: pin set, mirror, pitch, lead miss, small/missing EP; WARN: origin offset, IPC size,
  mask sliver, pin-1 silk. B23 root cause found with it: pads are stored relative to each library's
  origin, and a new blank library's origin differs from a downloaded one.
  DONE 2026-09-26 (later): MCP tools check_footprint (+ overlay PNG: green datasheet / red copper) and
  datasheet_drawing_pages (pypdfium2 renders PDF drawing pages to PNG - new dependency). Drawing-read
  method validated: TPSM33610 RDN0011B drawing read = keypad footprint; TPSM365R6 RDN0011A PASS on it.
  NEXT: land pattern FROM A DRAWING (screenshot -> dims -> pads, with a rendered
  preview to confirm the read); symbol-pin vs pad cross-check; spec -> create_footprints_batch.
- **B27 (2026-09-26) Custom-shape pads.** Altium pads can have a Custom shape (outline-defined). The
  PcbLib/PcbDoc reader does not decode them yet (it does decode component copper REGIONS, used by older
  footprints such as LFPAK56 drains). Decode the custom outline, treat it like a region in check/overlay;
  and probe the API to CREATE custom pads from create_footprints_batch (unverified - needs a sandbox probe).
  Use case: dual LFPAK56 / PowerPAK SO-8L footprint for the OV4F keypad Q3.
- **B28 (2026-09-26) copy_sheet MCP tool. DONE (offline-tested, lint clean; not yet run as the tool).**
  server/sheet_copy.py + copy_sheet: copy a sheet to a NEW file, rename ports (+IO types), reset only
  letters+number designators (named ones like TP_5v0 / R_S3 kept - the trap found by hand), save, read the
  netlist back. Every API call in its script was used live on 2026-09-26.
- **B23 CORRECTED (2026-09-26 pm, live proof).** create_footprints_batch takes coordinates RELATIVE TO THE
  LIBRARY ORIGIN. A 50000-based spec (origin's raw value) landed the new OV4F Q3 footprint 50000 mil off,
  read back from the saved .PcbLib (pads are stored origin-relative). The morning conclusion ("create uses
  raw coords") came from get_footprint_primitives, which dumps ABSOLUTE coords - subtract the origin before
  reusing a dump as a spec. off_origin now targets 0,0; docstrings fixed. The Altium-side origin report
  (pcb_utils.pas) ran fine live. Recovery used: IPCB_Primitive.MoveByXY on each primitive of the one
  footprint (index-walk with a fresh GroupIterator per primitive; no TStringList.AddObject - unverified).
- **B31 (2026-10-03) save_doc / place_components while the user is mid-command lock Altium's saves.**
  OV4F keypad B_0: place_components (49 TPs) and then save_doc ran while Steve had an interactive PCB
  command active. save_doc raised "A command is currently active and save cannot be completed. Save a
  copy?" and timed out (wedge marker set); afterwards Steve's own Ctrl+S was refused the same way and
  Run > Stop was greyed. Recovery: Save a copy (Yes), close Altium without saving, rename the copy in.
  Fix: before any write/save, probe for an active interactive command (or the modal) and REFUSE with a
  message ("finish/Esc the current command, then retry"); never call DoFileSave from a script while
  one is active. Until then: the user saves; tools only read.
- **B32 (2026-10-03) scripts wedge when the focused document is not the PCB** (e.g. the DRC report
  HTML tab). run_altium_script has no doc_path; GetCurrentPCBBoard misbehaves and the sandbox pauses.
  Fix: run_altium_script(doc_path=...) that focuses the board first, like pcb_query does.
- **B33 (2026-10-03) violation reader + DRC report reader.** Listing eViolationObject (Name,
  Description, BoundingRectangle - verified live) worked once and wedged once (B32). Also parse the
  saved "Design Rule Check - *.drc" report offline: per-rule counts + each violation line. Offline is
  safer and is what was used for the rest of the session.
- **B34 (2026-10-04) offline routed-net tools.** The keypad hardware repo now has
  tools/layout_checks/ (pcbfile.py loader with tracks/vias/arcs/regions + nets, origin from Board6;
  padnet_diff, net_check connectivity/clearance, tagconnect_check, driver_pin_map). Move the loader
  into server/ (it extends pcblib_file) and expose padnet_diff + net_check as MCP tools.
- **B35 (2026-10-03) create_net_class reports nets_added 0 although the nets were added** (verified
  with IsMember, twice). Return the IsMember count.
- **B36 (2026-10-03) run_altium_script allow_new_api must be a LIST of member names**; passing true
  fails validation. Say so in the docstring with an example.
- **B31-B36 status (2026-10-04):** B31 save_doc now watches for the "command is currently active" box,
  answers No and returns refused=command_active (was: hang + wedge) - NOT yet run live; B32/B36
  documented in run_altium_script (focus first; allow_new_api is a list); B33 DONE drc_report tool
  (offline .drc parser, tested on the keypad report); B34 DONE pcb_net_check + pcb_padnet_diff tools
  (server/pcb_nets.py, tested offline); B35 create_net_class counts members with IsMember - NOT yet run
  live. New tools load after an MCP server restart.

- **B37 (2026-10-08) stuck Altium: read dialogs, click a named button, guard against runaway edit scripts.**
  Case (Fixture Base, 2026-10-07 night, user away): a sandbox script changed vias INSIDE its `BoardIterator`
  loop (`PCBM_BeginModify` / `IsTenting_Top` / `PCBM_EndModify`); the iterator revisited the same two vias
  for ever (12 000+ log steps), the run timed out, Altium showed a `TMessageForm` "Error" box, and the
  `-REditScript:Stop` attempt started a second X2.EXE showing "Information" (another instance busy).
  Earlier unsaved rule edits from the same session were lost. Gaps and proposed fixes:
  1. **Dialog tool.** `dev/x2_dialogs.py` (new, ctypes only) lists every dialog owned by an X2.EXE process
     with pid, class, title, readable text and button captions, and can click one button by caption
     (`BM_CLICK` - Altium's own "Error" boxes ignore `WM_CLOSE`, and their OK button's class has no
     "Button" in its name, so match on the caption). Listing verified live; the click was NOT run (blocked by
     the agent's permission mode). Wanted as MCP tools: `altium_dialogs()` (read only) and
     `altium_dialog_click(hwnd, button)`, scoped to X2.EXE windows, with an allow-list of captions
     (OK / Cancel / No / Don't Save - never Save / Yes / Reload without the caller naming it).
  2. **Linter:** refuse a script that calls `PCBM_BeginModify`, `AddPCBObject` or `RemovePCBObject` between
     `FirstPCBObject` and `BoardIterator_Destroy` (collect first, modify in a second pass).
  3. **Runaway guard:** `SandboxLog` counts calls and aborts the script (raise) past a limit, e.g. 2 000.
  4. **Auto-save:** offer `save=True` on `run_altium_script` so a successful edit is on disk before the next one.
  5. **Recovery order** for `unwedge.py`: click OK on "Error" in the main instance, click OK on
     "Information" in the extra instance (it then exits), `-REditScript:Stop`, probe, clear the marker.
  6. **Away mode:** a server flag that refuses unverified-API edit scripts when nobody can clear a dialog.
- **B37 live use (2026-10-08, later the same day):** `altium_health` + `altium_health(clear_wedge=True)` used
  after a false wedge (see B42); the iterator linter rule held through 11 edit scripts (locks, 551 routed
  objects, 94 stitching vias, library footprint and symbol) with no hang. `altium_dialog_click` still not
  exercised on a live stuck dialog.
- **B37 status (2026-10-08):** ROOT CAUSE of "Error box never dismissed": the guard counted only `Button` /
  `TButton`; the OK of Altium's script Error box is a `TXPBitBtn` (read live), so the box had "0 buttons"
  and the single-button IDOK path never fired. DONE in `server/altium_guard.py`: `BUTTON_CLASSES`,
  `dialog_buttons`, `describe_altium_dialogs`, `click_dialog_button` (BM_CLICK, safe-caption list,
  Altium windows only), `dismiss_altium_dialogs` now also BM_CLICKs a lone button; linter refuses
  `PCBM_BeginModify` / `AddPCBObject` / `RemovePCBObject` between `FirstPCBObject` and the iterator's
  Destroy. DONE in `server/main.py`: tools `altium_dialogs` (read only) and `altium_dialog_click`;
  `altium_health` returns `dialog_details`. 10 new offline tests (63 in test_altium_guard, all pass).
  `describe_altium_dialogs` read the two live stuck dialogs correctly. NOT run live: any click (BM_CLICK on a
  TXPBitBtn is untested), the new MCP tools (the server must be restarted to load them). NOT done: runaway
  guard in SandboxLog (raising in DelphiScript would itself pause the engine - needs another idea),
  `save=True` on run_altium_script, away mode, the unwedge.py recovery order. Uncommitted.
