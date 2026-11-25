"""Build a circuit from a JSON netlist specification and verify Proteus's netlist against it.

Specification::

    {"components": [{"ref": "U1", "device": "74HC161", "value": "74HC161",
                     "x_mm": 0, "y_mm": 0, "rotation": 0, "properties": {"INIT": "0"}}, ...],
     "nets": {"CLK": ["U1.2", "U2:A.1"], "GND": ["U1.8"], "VCC": ["U1.16"], ...},
     "titles": [{"text": "PROGRAM_COUNTER", "x_mm": 0, "y_mm": 40}]}

Endpoints are 'REF.PIN' with a pin name or number; gates use 'U2:A.1'. Nets named GND or VCC
get ground/power terminals. With wiring="labels" every other net is joined with net labels carrying
its name; with wiring="channels" nets that flow left to right get real wires (see channels.py) and
the rest keep labels. A net with one endpoint is labelled too, so a later probe or part can join
it by name. Titles are free-standing labels: text on the sheet that names a region.
"""
import json
from pathlib import Path

POWER = {"GND": "ground", "VCC": "power"}


def load(spec):
    if isinstance(spec, (str, Path)):
        spec = json.loads(Path(spec).read_text(encoding="utf-8"))
    if not isinstance(spec, dict) or not isinstance(spec.get("components"), list) \
            or not isinstance(spec.get("nets"), dict):
        raise ValueError("spec needs a 'components' list and a 'nets' object")
    seen = {}
    for name, endpoints in spec["nets"].items():
        if not isinstance(endpoints, list) or not endpoints:
            raise ValueError(f"net {name}: needs a non-empty list of endpoints")
        for endpoint in endpoints:
            if endpoint in seen:
                raise ValueError(f"{endpoint} is in nets {seen[endpoint]} and {name}")
            seen[endpoint] = name
    return spec


def build(circuit, spec, to_native, wiring="labels"):
    """Place and wire ``spec`` into ``circuit``. ``to_native`` converts millimetres."""
    from .wiring import link
    from .channels import route
    if wiring not in ("labels", "channels"):
        raise ValueError("wiring must be 'labels' or 'channels'")
    spec = load(spec)
    unmodelled = set()
    with circuit.batch():
        for part in spec["components"]:
            device = part["device"]
            if device not in circuit.templates:
                circuit.import_device(device)
            properties = dict(part.get("properties") or {})
            circuit.add(device, part["ref"], part.get("value", device), to_native(part["x_mm"]),
                        to_native(part["y_mm"]), rotation=int(part.get("rotation", 0)), properties=properties)
            models = circuit.templates[device].get("model_evidence")
            if models is not None and not models and not properties.keys() & {"MODFILE", "PRIMITIVE", "MODDLL"}:
                unmodelled.add(part["ref"].split(":")[0] + " " + device)
        nets = {name: [_resolve(circuit, e) for e in endpoints] for name, endpoints in spec["nets"].items()}
        wired = {}
        if wiring == "channels":
            wired, _ = route(circuit, {n: e for n, e in nets.items() if n not in POWER})
            for connections in wired.values():
                circuit._connections.extend(connections)
        for title in spec.get("titles", []):
            circuit.add_terminal("label", title["text"], None,
                                 position=(to_native(title["x_mm"]), to_native(title["y_mm"])))
        for name, endpoints in nets.items():
            if name in wired:
                continue
            if name in POWER:
                for endpoint in endpoints:
                    circuit.add_terminal(POWER[name], name, endpoint)
            elif len(endpoints) == 1:
                circuit.add_terminal("label", name, endpoints[0])
            else:
                for endpoint in endpoints[1:]:
                    link(circuit, endpoints[0], endpoint, style="label", name=name)
    result = {"components": len(spec["components"]), "nets": len(spec["nets"]), "wired_nets": len(wired)}
    if unmodelled:
        # Proteus refuses to simulate these ("No model specified"); give them a model property.
        result["warnings"] = [f"{part}: the library device has no simulation model (MODFILE/PRIMITIVE)"
                              for part in sorted(unmodelled)]
    return result


def _resolve(circuit, endpoint):
    ref, pin = circuit._endpoint(endpoint)
    return f"{ref}.{pin}"


def verify(circuit, spec, netlist):
    """Compare Proteus's compiled ``netlist`` with ``spec`` on the specified pins only.

    split: a spec net that Proteus has in more than one net. short: one Proteus net holding pins
    of different spec nets. missing: a spec pin absent from the netlist. Pins the spec does not
    mention (probes, hidden power pins) are ignored, except that GND/VCC must reach those nets.
    """
    spec = load(spec)
    where = {}
    for index, net in enumerate(netlist["nets"]):
        for pin in net["pins"]:
            where[(pin["ref"], pin["pin"])] = (index, net["name"])
    expected, problems = {}, {"split": [], "short": [], "missing": []}
    for name, endpoints in spec["nets"].items():
        found = set()
        for endpoint in endpoints:
            ref, pin = circuit._endpoint(endpoint)
            hit = where.get((ref, pin))
            if hit is None:
                problems["missing"].append(f"{name}: {ref}.{pin}")
                continue
            found.add(hit[0])
            expected.setdefault(hit[0], set()).add(name)
        if len(found) > 1:
            problems["split"].append({name: sorted(netlist["nets"][i]["name"] for i in found)})
        if name in POWER and found and {netlist["nets"][i]["name"] for i in found} != {name}:
            problems["split"].append({name: "not on the Proteus " + name + " net"})
    for index, names in expected.items():
        if len(names) > 1:
            problems["short"].append({netlist["nets"][index]["name"]: sorted(names)})
    return {"ok": not any(problems.values()), "nets_checked": len(spec["nets"]), **problems}
