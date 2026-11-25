"""Channel routing: real wires for left-to-right nets, checked against parts and other wires.

A net is wired when it has one driver pin facing right and every other pin faces left further
right (the usual flow of a schematic drawn in columns). Its wire leaves the driver, runs down a
vertical lane in the gap before the first input and branches into each input with a junction.
A lane is accepted only if no segment enters a part and no segment overlaps or touches another
net's wire. Anything else is left to the caller (net labels). Connectivity in Proteus is stored
by pin reference, so these checks keep the drawing honest; they do not change the netlist.
"""
GRID = 254000  # 0.1 in, Proteus's default snap, in native units
MAX_STUB = 6000000    # 60 mm: a longer horizontal run reads better as a pair of net labels
MAX_TRUNK = 25000000  # 250 mm of vertical lane


def _boxes(circuit):
    boxes = {}
    for part in circuit._parts:
        pins = circuit.pins(part["ref"])
        if not pins:
            continue
        xs = [p["position"][0] for p in pins]
        ys = [p["position"][1] for p in pins]
        boxes[part["ref"]] = (min(xs), min(ys) - GRID, max(xs), max(ys) + GRID)
    return boxes


def _crosses_box(segment, box):
    (x1, y1), (x2, y2) = segment
    left, bottom, right, top = box
    if y1 == y2:
        return bottom < y1 < top and min(x1, x2) < right and max(x1, x2) > left
    return left < x1 < right and min(y1, y2) < top and max(y1, y2) > bottom


def _touches(a, b):
    """True if two orthogonal segments of different nets would look connected.

    Overlapping parallel segments and T-joints (an end lying on the other segment) look like
    connections. A plain crossing, interior to both segments, reads as no connection."""
    (ax1, ay1), (ax2, ay2) = a
    (bx1, by1), (bx2, by2) = b
    if max(min(ax1, ax2), min(bx1, bx2)) > min(max(ax1, ax2), max(bx1, bx2)) or \
            max(min(ay1, ay2), min(by1, by2)) > min(max(ay1, ay2), max(by1, by2)):
        return False  # bounding boxes apart
    if (ax1 == ax2) == (bx1 == bx2):
        return True  # parallel on one line and sharing points
    point = (ax1, by1) if ax1 == ax2 else (bx1, ay1)
    return point in (a[0], a[1], b[0], b[1])


def route(circuit, nets):
    """{name: ["REF.PIN", ...]} -> ({name: [connection, ...]}, [names left unrouted])."""
    boxes = _boxes(circuit)
    taken = []  # (segment, name) of wires already placed
    routed, rest = {}, []
    order = sorted(nets, key=lambda n: -len(nets[n]))
    for name in order:
        pins = []
        for endpoint in nets[name]:
            ref, pin = circuit._endpoint(endpoint)
            info = next(p for p in circuit.pins(ref) if p["name"] == pin)
            pins.append(((ref, pin), tuple(info["position"]), tuple(info["direction"])))
        drivers = [p for p in pins if p[2] == (1, 0)]
        sinks = [p for p in pins if p[2] == (-1, 0)]
        if len(pins) < 2 or len(drivers) != 1 or len(drivers) + len(sinks) != len(pins):
            rest.append(name)
            continue
        (dkey, (dx, dy), _), first_in = drivers[0], min(s[1][0] for s in sinks)
        if first_in - dx < 4 * GRID or max(s[1][0] for s in sinks) - dx > 2 * MAX_STUB                 or max(s[1][1] for s in pins) - min(s[1][1] for s in pins) > MAX_TRUNK:
            rest.append(name)
            continue
        own = {key[0] for key, _, _ in pins}
        result = None
        # Lanes from the input side back towards the driver: short input stubs read best.
        for lane in range(first_in - 2 * GRID, dx + GRID, -GRID):
            stubs = [((dx, dy), (lane, dy))] + [((sx, sy), (lane, sy)) for _, (sx, sy), _ in sinks]
            ys = sorted({dy} | {s[1][1] for s in sinks})
            trunk = [((lane, a), (lane, b)) for a, b in zip(ys, ys[1:])]
            segments = [s for s in stubs + trunk if s[0] != s[1]]
            if any(_crosses_box(seg, box) for seg in segments for ref, box in boxes.items()):
                continue
            if any(_touches(seg, other) for seg in segments for other, _ in taken):
                continue
            result = (lane, ys, segments)
            break
        if result is None:
            rest.append(name)
            continue
        lane, ys, segments = result
        taken += [(seg, name) for seg in segments]
        label = [{"text": name, "auto_position": True}]
        if len(pins) == 2:
            (skey, (sx, sy), _), = sinks
            points = [(dx, dy), (sx, sy)] if dy == sy else [(dx, dy), (lane, dy), (lane, sy), (sx, sy)]
            routed[name] = [{"first": dkey, "second": skey, "points": points, "labels": label}]
            continue
        positions = [(lane, y) for y in ys]
        layout = {"junctions": positions,
                  "paths": {key: [loc, (lane, loc[1])] for key, loc, _ in pins},
                  "links": [{"first": a, "second": b, "points": [a, b]} for a, b in zip(positions, positions[1:])],
                  "labels": {dkey: label}}
        routed[name] = [{"first": dkey, "second": key, "points": None, "layout": layout}
                        for key, _, _ in sinks]
    return routed, rest
