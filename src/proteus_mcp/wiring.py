"""Clean connections: a straight wire when two pins face each other on one line, otherwise a
matching pair of net labels. Bent multi-segment wires generated from code are avoided because
they look erratic in Proteus; labels are the conventional, readable alternative."""
import re

from .engine.proteus_project import UnsupportedFormat

CHAR = 140000  # label character width in native units (same estimate as the KiCad importer)


def _pin(circuit, endpoint):
    ref, name = circuit._endpoint(endpoint)
    pin = next(p for p in circuit.pins(ref) if p["name"] == name)
    return (ref, name), pin["position"], pin["direction"]


def _sign(value):
    return (value > 0) - (value < 0)


def facing_straight(a_pos, a_dir, b_pos, b_dir):
    """True when a single straight wire leaves both pins in their own exit direction."""
    if a_pos == b_pos or (a_pos[0] != b_pos[0] and a_pos[1] != b_pos[1]):
        return False
    towards = (_sign(b_pos[0] - a_pos[0]), _sign(b_pos[1] - a_pos[1]))
    return tuple(a_dir) == towards and tuple(b_dir) == (-towards[0], -towards[1])


def net_label(circuit, endpoint):
    """Name of a label/power/ground terminal already on the net of ``endpoint``, or None."""
    pin = circuit._endpoint(endpoint)
    net = next((group for group in circuit.nets() if pin in [tuple(item) for item in group]), [])
    members = {tuple(item) for item in net}
    for terminal in circuit.terminals():
        name = terminal.get("name") or ("GND" if terminal["kind"] == "ground" else "")
        if (terminal["kind"] in ("label", "power", "ground") and name and terminal.get("pin") is not None
                and tuple(terminal["pin"]) in members):
            return terminal["kind"], name
    return None


def link(circuit, a, b, style="auto", name=None):
    """Connect endpoints ``a`` and ``b``; returns {"style": "wire"|"label"|"routed", "net": name}.

    style: "auto" (straight wire if possible, else labels), "label", "wire" (straight only),
    "routed" (the old multi-segment router, on request only)."""
    if style not in ("auto", "label", "wire", "routed"):
        raise ValueError("style must be auto, label, wire or routed")
    (a_key, a_pos, a_dir), (b_key, b_pos, b_dir) = _pin(circuit, a), _pin(circuit, b)
    if style == "routed":
        circuit.connect(a, b)
        return {"style": "routed", "net": None}
    if style in ("auto", "wire") and facing_straight(a_pos, a_dir, b_pos, b_dir):
        try:
            # Facing pins on one line: the router's shortest candidate is the single straight
            # segment. Not pinning the points keeps the wire branchable (probes, junctions).
            circuit.connect(a, b)
            return {"style": "wire", "net": None}
        except UnsupportedFormat:
            if style == "wire":
                raise  # e.g. joining a net that already has three or more pins
    if style == "wire":
        raise ValueError("The pins are not on one straight line facing each other; "
                         "move a part, or use style='label'")
    existing = [net_label(circuit, a), net_label(circuit, b)]
    kinds = {e for e in existing if e}
    if len(kinds) > 1:
        raise ValueError(f"{a} and {b} are already on differently named nets: {sorted(kinds)}")
    kind, label = next(iter(kinds)) if kinds else ("label", name or _auto_name(circuit, a_key))
    added = []
    try:
        for endpoint, have, pos, direction in ((a, existing[0], a_pos, a_dir), (b, existing[1], b_pos, b_dir)):
            if not have:
                # Label text always reads to the right of the terminal. On a left-facing pin, stand
                # the label off far enough that its text fits on the short wire, clear of the part.
                position = None
                if kind == "label" and tuple(direction) == (-1, 0):
                    position = (pos[0] - (len(label) + 4) * CHAR, pos[1])
                added.append(circuit.add_terminal(kind, "" if kind == "ground" and label == "GND" else label,
                                                  pin=endpoint, position=position))
    except UnsupportedFormat:
        for terminal in added:
            circuit.delete_terminal(terminal)
        if style == "label":
            raise
        # A pin that already carries wires cannot take a label here; use a routed wire instead.
        circuit.connect(a, b)
        return {"style": "routed", "net": None}
    return {"style": "label", "net": label}


AUTO = re.compile(r"N_[A-Za-z0-9_]+")


def promote_auto_label(circuit, endpoint, kind, name):
    """If ``endpoint``'s net is only joined by auto labels (N_...), turn them into ``kind``/``name``
    (e.g. ground GND) instead of stacking a second terminal on the pin. Returns True if done."""
    pin = circuit._endpoint(endpoint)
    net = next((group for group in circuit.nets() if pin in [tuple(item) for item in group]), [])
    members = {tuple(item) for item in net}
    autos = [t for t in circuit.terminals() if t["kind"] == "label" and t.get("pin") is not None
             and tuple(t["pin"]) in members and AUTO.fullmatch(t.get("name") or "")]
    if not autos or len({t["name"] for t in autos}) != 1:
        return False
    label = autos[0]["name"]
    for terminal in [t for t in circuit.terminals() if t["kind"] == "label" and t.get("name") == label]:
        circuit.update_terminal(terminal["id"], kind=kind, name="" if kind == "ground" and name == "GND" else name)
    return True


def _auto_name(circuit, key):
    base = re.sub(r"[^A-Za-z0-9_]", "_", f"N_{key[0]}_{key[1].split('/')[0]}")
    used = {t.get("name") for t in circuit.terminals()}
    name, index = base, 2
    while name in used:
        name, index = f"{base}_{index}", index + 1
    return name
