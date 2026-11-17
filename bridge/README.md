# MCPBRIDGE.DLL - Proteus live-simulation probe

`MCPBRIDGE.DLL` is a 32-bit Proteus VSM model. It lets an external controller
(the proteus-mcp server) read and drive the pins of a running Proteus
simulation.

Attach it to a part in Proteus:

- `PRIMITIVE=DIGITAL` (or `ANALOG`)
- `MODDLL=<path to MCPBRIDGE.DLL>`

While the simulation runs, the model opens the named pipe
`\\.\pipe\proteus_mcp`.

## Get the DLL

The easiest way is to download the prebuilt `MCPBRIDGE.DLL` from the
**GitHub Releases** page of this repository.

## Build it yourself

Requirements:

- Visual Studio 2019 or 2022 Build Tools with the **C++ workload** (x86 toolset).
- Labcenter's `vsm.hpp`. It is not shipped in this repository. It comes with
  the Labcenter VSM SDK. Put it in one of these places:
  - `bridge/sdk/vsm.hpp`, or
  - any folder, with the environment variable `VSM_SDK` set to that folder.

Build:

```bat
bridge\build.bat
```

The output is `bridge\MCPBRIDGE.DLL`.

The `bridge/sdk/` folder is git-ignored on purpose, so `vsm.hpp` is never
committed.

## Part properties

| Property     | Default       | Meaning                                                              |
|--------------|---------------|----------------------------------------------------------------------|
| `BRIDGEPINS` | (common names)| Comma-separated list of pin names to expose                          |
| `PIPE`       | `proteus_mcp` | Pipe name, giving `\\.\pipe\<PIPE>`                                  |
| `TICK`       | `0.001`       | Service period in seconds of simulation time; also the resolution of `STEP` |
| `START`      | `RUN`         | `RUN` runs immediately; `HOLD` pauses at t=0 until a client connects |

## Line protocol

The client sends one ASCII line per request. The bridge replies with one line
that starts with `OK` or `ERR`.

| Request                | Reply                                                        |
|------------------------|--------------------------------------------------------------|
| `PING`                 | `OK pong`                                                    |
| `TIME`                 | `OK <time>` - current simulation time in seconds             |
| `PINS`                 | `OK <pin>,<pin>,...` - the exposed pins                      |
| `READ`                 | `OK t=<time> <pin>=<state> ...` - time and state of every exposed pin |
| `SET <pin> <0\|1\|Z>`  | `OK` - drive the pin to 0, 1 or high impedance (Z)           |
| `EVENTS`               | `OK <event>;<event>;...` - pin events recorded since the last call (the list is cleared) |
| `HOLD`                 | `OK held t=<time>` - pause the simulation                    |
| `STEP <seconds>`       | Replies only after the target time is reached                |
| `RUN`                  | `OK running` - resume the simulation                         |

Analog commands `APINS` and `AREAD` are being added. See the main README for
their usage.

## Troubleshooting

- **The DLL does not load:** Proteus 8 is a 32-bit program, so the DLL must be
  built for x86. `build.bat` already uses `/MACHINE:X86`.
- **Trace log:** the bridge writes a diagnostic log to `%TEMP%\mcpbridge.log`.
  Check it first.
- **`ERR simulation not running`:** the simulation is stopped. Press Run in
  Proteus, or send `RUN` if the bridge is in `HOLD`.
