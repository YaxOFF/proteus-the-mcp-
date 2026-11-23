"""Parser/mapping tests and offline native Proteus connectivity round trips."""
import json
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

from proteus_mcp import kicad_import as importer
from proteus_mcp.engine import Circuit, Library
from proteus_mcp.engine.proteus_paths import LIBRARY
from proteus_mcp.engine.proteus_project import UnsupportedFormat


FIXTURE = Path(__file__).parent / "fixtures" / "kicad_small.xml"
TEMPLATE = Path(__file__).parents[1] / "work" / "template813.pdsprj"
needs_proteus = pytest.mark.skipif(
    not TEMPLATE.is_file() or not Path(LIBRARY).is_dir(),
    reason="Requires template813.pdsprj and installed Proteus device libraries (no GUI)",
)


def test_parser_preserves_hierarchy_values_pins_and_nets():
    result = importer.parse_kicad_netlist(FIXTURE)
    assert len(result["components"]) == 9
    assert len(result["nets"]) == 5
    assert result["components"][0] == dict(
        ref="R1", value="10k", lib="Device", part="R", footprint="Resistor_SMD:R_0603",
        sheet_path="/POWER/", sheet_tstamps="/root/power/",
    )
    assert result["components"][4]["sheet_path"] == "/"
    assert result["libparts"]["Device:LED"][0] == dict(num="1", name="K", type="passive")
    assert result["nets"][2]["nodes"][1] == dict(ref="D1", pin="2", pinfunction="A", pintype="passive")


@pytest.mark.parametrize("mutation,reason", [
    ("duplicate", "duplicate component"), ("missing_ref", "Unknown component"),
    ("two_nets", "multiple nets"), ("duplicate_net", "Duplicate net"),
])
def test_parser_rejects_inconsistent_connectivity(tmp_path, mutation, reason):
    tree = ET.parse(FIXTURE)
    root = tree.getroot()
    if mutation == "duplicate":
        root.find("components").append(ET.fromstring('<comp ref="R1"><libsource lib="Device" part="R"/></comp>'))
    elif mutation == "missing_ref":
        root.find("./nets/net/node").set("ref", "MISSING1")
    elif mutation == "two_nets":
        ET.SubElement(root.findall("./nets/net")[1], "node", ref="R1", pin="2")
    else:
        root.findall("./nets/net")[1].set("name", "GND")
    path = tmp_path / "bad.xml"
    tree.write(path)
    with pytest.raises(ValueError, match=reason):
        importer.parse_kicad_netlist(path)


def test_parser_rejects_non_netlist(tmp_path):
    path = tmp_path / "other.xml"
    path.write_text("<export/>")
    with pytest.raises(ValueError, match="KiCad XML"):
        importer.parse_kicad_netlist(path)


def test_pin_names_polarity_aliases_numbers_and_ambiguity():
    mapping, missing = importer.resolve_pins(
        [dict(num="1", name="K"), dict(num="2", name="A")],
        [dict(name="A", number=""), dict(name="K", number="")],
        importer.load_mapping()["Device:LED"]["pins"],
    )
    assert mapping == {"1": "K", "2": "A"} and not missing
    mapping, missing = importer.resolve_pins(
        [dict(num="1", name="PB0"), dict(num="9", name="~{RESET}"),
         dict(num="31", name="GND"), dict(num="2", name="unknown"), dict(num="3", name="ambiguous")],
        [dict(name="PB0/T0/XCK", number="1"), dict(name="$RESET$", number="9"),
         dict(name="GND", number="11/31"), dict(name="SIGNAL", number="2"),
         dict(name="AMB1", number="3"), dict(name="AMB2", number="3")],
    )
    assert mapping == {"1": "PB0/T0/XCK", "9": "$RESET$", "31": "GND", "2": "SIGNAL"}
    assert missing == ["3"]
    assert importer.resolve_pins([dict(num="1", name="A")], [dict(name="A", number="1")], {"1": "BAD"}) == ({}, ["1"])


def test_names_are_ascii_short_unique_and_deterministic():
    names = ["/+3V3_SENS", "/A-B", "/A/B", "GND", "/GND", "gnd", "", "µ" * 500, "A" * 500]
    result = importer.sanitize_net_names(names)
    assert result == importer.sanitize_net_names(reversed(names))
    assert len({n.casefold() for n in result.values()}) == len(names)
    assert result["GND"] == "GND"
    assert all(n.isascii() and len(n) < 255 for n in result.values())


class FakeLibrary:
    def search(self, query, limit=30):
        return ([dict(name="ATMEGA16", library="AVR2")] if query == "ATMEGA16" else [])


def test_mapping_part_fallback_power_connectors_and_near_match():
    choose = importer.choose_mapping
    assert choose(dict(lib="Custom", part="R"), {"R": {"device": "RESISTOR"}}, FakeLibrary())["device"] == "RESISTOR"
    assert choose(dict(lib="power", part="+5V"), {}, FakeLibrary())["terminal_kind"] == "power"
    assert choose(dict(lib="power", part="PWR_FLAG"), {}, FakeLibrary())["representation"] == "drop"
    assert choose(dict(lib="MCU_Microchip_ATmega", part="ATmega16L-8P"), {}, FakeLibrary())["device"] == "ATMEGA16"
    assert choose(dict(lib="Connector_Generic", part="Conn_02x99_Odd_Even"), {}, FakeLibrary())["representation"] == "terminal_only"
    with pytest.raises(ValueError, match="No exact"):
        choose(dict(lib="IC", part="UNAVAILABLE"), {}, FakeLibrary())


def test_find_cli_override_and_numeric_version_order(tmp_path, monkeypatch):
    monkeypatch.delenv("KICAD_CLI", raising=False)
    monkeypatch.setattr(importer.shutil, "which", lambda name: None)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    for version in ("9.0", "10.0"):
        path = tmp_path / "KiCad" / version / "bin" / "kicad-cli.exe"
        path.parent.mkdir(parents=True)
        path.touch()
    assert importer.find_kicad_cli().parents[1].name == "10.0"
    monkeypatch.setenv("KICAD_CLI", str(tmp_path / "KiCad/9.0/bin/kicad-cli.exe"))
    assert importer.find_kicad_cli().parents[1].name == "9.0"
    monkeypatch.setenv("KICAD_CLI", str(tmp_path / "missing.exe"))
    with pytest.raises(FileNotFoundError, match="KICAD_CLI"):
        importer.find_kicad_cli()


def test_export_uses_flattening_cli_and_propagates_errors(tmp_path, monkeypatch):
    source, output = tmp_path / "root.kicad_sch", tmp_path / "net.xml"
    source.write_bytes(b"sentinel source")
    monkeypatch.setattr(importer, "find_kicad_cli", lambda: Path("cli.exe"))
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        Path(args[args.index("-o") + 1]).write_bytes(FIXTURE.read_bytes())
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(importer.subprocess, "run", run)
    assert importer.export_kicad_netlist(source, output) == output
    assert calls[0][1:6] == ["sch", "export", "netlist", "--format", "kicadxml"]
    assert source.read_bytes() == b"sentinel source"
    monkeypatch.setattr(importer.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=2, stderr="bad schematic", stdout=""))
    with pytest.raises(RuntimeError, match="bad schematic"):
        importer.export_kicad_netlist(source, output)
    monkeypatch.setattr(importer.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stderr="", stdout=""))
    with pytest.raises(RuntimeError, match="without producing"):
        importer.export_kicad_netlist(source, output)
    assert output.read_bytes() == FIXTURE.read_bytes()


@needs_proteus
def test_end_to_end_exact_pin_partitions(tmp_path):
    output = tmp_path / "small.pdsprj"
    report = importer.kicad_to_proteus(FIXTURE, output, TEMPLATE)
    assert report["counts"]["placed"] == 6
    assert report["counts"]["handled_symbols"] == 3
    assert not report["skipped"]
    assert report["counts"]["nets_reproduced"] == 5
    assert report["verification"]["passed"]
    assert json.loads(output.with_suffix(".report.json").read_text(encoding="utf-8")) == report
    reopened = Circuit.open(output)
    expected = {
        frozenset({("R1", "2"), ("C1", "2"), ("J1", "2")}),
        frozenset({("R1", "1"), ("R2", "1"), ("J1", "1")}),
        frozenset({("R2", "2"), ("D1", "A")}),
        frozenset({("C1", "1"), ("D1", "K")}),
        frozenset({("J2", "1")}),
    }
    assert {frozenset(net) for net in reopened.nets()} == expected
    assert not any(t.get("pin") == ("J2", "1") for t in reopened.terminals())
    assert {t["kind"] for t in reopened.terminals()} >= {"label", "ground", "power"}


@needs_proteus
def test_skipped_part_keeps_surviving_net_and_reports_reason(tmp_path):
    tree = ET.parse(FIXTURE)
    root = tree.getroot()
    root.find("components").append(ET.fromstring('<comp ref="U99"><value>missing</value><libsource lib="Custom" part="NOT_A_REAL_DEVICE_123"/></comp>'))
    ET.SubElement(root.findall("./nets/net")[2], "node", ref="U99", pin="1")
    source = tmp_path / "partial.xml"
    tree.write(source)
    report = importer.kicad_to_proteus(source, tmp_path / "partial.pdsprj", TEMPLATE, placeholders=False)
    assert report["counts"]["placed"] == 6 and report["counts"]["skipped"] == 1
    assert report["skipped"][0]["ref"] == "U99"
    assert "No exact" in report["skipped"][0]["reason"]
    assert report["counts"]["nets_reproduced"] == 4
    assert report["counts"]["nets_surviving_verified"] == 5
    assert report["verification"]["passed"]


@needs_proteus
def test_seed_pin_maps_exist_in_actual_library():
    library = Library()
    for key, entry in importer.load_mapping().items():
        if not entry["device"]:
            continue
        info = library.get(entry["device"], entry["library"])
        assert set(entry["pins"].values()) <= {p["name"] for p in info["pins"]}, key


@needs_proteus
@pytest.mark.parametrize("failure", ["import", "missing_pin", "merged_pins"])
def test_failures_skip_whole_parts_without_merging_nets(tmp_path, monkeypatch, failure):
    table = importer.load_mapping()
    if failure == "import":
        original = Circuit.import_device
        def import_device(self, device, *args, **kwargs):
            if device == "LED-RED":
                raise UnsupportedFormat("Missing graphic dependencies: ['$TEST']")
            return original(self, device, *args, **kwargs)
        monkeypatch.setattr(Circuit, "import_device", import_device)
        skipped = "D1"
    else:
        table["Device:LED"]["pins"]["2"] = "BAD" if failure == "missing_pin" else "K"
        monkeypatch.setattr(importer, "load_mapping", lambda: table)
        skipped = "D1"
    report = importer.kicad_to_proteus(FIXTURE, tmp_path / "failure.pdsprj", TEMPLATE)
    assert report["counts"]["placed"] == 5
    assert [s["ref"] for s in report["skipped"]] == [skipped]
    assert report["verification"]["passed"]
    if failure == "import":
        assert "structural" in report["engine_recommendations"][0]["recommendation"]
    else:
        assert ("Unresolved" if failure == "missing_pin" else "merge distinct") in report["skipped"][0]["reason"]


@needs_proteus
def test_connector_terminal_only_fallback(tmp_path):
    tree = ET.parse(FIXTURE)
    tree.getroot().find("./components/comp[@ref='J1']/libsource").set("part", "Conn_01x99")
    source = tmp_path / "terminals.xml"
    tree.write(source)
    output = tmp_path / "terminals.pdsprj"
    report = importer.kicad_to_proteus(source, output, TEMPLATE)
    assert report["counts"]["placed"] == 5
    assert report["counts"]["handled_symbols"] == 4
    assert report["verification"]["passed"]
    part = next(p for p in report["parts"] if p["ref"] == "J1")
    assert part["status"] == "terminal_only"
    assert len([t for t in Circuit.open(output).terminals() if t["pin"] is None]) == 2


def test_verifier_detects_merge_split_and_unused_pin_merges():
    class Merged:
        def nets(self):
            return [[("R1", "1"), ("R2", "1")], [("X1", "1"), ("X2", "1")]]
    expected = [dict(name="a", pins=[("R1", "1")]), dict(name="b", pins=[("R2", "1")])]
    result = importer.verify_connectivity(Merged(), expected)
    assert not result["passed"] and len(result["failures"]) == 2
    assert len(result["unexpected_merges"]) == 1
    class Split:
        def nets(self):
            return [[("R1", "1")], [("R2", "1")]]
    assert not importer.verify_connectivity(Split(), [dict(name="a", pins=[("R1", "1"), ("R2", "1")])])["passed"]


def test_cannot_overwrite_source_or_template():
    with pytest.raises(ValueError, match="distinct paths"):
        importer.kicad_to_proteus(FIXTURE, TEMPLATE, TEMPLATE)


@needs_proteus
@pytest.mark.parametrize("pin_numbers,expected", [
    (["1", "2", "4", "6"], "CONN-SIL6"),
    ([str(n) for n in range(1, 17)], "CONN-DIL16"),
    ([str(n) for n in range(1, 21)], "CONN-DIL20"),
    ([str(n) for n in range(1, 36)], "CONN-DIL40"),
])
def test_placeholder_header_selection_preserves_pad_numbers(pin_numbers, expected):
    entry = importer._placeholder_mapping(dict(part="UNKNOWN_IC"),
                                        [dict(num=n) for n in pin_numbers], Library())
    assert entry["device"] == expected
    assert entry["pins"] == {n: n for n in pin_numbers}
    assert "no simulation model" in entry["representation"]
    assert entry["library"] == "CONNDVC"


@needs_proteus
def test_unknown_ic_placeholder_preserves_all_pin_groups(tmp_path):
    tree = ET.parse(FIXTURE)
    root = tree.getroot()
    root.find("components").append(ET.fromstring('<comp ref="U99"><value>custom value</value><libsource lib="Custom" part="NOT_A_REAL_DEVICE_123"/></comp>'))
    libpart = ET.SubElement(root.find("libparts"), "libpart", lib="Custom", part="NOT_A_REAL_DEVICE_123")
    pins = ET.SubElement(libpart, "pins")
    for number in ("1", "2", "3"):
        ET.SubElement(pins, "pin", num=number, name="CUSTOM", type="passive")
    for net, number in zip(root.findall("./nets/net"), ("1", "2", "3")):
        ET.SubElement(net, "node", ref="U99", pin=number)
    source = tmp_path / "placeholder.xml"
    tree.write(source)
    output = tmp_path / "placeholder.pdsprj"
    report = importer.kicad_to_proteus(source, output, TEMPLATE)
    assert report["counts"]["placed"] == 7
    assert report["counts"]["placeholders"] == 1
    assert report["counts"]["nets_reproduced"] == 5
    assert report["verification"]["passed"] and not report["skipped"]
    part = next(p for p in report["parts"] if p["ref"] == "U99")
    assert report["simulation"]["verified"] is False
    assert report["simulation"]["placeholder_refs"] == ["U99"]
    assert part["placeholder"] and part["proteus_value"] == "NOT_A_REAL_DEVICE_123"
    reopened = Circuit.open(output)
    for index, number in enumerate(("1", "2", "3")):
        assert {tuple(p) for p in report["nets"][index]["pins"]} in [set(n) for n in reopened.nets()]
        assert ("U99", number) in map(tuple, report["nets"][index]["pins"])


def test_cli_no_placeholders_option(monkeypatch, capsys):
    captured = {}
    def convert(*args, **kwargs):
        captured.update(kwargs)
        return {"counts": {"placeholders": 0}}
    monkeypatch.setattr(importer, "kicad_to_proteus", convert)
    assert importer.main([str(FIXTURE), "-o", "unused.pdsprj", "--template", str(TEMPLATE), "--no-placeholders"]) == 0
    assert captured["placeholders"] is False


@needs_proteus
def test_only_isolated_declared_no_connect_pad_may_be_omitted(tmp_path):
    tree = ET.parse(FIXTURE)
    root = tree.getroot()
    root.find("components").append(ET.fromstring('<comp ref="U99"><value>MAX31856</value><libsource lib="Sensor_Temperature" part="MAX31856"/></comp>'))
    net = ET.SubElement(root.find("nets"), "net", code="6", name="unconnected-U99-DNC")
    ET.SubElement(net, "node", ref="U99", pin="6", pinfunction="DNC", pintype="no_connect")
    source = tmp_path / "nc.xml"
    tree.write(source)
    report = importer.kicad_to_proteus(source, tmp_path / "nc.pdsprj", TEMPLATE)
    assert report["counts"]["placed"] == 7
    assert report["counts"]["no_connect_nets_preserved"] == 1
    assert report["counts"]["nets_reproduced"] == 5
    assert report["counts"]["nets_accounted_for"] == 6
    assert report["verification"]["passed"]
    ET.SubElement(net, "node", ref="R1", pin="3")
    tree.write(source)
    report = importer.kicad_to_proteus(source, tmp_path / "bad_nc.pdsprj", TEMPLATE)
    assert any(p["ref"] == "U99" and "Unresolved connected pins" in p["reason"] for p in report["skipped"])
