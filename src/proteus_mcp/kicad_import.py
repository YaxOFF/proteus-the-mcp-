"""Offline KiCad XML netlist to Proteus 8 importer (Python standard library only).

Hierarchy is flattened by kicad-cli. Symbols are rebuilt on a sheet-grouped grid;
this preserves connectivity, not the original drawing or simulation behaviour.
Every saved project is reopened and checked for missing connections AND merges.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

from .engine import Circuit, Library
from .engine.proteus_project import UnsupportedFormat


def find_kicad_cli() -> Path:
    """Find the explicit KICAD_CLI executable or newest installed Windows version."""
    override = os.environ.get("KICAD_CLI")
    if override:
        path = Path(override.strip('"'))
        if not path.is_file():
            raise FileNotFoundError(f"KICAD_CLI does not exist: {path}")
        return path
    root = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "KiCad"
    candidates = list(root.glob("*/bin/kicad-cli.exe"))
    if not candidates:
        raise FileNotFoundError("KiCad CLI not found; set KICAD_CLI")
    return max(candidates, key=lambda p: tuple(int(n) for n in re.findall(r"\d+", p.parents[1].name)))


def export_kicad_netlist(kicad_sch, out_xml) -> Path:
    """Export a root schematic, including child sheets, without modifying it."""
    source, destination = Path(kicad_sch).resolve(), Path(out_xml).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if source == destination:
        raise ValueError("Netlist output must differ from the schematic")
    destination.parent.mkdir(parents=True, exist_ok=True)
    cli = find_kicad_cli()
    with tempfile.TemporaryDirectory(prefix=".kicad-export-", dir=destination.parent) as temporary:
        exported = Path(temporary) / "net.xml"
        result = subprocess.run(
            [str(cli), "sch", "export", "netlist", "--format", "kicadxml",
             "-o", str(exported), str(source)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(f"KiCad export failed ({result.returncode}): {result.stderr or result.stdout}")
        if not exported.is_file():
            raise RuntimeError("KiCad reported success without producing a netlist")
        os.replace(exported, destination)
    return destination


def parse_kicad_netlist(xml_path) -> dict:
    """Read components, library pins and flattened nets from KiCad XML D/E."""
    root = ET.parse(xml_path).getroot()
    if root.tag != "export" or root.find("components") is None or root.find("nets") is None:
        raise ValueError("Expected a KiCad XML export with components and nets")
    components, libparts, nets, refs, owners = [], {}, [], set(), {}
    for element in root.findall("./components/comp"):
        ref = element.get("ref", "")
        if not ref or ref in refs:
            raise ValueError(f"Missing or duplicate component reference: {ref}")
        refs.add(ref)
        libsource, sheet = element.find("libsource"), element.find("sheetpath")
        if libsource is None:
            raise ValueError(f"Missing libsource for {ref}")
        components.append(dict(
            ref=ref, value=element.findtext("value", ""), lib=libsource.get("lib", ""),
            part=libsource.get("part", ""), footprint=element.findtext("footprint", ""),
            sheet_path=sheet.get("names", "/") if sheet is not None else "/",
            sheet_tstamps=sheet.get("tstamps", "") if sheet is not None else "",
        ))
    for element in root.findall("./libparts/libpart"):
        key = f"{element.get('lib', '')}:{element.get('part', '')}"
        libparts[key] = [dict(num=p.get("num", ""), name=p.get("name", ""),
                              type=p.get("type", "")) for p in element.findall("./pins/pin")]
    names = set()
    for element in root.findall("./nets/net"):
        name = element.get("name") or f"Net_{element.get('code', len(nets) + 1)}"
        if name in names:
            raise ValueError(f"Duplicate net name: {name}")
        names.add(name)
        nodes, seen = [], set()
        for node in element.findall("node"):
            ref, pin = node.get("ref", ""), node.get("pin", "")
            if ref not in refs or not pin:
                raise ValueError(f"Unknown component or empty pin in net {name}: {ref}.{pin}")
            endpoint = (ref, pin)
            if endpoint in owners and owners[endpoint] != name:
                raise ValueError(f"Pin {ref}.{pin} belongs to multiple nets")
            owners[endpoint] = name
            if endpoint not in seen:
                nodes.append(dict(ref=ref, pin=pin, pinfunction=node.get("pinfunction", ""),
                                  pintype=node.get("pintype", "")))
                seen.add(endpoint)
        nets.append(dict(name=name, code=element.get("code", ""), nodes=nodes))
    return dict(components=components, libparts=libparts, nets=nets)


def load_mapping() -> dict:
    """Load the reviewable, library-qualified mapping table beside this module."""
    return json.loads(Path(__file__).with_name("kicad_map.json").read_text(encoding="utf-8"))


def _key(component):
    return f"{component['lib']}:{component['part']}"


def _function(name):
    # KiCad ~{RESET} and Proteus $RESET$ describe the same active-low signal.
    return re.sub(r"[~{}$\s]", "", name).casefold()


def resolve_pins(kicad_pins, proteus_pins, explicit=None) -> tuple[dict, list]:
    """Resolve explicit maps, then unique signal names/aliases, then pad numbers.

    Slash-separated package pads (e.g. GND 11/31) are supported. Callers must
    reject aliases that would join different source nets before placing a part.
    """
    mapping, missing = {}, []
    explicit = explicit or {}
    for pin in kicad_pins:
        number = str(pin["num"])
        target = explicit.get(number)
        if target is not None:
            matches = [p for p in proteus_pins if p["name"] == str(target)]
            if not matches:
                matches = [p for p in proteus_pins if str(target) in p.get("number", "").split("/")]
        else:
            function = _function(pin.get("name", ""))
            matches = [p for p in proteus_pins if function and _function(p["name"]) == function]
            if not matches and function:
                matches = [p for p in proteus_pins if function in _function(p["name"]).split("/")]
            if len(matches) != 1:
                matches = [p for p in proteus_pins if number in p.get("number", "").split("/")]
        if len(matches) == 1:
            mapping[number] = matches[0]["name"]
        else:
            missing.append(number)
    return mapping, missing


def _candidate_names(part):
    names = [part.upper()]
    stem = part.split("-", 1)[0].upper()
    names.append(stem)
    # AVR speed/package and low-voltage suffix, e.g. ATmega16L-8P.
    if re.fullmatch(r"AT(?:MEGA|TINY)\d+L", stem):
        names.append(stem[:-1])
    return list(dict.fromkeys(names))


def choose_mapping(component, table, library) -> dict:
    """Use explicit lib:part/part entries, connector rules, or conservative IC matches."""
    key, part = _key(component), component["part"]
    entry = table.get(key, table.get(part))
    if entry is None:
        patterns = [k for k in table if "*" in k and fnmatch.fnmatchcase(key, k)]
        if patterns:
            entry = table[max(patterns, key=len)]
    if entry is not None and not entry.get("rule"):
        return dict(entry, source="explicit")
    if component["lib"] == "power":
        return dict(device=None, pins={}, value="keep", source="power",
                    representation="drop" if part == "PWR_FLAG" else "terminal",
                    terminal_kind="ground" if part.upper().startswith("GND") else "power")
    connector = re.fullmatch(r"Conn_(01|02)x(\d+)(?:_.*)?", part)
    if component["lib"] == "Connector_Generic" and connector:
        count = int(connector[1]) * int(connector[2])
        prefixes = ("CONN-SIL", "CONN-DIL") if connector[1] == "01" else ("CONN-DIL", "CONN-SIL")
        for prefix in prefixes:
            name = prefix + str(count)
            entries = [e for e in library.search(name, limit=1000) if e["name"] == name]
            if len(entries) == 1:
                return dict(device=name, library=entries[0]["library"],
                            pins={str(n): str(n) for n in range(1, count + 1)},
                            value="keep", source="connector", substitution="Connector geometry may differ")
        return dict(device=None, pins={}, value="keep", source="connector",
                    representation="terminal_only", substitution="Connector represented by free net terminals")
    for candidate in _candidate_names(part):
        entries = [e for e in library.search(candidate, limit=1000)
                   if e["name"].casefold() == candidate.casefold()]
        if len(entries) == 1:
            return dict(device=entries[0]["name"], library=entries[0]["library"], pins={},
                        value="device", source="library_exact" if candidate == part.upper() else "library_near",
                        substitution="Device/model equivalence has not been established")
        if len(entries) > 1:
            raise ValueError(f"Ambiguous Proteus device {candidate}; add an explicit library-qualified map")
    raise ValueError(f"No exact or conservative near Proteus library match for {part}")


def sanitize_net_names(names) -> dict:
    """Return deterministic ASCII identifiers unique even under case folding."""
    result, used = {}, set()
    for name in sorted(set(names)):
        ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
        base = re.sub(r"[^A-Za-z0-9_]", "_", ascii_name).strip("_") or "NET"
        if base[0].isdigit():
            base = "N_" + base
        base = base[:48]
        # Reserve the default ground name so /GND cannot steal GND's identifier.
        candidate = base
        if (candidate.casefold() == "gnd" and name != "GND") or candidate.casefold() in used:
            candidate = base + "_" + hashlib.sha256(name.encode()).hexdigest()[:10]
        while candidate.casefold() in used:
            candidate += "_"
        used.add(candidate.casefold())
        result[name] = candidate
    return result


def _ascii_value(value):
    value = value.replace("µ", "u").replace("μ", "u").replace("Ω", "Ohm")
    return "".join(c if 32 <= ord(c) < 127 else "?" for c in value)[:240] or "?"


def _part_pins(component, design):
    pins = {p["num"]: dict(p) for p in design["libparts"].get(_key(component), [])}
    for net in design["nets"]:
        for node in net["nodes"]:
            if node["ref"] == component["ref"]:
                p = pins.setdefault(node["pin"], dict(num=node["pin"], name=""))
                if node["pinfunction"]:
                    p["name"] = node["pinfunction"]
    return list(pins.values())


def _layout(components, pin_counts):
    """Group sheets in separate row bands; wide cells leave room for net text."""
    positions, sheets, y = {}, {}, 0
    for component in components:
        sheets.setdefault(component["sheet_path"], []).append(component)
    for sheet in sorted(sheets):
        parts = sorted(sheets[sheet], key=lambda c: (re.sub(r"\d+", "", c["ref"]),
                                                    [int(n) for n in re.findall(r"\d+", c["ref"])], c["ref"]))
        for start in range(0, len(parts), 6):
            row = parts[start:start + 6]
            height = max(12000000, max(pin_counts[c["ref"]] for c in row) * 600000 + 4000000)
            for column, component in enumerate(row):
                positions[component["ref"]] = (column * 24000000, y - height // 2)
            y -= height
        y -= 12000000
    return positions


def _terminal_positions(circuit, placed, nets, names):
    """Fan labels into 5.08 mm rows with text clearance on either side of a part."""
    named_pins = {(node["ref"], placed[node["ref"]]["pins"][node["pin"]]): names[net["name"]]
                  for net in nets if len(net["nodes"]) > 1 for node in net["nodes"] if node["ref"] in placed}
    positions = {}
    for ref, item in placed.items():
        pins = [p for p in circuit.pins(ref) if (ref, p["name"]) in named_pins]
        if not pins:
            continue
        bounds = [[p["position"][axis] for p in pins] for axis in (0, 1)]
        horizontal_ys = []
        for sign in (-1, 1):
            side = sorted((p for p in pins if p["direction"] == (sign, 0)), key=lambda p: p["position"][1])
            for index, pin in enumerate(side):
                endpoint = (ref, pin["name"])
                # Native terminal text has fixed orientation. Reserve enough
                # width even when a left-facing terminal's text extends right.
                clearance = (len(named_pins[endpoint]) + 4) * 200000 if sign < 0 else 1000000
                x = (min(bounds[0]) if sign < 0 else max(bounds[0])) + sign * clearance
                y = item["position"][1] + (2 * index - len(side) + 1) * 254000
                positions[endpoint] = (x, y)
                horizontal_ys.append(y)
        for sign in (-1, 1):
            side = sorted((p for p in pins if p["direction"] == (0, sign)), key=lambda p: p["position"][0])
            extent = bounds[1] + horizontal_ys
            edge = min(extent) if sign < 0 else max(extent)
            for index, pin in enumerate(side):
                positions[(ref, pin["name"])] = (pin["position"][0], edge + sign * (1000000 + index * 508000))
    return positions


def verify_connectivity(circuit, expected_nets) -> dict:
    """Compare exact endpoint partitions, including singletons and extra device pins."""
    actual = [set(map(tuple, net)) for net in circuit.nets()]
    by_pin = {pin: net for net in actual for pin in net}
    failures = []
    for item in expected_nets:
        expected = set(map(tuple, item["pins"]))
        if not expected:
            item["verified"] = False
            continue
        group = by_pin.get(next(iter(expected)), set())
        item["verified"] = group == expected
        if not item["verified"]:
            failures.append(dict(name=item["name"], expected=[list(p) for p in sorted(expected)],
                                 actual=[list(p) for p in sorted(group)]))
    # Also catch a merge made entirely out of unused device pins.
    covered = {tuple(pin) for item in expected_nets for pin in item["pins"]}
    unexpected = [[list(p) for p in sorted(net)] for net in actual if len(net) > 1 and not net.intersection(covered)]
    return dict(passed=not failures and not unexpected, failures=failures, unexpected_merges=unexpected)


def _terminal_kind(name):
    leaf = name.rsplit("/", 1)[-1].upper()
    if leaf.startswith("GND"):
        return "ground"
    if leaf.startswith(("+", "-")) or leaf in ("VCC", "VDD", "VSS", "VEE"):
        return "power"
    return "label"


def _write_report(report, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def kicad_to_proteus(kicad_sch_or_xml, out_pdsprj, template, report_path=None) -> dict:
    """Import into a new project and return a detailed, reopened-file audit report.

    Existing destinations may be replaced, but the input and template cannot be
    overwritten. Failed pin/device mappings skip whole parts; source nets remain
    separate and connect all surviving mapped endpoints.
    """
    source, output, template = map(lambda p: Path(p).resolve(), (kicad_sch_or_xml, out_pdsprj, template))
    report_path = Path(report_path).resolve() if report_path else output.with_suffix(".report.json")
    if output in (source, template) or report_path in (source, template, output):
        raise ValueError("Input, template, project output and report must have distinct paths")
    if source.suffix.lower() == ".kicad_sch":
        with tempfile.TemporaryDirectory(prefix="kicad-import-") as temporary:
            design = parse_kicad_netlist(export_kicad_netlist(source, Path(temporary) / "net.xml"))
    else:
        design = parse_kicad_netlist(source)
    circuit, library, table = Circuit(template_project=template), Library(), load_mapping()
    report = dict(input=str(source), output=str(output), template=str(template),
                  library_directory=str(library.directory),
                  parts=[], skipped=[], warnings=[], net_name_map=sanitize_net_names(n["name"] for n in design["nets"]),
                  nets=[], engine_recommendations=[],
                  limitations=["Layout is regenerated on one sheet grouped by source sheet path",
                               "Connectivity verification does not establish simulation or model equivalence",
                               "Wheel distributors must include kicad_map.json as package data; packaging configuration is outside this change's allowed files"])
    pin_counts = {c["ref"]: len(_part_pins(c, design)) for c in design["components"]}
    positions, placed, special, failed_devices = _layout(design["components"], pin_counts), {}, {}, {}
    owners = {(n["ref"], n["pin"]): net["name"] for net in design["nets"] for n in net["nodes"]}
    for component in design["components"]:
        ref = component["ref"]
        item = dict(component, kicad=_key(component), status="skipped")
        report["parts"].append(item)
        try:
            entry = choose_mapping(component, table, library)
            item.update(mapping=entry)
            if entry.get("representation") in ("terminal", "drop", "terminal_only"):
                item["status"] = entry["representation"]
                special[ref] = item
                if entry.get("substitution"):
                    report["warnings"].append(f"{ref}: {entry['substitution']}")
                continue
            device = entry["device"]
            if device in failed_devices:
                raise UnsupportedFormat(failed_devices[device])
            if device not in circuit.templates:
                try:
                    circuit.import_device(device, library=entry.get("library"))
                except UnsupportedFormat as exc:
                    failed_devices[device] = str(exc)
                    raise
            # The imported template reflects selected-package pad numbers; raw
            # Library.get pins can contain reused graphic annotation numbers.
            proteus_pins = [dict(name=name, number=number) for name, number in circuit.templates[device]["pins"]]
            pins, missing = resolve_pins(_part_pins(component, design), proteus_pins, entry.get("pins"))
            required = {pin for (owner, pin) in owners if owner == ref}
            if required.intersection(missing):
                raise ValueError(f"Unresolved connected pins: {sorted(required.intersection(missing))}")
            destinations = {}
            for pin in required:
                dest, net = pins[pin], owners[(ref, pin)]
                if dest in destinations and destinations[dest] != net:
                    raise ValueError(f"Proteus pin {dest} would merge distinct KiCad nets")
                destinations[dest] = net
            value = _ascii_value(component["value"]) if entry.get("value", "keep") == "keep" else device
            x, y = positions[ref]
            circuit.add(device, ref, value, x, y)
            item.update(status="placed", device=device, pins=pins, unmapped_unconnected_pins=missing,
                        proteus_value=value, position=[x, y], verified_proteus_pins=proteus_pins)
            placed[ref] = item
            if entry.get("substitution"):
                report["warnings"].append(f"{ref}: {entry['substitution']}")
            if value != component["value"]:
                report["warnings"].append(f"{ref}: stored value {value!r}; source value retained in report")
        except (UnsupportedFormat, ValueError, UnicodeError) as exc:
            item["reason"] = str(exc)
            report["skipped"].append(dict(ref=ref, kicad=_key(component), reason=str(exc)))
    terminal_positions = _terminal_positions(circuit, placed, design["nets"], report["net_name_map"])
    attached = set()
    for net in design["nets"]:
        name = report["net_name_map"][net["name"]]
        endpoints, missing, terminals = set(), [], []
        kind = _terminal_kind(net["name"])
        for node in net["nodes"]:
            ref, pin = node["ref"], node["pin"]
            if ref in placed:
                endpoints.add((ref, placed[ref]["pins"][pin]))
            elif ref in special:
                representation = special[ref]["status"]
                if representation == "terminal_only":
                    terminals.append(node)
                elif representation == "terminal":
                    kind = special[ref]["mapping"]["terminal_kind"]
            else:
                missing.append(node)
        item = dict(name=net["name"], proteus_name=name, pins=[list(p) for p in sorted(endpoints)], missing_nodes=missing,
                    fully_placed=not missing and not terminals and bool(endpoints),
                    source_pin_count=len(net["nodes"]), terminal_only_nodes=terminals)
        report["nets"].append(item)
        # Single-source-pin nets are deliberately left unconnected.
        if len(net["nodes"]) < 2:
            continue
        for endpoint in sorted(endpoints):
            if endpoint in attached:
                continue  # grouped physical pads already share this logical pin
            circuit.add_terminal(kind, "" if kind == "ground" and name == "GND" else name, endpoint,
                                 position=terminal_positions[endpoint])
            attached.add(endpoint)
        for node in terminals:
            x, y = positions[node["ref"]]
            # One labelled terminal per connector pin, in the connector's cell.
            circuit.add_terminal(kind, name, position=(x, y - int(node["pin"]) * 508000))
    for device, reason in failed_devices.items():
        recommendation = "Extend the installed-device importer to support this observed definition layout"
        if "Missing graphic dependencies" in reason:
            recommendation = (
                "In component_codec.Circuit.import_device, replace the whole-device-byte regex for $NAME\\0 "
                "with structural graphics-reference parsing. _device_pins already identifies each pin's glyph; "
                "logical pin names ($CS/$DRDY/$FAULT) and ATMEGA16's raw-byte $Y match must not be treated as glyph references. "
                "Retain dependency validation for actual glyph references and add regression import/save/reopen tests."
            )
        report["engine_recommendations"].append(dict(device=device, blocker=reason, recommendation=recommendation))
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        circuit.save(output, title=source.stem, overwrite=True)
        reopened = Circuit.open(output)
        report["verification"] = verify_connectivity(reopened, report["nets"])
    except (UnsupportedFormat, ValueError, OSError) as exc:
        report["verification"] = dict(passed=False, error=str(exc))
        _write_report(report, report_path)
        raise
    fully = [n for n in report["nets"] if n["fully_placed"]]
    reproduced = sum(n["verified"] for n in fully)
    report["counts"] = dict(kicad_parts=len(design["components"]), placed=len(placed),
                            skipped=len(report["skipped"]), handled_symbols=len(special),
                            nets=len(design["nets"]), nets_fully_placed=len(fully),
                            nets_reproduced=reproduced,
                            multi_pin_nets_reproduced=sum(n["verified"] and n["source_pin_count"] > 1 for n in fully),
                            nets_with_surviving_pins=sum(bool(n["pins"]) for n in report["nets"]),
                            nets_surviving_verified=sum(n["verified"] for n in report["nets"]),
                            reproduced_net_ratio=reproduced / len(design["nets"]) if design["nets"] else 1.0,
                            fully_placed_net_ratio=reproduced / len(fully) if fully else None)
    _write_report(report, report_path)
    if not report["verification"]["passed"]:
        raise UnsupportedFormat(f"Reopened project failed connectivity verification; see {report_path}")
    return report


def main(argv=None) -> int:
    """CLI entry point for schematic or pre-exported XML input."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        report = kicad_to_proteus(args.source, args.output, args.template, args.report)
    except (OSError, ValueError, RuntimeError, ET.ParseError, subprocess.TimeoutExpired) as exc:
        parser.exit(1, f"Import failed: {exc}\n")
    print(json.dumps(report["counts"], indent=2))
    print("Reopened project: connectivity verified; no unexpected merges")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
