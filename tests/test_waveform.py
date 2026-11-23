import re

import pytest

from proteus_mcp.waveform import delay, measure, write_vcd


def square(duty=0.5, cycles=5, pin='P'):
    rows = []
    for i in range(cycles):
        rows.extend([dict(t=i * .001, pin=pin, value='1'),
                     dict(t=(i + duty) * .001, pin=pin, value='0')])
    rows.append(dict(t=cycles * .001, pin=pin, value='1'))
    return rows


@pytest.mark.parametrize('duty', [0.5, 0.25])
def test_square_measurements(duty):
    result = measure(square(duty), 'P')
    assert result['edges'] == 11
    assert result['frequency_hz'] == pytest.approx(1000)
    assert result['period_s'] == pytest.approx(.001)
    assert result['duty_cycle'] == pytest.approx(duty)
    assert result['high_time_s'] == pytest.approx(duty * .001)
    assert result['low_time_s'] == pytest.approx((1 - duty) * .001)
    assert result['first_edge_s'] == 0
    assert result['last_edge_s'] == .005


def test_sort_filter_and_duplicates():
    rows = square() + [dict(t=.0001, pin='P', value='1'), dict(t=.0002, pin='OTHER', value='1')]
    assert measure(reversed(rows), 'P') == measure(square(), 'P')


def test_missing_pin_and_single_observation():
    missing = measure(square(), 'missing')
    assert missing['edges'] == 0
    assert all(value is None for key, value in missing.items() if key != 'edges')
    single = measure([dict(t=1, pin='P', value='1')], 'P')
    assert single['frequency_hz'] is None
    assert single['duty_cycle'] is None
    assert single['first_edge_s'] == single['last_edge_s'] == 1


def test_xz_break_cycles_and_pulses():
    rows = [dict(t=i, pin='P', value=v) for i, v in enumerate(['1', '0', 'X', 'Z', '1', '0'])]
    result = measure(rows, 'P')
    assert result['frequency_hz'] is None
    assert result['high_time_s'] == 1
    assert result['low_time_s'] is None
    assert result['duty_cycle'] is None


def test_zero_duration_glitches_do_not_produce_period():
    rows = [dict(t=0, pin='P', value=v) for v in ['0', '1', '0']]
    assert measure(rows, 'P')['frequency_hz'] is None


def test_delay_first_matching_edges():
    rows = [dict(t=0, pin=pin, value='0') for pin in ['A', 'B']]
    rows += [dict(t=t, pin=pin, value=value) for t, pin, value in
             [(.001, 'A', '1'), (.0012, 'B', '1'), (.002, 'A', '0'), (.0025, 'B', '0')]]
    assert delay(rows, 'A', 'rise', 'B', 'rise') == pytest.approx(.0002)
    assert delay(rows, 'A', 'fall', 'B', 'fall') == pytest.approx(.0005)
    assert delay(rows, 'B', 'fall', 'A', 'rise') == pytest.approx(-.0015)
    assert delay(rows, 'A', 'any', 'B', 'any') == 0
    assert delay(rows, 'A', 'rise', 'missing', 'any') is None
    with pytest.raises(ValueError, match='edge'):
        delay(rows, 'A', 'high', 'B', 'any')


def test_unknown_to_high_is_not_assumed_rising():
    rows = [dict(t=i, pin='P', value=v) for i, v in enumerate(['X', '1', '0', '1'])]
    assert delay(rows, 'P', 'rise', 'P', 'fall') == -1


def parse_vcd(path):
    """Small independent scalar reader used to check definitions and timestamps."""
    names, events = {}, []
    tick = None
    timestamp = 0
    for line in path.read_text(encoding='ascii').splitlines():
        if line.startswith('$timescale'):
            match = re.fullmatch(r'\$timescale (1|10|100) (s|ms|us|ns|ps|fs) \$end', line)
            assert match is not None
            scales = dict(s=1, ms=1e-3, us=1e-6, ns=1e-9, ps=1e-12, fs=1e-15)
            tick = int(match[1]) * scales[match[2]]
        elif line.startswith('$var'):
            fields = line.split()
            assert fields[:3] == ['$var', 'wire', '1'] and fields[-1] == '$end'
            assert fields[3] not in names
            names[fields[3]] = fields[4].removeprefix('\\')
        elif line.startswith('#'):
            previous = timestamp
            timestamp = int(line[1:])
            assert timestamp >= previous
        elif line and line[0] in '01xz':
            assert line[1:] in names and tick is not None
            events.append(dict(t=timestamp * tick, pin=names[line[1:]], value=line[0].upper()))
    return names, events


def test_vcd_golden_and_round_trip(tmp_path):
    path = tmp_path / 'golden.vcd'
    rows = [dict(t=1e-9, pin='P', value='1'), dict(t=2e-9, pin='1', value='Z')]
    assert write_vcd(reversed(rows), path, initial={'P': '0', 'static': '1'}) == str(path.resolve())
    assert path.read_text() == (
        '$version proteus-mcp $end\n$timescale 1 ns $end\n$scope module probes $end\n'
        '$var wire 1 v0 \\1 $end\n$var wire 1 v1 P $end\n$var wire 1 v2 static $end\n'
        '$upscope $end\n$enddefinitions $end\n#0\n$dumpvars\nxv0\n0v1\n1v2\n$end\n'
        '#1\n1v1\n#2\nzv0\n')
    names, parsed = parse_vcd(path)
    assert set(names.values()) == {'1', 'P', 'static'}
    assert parsed[-2:] == rows


@pytest.mark.parametrize('timescale', ['1ns', '10 us', '100ps', '1s', '1fs'])
def test_vcd_timescales_and_quantization(tmp_path, timescale):
    path = tmp_path / 'test.vcd'
    write_vcd([dict(t=.002, pin='U1.2', value='X')], path, timescale)
    _, rows = parse_vcd(path)
    assert rows[-1]['value'] == 'X'
    assert rows[-1]['pin'] == 'U1.2'


def test_vcd_rounds_half_up_and_preserves_equal_time_order(tmp_path):
    path = tmp_path / 'test.vcd'
    write_vcd([dict(t=.5e-9, pin='P', value=v) for v in ['1', '0']], path)
    _, rows = parse_vcd(path)
    assert [r['t'] for r in rows[-2:]] == [1e-9, 1e-9]
    assert [r['value'] for r in rows[-2:]] == ['1', '0']


@pytest.mark.parametrize('timescale', ['2ns', '1min', '', '0ns'])
def test_bad_timescale(tmp_path, timescale):
    with pytest.raises(ValueError, match='timescale'):
        write_vcd([], tmp_path / 'bad.vcd', timescale)


@pytest.mark.parametrize('event', [dict(t=-1, pin='P', value='0'), dict(t=float('nan'), pin='P', value='0'),
                                    dict(t=float('inf'), pin='P', value='0'), dict(t=0, pin='bad name', value='1'),
                                    dict(t=0, pin='P', value='Q'), dict(time=0, pin='P', value='1')])
def test_bad_events(event, tmp_path):
    with pytest.raises(ValueError):
        measure([event], 'P')
    with pytest.raises(ValueError):
        write_vcd([event], tmp_path / 'bad.vcd')


def test_vcd_more_than_printable_single_character_identifiers(tmp_path):
    rows = [dict(t=0, pin=f'PIN{i}', value='1') for i in range(110)]
    path = tmp_path / 'many.vcd'
    write_vcd(rows, path)
    names, parsed = parse_vcd(path)
    assert len(names) == 110
    assert len(parsed) == 220
