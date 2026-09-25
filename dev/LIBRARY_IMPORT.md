# Building a workspace component library (Altium 365) - what works

Learned 2026-09-24 putting 290 standard resistors (E24, 0402 + 0603, 1 % RMCF with
Yageo AC / Vishay CRCW part choices) into an Altium 365 workspace.

## The route that works: SchLib + Library Importer  (`build_passive_schlib`)
1. Download the workspace **symbol** (e.g. RES-2) as a .SchLib and the **footprint** as a
   .PcbLib: Explorer > right-click item > Edit > File > Save As (or Operations > Download).
   Models are referenced by item ID in the workspace (SYM-..., PCC-...).
2. `build_passive_schlib(template_schlib, out_schlib, footprint, rows)` - copies the
   template, writes one component per row (name, description, comment, parameters,
   Manufacturer n / Manufacturer Part Number n, PCBLIB link), saves, verifies offline.
3. Altium: File > Import Library > "+ Library" the built SchLib AND the PcbLib > check
   Types shows the right type (template params are picked up from the component type) >
   Validate > Import (accept warnings).
   - Expected warnings only: "model has the same geometry as ..." (= ONE shared symbol,
     good) and "duplicated with components by Part Choices" (older parts carry the same
     MPN - retire them later).
   - Result for 142 parts: "Imported components: 142, Imported models: 2".
   - Pasted/imported MPNs resolve supply-chain data automatically.
4. Verify: Components panel count; spot-check a part's stock/price and footprint. To find a
   duplicate among hundreds: Explorer folder > Ctrl+A > Ctrl+C > paste to a text file and
   diff names against the intended list.

## Routes that do NOT work (don't retry)
- **DbLib + Library Importer:** components and Part Choices import, but the importer never
  loads the symbol/footprint from a DbLib - "Symbol name cannot be empty", with absolute
  Library Path, file-name-only paths + LibrarySearchPath, and a Symbol Name column alike.
- **Adding the raw template SchLib as a source** imports the template symbol itself as a
  component (and chases its default footprint link).

## The batch grid (Component Editor batch mode) - works, but slow
Explorer > Ctrl-click 2 components > right-click Edit. Gotchas:
- FolderPath / Item ID are auto; paste starts at **Name**.
- One pasted row per **Add Component** click - no auto-extend.
- "Datasheets" column appears only if a selected part has a datasheet (column shift trap).
- Models by **item ID** in PCBLIB (default) / SCHLIB columns.
- Part Choices are Manufacturer/Part Number column pairs; Altium manufacturer names:
  "Stackpole Electronics", "Yageo Group", "Vishay".
- File > Save is LOCAL; **File > Save to Server (Ctrl+Alt+S)** releases.

## Workspace housekeeping seen along the way
- Parts placed from Manufacturer Part Search are auto-acquired into the workspace (clutter).
- Generic footprints existed only at least density (_L), several duplicates of each.
- Old generic parts sit in Draft state; retire via lifecycle once boards use the standard parts.

## Capacitors (2026-09-24)

- Template symbol: SYM-006-0000-2 (CAP-NP-2.SchLib). One SchLib per footprint size, because every
  component in a build shares one footprint: 0402 (7), 0603 (15), 0805 (6) built from
  `library_gen.CAPACITORS`.
- Footprint = the TALLEST `_L` variant of each size (`CAPC0402(1005)60_L`, `CAPC0603(1608)100_L`,
  `CAPC0805(2012)145_L`); pads are the same across heights, so the tallest covers every part.
- 1206/1210: no standalone footprint item in the workspace (the IPC-named `CAPC3216X190X55L30T25`
  exists only inside a generic component) - build them before those caps can import.
- Component names over 31 characters are fine: only the OLE storage label is truncated; the
  LibReference inside keeps the full name. `verify` compares the first 31 characters.
- The Capacitor template's **Tolerance** parameter is typed Percent: "±0.5pF" fails Validate
  ("cannot be converted to unit of type Percent"). Put absolute tolerances in a separate
  "Tolerance Absolute" parameter.
- Add EVERY size's PcbLib as a source when importing several SchLibs in one run, or each
  missing one gives "Footprint ... is not found in available libraries".
- 1206/1210 had no standalone footprint item: built with `create_footprints_batch` (spec in raw
  library mils, origin 50000/50000 - same as `get_footprint_primitives` dumps), starting from a
  copy of the downloaded 0805 PcbLib (delete that footprint before import).
- 3D body (manual, ~1 min each): Tools > Manage 3D Bodies for Library > row "Shape created from
  bounding rectangle on Mechanical13" > Overall Height e.g. `1.9mm`, Registration Layer
  **Top 3D Body** (newer Altium lists layer TYPES, not "Mechanical 13") > click "Not In Component".
