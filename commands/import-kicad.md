---
description: Import a KiCad schematic into Proteus, check it and show a picture
argument-hint: <path/to/design.kicad_sch> [out.pdsprj]
---
Import the KiCad schematic `$1` into Proteus with the proteus MCP tools (output: `$2`, or the same
name with .pdsprj in a new `proteus` folder next to it; never overwrite an existing file).
Then open it in Proteus, export a PNG and look at it, run the ERC, close Proteus, and report in a
table: parts placed/skipped, nets verified, substitutions, ICs without a simulation model, ERC errors
and which of them matter. Follow the proteus skill.
