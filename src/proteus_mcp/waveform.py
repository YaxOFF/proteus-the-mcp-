"""Digital measurements and scalar VCD export (stdlib only).

Events have the BridgeClient.events() shape: ``{t: seconds, pin: str,
value: '0'|'1'|'Z'|'X'}``. Input may be unsorted; equal-time ordering is
preserved. X/Z break binary pulse/period measurements. No state is inferred
before the first observation, and duplicate values do not count as edges.
"""

from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import math
import re
from statistics import mean


def _events(events):
    out = []
    for index, event in enumerate(events):
        try:
            t = float(event['t'])
            pin = event['pin']
            value = str(event['value']).upper()
        except (KeyError, TypeError, ValueError, OverflowError) as error:
            raise ValueError(f'event {index}: expected t, pin and value') from error
        if not math.isfinite(t) or t < 0:
            raise ValueError(f'event {index}: t must be finite and nonnegative')
        _pin(pin)
        if value not in ('0', '1', 'Z', 'X'):
            raise ValueError(f'event {index}: value must be 0, 1, Z or X')
        out.append(dict(t=t, pin=pin, value=value))
    return sorted(out, key=lambda event: event['t'])


def _pin(pin):
    if not isinstance(pin, str) or not pin or any(not 33 <= ord(c) <= 126 for c in pin):
        raise ValueError('pin must be a nonempty printable ASCII name without spaces')


def _changes(events, pin):
    previous = None
    for event in _events(events):
        if event['pin'] == pin and event['value'] != previous:
            yield event
            previous = event['value']


def write_vcd(events, path, timescale='1ns', initial=None):
    """Write GTKWave-compatible 1-bit wires; return the absolute output path.

    ``initial`` maps pins to their state at t=0 (otherwise X). All pins in
    either input are declared. Timescale multipliers are 1, 10 or 100 and
    units s/ms/us/ns/ps/fs. Times round half up to the closest tick; changes
    sharing a tick retain their order. Unusual pin names are VCD-escaped.
    """
    match = re.fullmatch(r'(1|10|100)\s*(s|ms|us|ns|ps|fs)', timescale)
    if match is None:
        raise ValueError('timescale must be 1, 10 or 100 followed by s/ms/us/ns/ps/fs')
    units = dict(s=0, ms=-3, us=-6, ns=-9, ps=-12, fs=-15)
    tick = Decimal(match[1]) * Decimal(10) ** units[match[2]]
    rows = _events(events)
    initial_rows = _events(dict(t=0, pin=p, value=v) for p, v in (initial or {}).items())
    states = {event['pin']: event['value'] for event in initial_rows}
    pins = sorted({event['pin'] for event in rows} | states.keys())
    # Valid VCD identifiers need only be unique printable non-whitespace strings.
    codes = {pin: f'v{i}' for i, pin in enumerate(pins)}
    lines = ['$version proteus-mcp $end', f'$timescale {match[1]} {match[2]} $end',
             '$scope module probes $end']
    for pin in pins:
        name = pin if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_$]*', pin) else '\\' + pin
        lines.append(f'$var wire 1 {codes[pin]} {name} $end')
    lines += ['$upscope $end', '$enddefinitions $end', '#0', '$dumpvars']
    lines += [states.get(pin, 'X').lower() + codes[pin] for pin in pins]
    lines.append('$end')
    last_tick = 0
    for event in rows:
        timestamp = int((Decimal(str(event['t'])) / tick).to_integral_value(rounding=ROUND_HALF_UP))
        if timestamp != last_tick:
            lines.append(f'#{timestamp}')
            last_tick = timestamp
        lines.append(event['value'].lower() + codes[event['pin']])
    destination = Path(path).resolve()
    destination.write_text('\n'.join(lines) + '\n', encoding='ascii')
    return str(destination)


def measure(events, pin):
    """Return edge count, mean period/pulse widths and fractional duty (0..1).

    ``edges`` includes the first observed value; first/last_edge_s refer to
    those observations. A period requires a complete 0-1-0 or 1-0-1 cycle.
    High/low times average positive, bounded binary intervals; duty is
    mean_high / (mean_high + mean_low). Missing measurements are None.
    """
    _pin(pin)
    rows = list(_changes(events, pin))
    widths = {'0': [], '1': []}
    periods = []
    # rows[0] is the first *observation* (state when recording began), not a transition, so an
    # interval starting there is a partial pulse and is excluded from widths and periods.
    for a, b in zip(rows[1:], rows[2:]):
        duration = b['t'] - a['t']
        if a['value'] in widths and b['value'] in widths and duration > 0:
            widths[a['value']].append(duration)
    for a, b, c in zip(rows[1:], rows[2:], rows[3:]):
        if (a['value'] in widths and b['value'] in widths and a['value'] == c['value']
                and a['t'] < b['t'] < c['t']):
            periods.append(c['t'] - a['t'])
    high = mean(widths['1']) if widths['1'] else None
    low = mean(widths['0']) if widths['0'] else None
    period = mean(periods) if periods else None
    return dict(edges=len(rows), frequency_hz=1 / period if period else None,
                period_s=period, duty_cycle=high / (high + low) if high is not None and low is not None else None,
                high_time_s=high, low_time_s=low,
                first_edge_s=rows[0]['t'] if rows else None,
                last_edge_s=rows[-1]['t'] if rows else None)


def delay(events, pin_a, edge_a, pin_b, edge_b):
    """Signed first-B minus first-A time, or None if an edge is missing.

    Rise/fall require a known 0->1/1->0 transition. 'any' also includes the
    first observation and X/Z changes. B may precede A (negative delay).
    """
    rows = _events(events)

    def first(pin, edge):
        _pin(pin)
        if edge not in ('rise', 'fall', 'any'):
            raise ValueError("edge must be 'rise', 'fall' or 'any'")
        previous = None
        for event in _changes(rows, pin):
            value = event['value']
            if (edge == 'any' or (edge == 'rise' and previous == '0' and value == '1')
                    or (edge == 'fall' and previous == '1' and value == '0')):
                return event['t']
            previous = value
        return None

    a, b = first(pin_a, edge_a), first(pin_b, edge_b)
    return b - a if a is not None and b is not None else None
