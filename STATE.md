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

## Supported boundary

See `ISSUES.md`. Windows and a compatible installed/licensed Proteus are required.
This is an experimental integration, not universal compatibility. Missing IC
simulation models remain an explicit limitation; no invented behaviour is shipped.
Local `work/` evidence and proprietary sample projects are ignored and are not
part of the public repository. Record a reproducible test summary here after
verification; do not publish those private/generated source artifacts.
