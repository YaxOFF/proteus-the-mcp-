"""Structural dependency parsing and offline installed-device round trips."""
from pathlib import Path
import re
import struct

import pytest

from proteus_mcp.engine import Circuit, Library
from proteus_mcp.engine import device_library
from proteus_mcp.engine.component_codec import _graphic_references, _device_graphic_references
from proteus_mcp.engine.proteus_paths import LIBRARY
from proteus_mcp.engine.proteus_project import UnsupportedFormat


TEMPLATE = Path(__file__).parents[1] / "work/template813.pdsprj"
needs_proteus = pytest.mark.skipif(not TEMPLATE.is_file() or not Path(LIBRARY).is_dir(),
                                 reason="Requires offline Proteus library and template813.pdsprj")


def _drawing(records):
    content = b"".join(records)
    return struct.pack("<3I", len(content) + 12, len(records), 1) + content


def test_structural_child_reference_and_arbitrary_binary_text():
    child = bytearray(28) + b"ORDINARY_CHILD\0"
    struct.pack_into("<I", child, 0, len(child))
    child[8:10] = b"\x06\x02"
    rectangle = bytearray(40)
    struct.pack_into("<I", rectangle, 0, len(rectangle))
    rectangle[8:10] = b"\x01\x02"
    rectangle[28:31] = b"$Y\0"
    marker = bytearray(28) + b"$MKRORIGIN\0"
    struct.pack_into("<I", marker, 0, len(marker))
    marker[8:10] = b"\x08\x02"
    assert _graphic_references(_drawing([rectangle, child, marker])) == {"ORDINARY_CHILD", "$MKRORIGIN"}
    with pytest.raises(UnsupportedFormat, match="Truncated|Invalid"):
        _graphic_references(_drawing([child])[:-1])


@needs_proteus
@pytest.mark.parametrize("device,expected", [
    ("ATMEGA16", {"$RESET$", "PB0/T0/XCK", "GND", "VCC"}),
    ("MAX31856", {"$CS", "$DRDY", "$FAULT", "T+", "T-"}),
])
def test_import_place_save_reopen(tmp_path, device, expected):
    library = Library()
    _, body = library.definition(device)
    dependencies = _device_graphic_references(body)
    assert not dependencies.intersection({"$Y", "$CS", "$DRDY", "$FAULT"})
    assert any(name.startswith("$PIN") for name in dependencies)
    circuit = Circuit(template_project=TEMPLATE)
    circuit.import_device(device)
    circuit.add(device, "U1", device, 0, 0)
    pins = circuit.pins("U1")
    assert expected <= {p["name"] for p in pins}
    if device == "ATMEGA16":
        assert next(p for p in pins if p["name"] == "GND")["number"] == "11/31"
    path = tmp_path / f"{device}.pdsprj"
    circuit.save(path)
    reopened = Circuit.open(path)
    assert reopened.pins("U1") == pins
    assert reopened.components()[0]["device"] == device


@needs_proteus
@pytest.mark.parametrize("missing", ["$PINDEFAULT", "$MKRORIGIN", "$MKRNODE"])
def test_genuinely_missing_dependency_still_fails_atomically(monkeypatch, missing):
    circuit = Circuit(template_project=TEMPLATE)
    original = device_library.definition_groups
    def without_pin_graphic(dsn):
        groups = original(dsn)
        groups["graphics"].pop(missing, None)
        return groups
    monkeypatch.setattr(device_library, "definition_groups", without_pin_graphic)
    before = circuit._dsn
    with pytest.raises(UnsupportedFormat, match="Missing graphic dependencies.*" + re.escape(missing)):
        circuit.import_device("MAX31856")
    assert circuit._dsn == before
    assert "MAX31856" not in circuit.templates
