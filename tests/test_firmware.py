from pathlib import Path
import subprocess
from unittest.mock import Mock

import pytest

from proteus_mcp import firmware


def toolchain(directory):
    directory.mkdir(parents=True, exist_ok=True)
    for name in ('gcc', 'objcopy', 'size'):
        (directory / f'avr-{name}.exe').touch()
    return directory / 'avr-gcc.exe'


def test_discovery_priority(monkeypatch, tmp_path):
    override = toolchain(tmp_path / 'configured')
    on_path = toolchain(tmp_path / 'path')
    monkeypatch.setenv('PROTEUS_MCP_AVR_GCC', str(override))
    monkeypatch.setattr(firmware.shutil, 'which', lambda name: str(on_path))
    assert firmware.find_avr_toolchain()['gcc'] == str(override)
    monkeypatch.delenv('PROTEUS_MCP_AVR_GCC')
    assert firmware.find_avr_toolchain()['gcc'] == str(on_path)
    monkeypatch.setenv('PROTEUS_MCP_AVR_GCC', str(tmp_path / 'absent.exe'))
    assert firmware.find_avr_toolchain()['gcc'] == str(on_path)


def test_discovery_bundled(monkeypatch, tmp_path):
    gcc = toolchain(tmp_path / 'Tools/ARDUINO/hardware/tools/avr/bin')
    monkeypatch.delenv('PROTEUS_MCP_AVR_GCC', raising=False)
    monkeypatch.setattr(firmware.shutil, 'which', lambda name: None)
    monkeypatch.setattr(firmware.proteus_paths, 'home', lambda: tmp_path)
    assert firmware.find_avr_toolchain() == dict(
        gcc=str(gcc), objcopy=str(gcc.with_name('avr-objcopy.exe')),
        size=str(gcc.with_name('avr-size.exe')))


def test_discovery_missing_and_bad_override(monkeypatch, tmp_path):
    monkeypatch.setattr(firmware.shutil, 'which', lambda name: None)
    monkeypatch.setattr(firmware.proteus_paths, 'home', lambda: tmp_path)
    monkeypatch.setenv('PROTEUS_MCP_AVR_GCC', str(tmp_path / 'missing.exe'))
    with pytest.raises(FileNotFoundError, match='PROTEUS_MCP_AVR_GCC'):
        firmware.find_avr_toolchain()
    gcc = toolchain(tmp_path / 'incomplete')
    gcc.with_name('avr-size.exe').unlink()
    monkeypatch.setenv('PROTEUS_MCP_AVR_GCC', str(gcc))
    with pytest.raises(FileNotFoundError, match='avr-size.exe'):
        firmware.find_avr_toolchain()


@pytest.mark.parametrize('name,expected', [('ATMEGA328P', 'atmega328p'),
                                         ('ATMEGA1284P', 'atmega1284p'),
                                         ('ATMEGA16', 'atmega16'), (' attiny85 ', 'attiny85')])
def test_mcu_name(name, expected):
    assert firmware.mcu_name(name) == expected


@pytest.mark.parametrize('name', ['', '-mmcu=other', 'ATMEGA328P-PU', None])
def test_bad_mcu_name(name):
    with pytest.raises(ValueError, match='MCU'):
        firmware.mcu_name(name)


@pytest.fixture
def mock_build(monkeypatch, tmp_path):
    source = tmp_path / 'source with spaces.c'
    source.write_text('int main(void) { for (;;) {} }')
    gcc = toolchain(tmp_path / 'bin')
    monkeypatch.setenv('PROTEUS_MCP_AVR_GCC', str(gcc))
    runner = Mock(side_effect=[
        subprocess.CompletedProcess([], 0, '', 'source.c: warning: example\n'),
        subprocess.CompletedProcess([], 0, '', ''),
        subprocess.CompletedProcess([], 0, '   text    data     bss     dec     hex filename\n'
                                    '    100      12      20     132      84 source with spaces.elf\n', ''),
    ])
    monkeypatch.setattr(firmware.subprocess, 'run', runner)
    return source, runner


def test_build_commands_and_size(mock_build, tmp_path):
    source, runner = mock_build
    result = firmware.build([str(source)], 'ATMEGA328P', 8_000_000, tmp_path / 'out',
                            include_dirs=[tmp_path / 'headers'], defines=['LED=5'], extra_flags=['-Wall'])
    assert result['flash_bytes'] == 112
    assert result['ram_bytes'] == 32
    assert result['warnings'] == ['source.c: warning: example']
    assert result['elf'].endswith('source with spaces.elf')
    assert result['hex'].endswith('source with spaces.hex')
    command = result['command']
    assert all(flag in command for flag in ('-Os', '-std=gnu11', '-mmcu=atmega328p',
                                           '-DF_CPU=8000000UL', '-DLED=5', '-Wall'))
    assert str(source) in command
    assert runner.call_count == 3
    objcopy = runner.call_args_list[1].args[0]
    assert objcopy[1:] == ['-O', 'ihex', '-R', '.eeprom', result['elf'], result['hex']]
    assert runner.call_args_list[2].args[0][1:] == ['--format=berkeley', result['elf']]
    assert all(call.kwargs['shell'] is False for call in runner.call_args_list)


def test_compile_failure_retains_diagnostics_and_removes_stale_hex(mock_build, tmp_path):
    source, runner = mock_build
    output = tmp_path / 'out'
    output.mkdir()
    stale = output / (source.stem + '.hex')
    stale.write_text('old firmware')
    runner.side_effect = [subprocess.CompletedProcess([], 1, '', 'fatal error: missing.h')]
    with pytest.raises(firmware.FirmwareError) as caught:
        firmware.build([str(source)], 'atmega328p', 1_000_000, output)
    assert caught.value.stderr == 'fatal error: missing.h'
    assert caught.value.returncode == 1
    assert '-mmcu=atmega328p' in caught.value.command
    assert runner.call_count == 1
    assert not stale.exists()


@pytest.mark.parametrize('stage', [1, 2])
def test_auxiliary_tool_failure(mock_build, tmp_path, stage):
    source, runner = mock_build
    results = [subprocess.CompletedProcess([], 0, '', '') for _ in range(stage)]
    results.append(subprocess.CompletedProcess([], 2, '', 'tool failed'))
    runner.side_effect = results
    with pytest.raises(firmware.FirmwareError, match='tool failed'):
        firmware.build([str(source)], 'ATMEGA16', 8_000_000, tmp_path / 'out')
    assert runner.call_count == stage + 1


def test_unrecognized_size_is_not_zero(mock_build, tmp_path):
    source, runner = mock_build
    runner.side_effect = [subprocess.CompletedProcess([], 0, '', '')] * 3
    with pytest.raises(firmware.FirmwareError, match='Unrecognized avr-size'):
        firmware.build([str(source)], 'ATMEGA16', 8_000_000, tmp_path / 'out')


@pytest.mark.parametrize('sources,clock', [([], 1), ('blink.c', 1), (['missing.c'], 1)])
def test_bad_source_inputs(sources, clock, tmp_path):
    with pytest.raises((ValueError, FileNotFoundError)):
        firmware.build(sources, 'ATMEGA16', clock, tmp_path / 'out')


@pytest.mark.parametrize('clock', [0, -1, True, 1.5])
def test_bad_build_clock(mock_build, tmp_path, clock):
    source, runner = mock_build
    with pytest.raises(ValueError, match='f_cpu'):
        firmware.build([str(source)], 'ATMEGA16', clock, tmp_path / 'out')
    runner.assert_not_called()


@pytest.mark.parametrize('clock,expected', [(8_000_000, '8MHz'), (7_372_800, '7.3728MHz'),
                                         (32_768, '32.768kHz'), (500, '500Hz'), (None, None)])
def test_load_program(mock_build, clock, expected):
    source, _ = mock_build
    target = Mock()
    result = firmware.load_program(target, 'U1', source, clock)
    properties = dict(PROGRAM=str(source.resolve()))
    if expected is not None:
        properties['CLKFREQ'] = properties['CLOCK'] = expected
    target.set_properties.assert_called_once_with('U1', **properties)
    assert result == dict(ref='U1', properties=properties)


def test_load_rejects_missing_file_and_bad_clock(tmp_path):
    target = Mock()
    with pytest.raises(FileNotFoundError):
        firmware.load_program(target, 'U1', tmp_path / 'missing.hex')
    path = tmp_path / 'blink.hex'
    path.touch()
    with pytest.raises(ValueError, match='clock_hz'):
        firmware.load_program(target, 'U1', path, 0)
    target.set_properties.assert_not_called()


@pytest.mark.proteus
def test_bundled_avr_compiles_blink(monkeypatch, tmp_path):
    gcc = firmware.proteus_paths.home() / 'Tools/ARDUINO/hardware/tools/avr/bin/avr-gcc.exe'
    if not gcc.is_file():
        pytest.skip('Proteus-bundled avr-gcc.exe not found')
    monkeypatch.setenv('PROTEUS_MCP_AVR_GCC', str(gcc))
    try:
        firmware.find_avr_toolchain()
    except FileNotFoundError as error:
        pytest.skip(str(error))
    source = tmp_path / 'blink.c'
    source.write_text('#include <avr/io.h>\n#include <util/delay.h>\n'
                      'int main(void) { DDRB |= _BV(DDB5); for (;;) { '
                      'PORTB ^= _BV(PORTB5); _delay_ms(100); } }\n')
    result = firmware.build([str(source)], 'ATMEGA328P', 8_000_000, tmp_path / 'firmware output')
    assert Path(result['elf']).read_bytes()[:4] == b'\x7fELF'
    hex_lines = Path(result['hex']).read_text().splitlines()
    assert hex_lines[-1] == ':00000001FF'
    for line in hex_lines:
        assert line.startswith(':')
        record = bytes.fromhex(line[1:])
        assert len(record) == record[0] + 5 and sum(record) % 256 == 0
    assert 0 < result['flash_bytes'] < 32768
    assert 0 <= result['ram_bytes'] < 2048
