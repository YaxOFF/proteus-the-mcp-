import json
import math

import pytest

from proteus_mcp.bridge_client import BridgeError
from proteus_mcp.testbench import run_testbench, validate_bench


class FakeBridge:
    """Simulation-time 1 kHz, 25% duty output and an independently driven input."""

    def __init__(self, start=0):
        self.t = start
        self.values = {'P': '1', 'IN': 'Z'}
        self.queue = []
        self.calls = []

    def hold(self):
        self.calls.append(('hold',))
        return self.t

    def read(self):
        self.calls.append(('read',))
        return dict(t=self.t, pins=dict(self.values))

    def aread(self):
        self.calls.append(('aread',))
        return dict(t=self.t, volts={'A': 2.5})

    def set(self, pin, value):
        self.calls.append(('set', pin, value))
        self.values[pin] = value
        self.queue.append(dict(t=self.t, pin=pin, value=value))

    def step(self, seconds):
        self.calls.append(('step', seconds))
        end = self.t + seconds
        for cycle in range(math.floor(self.t / .001), math.floor(end / .001) + 1):
            for t, value in [((cycle + .25) * .001, '0'), ((cycle + 1) * .001, '1')]:
                if self.t + 1e-12 < t <= end + 1e-12:
                    self.queue.append(dict(t=t, pin='P', value=value))
                    self.values['P'] = value
        self.t = end
        return self.t

    def events(self):
        self.calls.append(('events',))
        out, self.queue = self.queue, []
        return out


def bench(*steps):
    return dict(name='demo', steps=list(steps))


def test_runner_all_operations_and_vcd(tmp_path):
    bridge = FakeBridge()
    definition = bench(
        {'set': {'IN': '1'}}, {'run': .005}, {'expect': {'IN': '1'}},
        {'expect_analog': {'A': {'min': 2.4, 'max': 2.6}}},
        {'expect_frequency': {'pin': 'P', 'min_hz': 999, 'max_hz': 1001}},
        {'expect_duty': {'pin': 'P', 'min': .24, 'max': .26}},
        {'wait_for': {'pin': 'P', 'value': '0', 'timeout': .001, 'poll': .0001}},
    )
    result = run_testbench(bridge, definition, tmp_path / 'capture.vcd')
    assert result['name'] == 'demo' and result['passed'] is True
    assert len(result['steps']) == 7
    assert all(row['passed'] and row['message'] == 'ok' for row in result['steps'])
    assert [row['index'] for row in result['steps']] == list(range(7))
    assert result['steps'][4]['observed']['frequency_hz'] == pytest.approx(1000)
    assert result['steps'][5]['observed']['duty_cycle'] == pytest.approx(.25)
    assert result['sim_time_s'] == pytest.approx(.0053)
    assert result['vcd'] == str((tmp_path / 'capture.vcd').resolve())
    assert '$var wire 1' in (tmp_path / 'capture.vcd').read_text()
    assert bridge.calls[0] == ('hold',)
    json.dumps(result, allow_nan=False)


def test_failures_recorded_and_continue():
    bridge = FakeBridge()
    result = run_testbench(bridge, bench(
        {'expect': {'IN': '1'}},
        {'expect_analog': {'A': {'min': 3, 'max': 4}}},
        {'expect_frequency': {'pin': 'P', 'min_hz': 999, 'max_hz': 1001}},
        {'run': .004},
        {'expect_frequency': {'pin': 'P', 'min_hz': 10, 'max_hz': 20}},
        {'expect_duty': {'pin': 'P', 'min_duty': .4, 'max_duty': .6}},
        {'wait_for': {'pin': 'IN', 'value': '1', 'timeout': .00025, 'poll': .0001}},
        {'set': {'IN': '1'}}, {'expect': {'IN': '1'}},
    ))
    assert result['passed'] is False
    assert [row['passed'] for row in result['steps']] == [False, False, False, True, False, False, False, True, True]
    assert 'None' in result['steps'][2]['message']
    assert 'timed out' in result['steps'][6]['message']
    assert result['sim_time_s'] == pytest.approx(.00425)
    assert result['vcd'] is None
    assert [call[1] for call in bridge.calls if call[0] == 'step'][-1] == pytest.approx(.00005)


def test_latest_run_replaces_measurement_window_and_drains_old_history():
    bridge = FakeBridge()
    bridge.queue = [dict(t=-1, pin='P', value='0')]  # irrelevant pre-bench history
    result = run_testbench(bridge, bench(
        {'run': .004}, {'expect_frequency': {'pin': 'P', 'min_hz': 990, 'max_hz': 1010}},
        {'run': .00001}, {'expect_frequency': {'pin': 'P', 'min_hz': 990, 'max_hz': 1010}},
    ))
    assert result['steps'][1]['passed'] is True
    assert result['steps'][3]['passed'] is False
    assert result['steps'][3]['observed']['frequency_hz'] is None


def test_wait_does_not_replace_latest_run_window():
    result = run_testbench(FakeBridge(), bench(
        {'run': .004}, {'wait_for': {'pin': 'P', 'value': '0', 'timeout': .001, 'poll': .0001}},
        {'expect_frequency': {'pin': 'P', 'min_hz': 990, 'max_hz': 1010}},
    ))
    assert result['passed']


def test_wait_immediate_and_zero_timeout():
    bridge = FakeBridge(start=12)
    result = run_testbench(bridge, bench(
        {'wait_for': {'pin': 'P', 'value': '1', 'timeout': 0}},
        {'wait_for': {'pin': 'IN', 'value': '1', 'timeout': 0}},
    ))
    assert [row['passed'] for row in result['steps']] == [True, False]
    assert result['sim_time_s'] == 0
    assert not any(call[0] == 'step' for call in bridge.calls)


def test_default_poll_and_elapsed_time_from_nonzero_start():
    bridge = FakeBridge(start=12)
    result = run_testbench(bridge, bench({'run': .002}))
    assert result['sim_time_s'] == pytest.approx(.002)
    assert validate_bench(bench({'wait_for': {'pin': 'P', 'value': '1', 'timeout': 1}}))


def test_json_file_and_empty_bench(tmp_path):
    path = tmp_path / 'bench.json'
    path.write_text(json.dumps(bench({'set': {'IN': '0'}})), encoding='utf-8')
    assert validate_bench(path)['steps'] == [{'set': {'IN': '0'}}]
    assert run_testbench(FakeBridge(), str(path))['passed']
    assert run_testbench(FakeBridge(), bench())['passed']


@pytest.mark.parametrize('bad', [
    None, {}, {'name': '', 'steps': []}, {'name': 'x', 'steps': {}},
    {'name': 'x', 'steps': [], 'extra': True},
    bench({}), bench({'run': 1, 'set': {'P': '1'}}), bench({'sleep': 1}),
    bench({'run': 0}), bench({'run': -1}), bench({'run': True}), bench({'run': float('nan')}),
    bench({'run': 10**1000}),
    bench({'set': {}}), bench({'set': {'P': 'X'}}), bench({'expect': {'P': 1}}),
    bench({'set': {'bad pin': '1'}}), bench({'expect_analog': {'A': {'min': 3, 'max': 2}}}),
    bench({'expect_analog': {'A': {'min': 0}}}),
    bench({'expect_frequency': {'pin': 'P', 'min_hz': -1, 'max_hz': 2}}),
    bench({'expect_frequency': {'pin': 'P', 'min_hz': 0, 'max_hz': float('inf')}}),
    bench({'expect_duty': {'pin': 'P', 'min': 0, 'max': 1.1}}),
    bench({'expect_duty': {'pin': 'P', 'min_duty': 0, 'max': 1}}),
    bench({'wait_for': {'pin': 'P', 'value': '1', 'timeout': 1, 'poll': 0}}),
    bench({'wait_for': {'pin': 'P', 'value': '1', 'timeout': -1}}),
    bench({'wait_for': {'pin': 'P', 'value': 'bad', 'timeout': 1}}),
])
def test_validation_precedes_bridge_mutation(bad):
    bridge = FakeBridge()
    with pytest.raises(ValueError):
        run_testbench(bridge, bad)
    assert not bridge.calls


def test_protocol_error_stops_immediately():
    bridge = FakeBridge()

    def broken(seconds):
        raise BridgeError('disconnected')

    bridge.step = broken
    with pytest.raises(BridgeError, match='disconnected'):
        run_testbench(bridge, bench({'run': .001}, {'set': {'IN': '1'}}))
    assert bridge.values['IN'] == 'Z'


@pytest.mark.parametrize('reading', [None, {}, {'t': 0, 'pins': {'P': 'bad'}},
                                      {'t': float('nan'), 'pins': {}}, {'t': 1, 'pins': {}}])
def test_malformed_readings(reading):
    bridge = FakeBridge()
    bridge.read = lambda: reading
    with pytest.raises(BridgeError):
        run_testbench(bridge, bench({'expect': {'P': '1'}}))


def test_missing_pin_is_a_protocol_error():
    with pytest.raises(BridgeError, match='missing requested pin'):
        run_testbench(FakeBridge(), bench({'expect': {'absent': '1'}}))


def test_nonadvancing_step_is_a_protocol_error():
    bridge = FakeBridge()
    bridge.step = lambda seconds: bridge.t
    with pytest.raises(BridgeError, match='did not advance'):
        run_testbench(bridge, bench({'wait_for': {'pin': 'P', 'value': '0', 'timeout': 1}}))


def test_malformed_events_are_protocol_errors():
    bridge = FakeBridge()
    replies = iter([[], [dict(t='bad', pin='P', value='0')]])
    bridge.events = lambda: next(replies)
    with pytest.raises(BridgeError, match='malformed bridge events'):
        run_testbench(bridge, bench({'run': .001}))


def test_wait_timeout_does_not_pass_when_bridge_overshoots():
    bridge = FakeBridge()
    step = bridge.step
    bridge.step = lambda seconds: step(.0005)
    result = run_testbench(bridge, bench({'wait_for': {'pin': 'P', 'value': '0', 'timeout': .0001}}))
    assert not result['passed']
    assert result['sim_time_s'] == .0005
