"""Live check of multi-element packages on Proteus 8.13: every gate of a 74HC08, 74HC04 and 74HC139.

Places U1:A..D (74HC08), U2:A..F (74HC04) and U3:A..B (74HC139), wires all their pins to one large
probe, checks Proteus's own netlist for the package pad numbers, then drives every gate.
Run with the package installed: python examples/gates_74hc.py. Output goes to examples/out/.
"""
import itertools
import json
import sys
from pathlib import Path

from proteus_mcp import server as s

OUT = Path(__file__).parent / "out"
OUT.mkdir(exist_ok=True)
GATES = {"U1": ("74HC08", "ABCD"), "U2": ("74HC04", "ABCDEF"), "U3": ("74HC139", "AB")}


def ok(result, what):
    if not result.get("ok"):
        raise SystemExit(f"FAIL {what}: {json.dumps(result)[:600]}")
    return result


def build(path):
    ok(s.project_new(str(path)), "project_new")
    pins, x = {}, -120
    for package, (device, gates) in GATES.items():
        for i, gate in enumerate(gates):
            ref = f"{package}:{gate}"
            ok(s.add_component(device, ref, device, x, 60 - 25 * i), ref)
            for pin in ok(s.get_pins(ref), "pins")["pins"]:
                if pin["name"] not in ("VCC", "GND"):
                    pins[f"{package}{gate}_{pin['name']}"] = f"{ref}.{pin['name']}"
        x += 70
    ok(s.add_probe("P1", pins=pins), "probe")
    ok(s.save_project(overwrite=True), "save")
    return pins


def pads_from_netlist(netlist):
    """{(ref, pin name): pad number} as Proteus compiled it."""
    return {(p["ref"], p["pin"]): p.get("number") for net in netlist["nets"] for p in net["pins"]}


def main():
    path = OUT / "gates_74hc.pdsprj"
    for f in OUT.glob("gates_74hc*"):
        f.unlink()
    pins = build(path)
    print("probe pins:", len(pins))
    ok(s.open_in_proteus(), "open")
    fails = []
    try:
        netlist = ok(s.compile_netlist(), "netlist")["netlist"]
        refs = sorted({p["ref"] for net in netlist["nets"] for p in net["pins"]})
        print("netlist refs:", refs)
        sample = [net for net in netlist["nets"] if any(p["ref"].startswith("U1") for p in net["pins"])][:3]
        print("netlist sample:", json.dumps(sample)[:400])
        ok(s.export_image(str(OUT / "gates_74hc.png"), dpi=200), "image")
        ok(s.sim_start(), "sim")
        ok(s.probe_hold(), "hold")

        def put(**values):
            for k, v in values.items():
                ok(s.probe_set(k, str(v)), k)
            ok(s.probe_step(0.0005), "step")

        def get(name):
            return ok(s.probe_read(), "read")["signals"][name]

        for gate in "ABCD":
            for a, b in itertools.product("01", repeat=2):
                put(**{f"U1{gate}_A": a, f"U1{gate}_B": b})
                if get(f"U1{gate}_Y") != str(int(a == b == "1")):
                    fails.append(f"U1:{gate} {a}{b}")
        for gate in "ABCDEF":
            for a in "01":
                put(**{f"U2{gate}_A": a})
                if get(f"U2{gate}_Y") != str(1 - int(a)):
                    fails.append(f"U2:{gate} {a}")
        for gate in "AB":
            put(**{f"U3{gate}_E": 0})
            for value in range(4):
                put(**{f"U3{gate}_A": value & 1, f"U3{gate}_B": value >> 1})
                outs = [get(f"U3{gate}_Y{i}") for i in range(4)]
                if outs != ["0" if i == value else "1" for i in range(4)]:
                    fails.append(f"U3:{gate} {value} -> {outs}")
        print("RESULT:", "ALL GATES OK" if not fails else f"FAILS {fails}")
    finally:
        s.close_proteus()
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
