# Project state

Updated: 2026-11-23. Release 0.1.0 verified and published.

Read `AGENTS.md` first. Current task: finish reliability/portability cleanup,
document supported boundaries, verify the installed package and publish to the
user-authorized public GitHub repository `mahmoud-elghamry/proteus-mcp`.

## Verified so far

- Local target: Proteus 8.13.31525.0, Python 3.10.11, MCP SDK 2.3.0.
- 41 MCP tools registered.
- Offline suite: 64 passed, 4 deselected, in `work/completion-offline-final.txt`.
- Real direct integration: 3 passed in `work/completion-live-initial.txt`.
- Real stdio MCP flow, including 5 V divider, AND gate, held stop/restart and
  save/close, passed in `work/completion-mcp-live3.txt` (26.72 s), from a working
  folder with spaces outside the package directory.
- Old test hangs were traced to the restricted Windows event-loop socket;
  the normal local environment runs them. Named-pipe tests pass there.

## Release 0.1.0 verification (2026-11-23)

- Offline suite: 64 passed, 4 skipped (the 4 are the `-m proteus` live tests).
- Live suite on Proteus 8.13 (`-m proteus`): 4 passed in 64 s, including the
  stdio MCP end-to-end flow; no PDS.EXE left running.
- Wheel built and installed into a fresh venv outside the repo: server starts
  over stdio, `proteus_info` finds PDS.EXE 8.13.31525.0, `kicad_map.json` ships.
- Template cache `template-v2.pdsprj` has the Labcenter master-sheet title
  block removed (no logo under generated circuits).
- Published file set checked: no SDK header, DLL, Proteus project, `work/`
  artifact or personal path. `MCPBRIDGE.DLL` is attached to the GitHub release.

## 0.2.0 work in progress (2026-11-23)

- New live tools `export_image` (png/svg/pdf via Proteus's own export dialogs), `run_erc`
  (Proteus Electrical Rules Check, report saved and parsed), `set_sheet_size` (A4..A0). 44 tools.
  Verified on 8.13 with `work/menus/try_exports.py` (SVG/PDF/PNG written, ERC parsed).
- KiCad importer: compact measured shelf-pack layout centred on the sheet and sheet-size choice
  (was a 1.2 m x 6.5 m grid that left the picture empty); `Circuit.batch()` defers whole-sheet
  validation (8ch import 6 min -> 27 s); pad-number fallback refused for named pins on multi-pin
  parts (TQFP-44 ATmega1284P had been mapped by DIP-40 numbers); pad suffix (`PB1_41`) stripped;
  maps for 2N7002, G6K-2 (RELAY2P, pinout from KiCad symbol), SM712/mounting holes dropped with notes.
- 8ch: 202/202 parts, 163/163 nets accounted, fits A0. 24ch (`hardware/24ch/thermo24`):
  459/470 placed (11 holes/TVS dropped), 286/286 nets reproduced, 20 placeholder ICs
  (AD7124-8, TMP126/ADT7310, ISO7761, LM5164, ADM2587E, ...); block 1437x972 mm exceeds A0.
- Claude Code plugin: `.claude-plugin/plugin.json`, `marketplace.json`, `.mcp.json` (uvx from git),
  `skills/proteus/SKILL.md`, `commands/`; `claude plugin validate .` passes.
- Codex-written, reviewed and integrated: `firmware.py`, `waveform.py`, `testbench.py`, `doctor.py`
  (+ tests). Server now has 52 tools: firmware_build/load, run_testbench, measure_signal,
  measure_delay, export_vcd, probe_clear_events, doctor.
- Live end-to-end (work/fw): blink.c built with Proteus-bundled avr-gcc for ATMEGA328P @ 8 MHz,
  loaded, simulated; measured 99.99 Hz / 50 % duty; testbench 5/5 passed; VCD written.
  Review fixes: AVR CLKDIV8 fuse defaults to programmed (clock/8) -> cleared when clock_hz given;
  measure() ignored the first observation as a pulse; probe signal names stored on the part
  (`SIGNALS`) and translated by the server (raw pin-number readings unchanged).
- Offline suite: 190 passed. Live suite (-m proteus) on Proteus 8.13: 5 passed (incl. stdio MCP e2e).

## Supported boundary

See `ISSUES.md`. Windows and a compatible installed/licensed Proteus are required.
This is an experimental integration, not universal compatibility. Missing IC
simulation models remain an explicit limitation; no invented behaviour is shipped.
Local `work/` evidence and proprietary sample projects are ignored and are not
part of the public repository. Record a reproducible test summary here after
verification; do not publish those private/generated source artifacts.
