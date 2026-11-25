"""Live smoke test on Proteus 8.13: a 74HC08 truth table and a 74HC161 counter, driven by one probe.

Run with the package installed: python examples/smoke_74hc.py [and] [cnt]. Output goes to examples/out/.
"""
import json, sys, time
from pathlib import Path
from proteus_mcp import server as s

OUT = Path(__file__).parent / "out"
OUT.mkdir(exist_ok=True)


def ok(r, what):
    if not r.get("ok"):
        print("FAIL", what, json.dumps(r)[:800]); s.close_proteus(); sys.exit(1)
    return r


def run(name, build, check):
    path = OUT / f"{name}.pdsprj"
    for f in OUT.glob(f"{name}*"):
        f.unlink()
    ok(s.project_new(str(path)), "project_new")
    build()
    ok(s.save_project(), "save")
    ok(s.open_in_proteus(), "open")
    ok(s.compile_netlist(), "netlist")
    ok(s.export_image(str(OUT / f"{name}.png"), dpi=200), "image")
    ok(s.sim_start(), "sim_start")
    try:
        check()
    finally:
        s.sim_stop(); s.close_proteus()


def and_build():
    ok(s.add_component("74HC08", "U1", "74HC08", 50, 50), "add U1")
    print("U1 pins:", [(p["name"], p["number"]) for p in ok(s.get_pins("U1"), "pins")["pins"]][:6])
    ok(s.add_probe("P1", pins={"A": "U1.1", "B": "U1.2", "Y": "U1.3"}), "probe")


def and_check():
    bench = {"name": "AND", "steps": []}
    for a in "01":
        for b in "01":
            bench["steps"] += [{"set": {"A": a, "B": b}}, {"run": 0.001},
                               {"expect": {"Y": "1" if a == b == "1" else "0"}}]
    print("AND testbench:", json.dumps(ok(s.run_testbench(bench), "bench"))[:600])


def cnt_build():
    ok(s.add_component("74HC161", "U1", "74HC161", 50, 50), "add U1")
    print("U1 pins:", [(p["name"], p["number"]) for p in ok(s.get_pins("U1"), "pins")["pins"]])
    ok(s.add_probe("P1", pins={"CLK": "U1.2", "MR": "U1.1", "EN": "U1.7", "ENT": "U1.10", "LD": "U1.9",
                               "QA": "U1.14", "QB": "U1.13", "QC": "U1.12", "QD": "U1.11"}), "probe")


def cnt_check():
    for pin, v in {"MR": "0", "EN": "1", "ENT": "1", "LD": "1", "CLK": "0"}.items():
        ok(s.probe_set(pin, v), "set")
    ok(s.probe_hold(), "hold"); ok(s.probe_step(0.001), "step")
    ok(s.probe_set("MR", "1"), "release reset"); ok(s.probe_step(0.001), "step")
    seen = []
    for i in range(18):
        ok(s.probe_set("CLK", "1"), "clk"); ok(s.probe_step(0.001), "step")
        ok(s.probe_set("CLK", "0"), "clk"); ok(s.probe_step(0.001), "step")
        sig = ok(s.probe_read(), "read")["signals"]
        seen.append(int("".join(sig[q] for q in ("QD", "QC", "QB", "QA")), 2))
    expected = [(i + 1) % 16 for i in range(18)]
    print("counter seen    :", seen)
    print("counter expected:", expected, "PASS" if seen == expected else "MISMATCH")


which = sys.argv[1:] or ["and", "cnt"]
if "and" in which:
    run("and74hc08", and_build, and_check)
if "cnt" in which:
    run("cnt74hc161", cnt_build, cnt_check)
