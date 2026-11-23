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
* One header gives up to 16 digital pins (CONN-SIL18 is the largest size that exists
  with >= 16 pins) or 8 analog pins (CONN-SIL8/10). A circuit can hold one digital and
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
LIMITS = {"digital": 16, "analog": 8}
SIL_SIZES = tuple(range(1, 11)) + (12, 18)   # CONN-SIL sizes that exist in CONNDVC
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


def _normalise(pins):
    """``{"CLK": "U1.CLK"}`` or ``["U1.CLK", "U1.Q"]`` (signal name = endpoint) -> ordered dict."""
    if isinstance(pins, dict):
        return dict(pins)
    return {str(endpoint): endpoint for endpoint in pins}


def _named_terminal(circuit, endpoint):
    """A label/power/ground terminal already naming the net of ``endpoint`` (or None)."""
    pin = circuit._endpoint(endpoint)
    net = next((group for group in circuit.nets() if pin in [tuple(item) for item in group]), [])
    members = {tuple(item) for item in net}
    for terminal in circuit.terminals():
        name = terminal.get("name") or ("GND" if terminal["kind"] == "ground" else "")
        if (terminal["kind"] in ("label", "power", "ground") and name and terminal.get("pin") is not None
                and tuple(terminal["pin"]) in members):
            return terminal["kind"], name
    return None


def _wire(circuit, probe_pin, endpoint, net):
    """Join ``probe_pin`` to ``endpoint``.

    The codec only routes simple wires, so in order: reuse a label/power/ground terminal that
    already names the target net (no routing needed); draw a wire; put the same new label on
    both pins (target pin must carry no wire).
    """
    from .engine.proteus_project import UnsupportedFormat
    named = _named_terminal(circuit, endpoint)
    if named is not None:
        circuit.add_terminal(named[0], named[1], pin=probe_pin)
        return
    try:
        circuit.connect(probe_pin, endpoint)
        return
    except UnsupportedFormat as wire_error:
        reason = str(wire_error)
    added = []
    try:
        for pin in (probe_pin, endpoint):
            added.append(circuit.add_terminal("label", net, pin=pin))
    except UnsupportedFormat as label_error:
        for terminal in added:
            circuit.delete_terminal(terminal)
        raise ProbeError(f"cannot wire {probe_pin} to {endpoint}: {reason}. Observe a pin whose net "
                         "has a net label, ground or power terminal, move the probe (x, y), "
                         "or route the net manually.") from label_error


def add_probe(circuit, ref, x, y, kind="digital", pins=(), *, dll=None, pipe="proteus_mcp", **properties):
    """Place a probe header at (x, y), wire it to ``pins`` and attach MCPBRIDGE.DLL.

    ``pins`` maps a signal name to the circuit endpoint it observes, e.g.
    ``{"CLK": "U1.CLK", "Q": "U1.Q"}`` (a plain list of endpoints uses the endpoint as the
    name). Up to 16 (digital) / 8 (analog) signals; the i-th signal goes to probe pin i.
    Returns a ``Probe`` whose ``.signals()`` renames bridge readings back to signal names.
    """
    kind = _kind(kind)
    pins = _normalise(pins)
    if not pins:
        raise ValueError("pins must name at least one endpoint to observe")
    if len(pins) > LIMITS[kind]:
        raise ValueError(f"a {kind} probe supports at most {LIMITS[kind]} pins, got {len(pins)}")
    size = next(n for n in SIL_SIZES if n >= len(pins))
    device = f"CONN-SIL{size}"
    names = [str(i) for i in range(1, len(pins) + 1)]
    props = _properties(kind, names, dll, pipe, properties)
    if device not in circuit.templates:
        circuit.import_device(device, LIBRARY)
    circuit.add(device, ref, device, x, y, properties=props)
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
    path = session.project.parent / f"probe-{os.getpid()}-{int(time.time() * 1000)}.adi"
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
