"""Multi-element packages (U1:A..D): shared pinout parsing and the saved CDB records."""
import struct
from pathlib import Path

import pytest

from proteus_mcp.engine.component_codec import split_ref
from proteus_mcp.engine.device_library import parse_shared_pinout
from proteus_mcp.engine.proteus_paths import LIBRARY
from proteus_mcp.engine.proteus_project import UnsupportedFormat

PINOUT_74HC02 = """*PINOUT 74HC02

ELEMENTS=4
PINS=14
IP A = 2,5,8,11
IP B = 3,6,9,12
OP Y = 1,4,10,13
PP (VCC) = 14
PP (GND) = 7
PINSWAP=A,B
GATESWAP=TRUE"""


def test_parse_shared_pinout():
    pinout = parse_shared_pinout(PINOUT_74HC02)
    assert pinout == {"elements": 4, "power": {"VCC": "14", "GND": "7"},
                      "pins": {"A": ["2", "5", "8", "11"], "B": ["3", "6", "9", "12"], "Y": ["1", "4", "10", "13"]}}
    with pytest.raises(UnsupportedFormat, match="ELEMENTS"):
        parse_shared_pinout(PINOUT_74HC02.replace("IP A = 2,5,8,11", "IP A = 2,5"))


def test_split_ref():
    assert split_ref("U5") == ("U5", None)
    assert split_ref("U5:B") == ("U5", 1)
    for bad in ("U5:", "U5:b", "5U", "U5:AB"):
        with pytest.raises(UnsupportedFormat):
            split_ref(bad)


@pytest.mark.skipif(not Path(LIBRARY).is_dir(), reason="Requires the installed Proteus library")
def test_gates_share_one_component_record(tmp_path):
    from proteus_mcp.engine import Circuit
    from proteus_mcp.engine.component_codec import _entities
    from proteus_mcp.engine.proteus_project import Project
    from proteus_mcp.templates import ensure_template

    try:
        circuit = Circuit(ensure_template())
    except (FileNotFoundError, RuntimeError, UnsupportedFormat) as exc:
        pytest.skip(f"No usable Proteus template: {exc}")
    circuit.import_device("74HC02")
    circuit.add("74HC02", "U1:A", "74HC02", 0, 0)
    circuit.add("74HC02", "U1:C", "74HC02", 0, 2000000)
    assert [(p["name"], p["number"]) for p in circuit.pins("U1:C") if p["name"] != "VCC"][:3] == \
        [("A", "8"), ("B", "9"), ("Y", "10")]
    with pytest.raises(UnsupportedFormat, match="gates"):
        circuit.add("74HC02", "U1", "74HC02", 0, 4000000)
    with pytest.raises(UnsupportedFormat, match="4 gates"):
        circuit.add("74HC02", "U1:E", "74HC02", 0, 4000000)
    saved = circuit.save(tmp_path / "gates.pdsprj")
    project = Project(saved)
    assert sorted(project._components) == ["U1:A", "U1:C"]
    entities, _ = _entities(project._cdb)
    package_records = {project._components[ref]["cdb"]["ref"] for ref in ("U1:A", "U1:C")}
    assert package_records == {"U1"}
    assert {ref: (entities[ref]["property_index"], entities[ref]["auxiliary"]) for ref in ("U1:A", "U1:C")} == \
        {"U1:A": (1, 0), "U1:C": (1, 2)}
    assert dict(entities["U1:C"]["pins"])["Y"] == "10"
