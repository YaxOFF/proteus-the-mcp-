# Third-Party Notices

This file lists third-party code, trademarks and tools that are referenced by
or used with proteus-mcp.

## 1. proteus-automatic-api (modified fork)

The directory `src/proteus_mcp/engine/` is a modified fork of
**proteus-automatic-api**:

- Project: https://github.com/kudoumakoto6523-design/Proteus_automatic_package
- License: MIT
- Copyright (c) 2026 proteus-automatic-api contributors

### Changes made in this fork

- Added support for the Proteus 8.13 project file version 830.
- Made the Proteus save dialog handling independent of the Proteus version.
- Added auto-detection of Proteus install paths (`proteus_paths.py`).
- Changed internal imports to relative imports so the package can be packaged.
- Removed the research and demo modules that are not needed by the server.

### Upstream license text

The original code is licensed under the following terms. The copyright line
below is the one from the upstream project.

```
MIT License

Copyright (c) 2026 proteus-automatic-api contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## 2. Live-simulation probe (`bridge/`)

The probe in `bridge/` (`mcpbridge.cpp`) was written from scratch for this
project. It was inspired by the public example
https://github.com/MockbaTheBorg/VSMModel, but no code from that project is
included.

## 3. Labcenter VSM SDK header (`vsm.hpp`) - not included

Labcenter's `vsm.hpp` (VSM SDK header) is **not** included in this repository
and is **not** covered by this project's license. It is copyrighted by
Labcenter Electronics Ltd.

To build the probe, obtain `vsm.hpp` yourself. It ships with the Labcenter VSM
SDK. Place it in `bridge/sdk/vsm.hpp`, or set the `VSM_SDK` environment
variable to the folder that contains it. The `bridge/sdk/` folder is
git-ignored on purpose.

## 4. Trademarks

- "Proteus", "VSM", "ISIS" and "Labcenter" are trademarks of Labcenter
  Electronics Ltd.
- This project is not affiliated with, sponsored by, or endorsed by Labcenter
  Electronics Ltd.

## 5. KiCad

- "KiCad" is a trademark of the KiCad project.
- The KiCad importer does not bundle KiCad. It only calls the `kicad-cli`
  program that the user has installed on their own machine.
