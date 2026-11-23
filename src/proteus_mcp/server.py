"""MCP server exposing Labcenter Proteus 8 editing, live control and simulation as tools.

Units at the tool API are millimetres; the engine uses integers where 100000 = 1 mm.
Heavy or optional modules are imported lazily inside tools so the server always starts.
"""
import functools
from pathlib import Path

try:
    from mcp.server.fastmcp import FastMCP  # mcp 1.x
except ImportError:  # mcp 2.x renamed FastMCP to MCPServer
    from mcp.server.mcpserver import MCPServer as FastMCP

from .workspace import WORKSPACE as W, WorkspaceError

mcp = FastMCP("proteus")
UNIT = 100000  # engine units per millimetre

HINTS = {
    "unknown or ambiguous pin": "use get_pins(ref) to see valid pin names/numbers; endpoints are 'REF.PIN'",
    "unknown pin": "use get_pins(ref) to see valid pin names/numbers; endpoints are 'REF.PIN'",
    "not in the imported template": "call import_device(name) first (find names with library_search)",
    "destination exists": "pass overwrite=true to replace the file, or choose a new path",
    "duplicate": "choose a reference that is not used yet (list_components)",
    "invalid reference": "references look like R1, C12, U3 (capital letters then a digit)",
    "unknown component": "check the reference with list_components",
    "dll": "probe DLL not found: build bridge/build.bat or download MCPBRIDGE.DLL from Releases",
}


def _mm(value):
    return int(round(float(value) * UNIT))


def _to_mm(units):
    return round(units / UNIT, 4)


def _jsonable(value):
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _hint_for(exc):
    if isinstance(exc, WorkspaceError):
        return exc.hint
    text = str(exc).lower()
    for key, hint in HINTS.items():
        if key in text:
            return hint
    if isinstance(exc, ModuleNotFoundError):
        return f"module '{exc.name}' is missing from this install of proteus-mcp"
    if isinstance(exc, FileNotFoundError):
        return "check that the path exists; Proteus must be installed for live tools (see proteus_info)"
    if type(exc).__name__ == "BridgeError":
        return ("is a probe placed (add_probe), the project saved and reopened with open_in_proteus, "
                "and sim_start running? probe DLL not found: build bridge/build.bat or download "
                "MCPBRIDGE.DLL from Releases")
    return ""


def tool(func):
    """Register func as an MCP tool; exceptions become {'ok': False, 'error', 'hint'}."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            result = func(*args, **kwargs)
            result = {"ok": True, **result} if isinstance(result, dict) else {"ok": True, "result": result}
            return _jsonable(result)
        except Exception as exc:  # report to the AI instead of crashing the server
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "hint": _hint_for(exc)}
    mcp.tool()(wrapper)
    return wrapper


# ---------------------------------------------------------------- info
def _pds_version(exe):
    import ctypes
    from ctypes import wintypes
    version = ctypes.windll.version
    size = version.GetFileVersionInfoSizeW(str(exe), None)
    if not size:
        return None
    buf = ctypes.create_string_buffer(size)
    version.GetFileVersionInfoW(str(exe), 0, size, buf)
    ptr, length = ctypes.c_void_p(), wintypes.UINT()
    if not version.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(length)):
        return None

    class Fixed(ctypes.Structure):
        _fields_ = [(n, wintypes.DWORD) for n in (
            "sig", "struc", "ms", "ls", "pms", "pls", "mask", "flags", "os", "type", "sub", "dms", "dls")]
    info = ctypes.cast(ptr, ctypes.POINTER(Fixed)).contents
    return f"{info.ms >> 16}.{info.ms & 0xFFFF}.{info.ls >> 16}.{info.ls & 0xFFFF}"


def _bridge_dll():
    from .probe import find_dll
    try:
        return Path(find_dll())
    except FileNotFoundError:
        return None


def _kicad_cli():
    from .kicad_import import find_kicad_cli
    try:
        return str(find_kicad_cli())
    except FileNotFoundError:
        return None


@tool
def proteus_info():
    """[info] Report the environment: Proteus install paths, PDS.EXE version, whether the probe
    bridge DLL (MCPBRIDGE.DLL) and kicad-cli were found, and the current workspace state.
    Call this first when something live (open_in_proteus, probes) fails."""
    from .engine import proteus_paths as p
    exe = Path(p.EXECUTABLE)
    dll, kicad = _bridge_dll(), _kicad_cli()
    return {
        "proteus_home": str(p.home()), "pds_exe": str(exe), "pds_exe_found": exe.exists(),
        "pds_version": _pds_version(exe) if exe.exists() else None,
        "library_dir": str(p.LIBRARY), "library_found": Path(p.LIBRARY).is_dir(),
        "bridge_dll": str(dll) if dll else None, "bridge_dll_found": dll is not None,
        "kicad_cli": kicad, "kicad_cli_found": kicad is not None,
        "workspace": _state(),
    }


# ---------------------------------------------------------------- library
@tool
def library_search(query: str = "", limit: int = 30):
    """[library] Search the installed Proteus device library by name (space separated words, all
    must match). Returns [{name, library}]. Use a returned name with import_device or add_component."""
    from .engine import Library
    return {"devices": [{"name": e["name"], "library": e["library"]}
                        for e in Library().search(query, int(limit))]}


@tool
def library_get(name: str):
    """[library] Describe one library device: pins, properties and symbol details."""
    from .engine import Library
    return {"device": Library().get(name)}


# ---------------------------------------------------------------- project
def _state():
    c = W.circuit
    return {
        "open": c is not None, "path": str(W.path) if W.path else None, "dirty": W.dirty,
        "components": len(c.components()) if c is not None else 0,
        "proteus_open": W.session is not None, "live_path": str(W.live_path) if W.live_path else None,
        "live_stale": W.live_dirty, "bridge_connected": W.bridge is not None,
    }


def _default_template():
    from .templates import ensure_template
    return Path(ensure_template())


@tool
def project_new(path: str, template: str = None):
    """[project] Start a new empty in-memory project. `path` is where save_project will write it
    by default (.pdsprj); nothing is written yet. `template` is an optional template .pdsprj
    (default: an automatically provided Proteus 8 template)."""
    from .engine import Circuit
    if not str(path).lower().endswith(".pdsprj"):
        raise ValueError("path must end with .pdsprj")
    tpl = Path(template) if template else _default_template()
    W.set_circuit(Circuit(template_project=tpl), path=path, dirty=True)
    return {"path": str(Path(path)), "template": str(tpl), "state": _state()}


@tool
def project_open(path: str):
    """[project] Open an existing .pdsprj for offline editing (replaces the current project and closes
    any live Proteus session). Only sheets whose objects are fully understood can be opened."""
    from .engine import Circuit
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    W.set_circuit(Circuit.open(p), path=p, dirty=False)
    return {"state": _state()}


@tool
def project_summary():
    """[project] Summarise the open project: path, unsaved-changes flag, component/net counts, power
    rails, terminals and live (Proteus) state."""
    c = W.need_circuit()
    return {"state": _state(), "nets": len(c.nets()), "connections": len(c.connections()),
            "terminals": c.terminals(), "power_rails": c.power_rails(),
            "devices_available": sorted(c.templates)}


@tool
def list_components():
    """[project] List components: ref, device, value, position in mm and properties."""
    out = []
    for item in W.need_circuit().components():
        pos = item.pop("position")
        item["x_mm"], item["y_mm"] = _to_mm(pos["x"]), _to_mm(pos["y"])
        out.append(item)
    return {"components": out}


@tool
def list_nets():
    """[project] List nets as lists of 'REF.PIN' strings (terminal/label names are merged in)."""
    nets = [[f"{ref}.{pin}" for ref, pin in net] for net in W.need_circuit().nets()]
    return {"nets": nets}


@tool
def get_pins(ref: str):
    """[project] List a component's pins: name, number, direction and absolute position in mm.
    Use `REF.name` or `REF.number` as connect() endpoints."""
    pins = []
    for pin in W.need_circuit().pins(ref):
        x, y = pin.pop("position")
        pin["x_mm"], pin["y_mm"] = _to_mm(x), _to_mm(y)
        pins.append(pin)
    return {"ref": ref, "pins": pins}


@tool
def save_project(path: str = None, overwrite: bool = False):
    """[project] Write the in-memory project to disk. Without `path`, writes to the project's path;
    an existing file is only replaced when overwrite=true. Live tools need the project saved."""
    c = W.need_circuit()
    target = Path(path) if path else W.path
    if target is None:
        raise WorkspaceError("no path given", "pass path=...\\name.pdsprj")
    c.save(target, overwrite=bool(overwrite))
    W.mark_saved(target)
    return {"saved": str(target), "state": _state()}


# ---------------------------------------------------------------- edit
def _edit(action):
    circuit = W.need_circuit()
    result = action(circuit)
    W.touch()
    return result


@tool
def add_component(device: str, ref: str, value: str, x_mm: float, y_mm: float, rotation: int = 0):
    """[edit] Place a component. `device` is a library device name (e.g. RESISTOR, CAPACITOR); it is
    imported automatically if missing. `ref` like R1, `value` like 10k, position in millimetres,
    rotation in degrees (multiple of 90)."""
    def act(c):
        if device not in c.templates:
            c.import_device(device)
        c.add(device, ref, value, _mm(x_mm), _mm(y_mm), rotation=int(rotation))
    _edit(act)
    return {"ref": ref, "device": device}


@tool
def import_device(name: str):
    """[edit] Import a library device into the project so it can be placed (add_component does this
    automatically for single-unit devices)."""
    _edit(lambda c: c.import_device(name))
    return {"imported": name}


@tool
def connect(a: str, b: str):
    """[edit] Wire two pins. Endpoints are 'REF.PIN' using a pin name or an unambiguous number,
    e.g. connect('R1.2', 'C1.1')."""
    _edit(lambda c: c.connect(a, b))
    return {"connected": [a, b]}


@tool
def disconnect(a: str, b: str = None):
    """[edit] Remove the wire between pins a and b, or, if b is omitted, detach pin a from everything."""
    _edit(lambda c: c.disconnect(a, b))
    return {"disconnected": [a, b]}


@tool
def add_terminal(kind: str, name: str, pin: str = None, x_mm: float = None, y_mm: float = None):
    """[edit] Add a terminal. kind is one of label, input, output, bidir, power, ground. `name` is
    the net name (e.g. GND, VCC, SIG). `pin` ('REF.PIN') attaches it to a pin; x_mm/y_mm optionally
    fix its position. Returns the terminal id."""
    position = (_mm(x_mm), _mm(y_mm)) if x_mm is not None and y_mm is not None else None
    terminal = _edit(lambda c: c.add_terminal(kind, name, pin, position=position))
    return {"terminal_id": terminal}


@tool
def move_component(ref: str, x_mm: float, y_mm: float):
    """[edit] Move a component to an absolute position in millimetres (attached wires are re-routed)."""
    _edit(lambda c: c.move(ref, _mm(x_mm), _mm(y_mm)))
    return {"ref": ref}


@tool
def rotate_component(ref: str, degrees: int = 90):
    """[edit] Rotate a component by `degrees` (multiple of 90) relative to its current rotation."""
    _edit(lambda c: c.rotate(ref, int(degrees)))
    return {"ref": ref}


@tool
def delete_component(ref: str):
    """[edit] Delete a component; connectivity between its former neighbours is preserved."""
    _edit(lambda c: c.delete_component(ref))
    return {"deleted": ref}


@tool
def set_value(ref: str, value: str):
    """[edit] Change a component's value text (e.g. '4k7', '100n'). Offline; see set_property_live
    to change a running project."""
    _edit(lambda c: c.update(ref, value=value))
    return {"ref": ref, "value": value}


@tool
def set_properties(ref: str, properties: dict):
    """[edit] Merge device properties (e.g. {"TOL": "1%"}) into a component. Offline."""
    _edit(lambda c: c.set_properties(ref, **dict(properties)))
    return {"ref": ref, "properties": properties}


@tool
def rename_component(old: str, new: str):
    """[edit] Change a component's reference designator, updating all wiring."""
    _edit(lambda c: c.rename(old, new))
    return {"old": old, "new": new}


@tool
def configure_power(rails: dict, mapping: dict = None):
    """[edit] Define power rails and bind net names to them. rails: {"VCC": 5.0, "VDD": 3.3} (volts);
    mapping: {"VCC_IN": "VCC"} net name -> rail name."""
    _edit(lambda c: c.configure_power(dict(rails), dict(mapping or {})))
    return {"rails": rails, "mapping": mapping or {}}


# ---------------------------------------------------------------- live
@tool
def open_in_proteus():
    """[live] Launch a hidden Proteus on the saved project file (the project must be saved and have no
    unsaved edits). Replaces any previous live session. Required before compile_netlist,
    set_property_live, sim_* and probe_*."""
    from .engine import Session, Simulation
    path = W.need_saved()
    W.close_live()
    session = Session(path)
    try:
        simulation = Simulation(session)
    except Exception:
        W.session = session
        W.close_live()
        raise
    W.attach_session(session, simulation, path)
    return {"pid": session.pid, "project": str(path)}


@tool
def compile_netlist():
    """[live] Ask Proteus to compile the logical netlist and return its parts and nets (ground truth
    for connectivity as Proteus sees it). Needs open_in_proteus."""
    return {"netlist": W.need_session().netlist()}


@tool
def set_property_live(ref: str, properties: dict):
    """[live] Change properties (e.g. {"VALUE": "2k2"}) of a component inside the running Proteus
    project and verify by read-back. Edits Proteus's copy; call close_proteus(save=true) to keep them."""
    session = W.need_session()
    return {"part": session.set_properties(ref, **dict(properties))}


@tool
def close_proteus(save: bool = False):
    """[live] Stop the simulation and close Proteus (and the probe bridge). save=true saves the live
    project first. Always call this when finished so no PDS.EXE stays running."""
    errors = W.close_live(save=bool(save))
    return {"closed": True, "warnings": errors}


# ---------------------------------------------------------------- simulation
@tool
def sim_start():
    """[simulation] Start (run) the simulation in the live Proteus session."""
    sim = W.need_simulation()
    sim.start()
    return {"status": sim.status()}


@tool
def sim_stop():
    """[simulation] Stop the simulation."""
    sim = W.need_simulation()
    W._close_bridge()
    sim.stop()
    return {"status": sim.status()}


@tool
def sim_pause():
    """[simulation] Pause the running simulation."""
    sim = W.need_simulation()
    sim.pause()
    return {"status": sim.status()}


@tool
def sim_status():
    """[simulation] Current simulation state (running/paused/stopped) and simulated time."""
    return {"status": W.need_simulation().status()}


@tool
def sim_log():
    """[simulation] Return the simulation log (messages, warnings, errors)."""
    return {"log": W.need_simulation().log()}


@tool
def export_graph(graph_name: str, csv_path: str):
    """[simulation] Run a graph-based simulation for the named graph and export its traces to a new
    .csv file. Returns the parsed trace data. Needs open_in_proteus."""
    from .engine import export_graph as engine_export, graphs
    session = W.need_session()
    try:
        return {"graph": engine_export(session, graph_name, csv_path)}
    except Exception as exc:
        names = [g["name"] for g in graphs(session)]
        raise RuntimeError(f"{exc} (graphs in project: {names})") from exc


# ---------------------------------------------------------------- probe
@tool
def add_probe(ref: str, x_mm: float, y_mm: float, kind: str = "digital", pins: list = None):
    """[probe] Place a live-simulation probe part (wired to MCPBRIDGE.DLL) in the offline project.
    kind 'digital' or 'analog'; `pins` lists 'REF.PIN' endpoints to observe. Then save_project,
    open_in_proteus and sim_start before using probe_read etc."""
    from .probe import add_probe as place
    kind = kind.lower()
    if kind not in ("digital", "analog"):
        raise ValueError("kind must be 'digital' or 'analog'")
    _edit(lambda c: place(c, ref, _mm(x_mm), _mm(y_mm), kind=kind, pins=pins))
    return {"ref": ref, "kind": kind, "pins": pins}


_EVENT_LOG = []  # every digital edge read from the probe (EVENTS drains the bridge)


def _drain_events():
    if W.session is not None:
        try:
            _EVENT_LOG.extend(_bridge().events())
        except Exception:  # bridge gone (simulation stopped): keep what was recorded
            pass
    return list(_EVENT_LOG)


class _NamedBridge:
    """Bridge view that speaks probe signal names (e.g. LED) instead of probe pin numbers."""

    def __init__(self, client, maps):
        self._client, self._maps = client, maps
        self._to_pin = {name: pin for pin, name in maps["digital"].items()}

    def __getattr__(self, name):
        return getattr(self._client, name)

    def _named(self, values, kind="digital"):
        # Keep the probe pin numbers too, so callers reading "1", "2", ... keep working.
        named = {self._maps[kind][pin]: value for pin, value in values.items() if pin in self._maps[kind]}
        return {**values, **named}

    def names(self, values, kind="digital"):
        """Only the named signals: {name: value}."""
        return {self._maps[kind][pin]: value for pin, value in values.items() if pin in self._maps[kind]}

    def read(self):
        """Digital levels keyed by signal name and by probe pin number."""
        reading = self._client.read()
        return dict(reading, pins=self._named(reading["pins"]))

    def aread(self):
        reading = self._client.aread()
        return dict(reading, volts=self._named(reading["volts"], "analog"))

    def set(self, pin, value):
        return self._client.set(self._to_pin.get(pin, pin), value)

    def events(self):
        return [dict(e, pin=self._maps["digital"].get(e["pin"], e["pin"])) for e in self._client.events()]


def _bridge():
    """Connect lazily to the probe bridge of the live session (signal names, not pin numbers)."""
    from .probe import signal_maps
    maps = signal_maps(W.circuit) if W.circuit is not None else {"digital": {}, "analog": {}}
    return _NamedBridge(_raw_bridge(), maps)


def _raw_bridge():
    if W.session is None:
        raise WorkspaceError("Proteus is not open on a project", "call open_in_proteus, then sim_start")
    if W.bridge is not None and not getattr(W.bridge, "connected", True):
        W._close_bridge()
    if W.bridge is None:
        from .bridge_client import BridgeClient
        client = BridgeClient()
        client.connect(5)
        W.attach_bridge(client)
    return W.bridge


@tool
def probe_read():
    """[probe] Read the current digital probe pin levels: {reading: {t, pins: {pin number: '0'|'1'|'Z'|'X'}},
    signals: {signal name: level}}.
    Needs a running simulation with a digital probe."""
    bridge = _bridge()
    reading = bridge._client.read()
    return {"reading": reading, "signals": bridge.names(reading["pins"])}


@tool
def probe_set(pin: str, value: str):
    """[probe] Drive a probe pin to '0', '1' or 'Z' (release)."""
    _bridge().set(pin, value)
    return {"pin": pin, "value": value}


@tool
def probe_hold():
    """[probe] Pause the simulation through the bridge and return the simulated time (seconds).
    Use probe_step to advance deterministically."""
    return {"t": _bridge().hold()}


@tool
def probe_step(seconds: float):
    """[probe] While held, advance the simulation by `seconds` and return the new simulated time."""
    return {"t": _bridge().step(float(seconds))}


@tool
def probe_run():
    """[probe] Release a hold and let the simulation run freely."""
    _bridge().run()
    return {"running": True}


@tool
def probe_events():
    """[probe] Return recorded pin transitions: [{t, pin, value}]."""
    events = _bridge().events()
    _EVENT_LOG.extend(events)
    return {"events": events}


@tool
def probe_analog():
    """[probe] Read analog probe voltages: {t, volts: {name: volts}}."""
    bridge = _bridge()
    reading = bridge._client.aread()
    return {"reading": reading, "signals": bridge.names(reading["volts"], "analog")}


# ---------------------------------------------------------------- import
@tool
def import_kicad(kicad_sch: str, out_pdsprj: str, placeholders: bool = True):
    """[import] Convert a KiCad schematic (.kicad_sch or exported netlist XML) to a Proteus .pdsprj.
    Writes the new file and returns the conversion report. Connectivity is verified, simulation
    equivalence is not. Unknown ICs become connectivity-only headers by default; set
    placeholders=false to skip them. Review skipped parts, substitutions and simulation warnings."""
    from .kicad_import import kicad_to_proteus
    if not Path(kicad_sch).exists():
        raise FileNotFoundError(kicad_sch)
    report_path = str(Path(out_pdsprj).with_suffix(".report.json"))
    report = kicad_to_proteus(kicad_sch, out_pdsprj, _default_template(), report_path=report_path,
                             placeholders=bool(placeholders))
    sheet = report.get("sheet", {})
    if sheet.get("size") and sheet["size"] != "A4":
        # The sheet size is a Proteus-side setting: apply it in a hidden Proteus and save.
        from .engine import Session
        from .exports import set_sheet_size
        session = Session(out_pdsprj)
        try:
            set_sheet_size(session, sheet["size"])
            session.save()
        finally:
            session.close(discard=True)
    summary = {k: report.get(k) for k in ("counts", "sheet", "skipped", "warnings", "simulation",
                                          "verification", "limitations")}
    return {"out": out_pdsprj, "report_path": report_path, "report": summary,
            "note": "Full per-part/per-net report is in report_path."}


# ---------------------------------------------------------------- review
@tool
def export_image(path: str, format: str = "png", all_sheets: bool = False, dpi: int = 100):
    """[live] Export the schematic as an image from Proteus itself: png (for you to look at),
    svg or pdf (all_sheets=true exports every sheet to one PDF). dpi (png only): 100, 200 or 300;
    use 200+ for small sheets or to zoom in. The file must not exist yet.
    Needs open_in_proteus. Returns the written path; read a PNG to see the circuit."""
    from .exports import export_graphics
    if int(dpi) not in (100, 200, 300):
        raise ValueError("dpi must be 100, 200 or 300")
    out = export_graphics(W.need_session(), path, format, all_sheets=bool(all_sheets), fit=False, dpi=dpi)
    return {"path": str(out), "bytes": out.stat().st_size}


@tool
def run_erc(report_path: str = None):
    """[live] Run Proteus's Electrical Rules Check. Returns {ok, errors, warnings} and saves the full
    report (default: next to the project as <name>.erc.txt, replaced if it exists). Needs open_in_proteus."""
    from .exports import erc
    session = W.need_session()
    target = Path(report_path) if report_path else session.project.with_suffix(".erc.txt")
    if not report_path and target.exists():
        target.unlink()
    result = erc(session, target)
    return {"report_path": str(target), "ok": result["ok"], "errors": result["errors"],
            "warnings": result["warnings"]}


@tool
def set_sheet_size(size: str):
    """[live] Set the current sheet size (A4, A3, A2, A1 or A0) in the live Proteus project.
    Call close_proteus(save=true) to keep it."""
    from .exports import set_sheet_size as apply
    return {"size": apply(W.need_session(), size)}


# ---------------------------------------------------------------- waveforms
@tool
def probe_clear_events():
    """[probe] Forget recorded probe edges (start a fresh measurement window)."""
    _drain_events()
    _EVENT_LOG.clear()
    return {"cleared": True}


@tool
def export_vcd(path: str):
    """[probe] Write every probe edge recorded so far to a VCD file (open it in GTKWave).
    Run the simulation (e.g. probe_step) first; probe_clear_events starts a new window."""
    from .waveform import write_vcd
    events = _drain_events()
    if not events:
        raise WorkspaceError("No probe events recorded", "start the simulation and probe_step first")
    write_vcd(events, path)
    return {"path": str(Path(path).resolve()), "events": len(events),
            "pins": sorted({e["pin"] for e in events})}


@tool
def measure_signal(pin: str):
    """[probe] Frequency, period, duty cycle and high/low times of a probe pin, from the edges
    recorded so far (use probe_clear_events, then probe_step, for a clean window)."""
    from .waveform import measure
    return {"pin": pin, "measurement": measure(_drain_events(), pin)}


@tool
def measure_delay(pin_a: str, pin_b: str, edge_a: str = "any", edge_b: str = "any"):
    """[probe] Seconds from the first edge_a ('rise'|'fall'|'any') on pin_a to the first edge_b on
    pin_b after it, from the recorded probe edges."""
    from .waveform import delay
    return {"delay_s": delay(_drain_events(), pin_a, edge_a, pin_b, edge_b)}


# ---------------------------------------------------------------- testbench
@tool
def run_testbench(bench, vcd_path: str = None):
    """[probe] Run a pass/fail testbench against the running simulation. bench is a dict or a .json
    path: {"name", "steps": [{"set": {"PIN": "1"}}, {"run": 0.001}, {"expect": {"PIN": "0"}},
    {"expect_analog": {"PIN": {"min": 4.5, "max": 5.5}}}, {"expect_frequency": {"pin": "P",
    "min_hz": 900, "max_hz": 1100}}, {"expect_duty": {...}}, {"wait_for": {"pin": "P", "value": "1",
    "timeout": 0.01, "poll": 0.0001}}]}. Failed expectations are reported, not raised.
    Needs sim_start and a probe on the named pins."""
    import json as _json
    from .testbench import run_testbench as run
    if isinstance(bench, str):
        bench = _json.loads(Path(bench).read_text(encoding="utf-8"))
    _drain_events()
    return run(_bridge(), bench, vcd_path=vcd_path)


# ---------------------------------------------------------------- firmware
@tool
def firmware_build(sources: list, mcu: str, f_cpu: int, out_dir: str, include_dirs: list = None,
                   defines: list = None):
    """[firmware] Compile AVR C sources with avr-gcc (the copy bundled with Proteus is found
    automatically) to .elf/.hex. mcu is a Proteus device or gcc name (ATMEGA328P / atmega328p);
    f_cpu in Hz. Returns hex path, flash/RAM bytes and warnings; compiler errors are returned in
    the error hint."""
    from .firmware import build
    return build(list(sources), mcu, int(f_cpu), out_dir, include_dirs=tuple(include_dirs or ()),
                 defines=tuple(defines or ()))


@tool
def firmware_load(ref: str, hex_path: str, clock_hz: int = None):
    """[firmware] Point microcontroller `ref` at a .hex file (PROGRAM) and optionally set its clock.
    With Proteus open (simulation stopped) it changes the live project; otherwise the offline
    circuit (then save_project). Typical loop: firmware_build -> firmware_load -> sim_start ->
    probe/testbench. For AVR parts clock_hz also clears the CLKDIV8 fuse so the
    simulated CPU really runs at clock_hz."""
    from .firmware import load_program
    device = next((p["device"] for p in W.need_circuit().components() if p["ref"] == ref), "")
    # Proteus AVR models default to CLKDIV8=0 (fuse programmed: clock / 8). An explicit clock means
    # the effective CPU clock, so the divider is switched off.
    extra = {"CLKDIV8": "1"} if clock_hz and device.upper().startswith(("ATMEGA", "ATTINY", "AT90")) else {}
    target = W.session if W.session is not None else None

    def apply(obj):
        result = load_program(obj, ref, hex_path, clock_hz)
        if extra:
            obj.set_properties(ref, **extra)
            result["properties"].update(extra)
        return result
    if target is not None:
        return apply(W.need_session())
    return _edit(apply)


# ---------------------------------------------------------------- diagnostics
@tool
def doctor():
    """[setup] Check this machine: Proteus install and version, library, template, probe DLL
    (32-bit), KiCad CLI, AVR toolchain, leftover PDS.EXE processes, MCP SDK. Each check has a fix."""
    from .doctor import diagnose
    return diagnose()


def main():
    mcp.run()


if __name__ == "__main__":
    main()
