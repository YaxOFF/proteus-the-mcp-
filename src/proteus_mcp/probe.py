"""Drop-in live probes: add MCPBRIDGE.DLL to any circuit with no GUI work.

Chosen approach (verified on Proteus 8.13)
------------------------------------------
A probe is an ordinary library connector, ``CONN-SIL<n>`` from CONNDVC (a 0.1in header,
pins named "1".."n"), whose instance properties are set to::

    PRIMITIVE = DIGITAL,MCPBRIDGE_D     (or ANALOG,MCPBRIDGE_A)
    MODDLL    = <full path to MCPBRIDGE.DLL>
    BRIDGEPINS = 1,2,...,k              (the pins that are wired)

Proteus then loads the DLL for that part and the DLL exposes the pins over the named
pipe ``\\\\.\\pipe\\proteus_mcp`` (see ``proteus_mcp.bridge_client.BridgeClient``).

* The text after the comma in PRIMITIVE selects the probe kind. Proteus builds both a
  SPICE and a DSIM model for every DLL part and asks the SPICE side which pins are analog
  *before* setup, so the DLL decides by that name: only ``MCPBRIDGE_A`` claims pins as
  analog; any other name (``MCPBRIDGE_D``) is a digital probe.
* One header gives up to 60 digital pins or 8 analog pins. Up to 18 pins use a
  ``CONN-SIL<n>`` header; larger digital probes use ``CONN-DIL<n>`` (up to CONN-DIL60, pins
  also named "1".."n"). The bridge DLL itself has no pin limit. A circuit can hold one digital and
  one analog probe at once; both answer on the same pipe. Only the first digital probe
  receives READ/SET/HOLD/STEP, several analog probes are listed as ``REF.PIN``.
* Wiring: the engine only routes simple wires, so each probe pin is joined to its target
  by (1) copying a label/power/ground terminal that already names the target net,
  else (2) a wire, else (3) a new net label on both pins (only for target pins that carry
  no wire). Observing a ground/label net always works; a long-routed wired net may raise
  ``ProbeError``. Place the probe in free space to the right of the circuit.

What did not work: setting PRIMITIVE=DIGITAL on a part whose model name is also analog
-- the old DLL answered ``isanalog`` TRUE for every part, so digital pins were never
bound. Hence the explicit model names. ADI cannot read PRIMITIVE back (it is not in the
netlist), so ``attach_probe`` on a live Session cannot verify it; the pipe is the proof.

Public API
----------
``add_probe(circuit, ref, x, y, kind="digital", pins=...)``  place + wire a new header.
``attach_probe(target, ref, kind="digital", pins=None)``     turn an existing part into a
probe; ``target`` is a ``Circuit`` (edits the model) or a live ``Session`` (ADI import).
"""
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

PRIMITIVES = {"digital": "DIGITAL,MCPBRIDGE_D", "analog": "ANALOG,MCPBRIDGE_A"}
LIMITS = {"digital": 60, "analog": 8}
# Header sizes that exist in CONNDVC, smallest first: single-row, then dual-row for big probes.
HEADERS = ([(n, f"CONN-SIL{n}") for n in (*range(1, 11), 12, 18)]
           + [(n, f"CONN-DIL{n}") for n in (20, 30, 34, 40, 50, 60)])
LIBRARY = "CONNDVC"


class ProbeError(Exception):
    pass


@dataclass
class Probe:
    ref: str
    kind: str
    device: str
    pins: dict = field(default_factory=dict)   # signal name -> probe pin name ("1".."n")

    def signals(self, values):
        """Rename a ``read()['pins']`` / ``aread()['volts']`` dict from probe pins to signal names."""
        return {name: values[pin] for name, pin in self.pins.items() if pin in values}


def find_dll(dll=None):
    """Locate MCPBRIDGE.DLL: explicit path, $PROTEUS_MCP_BRIDGE_DLL, the repo's bridge/ folder,
    then %LOCALAPPDATA%\\proteus-mcp and Proteus' MODELS/BIN folders."""
    candidates = [dll, os.environ.get("PROTEUS_MCP_BRIDGE_DLL"),
                  Path(__file__).resolve().parents[2] / "bridge" / "MCPBRIDGE.DLL",
                  Path(os.environ.get("LOCALAPPDATA", "")) / "proteus-mcp" / "MCPBRIDGE.DLL"]
    for item in candidates:
        if item and Path(item).is_file():
            return str(Path(item).resolve())
    from .engine.proteus_paths import home
    for folder in ("MODELS", "BIN"):
        candidate = home() / folder / "MCPBRIDGE.DLL"
        if candidate.is_file():
            return str(candidate.resolve())
    raise FileNotFoundError("MCPBRIDGE.DLL not found; build it with bridge\\build.bat or pass dll=...")


def _kind(kind):
    if kind not in PRIMITIVES:
        raise ValueError("kind must be 'digital' or 'analog'")
    return kind


def _properties(kind, pin_names, dll, pipe, extra):
    props = dict(PRIMITIVE=PRIMITIVES[kind], MODDLL=find_dll(dll))
    if pin_names:
        props["BRIDGEPINS"] = ",".join(pin_names)
    if pipe and pipe != "proteus_mcp":
        props["PIPE"] = pipe
    props.update({key.upper(): str(value) for key, value in extra.items()})
    return props


def signal_maps(circuit):
    """{'digital': {pin: name}, 'analog': {pin: name}} from probe parts' SIGNALS properties.

    The bridge drives the first digital probe; analog readings aggregate analog probes."""
    maps = {"digital": {}, "analog": {}}
    for part in circuit.components():
        props = part.get("properties", {})
        if "SIGNALS" not in props or "MODDLL" not in props:
            continue
        kind = "digital" if "DIGITAL" in props.get("PRIMITIVE", "").upper() else "analog"
        if kind == "digital" and maps["digital"]:
            continue
        for item in props["SIGNALS"].split(","):
            name, _, pin = item.partition("=")
            maps[kind].setdefault(pin, name)
    return maps


def _normalise(pins):
    """``{"CLK": "U1.CLK"}`` or ``["U1.CLK", "U1.Q"]`` (signal name = endpoint) -> ordered dict."""
    if isinstance(pins, dict):
        return dict(pins)
    return {str(endpoint): endpoint for endpoint in pins}


def _wire(circuit, probe_pin, endpoint, net):
    """Join ``probe_pin`` to ``endpoint`` with a straight wire or a label pair (never a zigzag)."""
    from .engine.proteus_project import UnsupportedFormat
    from .wiring import link
    try:
        link(circuit, probe_pin, endpoint, name=net)
    except (UnsupportedFormat, ValueError) as error:
        raise ProbeError(f"cannot connect {probe_pin} to {endpoint}: {error}. Move the probe (x, y) "
                         "or label the net first.") from error


def auto_position(circuit, pins):
    """A free spot right of the observed parts, level with the first observed pin (native units)."""
    endpoints = list(_normalise(pins).values()) if pins else []
    refs = {circuit._endpoint(e)[0] for e in endpoints}
    if not refs:
        raise ValueError("pins must name at least one endpoint to observe")
    xs = [p["position"][0] for ref in refs for p in circuit.pins(ref)]
    first = circuit._endpoint(endpoints[0])
    y = next(p["position"][1] for p in circuit.pins(first[0]) if p["name"] == first[1])
    x = max(xs) + 3000000  # 30 mm of room for the net labels
    return int(round(x / 254000) * 254000), int(round(y / 254000) * 254000)


def add_probe(circuit, ref, x, y, kind="digital", pins=(), *, dll=None, pipe="proteus_mcp", align=False,
              **properties):
    """Place a probe header at (x, y), wire it to ``pins`` and attach MCPBRIDGE.DLL.

    ``pins`` maps a signal name to the circuit endpoint it observes, e.g.
    ``{"CLK": "U1.CLK", "Q": "U1.Q"}`` (a plain list of endpoints uses the endpoint as the
    name). Up to 60 (digital) / 8 (analog) signals; the i-th signal goes to probe pin i.
    Returns a ``Probe`` whose ``.signals()`` renames bridge readings back to signal names.
    """
    kind = _kind(kind)
    pins = _normalise(pins)
    if not pins:
        raise ValueError("pins must name at least one endpoint to observe")
    if len(pins) > LIMITS[kind]:
        raise ValueError(f"a {kind} probe supports at most {LIMITS[kind]} pins, got {len(pins)}")
    device = next(name for size, name in HEADERS if size >= len(pins))
    names = [str(i) for i in range(1, len(pins) + 1)]
    for signal in pins:
        if not re.fullmatch(r"[!-~]+", str(signal)) or set(str(signal)) & set(",={}"):
            raise ValueError(f"probe signal name {signal!r}: printable ASCII without spaces, ',', '=' or braces")
    # Signal names are kept on the part so a reopened project still knows them.
    properties = dict(properties, SIGNALS=",".join(f"{s}={n}" for s, n in zip(pins, names)))
    props = _properties(kind, names, dll, pipe, properties)
    if device not in circuit.templates:
        circuit.import_device(device, LIBRARY)
    circuit.add(device, ref, device, x, y, properties=props, hide_properties=True)
    if align:
        # Put probe pin 1 level with the first observed pin so a straight wire can join them.
        target = circuit._endpoint(next(iter(pins.values())))
        target_y = next(p["position"][1] for p in circuit.pins(target[0]) if p["name"] == target[1])
        pin_y = next(p["position"][1] for p in circuit.pins(ref) if p["name"] == "1")
        if pin_y != target_y:
            circuit.move(ref, x, y + target_y - pin_y)
    probe = Probe(ref, kind, device, dict(zip(pins, names)))
    for (signal, endpoint), pin in zip(pins.items(), names):
        net = re.sub(r"[^A-Za-z0-9_]", "_", f"PRB_{ref}_{signal}")
        _wire(circuit, f"{ref}.{pin}", endpoint, net)
    return probe


def _adi_attach(session, ref, props):
    """Import properties through Proteus' ASCII Data Import tool (no read-back possible)."""
    if not re.fullmatch(r"[A-Za-z0-9_:+.\-]+", ref):
        raise ValueError("A literal reference is required")
    lines = [f'IF REF="{ref}"'] + [f' {key}="{value}"' for key, value in props.items()] + ["END", ""]
    path = session._workdir / f"probe-{os.getpid()}-{int(time.time() * 1000)}.adi"
    path.write_text("\n".join(lines), encoding="ascii")
    try:
        session.menu("Tool/ASCII Data Import Tool")
        session._file_dialog("ASCII Data Import", path)
        session._wait(session._ready)
        time.sleep(1)   # the import runs after the dialog closes; nothing to poll for
    finally:
        path.unlink()


def attach_probe(target, ref, kind="digital", pins=None, *, dll=None, pipe="proteus_mcp", **properties):
    """Turn the existing part ``ref`` into a probe.

    ``target`` is a ``Circuit`` (the properties are stored in the project) or a live
    ``Session`` (applied through ADI; call ``session.save()`` afterwards to keep them).
    ``pins`` optionally lists the pin names to expose; by default the DLL tries the usual
    names ("A".."D", "IN", "OUT", "D0".."D15", "1".."32", ...). The part must be a
    generic device whose pins are not already claimed by a model (connectors, headers).
    """
    kind = _kind(kind)
    names = [str(p) for p in pins] if pins else []
    props = _properties(kind, names, dll, pipe, properties)
    if hasattr(target, "set_properties") and hasattr(target, "components"):    # a Circuit
        target.set_properties(ref, **props)
    elif hasattr(target, "menu") and hasattr(target, "_file_dialog"):          # a Session
        _adi_attach(target, ref, props)
    else:
        raise TypeError("target must be a Circuit or a Session")
    return Probe(ref, kind, "", {name: name for name in names})
