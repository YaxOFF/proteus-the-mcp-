"""Offline tests for the MCP server: workspace rules, tool behaviour with fakes, real Circuit edits."""
import asyncio
import json
import sys
import types
from pathlib import Path

import pytest

from proteus_mcp import server
from proteus_mcp.workspace import WORKSPACE as W, Workspace

TEMPLATE = Path(__file__).resolve().parents[1] / "work" / "template813.pdsprj"
needs_template = pytest.mark.skipif(not TEMPLATE.exists(), reason="work/template813.pdsprj missing")


class FakeProcess:
    def __init__(self):
        self.terminated = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True


class FakeSession:
    instances = []

    def __init__(self, project):
        self.project, self.pid, self.process = Path(project), 4242, FakeProcess()
        self.closed = self.saved = False
        FakeSession.instances.append(self)

    def netlist(self):
        return {"parts": {"R1": {"value": "10k"}}, "nets": ()}

    def set_properties(self, ref, **props):
        return {"ref": ref, **props}

    def save(self):
        self.saved = True

    def close(self, discard=False):
        self.closed = True


class FakeSimulation:
    def __init__(self, session):
        self.session, self.state = session, "stopped"

    def start(self):
        self.state = "running"

    def stop(self):
        self.state = "stopped"

    def pause(self):
        self.state = "paused"

    def status(self):
        return {"state": self.state}

    def log(self):
        return ["line"]


class FakeBridge:
    def __init__(self, pipe_name="proteus_mcp"):
        self.closed = False

    def connect(self, wait_seconds=0):
        pass

    def close(self):
        self.closed = True

    def read(self):
        return {"t": 0.5, "pins": {"A": "1"}}

    def set(self, pin, value):
        self.last = (pin, value)

    def hold(self):
        return 1.0

    def step(self, seconds):
        return 1.0 + seconds

    def run(self):
        pass

    def events(self):
        return [{"t": 0.1, "pin": "A", "value": "1"}]

    def aread(self):
        return {"t": 0.5, "volts": {"A": 3.3}}


def _reset():
    W.close_live()
    W.circuit, W.path, W.dirty = None, None, False


@pytest.fixture(autouse=True)
def clean_workspace():
    _reset()
    FakeSession.instances.clear()
    yield
    _reset()


@pytest.fixture
def fakes(monkeypatch):
    import proteus_mcp.engine as engine
    monkeypatch.setattr(engine, "Session", FakeSession)
    monkeypatch.setattr(engine, "Simulation", FakeSimulation)
    bridge_mod = types.ModuleType("proteus_mcp.bridge_client")
    bridge_mod.BridgeClient = FakeBridge
    monkeypatch.setitem(sys.modules, "proteus_mcp.bridge_client", bridge_mod)


@pytest.fixture
def saved_project(tmp_path):
    path = tmp_path / "p.pdsprj"
    assert server.project_new(str(path), str(TEMPLATE))["ok"]
    assert server.add_component("RESISTOR", "R1", "10k", 20, 20)["ok"]
    assert server.save_project()["ok"]
    return path


# ---- registration ------------------------------------------------------------
EXPECTED = """proteus_info library_search library_get project_new project_open project_summary list_components
list_nets get_pins save_project add_component import_device connect disconnect add_terminal move_component
rotate_component delete_component set_value set_properties rename_component configure_power open_in_proteus
compile_netlist set_property_live close_proteus sim_start sim_stop sim_pause sim_status sim_log export_graph
add_probe probe_read probe_set probe_hold probe_step probe_run probe_events probe_analog import_kicad""".split()


def test_every_tool_registered_with_docstring():
    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    assert set(EXPECTED) <= set(tools)
    for name in EXPECTED:
        assert (tools[name].description or "").strip(), name
        assert tools[name].description.lstrip().startswith("["), name  # group tag


def test_results_are_json_serialisable():
    json.dumps(server.proteus_info())


def test_environment_and_probe_use_the_same_dll(tmp_path, monkeypatch):
    dll = tmp_path / "MCPBRIDGE.DLL"
    dll.touch()
    monkeypatch.setenv("PROTEUS_MCP_BRIDGE_DLL", str(dll))
    from proteus_mcp.probe import find_dll
    info = server.proteus_info()
    assert info["bridge_dll_found"]
    assert info["bridge_dll"] == find_dll() == str(dll)


# ---- workspace rules ---------------------------------------------------------
def test_edit_without_project_gives_hint():
    result = server.connect("R1.1", "R2.1")
    assert result["ok"] is False and "project_new" in result["hint"]


def test_live_tools_need_session():
    for call in (server.compile_netlist, server.sim_start, server.sim_status, server.probe_read):
        result = call()
        assert result["ok"] is False and "open_in_proteus" in result["hint"]


def test_open_in_proteus_without_project():
    assert "project_new" in server.open_in_proteus()["hint"]


@needs_template
def test_open_in_proteus_requires_saved_and_clean(tmp_path, fakes):
    server.project_new(str(tmp_path / "x.pdsprj"), str(TEMPLATE))
    result = server.open_in_proteus()
    assert not result["ok"] and "save_project" in result["hint"]
    server.save_project()
    assert server.open_in_proteus()["ok"]
    server.add_component("RESISTOR", "R9", "1k", 10, 10)
    assert W.live_dirty  # live session is now stale
    assert not server.open_in_proteus()["ok"]  # unsaved edit blocks reopening


@needs_template
def test_save_does_not_overwrite_by_default(saved_project):
    again = server.save_project()
    assert not again["ok"] and "overwrite" in again["hint"]
    assert server.save_project(overwrite=True)["ok"]


@needs_template
def test_live_flow_with_fakes(saved_project, fakes):
    assert server.open_in_proteus()["ok"]
    first = FakeSession.instances[0]
    assert server.compile_netlist()["netlist"]["parts"]["R1"]["value"] == "10k"
    assert server.set_property_live("R1", {"VALUE": "2k2"})["part"]["VALUE"] == "2k2"
    assert server.sim_start()["status"]["state"] == "running"
    assert server.sim_pause()["status"]["state"] == "paused"
    assert server.sim_log()["log"] == ["line"]
    assert server.probe_read()["reading"]["pins"] == {"A": "1"}
    assert server.probe_set("A", "0")["ok"]
    assert server.probe_hold()["t"] == 1.0 and server.probe_step(0.5)["t"] == 1.5
    assert server.probe_run()["ok"] and server.probe_events()["events"][0]["pin"] == "A"
    assert server.probe_analog()["reading"]["volts"]["A"] == 3.3
    bridge = W.bridge
    assert server.sim_stop()["status"]["state"] == "stopped"
    assert bridge.closed and W.bridge is None
    assert server.sim_start()["ok"]
    assert server.probe_read()["ok"] and W.bridge is not bridge
    bridge = W.bridge
    # a second session closes the first one cleanly
    assert server.open_in_proteus()["ok"]
    assert first.closed and bridge.closed and len(FakeSession.instances) == 2
    result = server.close_proteus(save=True)
    assert result["ok"] and FakeSession.instances[1].saved and FakeSession.instances[1].closed
    assert W.session is None and not server.sim_status()["ok"]


def test_import_exposes_placeholder_choice(tmp_path, monkeypatch):
    from proteus_mcp import kicad_import
    source = tmp_path / "net.xml"
    source.touch()
    captured = {}
    def convert(*args, **kwargs):
        captured.update(kwargs)
        return {"simulation": {"verified": False}}
    monkeypatch.setattr(kicad_import, "kicad_to_proteus", convert)
    monkeypatch.setattr(server, "_default_template", lambda: TEMPLATE)
    result = server.import_kicad(str(source), str(tmp_path / "out.pdsprj"), placeholders=False)
    assert result["ok"] and captured["placeholders"] is False


def test_close_stops_before_save_and_discards_only_when_requested():
    order = []
    session = FakeSession("x.pdsprj")
    sim = FakeSimulation(session)
    session.save = lambda: order.append("save")
    session.close = lambda discard=False: order.append(("close", discard))
    sim.stop = lambda: order.append("stop")
    ws = Workspace()
    ws.attach_session(session, sim, "x.pdsprj")
    assert not ws.close_live(save=True)
    assert order == ["stop", "save", ("close", False)]
    order.clear()
    ws.attach_session(session, sim, "x.pdsprj")
    assert not ws.close_live()
    assert order == ["stop", ("close", True)]


@needs_template
def test_project_open_closes_live_session(saved_project, fakes):
    server.open_in_proteus()
    session = FakeSession.instances[0]
    assert server.project_open(str(saved_project))["ok"]
    assert session.closed and W.session is None


def test_close_live_stops_simulation_and_closes():
    ws = Workspace()
    session = FakeSession("x.pdsprj")
    sim = FakeSimulation(session)
    sim.start()
    ws.attach_session(session, sim, "x.pdsprj")
    ws.close_live()
    assert session.closed and sim.state == "stopped" and ws.session is None


def test_close_failure_terminates_process():
    ws = Workspace()
    session = FakeSession("x.pdsprj")

    def stuck(discard=False):
        raise RuntimeError("stuck")
    session.close = stuck
    ws.attach_session(session, FakeSimulation(session), "x.pdsprj")
    errors = ws.close_live()
    assert errors and session.process.terminated


def test_missing_module_is_reported(monkeypatch):
    monkeypatch.setitem(sys.modules, "proteus_mcp.kicad_import", None)  # forces ImportError
    result = server.import_kicad("nope.kicad_sch", "out.pdsprj")
    assert result["ok"] is False and result["error"]


# ---- real offline edits ------------------------------------------------------
@needs_template
def test_offline_edit_roundtrip(tmp_path):
    path = tmp_path / "rc.pdsprj"
    assert server.project_new(str(path), str(TEMPLATE))["ok"]
    assert server.add_component("RESISTOR", "R1", "10k", 20, 20)["ok"]
    assert server.add_component("CAPACITOR", "C1", "100n", 40, 20)["ok"]
    assert server.connect("R1.2", "C1.1")["ok"]
    assert server.add_terminal("ground", "GND", pin="C1.2")["terminal_id"]
    assert server.save_project()["ok"] and path.exists()

    assert server.project_open(str(path))["ok"]
    comps = {c["ref"]: c for c in server.list_components()["components"]}
    assert comps["R1"]["value"] == "10k" and comps["R1"]["x_mm"] == 20.0
    assert ["C1.1", "R1.2"] in server.list_nets()["nets"]
    assert server.get_pins("R1")["pins"][1]["x_mm"] > 20
    assert any(t["kind"] == "ground" for t in server.project_summary()["terminals"])

    assert server.set_value("R1", "4k7")["ok"]
    assert server.rename_component("C1", "C2")["ok"]
    assert server.move_component("R1", 30, 30)["ok"]
    assert server.rotate_component("R1", 90)["ok"]
    assert server.disconnect("R1.2")["ok"]
    assert server.delete_component("C2")["ok"]
    assert [c["ref"] for c in server.list_components()["components"]] == ["R1"]
    assert W.dirty


@needs_template
def test_bad_pin_returns_hint(tmp_path):
    server.project_new(str(tmp_path / "a.pdsprj"), str(TEMPLATE))
    server.add_component("RESISTOR", "R1", "1k", 10, 10)
    result = server.connect("R1.nope", "R1.1")
    assert result["ok"] is False and "get_pins" in result["hint"]
