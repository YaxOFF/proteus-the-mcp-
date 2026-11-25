---
name: proteus
description: Use when the user wants to create, edit, import from KiCad, check (ERC), picture, or simulate a Labcenter Proteus 8 (.pdsprj) circuit, or to test firmware/behaviour of a circuit in Proteus. Covers the proteus MCP tools and the order they must be called in.
---

# Working with Proteus through the `proteus` MCP server

Proteus runs hidden on a private desktop: nothing appears on screen and no screen control is used.
Windows only; the user's own licensed Proteus 8 must be installed.

## Two modes

| Mode | Tools | Needs |
|---|---|---|
| Offline editing of the file | `project_new`, `project_open`, `add_component`, `connect`, `add_terminal`, `move_component`, `set_value`, `save_project`, ... | nothing running |
| Live Proteus | `open_in_proteus`, then `compile_netlist`, `export_image`, `run_erc`, `set_sheet_size`, `sim_*`, `probe_*`, finally `close_proteus` | a saved project |

Launch modes: `open_in_proteus()` hidden (default); `open_in_proteus(visible=true)` when the user
wants to watch; `attach_proteus(project)` when the user already has the project open in Proteus.
Attached Proteus is never closed or saved unless the user asks; `close_proteus` only detaches. To add
parts to an attached project: ask the user to save, edit offline, `save_project(overwrite=true)`,
`reload_in_proteus`. If reload reports unsaved changes, ask the user; never work around it.

Order for a live session: **save_project → open_in_proteus → work → close_proteus**. Always close,
so no PDS.EXE stays running. Offline edits made while Proteus is open are not seen by it: close, edit,
save, reopen.

## Look at what you built

After drawing or importing, call `open_in_proteus` then `export_image(path=".../circuit.png")` and
read the PNG. Fix placement problems you can see. Use `run_erc` before simulating and report its
errors in plain words; power-terminal/open-collector findings on imported designs are often benign,
say which ones.

## KiCad import

`import_kicad(kicad_sch, out_pdsprj)` flattens the hierarchy and rebuilds every part on one sheet with
net labels. Always report from the returned summary:
- `counts.nets_accounted_for / counts.nets` (connectivity, verified by reopening the file),
- `skipped` parts and `warnings` (substitutions such as generic relays),
- `simulation.placeholder_refs`: ICs with **no Proteus model** (connectivity-only headers). A design
  that depends on them cannot be simulated faithfully until they are replaced or driven by the probe.
Never claim the imported circuit simulates like the real board.

## Live probe

`add_probe` places a bridge part (MCPBRIDGE.DLL) on named nets; while the simulation runs,
`probe_read`/`probe_set`/`probe_step`/`probe_events`/`probe_analog` read and drive those nets with
simulated time held between steps. Use it to stimulate inputs and to check outputs numerically
instead of guessing from graphs.

## Honesty rules

- Placeholder or substituted parts are not models of the real chip; say so.
- A skipped or failed test does not validate a feature.
- Do not edit the user's original files to experiment; work on copies.
