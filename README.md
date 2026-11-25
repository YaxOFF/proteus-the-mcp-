# proteus-mcp

Let an MCP-compatible assistant create, inspect and edit Labcenter Proteus 8
schematics, control simulations, read digital/analog probes, and import KiCad
connectivity. The server exposes 54 tools over stdio.

[العربية](README.ar.md)

> **Community fork.** This repository continues
> [mahmoud-elghamry/proteus-mcp](https://github.com/mahmoud-elghamry/proteus-mcp)
> (MIT), whose engine is a modified fork of
> [proteus-automatic-api](https://github.com/kudoumakoto6523-design/Proteus_automatic_package)
> (MIT). All credit for the original work goes to those authors; see [NOTICE.md](NOTICE.md).
>
> Checked in this fork on Proteus 8.13.31525.0 (Windows 11, Python 3.12):
> - Source audit: the only runtime dependency is `mcp`, and the code makes no network calls.
>   `MCPBRIDGE.DLL` v0.1.0 (sha256 `5b9ec5ec553de304bb5643a808c95bd4fe007c3c51dedb499fa9ae996fcf2de0`)
>   imports only `KERNEL32` and exports the four standard VSM model entry points.
> - Offline suite: 162 passed, 28 skipped.
> - Live, over MCP stdio with a visible Proteus: a 74HC08 truth table, a 74HC161 counter and a
>   74HC161 + 74HC257 program-counter module, all driven and read through one digital probe.
>   See [examples/smoke_74hc.py](examples/smoke_74hc.py).
>
> Added in this fork:
> - **Gates of multi-element chips**: place `U1:A`..`U1:D` of a 74HC08 (or 74HC00/02/04/32/86/139 ...)
>   with the pad numbers from the library's `*PINOUT` record, as Proteus itself stores them.
> - **Probes up to 60 pins** (`CONN-DIL` headers above 18 pins).
> - **`build_circuit(spec)` / `verify_netlist(spec)`**: build a whole circuit from a JSON netlist and
>   check Proteus's compiled netlist against it (splits, shorts, missing pins). `build_circuit` warns
>   about library parts without a simulation model (e.g. 74HC541 in 8.13; 74LS541 has one).
> - **`sim_start` explains refusals**: it returns Proteus's log ("No model specified for U32", linker
>   errors) instead of timing out.
>
> Used to simulate a 32-chip 74HC 8-bit computer end to end (9 programs, instruction by instruction).
>
> Known limit on 8.13: interactive controls such as LOGICSTATE cannot be toggled through the tools,
> because the control-state reader targets the Proteus 8.16 build. Use a digital probe
> (`add_probe`) to drive and read signals.

**Status: experimental Windows integration.** The complete MCP-to-Proteus flow
has been tested locally with Proteus 8.13.31525.0 and Python 3.10.11. This is
not a guarantee for every Proteus build or project format. No Proteus licence,
libraries, sample projects, or SDK headers are included.

## Requirements

- Windows, Python 3.10+ and the Windows `py` launcher. Other operating systems
  cannot run the native Proteus tools.
- A working, licensed Proteus 8 installation with schematic capture, the sample
  projects and any simulation models required by your circuit.
- `MCPBRIDGE.DLL` for live probes only. Editing and ordinary simulation controls
  work without the bridge. Proteus 8 loads a **32-bit x86** bridge even when
  Python and Windows are 64-bit.
- KiCad CLI for importing `.kicad_sch` files. Importing an already exported
  KiCad XML netlist does not require KiCad.

Proteus 8.13 is the locally tested target. The underlying adapter also contains
8.16 support, including build-specific memory readers guarded by fingerprints;
other versions and translated menu labels need their own validation. Unsupported
project structures are rejected rather than rewritten by guesswork.

## Install

In PowerShell, clone and install with the same Python launcher used by your MCP
client:

```powershell
git clone https://github.com/YaxOFF/proteus-the-mcp-.git
cd proteus-mcp
py -3 -m pip install --user .
```

Or download the wheel from this repository's Releases page, change to its folder,
and install it:

```powershell
py -3 -m pip install --user .\proteus_mcp-0.1.0-py3-none-any.whl
```

The package does not depend on the clone's location or current working directory.
If using a virtual environment instead, install inside it and configure your
client to use that environment's Python executable. A virtual environment's
absolute executable path is necessarily local configuration, not a path shipped
in this project's code.

### As a Claude Code plugin

The repository is also a Claude Code plugin marketplace. The plugin bundles the MCP server
(started with `uvx`, so install [uv](https://docs.astral.sh/uv/) first), a `proteus` skill that
teaches the assistant the right call order and honesty rules, and the commands
`/proteus:import-kicad` and `/proteus:check-circuit`:

```text
/plugin marketplace add YaxOFF/proteus-the-mcp-
/plugin install proteus@proteus-mcp
```

### Optional live probes

Download `MCPBRIDGE.DLL` from Releases and place it in:

```powershell
New-Item -ItemType Directory -Force "$env:LOCALAPPDATA\proteus-mcp"
Copy-Item .\MCPBRIDGE.DLL "$env:LOCALAPPDATA\proteus-mcp\MCPBRIDGE.DLL"
```

Alternatively, build it with `bridge\build.bat`; see [bridge/README.md](bridge/README.md).
The installed package finds the DLL in the per-user folder automatically. No
administrator access or modification of the Proteus installation is needed.

### Connect an MCP client

For clients that accept `mcpServers` JSON, merge this entry into their existing
configuration. Do not replace unrelated servers:

```json
{
  "mcpServers": {
    "proteus": {
      "command": "py",
      "args": ["-3", "-m", "proteus_mcp.server"]
    }
  }
}
```

Examples are in [examples/](examples/). The client must be able to find `py` and
the Python installation where you installed the package. Restart the client
after changing its configuration. There is no server port, cloud account, API
key, or paid service required by this project.

First ask the assistant to call `proteus_info`. It reports the executable,
version, library, KiCad CLI and bridge it actually found. A discovered path is
not a successful simulation: use the smoke test below to verify the full flow.

### Nonstandard installations

Standard Windows installation folders are discovered from Windows environment
variables. For custom locations, set these variables in the MCP server's `env`
configuration (using the paths on that computer), then restart the server:

| Variable | Meaning |
| --- | --- |
| `PROTEUS_HOME` | Proteus installation folder containing `BIN\PDS.EXE` |
| `PROTEUS_DATA` | Data folder containing `LIBRARY` and `SAMPLES` |
| `PROTEUS_MCP_BRIDGE_DLL` | Full path to a custom bridge DLL |
| `KICAD_CLI` | Full path to `kicad-cli.exe`, if not on PATH or in its standard folder |
| `PROTEUS_MCP_VISIBLE` | Set `1` to use the ordinary desktop for debugging |

On the first new project, the server copies your installation's `Rescap.pdsprj`
sample, opens the copy in hidden Proteus, removes the master-sheet logo/title
artwork, saves it in the installed format and caches `template-v2.pdsprj` under
`%LOCALAPPDATA%\proteus-mcp`. The original sample is never edited. Master-sheet
cleanup uses Delete and leaves the user's clipboard alone.
After changing Proteus versions, remove this generated cache and let it rebuild.

## Watch it, or let it use your own Proteus

- **Watch:** `open_in_proteus(visible=true)` launches Proteus on your normal desktop instead of the
  hidden one. No configuration or restart needed. Avoid clicking in it while a tool is running.
- **Your own window:** open the project in Proteus yourself, then `attach_proteus(project)`.
  Images, ERC, netlist, property changes, firmware loading, simulation, probes and testbenches all
  act on your window. Safety: it never closes your Proteus, never saves unless asked, and
  `close_proteus` only detaches (your simulation keeps its state).
- **Adding parts to your open project:** save in Proteus, edit with the offline tools,
  `save_project(overwrite=true)`, then `reload_in_proteus`. If Proteus has unsaved changes the
  reload is refused (its prompt is cancelled), so nothing of yours is lost or overwritten in Proteus.

## Typical workflow

1. `proteus_info`, then `project_new(path)` or `project_open(path)`.
2. Use `library_search`, `library_get`, `add_component`, `get_pins`, `connect`,
   `add_terminal`, `set_value`, `move_component` and other editing tools.
3. `save_project`. Existing files require `overwrite=true`.
4. `open_in_proteus` launches an owned, hidden Proteus process. `compile_netlist`
   returns the connectivity compiled by Proteus itself.
5. `sim_start`, `sim_pause`, `sim_status`, `sim_log`, `sim_stop` control it.
6. Always finish with `close_proteus`. `save=true` stops simulation, saves the
   live changes, and closes. The default discards unsaved changes in the live app.

Coordinates are millimetres. Pin endpoints are `REF.PIN`, for example `R1.2`.
The server holds one offline circuit and one owned Proteus session at a time.
Work on copies of original projects. Saving the live app does not refresh the
offline circuit automatically; call `project_open` again before further editing
if you need to keep live property changes.

To review a design, with Proteus open:

- `export_image(path, format="png")` exports the sheet through Proteus's own export
  (png at 100/200/300 DPI, svg, or pdf with `all_sheets=true`); the assistant can read the PNG.
- `run_erc()` runs Proteus's Electrical Rules Check and returns errors and warnings separately.
- `set_sheet_size("A0")` picks the sheet size (A4..A0).

For probes, add `add_probe` before saving, then open and start simulation:

- Digital: `probe_read`, `probe_set`, `probe_events`.
- Analog: `probe_analog` (volts).
- Deterministic stepping: `probe_hold`, `probe_step(seconds)`, `probe_run`.

One digital probe exposes up to 16 pins; one analog probe exposes up to 8. The
current bridge drives the first digital probe; analog readings can aggregate
multiple analog probes. Run only one bridge-enabled Proteus session per default
pipe name; concurrent MCP servers using that same pipe are not isolated.

Tool results include `ok`; errors include `error` and an actionable `hint`. A
successful MCP transport response alone does not mean the operation succeeded.

## Firmware, testbenches and waveforms

1. `firmware_build(sources, mcu, f_cpu, out_dir)` compiles AVR C with avr-gcc (the copy that ships
   with Proteus is found automatically) and returns the .hex, flash/RAM use and warnings.
2. `firmware_load(ref, hex_path, clock_hz)` sets the microcontroller's PROGRAM and CLOCK. For AVR
   parts it also clears the CLKDIV8 fuse, which Proteus programs by default (clock / 8).
3. Add a probe on the pins to watch with friendly names, e.g. `add_probe("J1", 80, 0, "digital",
   {"LED": "U1.PB5/SCK/PCINT5"})`. Readings, events and testbenches then use `LED`.
4. `run_testbench(bench)` runs set / run / expect / expect_analog / expect_frequency /
   expect_duty / wait_for steps against the live simulation and reports pass/fail per step.
5. `measure_signal(pin)`, `measure_delay(a, b)` and `export_vcd(path)` (GTKWave) work on the
   recorded probe edges; `probe_clear_events` starts a new window.

`doctor` checks Proteus, library, template, probe DLL bitness, KiCad, avr-gcc and leftover
processes, with a fix for every problem.

Verified on Proteus 8.13: a blink program built for ATmega328P at 8 MHz was loaded, simulated and
measured at 99.99 Hz / 50 % duty, and its testbench passed.

## KiCad import and simulation models

Call `import_kicad(kicad_sch, out_pdsprj, placeholders=true)`. It writes a new
Proteus project and a neighbouring `.report.json`, then reopens the project and
verifies the reconstructed electrical pin groups for splits and unwanted merges.
The importer may replace an existing output/report: choose fresh paths.

This imports **connectivity**, not the source drawing or validated circuit
behaviour. Source sheets become one grouped grid. Generic substitutions may
have different ratings or behaviour. Firmware, power configuration and model
parameters need separate review.

Unknown ICs can be represented by numbered, model-free headers. For example,
an isolator drawn this way retains pin-to-net connections but does not actually
isolate or transmit signals during simulation. The report identifies these in
`simulation.placeholder_refs`, lists substitutions/skips, and always states
`simulation.verified=false`. Disable this fallback with `placeholders=false`;
unsupported parts are then reported as skipped. Neither mode manufactures a
missing simulation model.

CLI equivalent for a pre-exported netlist:

```powershell
py -3 -m proteus_mcp.kicad_import design.xml -o imported.pdsprj --template template.pdsprj --no-placeholders
```

The CLI template is a project saved by your own compatible Proteus installation,
such as the generated template reported above. Inspect `skipped`, `warnings`,
`counts` and `simulation` in every report; connectivity verification can pass
for surviving pins even when some source components were skipped.

## Develop and verify

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m pytest -q -m "not proteus"
.\.venv\Scripts\python -m pytest -q -m proteus
```

The second group launches real hidden Proteus instances on disposable copies.
It tests 5 V/3.75 V/0 V analog readings, AND-gate inputs/output and transitions,
hold/step, pause/resume/stop, probe attachment, template creation, plus the full
stdio MCP flow including stop/restart and save/close. Tests that depend on a
local library/template skip when prerequisites are absent. The offline library
tests currently look for a locally prepared `work/template813.pdsprj`; create a
copy of the generated template there to enable them. Never commit that sample.

Windows named pipes, local loopback sockets and starting Proteus must be
permitted by your test environment. A blocked Windows event-loop socket can
look like a stalled test; this is not evidence that the server is operational.

Build a wheel with `py -3 -m pip wheel . --no-deps -w dist`. The wheel contains
the Python package, mapping JSON and notices; the bridge DLL is a separate asset.
CI checks the portable Windows test subset and wheel build without a licensed
Proteus installation; it does not certify native simulation on every machine.

## Known limits and next work

- Single-sheet structures and the native records recognized by this adapter.
  PCB layout generation and arbitrary multi-sheet editing are outside scope.
- English native menu names and build-specific simulation controls.
- No automatic model-equivalence claim for KiCad imports.
- One active default bridge pipe; multi-process isolation is future work.
- Further useful work: broader version testing, model-specific import mappings,
  sheet-aware layout, waveform export workflows and an installer/diagnostics UI.

See [ISSUES.md](ISSUES.md) for the tracked limits and [STATE.md](STATE.md) for
the verified handoff. The engine is adapted from `proteus-automatic-api`; see
[NOTICE.md](NOTICE.md). Project code is MIT licensed. Proteus and its SDK remain
separate third-party software.
