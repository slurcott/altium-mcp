# Generic commands — design proposal

**Status:** proposal, 2026-09-21. Nothing here is built yet.

**Problem.** Routine edits still mean hand-writing DelphiScript in the sandbox, and
the sandbox is where the engine wedges. One day of test-fixture work (2026-09-20)
produced **77 sandbox scripts**. Sorted by what they did:

| Family | Scripts | Existing tool that covers it |
|---|---|---|
| Query schematic components + pins (SchDoc, SchLib) | 15 | partial: `get_schematic_data` (focused doc only, no pins/filters) |
| Query net labels, power ports, wires | 5 | none |
| Compiled-netlist queries (11 copies of one skeleton) | 11 | none on the schematic side |
| Query footprints / board | 6 | **yes**: `get_footprint_primitives` |
| Rename / set a property on matched objects | 5 (+2 embedded) | none |
| Delete matched objects | 2 (+3 embedded) | none |
| Place parts from a SchLib into an existing sheet | 3 | partial: `build_schematic` (new sheet only) |
| Net label / power port at a component pin | 2 | none |
| Create footprints (13 footprints) | 7 | **yes**: `create_footprints_batch`, `create_pcb_footprint` |
| Add a 3D body to a footprint | 2 | none |
| Create symbols, link a footprint model | 3 | **yes**: `create_symbols_batch`, `create_schematic_symbol` |
| Save / dirty-flag retries | 10 | none |
| Liveness / workspace | 3 | `altium_health` now |

**Where the time went:**

- Save handling: two retry chains (`savesym`→`savesym5`, `save`→`save4`) and one
  restart that threw away unsaved work.
- Placement with `.Location`: 17 invisible parts, 4 scripts to diagnose them, and
  a delete-and-replace.
- The same netlist skeleton written 11 times.
- About 16 scripts redid work an existing tool already does. This is probably
  because the fixture session drove the sandbox through `dev/run_sandbox.py` and
  did not have the MCP connected (worth confirming).

## Design rules (apply to every command below)

1. **Explicit target.** Every command takes a document path. There is no reliance on
   "the focused document", which is how `get_all_designators` returned nothing
   today. The open step is built in once:
   `GetDocumentByPath` → `OpenDocument` if nil → error if still nil → `ShowDocument`.
2. **Never a modal.** Failures return `{"success": false, "error": ...}`. Today the
   production path has 18 `ShowMessage` calls. On an unknown command it shows a
   modal and writes no response, so the bridge waits 120 s and the call looks
   like a wedge. That is how the modal in today's smoke test appeared.
3. **Pipe-delimited spec files for parameters**, written by Python. `build_circuit`
   and the batch creators already use this pattern. The request JSON is parsed
   line by line with `Pos()`, which cannot carry lists or maps.
4. **Writes batch, then save once, then verify from the file.** Each write command
   returns what it matched, what it changed, and a file-level confirmation.
   Nothing is reported as done on Altium's word alone (GOTCHAS §0).
5. **Match on values, applied atomically.** A rename map is resolved against the
   *original* values before anything is written, so swaps (Q1↔Q2) work.
6. **Matching vocabulary is shared by every command:** `exact`, `list`, `prefix`,
   `contains`. Objects are identified as follows:
   - components by designator or LibReference
   - pins by (component, pin designator *or* pin name)
   - net labels and power ports by text, with an optional point to pick one of several
   - wires by a point they pass through

## The commands

### 1. `save_doc(doc)` — replaces 10 scripts

This hides the per-kind save rules GOTCHAS §3 took a day to find:

- **PcbLib:** `Board.SetState_DocumentHasChanged`, read `Modified` once, save only if set.
- **SchLib / SchDoc:** touch an object with BeginModify/EndModify, then save
  unguarded, never after `PostProcess`.
- **Always:** afterwards, confirm the file's modification time moved and that its
  content parses.

It returns `{saved, verified_on_disk, strategy}`. The write commands below call it
by default (`save=true`).

### 2. `sch_query(doc, kind, match, fields, include_pins)` — replaces 20 scripts

- `kind`: `component | pin | netlabel | power | port | wire | parameter`.
- `fields`: from a fixed list. The API names behind each field are proven, so a
  field can never be a guessed identifier:
  - designator, libref, comment, description, x, y
  - bbox (catches the `.Location` bug), part_count, component_kind, orientation, mirrored
  - pin designator, pin name, pin hot-end x, pin hot-end y
- Works on SchDoc and SchLib. `include_pins` nests pins under components.

**Where it runs:** for a *saved* SchDoc it can answer from the file in Python,
with no Altium involved (phase 2). SchLib pins are binary, so those go through
Altium. The response says which source answered and warns that the file source
cannot see unsaved edits.

### 3. `netlist_query(project_or_doc, net, has_designator, only_pins_of, max_pins)` — replaces 11

It returns nets as `NAME (n): DES.PIN ...`, filtered the way the 11 scripts filtered:

- by net name (exact, list or contains)
- by nets that include a designator (list or prefix)
- by `?`, for unannotated parts
- by single-pin nets

**Source:** it builds the netlist from the saved SchDoc via `dev/netlist.py`'s
connectivity rules, **not** `DM_Compile`, which serves a cached netlist after
edits (GOTCHAS §3e). A compiled netlist stays available as `source=compiled`
and is labelled as possibly stale. **Open question:** netlist.py is single-sheet.
The fixture is one sheet; multi-sheet needs ports and sheet entries resolved.

### 4. `sch_edit(doc, ops, save=true)` — replaces 7 scripts and the multi-step jobs

`ops` is a list, applied in order within one run:

- `set`: kind, match, field, a `{old: new}` map (atomic), or a value.
  - Covers net label and power-port text, designators, pin designators
    (library *and* placed instance), comments and parameters.
- `delete`: kind, match. Uses the delete-and-rescan loop internally.

It returns every match, with before and after values, plus any match that found
nothing. A rename that hit zero objects is an error, not a silent success.

`fetdirect`, `dropenc1` and `replace`, three one-off multi-step jobs, each
become a single `sch_edit` call.

### 5. `sch_place(doc, lib, rows, save=true)` — replaces 3

`rows`: `libref, designator, x, y, rotation, mirror, comment, [params]`.

Built in:
- `MoveByXY` by a delta (never `.Location`)
- after placing, `BoundingRectangle` must bracket the target
- the pin count must match the library symbol, which catches the empty-`Replicate` case

The response includes each part's measured pin hot-ends, ready for wiring or labels.

### 6. `sch_label_pins(doc, component, {pin: net}, kind=netlabel|power, save=true)` — replaces 2

This is addressed by pin, never by coordinate, which removes the one-pitch-off
label class of bug. Pin hot-ends are computed inside Altium.

- **Verification:** after saving, it runs `netlist_query` and reports any pin that
  did not land on the intended net. `addu2`'s labels were deleted as "orphaned"
  30 minutes after they were placed.
- **Blocker:** the hot-end calculation is known to be wrong for mirrored parts
  (`check_connectivity.pas` header). That needs one bench experiment on a
  mirrored part before this command can be trusted.
- **Guard:** it refuses a net name that does not already exist on the sheet
  unless `new_net=true` (GOTCHAS §3d: the `GND` that should have been `FE_SGND`).

### 7. Lower priority

- `pcblib_add_body(lib, footprint, shape=cylinder|box, dims, layer)` (2 scripts).
- Pin designators computed from pin names (A_n→2n−1) stay a caller-side map; it
  is not worth a pattern language.

## Rollout

| Step | What | Altium needed? | Reason for the position |
|---|---|---|---|
| 1 | Remove the 18 `ShowMessage` modals; add explicit-doc open helper | one smoke run | Small, and every tool benefits |
| 2 | `save_doc` | yes | Largest single time sink |
| 3 | `sch_query` + `netlist_query` (file-backed) | little | 31 scripts; reads can't break the design |
| 4 | `sch_edit`, `sch_place` | yes | Writes, all verified by step 3's reads |
| 5 | Mirrored-pin experiment, then `sch_label_pins` | yes | Blocked on the experiment |

The DelphiScript for steps 2, 4 and 5 already exists in the fixture scripts.
The work is lifting the proven versions into `schematic_utils.pas` behind a
dispatcher entry, not discovering the API again.
