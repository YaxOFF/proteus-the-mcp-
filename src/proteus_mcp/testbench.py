"""Validated declarative digital/analog checks over an existing live bridge.

The runner owns neither connection nor simulation lifetime. It calls HOLD at
entry, drains pre-existing events and leaves the simulation held at exit.
RUN steps use STEP, never wall-clock sleeps. Measurements refer to the latest
RUN window; WAIT_FOR polling is retained for VCD but does not replace it.
The existing bridge retains at most 20,000 queued events without an overflow
flag. Keep run windows short enough to avoid losing earlier edges.
"""

import json
import math
from pathlib import Path

from .bridge_client import BridgeError
from . import waveform


def _number(value, context, *, positive=False, nonnegative=False):
    try:
        finite = isinstance(value, (float, int)) and math.isfinite(value)
    except OverflowError:
        finite = False
    if (isinstance(value, bool) or not isinstance(value, (float, int))
            or not finite or (positive and value <= 0)
            or (nonnegative and value < 0)):
        qualifier = 'positive ' if positive else 'nonnegative ' if nonnegative else ''
        raise ValueError(f'{context}: expected a finite {qualifier}number')
    return value


def _pin(value, context):
    try:
        waveform._pin(value)
    except ValueError as error:
        raise ValueError(f'{context}: {error}') from error


def _bounds(spec, lower, upper, context, *, duty=False, nonnegative=False):
    for key in (lower, upper):
        if key not in spec:
            raise ValueError(f'{context}: missing {key}')
        _number(spec[key], f'{context}.{key}', nonnegative=nonnegative or duty)
        if duty and spec[key] > 1:
            raise ValueError(f'{context}.{key}: duty must be a fraction from 0 to 1')
    if spec[lower] > spec[upper]:
        raise ValueError(f'{context}: {lower} must be <= {upper}')


def _keys(spec, required, optional, context):
    if not isinstance(spec, dict):
        raise ValueError(f'{context}: expected an object')
    missing, extra = set(required) - spec.keys(), spec.keys() - set(required) - set(optional)
    if missing or extra:
        raise ValueError(f'{context}: missing keys {sorted(missing)}; unexpected keys {sorted(extra, key=str)}')


def _duty_keys(spec):
    return ('min_duty', 'max_duty') if 'min_duty' in spec or 'max_duty' in spec else ('min', 'max')


def validate_bench(bench):
    """Return the validated dict (loading a JSON path), else raise ValueError.

    Every step contains exactly one operation. Frequency bounds are min_hz /
    max_hz. Duty bounds are min / max (min_duty / max_duty also accepted),
    as fractions from 0 to 1. WAIT_FOR poll defaults to 0.001 simulated seconds.
    Validation completes before any bridge mutation.
    """
    if isinstance(bench, (str, Path)):
        with Path(bench).open(encoding='utf-8-sig') as stream:
            bench = json.load(stream)
    _keys(bench, ('name', 'steps'), (), 'bench')
    if not isinstance(bench['name'], str) or not bench['name'].strip():
        raise ValueError('bench.name: expected a nonempty string')
    if not isinstance(bench['steps'], list):
        raise ValueError('bench.steps: expected a list')
    for index, step in enumerate(bench['steps']):
        context = f'steps[{index}]'
        if not isinstance(step, dict) or len(step) != 1:
            raise ValueError(f'{context}: expected exactly one operation')
        operation, spec = next(iter(step.items()))
        context += f'.{operation}'
        if operation == 'run':
            _number(spec, context, positive=True)
        elif operation in ('set', 'expect', 'expect_analog'):
            if not isinstance(spec, dict) or not spec:
                raise ValueError(f'{context}: expected a nonempty pin mapping')
            for pin, value in spec.items():
                _pin(pin, context)
                if operation == 'expect_analog':
                    _keys(value, ('min', 'max'), (), context + '.' + pin)
                    _bounds(value, 'min', 'max', context + '.' + pin)
                elif value not in (('0', '1', 'Z') if operation == 'set' else ('0', '1', 'Z', 'X')):
                    raise ValueError(f'{context}.{pin}: invalid digital state {value!r}')
        elif operation in ('expect_frequency', 'expect_duty'):
            if not isinstance(spec, dict):
                raise ValueError(f'{context}: expected an object')
            lower, upper = ('min_hz', 'max_hz') if operation == 'expect_frequency' else _duty_keys(spec)
            _keys(spec, ('pin', lower, upper), (), context)
            _pin(spec['pin'], context)
            _bounds(spec, lower, upper, context, duty=operation == 'expect_duty', nonnegative=True)
        elif operation == 'wait_for':
            _keys(spec, ('pin', 'value', 'timeout'), ('poll',), context)
            _pin(spec['pin'], context)
            if spec['value'] not in ('0', '1', 'Z', 'X'):
                raise ValueError(f'{context}.value: invalid digital state')
            _number(spec['timeout'], context + '.timeout', nonnegative=True)
            _number(spec.get('poll', 0.001), context + '.poll', positive=True)
        else:
            raise ValueError(f'{context}: unknown operation {operation!r}')
    return bench


def _time(value):
    try:
        return float(_number(value, 'bridge time', nonnegative=True))
    except (ValueError, OverflowError) as error:
        raise BridgeError(str(error)) from error


def _read(bridge, analog=False):
    data = bridge.aread() if analog else bridge.read()
    key = 'volts' if analog else 'pins'
    if not isinstance(data, dict) or 't' not in data or not isinstance(data.get(key), dict):
        raise BridgeError(f'malformed {key} reading: {data!r}')
    _time(data['t'])
    for pin, value in data[key].items():
        try:
            _pin(pin, 'bridge reading')
            if analog:
                _number(value, f'bridge analog {pin}')
            elif value not in ('0', '1', 'Z', 'X'):
                raise ValueError(f'bad digital value for {pin}: {value!r}')
        except ValueError as error:
            raise BridgeError(str(error)) from error
    return data


def _value(values, pin):
    if pin not in values:
        raise BridgeError(f'bridge reading missing requested pin {pin!r}')
    return values[pin]


def run_testbench(bridge, bench, vcd_path=None) -> dict:
    """Run checks, recording failed expectations and continuing subsequent steps.

    Bridge/protocol errors propagate immediately. sim_time_s is actual elapsed
    simulation time (including WAIT_FOR), measured from HOLD to the last step.
    Each observed value is JSON serializable. No run window means frequency /
    duty expectations fail with an unavailable measurement, rather than pass.
    """
    bench = validate_bench(bench)
    start = current = _time(bridge.hold())
    initial = _read(bridge)
    if not math.isclose(initial['t'], start, rel_tol=0, abs_tol=1e-12):
        raise BridgeError('READ time disagrees with held simulation time')
    bridge.events()  # discard pre-bench history; EVENTS is a destructive read
    all_events = [dict(t=start, pin=p, value=v) for p, v in initial['pins'].items()]
    window = []
    results = []

    def advance(seconds):
        nonlocal current
        before = current
        current = _time(bridge.step(seconds))
        if current <= before:
            raise BridgeError('STEP did not advance simulation time')
        try:
            captured = waveform._events(bridge.events())
        except (ValueError, TypeError) as error:
            raise BridgeError(f'malformed bridge events: {error}') from error
        if any(event['t'] < before - 1e-12 or event['t'] > current + 1e-12 for event in captured):
            raise BridgeError('event time outside the STEP capture window')
        all_events.extend(captured)
        return captured

    for index, step in enumerate(bench['steps']):
        operation, spec = next(iter(step.items()))
        passed, observed, message = True, None, 'ok'
        if operation == 'set':
            for pin, value in spec.items():
                bridge.set(pin, value)
            observed = dict(spec)
        elif operation == 'run':
            captured = advance(spec)
            window = captured
            observed = dict(t=current, events=len(captured))
        elif operation == 'expect':
            values = _read(bridge)['pins']
            observed = {pin: _value(values, pin) for pin in spec}
            passed = observed == spec
            if not passed:
                message = f'expected {spec!r}, observed {observed!r}'
        elif operation == 'expect_analog':
            values = _read(bridge, analog=True)['volts']
            observed = {pin: _value(values, pin) for pin in spec}
            passed = all(bounds['min'] <= observed[pin] <= bounds['max'] for pin, bounds in spec.items())
            if not passed:
                message = f'analog reading outside bounds: {observed!r}'
        elif operation in ('expect_frequency', 'expect_duty'):
            observed = waveform.measure(window, spec['pin'])
            key = 'frequency_hz' if operation == 'expect_frequency' else 'duty_cycle'
            lower, upper = ('min_hz', 'max_hz') if operation == 'expect_frequency' else _duty_keys(spec)
            value = observed[key]
            passed = value is not None and spec[lower] <= value <= spec[upper]
            if not passed:
                message = f'{key}={value!r}; expected [{spec[lower]}, {spec[upper]}] from latest run'
        elif operation == 'wait_for':
            deadline = current + spec['timeout']
            while True:
                value = _value(_read(bridge)['pins'], spec['pin'])
                observed = dict(t=current, value=value)
                passed = value == spec['value'] and current <= deadline + 1e-12
                if passed or current >= deadline:
                    break
                advance(min(spec.get('poll', 0.001), deadline - current))
            if not passed:
                message = f'timed out waiting for {spec["pin"]}={spec["value"]}'
        results.append(dict(index=index, step=step, passed=passed, observed=observed, message=message))
    vcd = waveform.write_vcd(all_events, vcd_path) if vcd_path is not None else None
    return dict(name=bench['name'], passed=all(result['passed'] for result in results),
                steps=results, sim_time_s=current - start, vcd=vcd)
