"""End-to-end: real Proteus 8.x + MCPBRIDGE.DLL. Run with ``pytest -m proteus tests/test_probe_e2e.py``.

Opens a hidden PDS.EXE on disposable copies under tmp_path, always stops the simulation
and closes Proteus again.
"""
from contextlib import contextmanager

import pytest

from proteus_mcp.bridge_client import BridgeClient
from proteus_mcp.engine import Circuit, Session, Simulation
from proteus_mcp.probe import add_probe, attach_probe, find_dll
from proteus_mcp.templates import ensure_template

pytestmark = pytest.mark.proteus


@pytest.fixture(autouse=True)
def _need_proteus(request):
    if "proteus" not in (request.config.getoption("-m") or ""):
        pytest.skip("opens Proteus; run with -m proteus")
    try:
        find_dll()
    except FileNotFoundError:
        pytest.skip("bridge/MCPBRIDGE.DLL is not built (run bridge\\build.bat)")


def divider_and_gate(probe=True):
    """5 V -> 1k/3k divider (probe sees 5, 3.75, 0 V) and an AND gate (probe sees A, B, Q)."""
    circuit = Circuit(template_project=ensure_template())
    circuit.import_device("VSOURCE", "ASIMMDLS")
    circuit.import_device("AND", "ACTIVE")
    circuit.add("VSOURCE", "V1", "5V", 1016000, 2032000)
    circuit.add("RESISTOR", "R1", "1k", 2540000, 2286000)
    circuit.add("RESISTOR", "R2", "3k", 5080000, 2286000)
    circuit.connect("V1.+", "R1.1").connect("R1.2", "R2.1").connect("R2.2", "V1.-")
    circuit.add_terminal("ground", "GND", pin="V1.-")
    circuit.add("AND", "U1", "AND", 2540000, -3048000)
    if probe:
        add_probe(circuit, "J1", 9144000, 3048000, "analog", {"VTOP": "V1.+", "VMID": "R1.2", "GND": "R2.2"})
        add_probe(circuit, "J2", 9144000, -3048000, "digital", {"A": "U1.D0", "B": "U1.D1", "Q": "U1.Q"})
    return circuit


@contextmanager
def live(project):
    session = Session(project)
    sim = Simulation(session)
    try:
        yield session, sim
    finally:
        try:
            if sim.status()["state"] != "stopped":
                sim.stop()
        finally:
            session.close(discard=True)


def exercise_digital(bridge):
    """HOLD/STEP/SET/EVENTS on an AND gate wired to probe pins 1 (A), 2 (B), 3 (Q)."""
    assert bridge.pins() == ["1", "2", "3"]
    t0 = bridge.hold()
    bridge.events()                                   # discard start-up edges
    bridge.set("1", 1)
    bridge.set("2", 1)
    t1 = bridge.step(0.01)
    assert t1 == pytest.approx(t0 + 0.01, abs=1e-9)
    assert bridge.read()["pins"] == dict.fromkeys(["1", "2", "3"], "1")
    bridge.set("2", 0)
    t2 = bridge.step(0.01)
    assert t2 == pytest.approx(t1 + 0.01, abs=1e-9)
    assert bridge.read()["pins"] == {"1": "1", "2": "0", "3": "0"}
    edges = [(e["pin"], e["value"]) for e in bridge.events()]
    assert edges == [("1", "1"), ("2", "1"), ("3", "1"), ("2", "0"), ("3", "0")]
    assert bridge.time() == pytest.approx(t2)
    bridge.run()


def test_both_probes_on_one_pipe_and_simulation_control(tmp_path):
    project = tmp_path / "probes.pdsprj"
    divider_and_gate().save(project, "probe e2e")
    with live(project) as (session, sim):
        assert sim.status()["state"] == "stopped"
        assert sim.start()["state"] == "running"
        with BridgeClient(timeout=15) as bridge:
            assert bridge.ping()
            assert bridge.apins() == ["1", "2", "3"]
            reading = bridge.aread()
            assert reading["volts"]["1"] == pytest.approx(5.0, rel=1e-3)
            assert reading["volts"]["2"] == pytest.approx(3.75, rel=1e-3)      # 5 V * 3k / (1k + 3k)
            assert reading["volts"]["3"] == pytest.approx(0.0, abs=1e-6)
            exercise_digital(bridge)
            assert bridge.aread()["volts"]["2"] == pytest.approx(3.75, rel=1e-3)
        # simulation control on 8.13: pause freezes the model, start resumes, stop ends it
        assert sim.pause()["state"] == "paused"
        assert sim.status()["state"] == "paused"
        assert sim.start()["state"] == "running"
        assert "MCP bridge" in sim.log()
        with pytest.raises(NotImplementedError, match="BridgeClient.step"):
            sim.run_for(0.01)
        assert sim.stop()["state"] == "stopped"


def test_attach_probe_to_existing_part_through_session(tmp_path):
    circuit = Circuit(template_project=ensure_template())
    circuit.import_device("CONN-SIL4", "CONNDVC")
    circuit.import_device("AND", "ACTIVE")
    circuit.add("AND", "U1", "AND", 2540000, -3048000)
    circuit.add("CONN-SIL4", "J1", "CONN-SIL4", 9144000, -3048000)       # plain header, no model
    circuit.connect("U1.D0", "J1.1").connect("U1.D1", "J1.2").connect("U1.Q", "J1.3")
    project = tmp_path / "attach.pdsprj"
    circuit.save(project, "attach e2e")
    with live(project) as (session, sim):
        attach_probe(session, "J1", "digital", pins=["1", "2", "3"])
        session.save()
        sim.start()
        with BridgeClient(timeout=15) as bridge:
            exercise_digital(bridge)
