# Known limitations and roadmap

## Current supported boundary

- **Windows only.** Local end-to-end evidence is Proteus 8.13.31525.0 with
  Python 3.10.11. Other Proteus builds and non-English UI menus need validation.
  Fingerprinted 8.16 internals in the inherited adapter are not a blanket
  compatibility promise.
- **Missing simulation models.** KiCad connectivity-only placeholders cannot
  simulate the original IC. The local six-IC example includes ISO7760DW,
  ISO7761DW, IA0505S, LP2985-1.8, LM5164DDA and ADM2587E, absent from the
  installed catalogue. Use validated vendor/Proteus models or review explicit
  functional replacements for a particular circuit. Disabling placeholders
  reports unsupported components as skipped.
- **Model substitutions.** Generic symbols preserve topology, not necessarily
  ratings, protection, isolation or transient behaviour. Firmware and supply
  configuration are not imported as a validated simulation setup.
- **Project format.** Only understood one-sheet native records are editable.
  Source KiCad layout/sheets are regenerated on one grouped sheet. PCB work is
  outside the current toolset.
- **Default pipe isolation.** One server workspace and one default bridge-enabled
  Proteus process at a time. The first digital probe is controllable; multiple
  analog probes are aggregated. Concurrent default pipes can collide.
- **Local installations.** Users with custom Proteus folders must supply
  `PROTEUS_HOME`/`PROTEUS_DATA`. New projects require the local Rescap sample.
  The generated template cache must be rebuilt after changing Proteus versions.
- **Offline/live state.** After saving live property changes, reopen the file
  before editing its offline representation or those changes may be replaced.

## Useful next improvements

1. Validate a matrix of Proteus builds, locales and Windows accounts on separate
   machines; expand support only with recorded live evidence.
2. Isolate bridge names per process and expose selection of multiple digital
   probes before supporting concurrent clients.
3. Add explicit, reviewed simulation models/mappings for chosen ICs; never mark
   a connectivity-only import as simulation-ready.
4. Add sheet-aware layout, richer native-object coverage and waveform workflows.
5. Add an optional setup/diagnostics interface once the supported build matrix
   is broader. Current configuration is local and environment-based.

These are limits and future work, not features claimed as complete.

- Large imports (thermo24, 459 parts) do not fit one A0 sheet; parts outside the border simulate but
  are cut from `export_image`. Next: split into several sheets or rotate/condense passives.
- thermo24 cannot be simulated end-to-end: AD7124-8, TMP126, ISO7761 and others have no Proteus
  model. Planned: probe-driven "virtual chips" (SPI slave in MCPBRIDGE + Python register model).
- `export_image(fit=True)` (select-all + marked area) did not enlarge the picture on 8.13; the tool
  uses the sheet scope.
