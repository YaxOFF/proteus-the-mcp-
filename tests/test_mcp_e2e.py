"""Exercise the actual stdio protocol, including a real Proteus run when selected."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import sys

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "work" / "template813.pdsprj"


@asynccontextmanager
async def connected(tmp_path):
    # Deliberately launch from outside the checkout: imports must work as installed.
    env = {"LOCALAPPDATA": str(tmp_path / "appdata")}
    for key in ("PROTEUS_HOME", "PROTEUS_DATA", "PROTEUS_MCP_BRIDGE_DLL", "PYTHONPATH"):
        if key in os.environ:
            env[key] = os.environ[key]
    params = StdioServerParameters(command=sys.executable,
                                   args=["-m", "proteus_mcp.server"], cwd=tmp_path, env=env)
    with anyio.fail_after(150):
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                yield session


async def call(session, tool_name, /, **arguments):
    print(f"MCP -> {tool_name}", flush=True)
    result = await session.call_tool(tool_name, arguments)
    print(f"MCP <- {tool_name}", flush=True)
    assert not getattr(result, "is_error", getattr(result, "isError", False)), result
    text = "".join(block.text for block in result.content if block.type == "text")
    data = json.loads(text)
    assert data["ok"], (tool_name, data)
    return data


@pytest.mark.skipif(not TEMPLATE.is_file(), reason="local Proteus template missing")
def test_stdio_edit_roundtrip(tmp_path):
    async def run():
        async with connected(tmp_path) as session:
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert {"project_new", "sim_start", "probe_analog", "import_kicad"} <= names
            path = tmp_path / "stdio.pdsprj"
            await call(session, "project_new", path=str(path), template=str(TEMPLATE))
            await call(session, "add_component", device="RESISTOR", ref="R1", value="1k", x_mm=20, y_mm=20)
            await call(session, "add_component", device="CAPACITOR", ref="C1", value="100n", x_mm=40, y_mm=20)
            await call(session, "connect", a="R1.2", b="C1.1")
            await call(session, "save_project")
            await call(session, "project_open", path=str(path))
            nets = await call(session, "list_nets")
            assert ["C1.1", "R1.2"] in nets["nets"]
            assert path.is_file()
    asyncio.run(run())


@pytest.mark.proteus
def test_stdio_real_simulation_and_restart(tmp_path, request):
    """Build, save, launch, measure, drive, stop, restart and close through MCP."""
    if "proteus" not in (request.config.getoption("-m") or ""):
        pytest.skip("opens Proteus; run with -m proteus")
    from proteus_mcp.probe import find_dll
    try:
        find_dll()
    except FileNotFoundError:
        pytest.skip("build bridge/MCPBRIDGE.DLL first")

    async def run():
        async with connected(tmp_path) as session:
            info = await call(session, "proteus_info")
            assert info["pds_exe_found"] and info["bridge_dll_found"]
            await call(session, "project_new", path=str(tmp_path / "live.pdsprj"))
            for device in ("VSOURCE", "AND"):
                await call(session, "import_device", name=device)
            for device, ref, value, x, y in (
                ("VSOURCE", "V1", "5V", 10.16, 20.32),
                ("RESISTOR", "R1", "1k", 25.4, 22.86),
                ("RESISTOR", "R2", "3k", 50.8, 22.86),
                ("AND", "U1", "AND", 25.4, -30.48),
            ):
                await call(session, "add_component", device=device, ref=ref, value=value, x_mm=x, y_mm=y)
            for a, b in (("V1.+", "R1.1"), ("R1.2", "R2.1"), ("R2.2", "V1.-")):
                await call(session, "connect", a=a, b=b)
            await call(session, "add_terminal", kind="ground", name="GND", pin="V1.-")
            await call(session, "add_probe", ref="J1", x_mm=91.44, y_mm=30.48,
                       kind="analog", pins=["V1.+", "R1.2", "R2.2"])
            await call(session, "add_probe", ref="J2", x_mm=91.44, y_mm=-30.48,
                       pins=["U1.D0", "U1.D1", "U1.Q"])
            await call(session, "save_project")
            try:
                await call(session, "open_in_proteus")
                nets = await call(session, "compile_netlist")
                assert "R1" in nets["netlist"]["parts"]
                await call(session, "sim_start")
                reading = await call(session, "probe_analog")
                assert reading["reading"]["volts"]["2"] == pytest.approx(3.75, rel=1e-3)
                t0 = (await call(session, "probe_hold"))["t"]
                await call(session, "probe_set", pin="1", value="1")
                await call(session, "probe_set", pin="2", value="1")
                t1 = (await call(session, "probe_step", seconds=0.01))["t"]
                assert t1 == pytest.approx(t0 + 0.01, abs=1e-9)
                pins = (await call(session, "probe_read"))["reading"]["pins"]
                assert pins == {"1": "1", "2": "1", "3": "1"}
                await call(session, "sim_stop")
                await call(session, "sim_start")
                reading = await call(session, "probe_analog")
                assert reading["reading"]["volts"]["2"] == pytest.approx(3.75, rel=1e-3)
                # Save while running exercises stop-before-save and graceful exit.
                closed = await call(session, "close_proteus", save=True)
                assert not closed["warnings"]
            finally:
                closed = await call(session, "close_proteus")
                assert not closed["warnings"]
    asyncio.run(run())
