"""Build AVR C firmware and configure existing Circuit/Session parts.

Property evidence (Proteus 8.13, read-only inspection of temporary copies):
DATA/SAMPLES/VSM for AVR/Arduino Infra Red Remote.pdsprj and Arduino IR
Decoder.pdsprj, member ROOT.DSN, declare PROGRAM (Program File) and CLOCK
(Clock Frequency) for DIGITAL,ATMEGA328P, with CLOCK=8MHz / CLOCK=16MHz.
Neither sample contains CLKFREQ. The engine's Simulation.set_firmware also
uses PROGRAM and CLOCK. No simulation equivalence is implied by loading code.
"""

import os
from pathlib import Path
import re
import shutil
import subprocess

from .engine import proteus_paths


class FirmwareError(RuntimeError):
    """A tool failed; stderr, stdout, command and returncode retain diagnostics."""

    def __init__(self, message, *, stderr='', stdout='', command=(), returncode=None):
        super().__init__(message + (f'\n{stderr}' if stderr else ''))
        self.stderr = stderr
        self.stdout = stdout
        self.command = list(command)
        self.returncode = returncode


def find_avr_toolchain():
    """Find gcc, objcopy and size together, returning absolute string paths.

    Search PROTEUS_MCP_AVR_GCC, PATH, then the discovered Proteus Arduino
    toolchain. Each candidate must contain all three executables; tools from
    different installations are never mixed.
    """
    override = os.environ.get('PROTEUS_MCP_AVR_GCC')
    on_path = shutil.which('avr-gcc.exe') or shutil.which('avr-gcc')
    candidates = ([Path(override)] if override else []) + ([Path(on_path)] if on_path else []) + [
        proteus_paths.home() / 'Tools' / 'ARDUINO' / 'hardware' / 'tools' / 'avr' / 'bin' / 'avr-gcc.exe']
    missing = []
    for gcc in candidates:
        suffix = gcc.suffix
        paths = dict(gcc=gcc, objcopy=gcc.with_name('avr-objcopy' + suffix),
                     size=gcc.with_name('avr-size' + suffix))
        absent = [str(path) for path in paths.values() if not path.is_file()]
        if not absent:
            return {key: str(path.resolve()) for key, path in paths.items()}
        missing.extend(absent)
    raise FileNotFoundError('AVR toolchain not found or incomplete. Set PROTEUS_MCP_AVR_GCC '
                            'to the full avr-gcc.exe path, add the AVR bin directory to PATH, '
                            'or install the Proteus Arduino tools. Missing: ' + ', '.join(missing))


def mcu_name(device: str) -> str:
    """Normalize a Proteus AVR name, e.g. ATMEGA328P -> atmega328p.

    This validates spelling shape, not whether a particular compiler supports
    that chip. Package suffixes are not guessed or silently removed.
    """
    if not isinstance(device, str):
        raise ValueError('MCU must be an AVR device name such as ATMEGA328P')
    name = device.strip().lower()
    if not re.fullmatch(r'(?:atmega|attiny|at90|atxmega|avr)[a-z0-9]+', name):
        raise ValueError('MCU must be an AVR device name such as ATMEGA328P')
    return name


def _run(command):
    result = subprocess.run(command, capture_output=True, text=True, errors='replace', shell=False)
    if result.returncode:
        raise FirmwareError(f'{Path(command[0]).name} failed (exit {result.returncode})',
                            stderr=result.stderr, stdout=result.stdout,
                            command=command, returncode=result.returncode)
    return result


def build(sources: list[str], mcu: str, f_cpu: int, out_dir,
          include_dirs=(), defines=(), extra_flags=()) -> dict:
    """Compile/link C sources; return elf/hex paths, byte counts and diagnostics.

    Outputs are named after the first source. ``command`` is the gcc argv;
    ``warnings`` contains diagnostic lines from all three tools. Static RAM
    excludes runtime stack/heap. Flash = text + data; RAM = data + bss, as
    documented in the AVR-LibC memory-sections manual.
    """
    if isinstance(sources, (str, bytes)) or not sources:
        raise ValueError('sources must be a nonempty list of source paths')
    source_paths = [Path(source).resolve() for source in sources]
    for source in source_paths:
        if not source.is_file():
            raise FileNotFoundError(source)
    name = mcu_name(mcu)
    if type(f_cpu) is not int or f_cpu <= 0:
        raise ValueError('f_cpu must be a positive integer in Hz')
    tools = find_avr_toolchain()
    destination = Path(out_dir).resolve()
    elf = destination / (source_paths[0].stem + '.elf')
    hex_file = elf.with_suffix('.hex')
    if elf in source_paths or hex_file in source_paths:
        raise ValueError('output paths would overwrite an input source')
    destination.mkdir(parents=True, exist_ok=True)
    command = [tools['gcc'], '-Os', '-std=gnu11', f'-mmcu={name}', f'-DF_CPU={f_cpu}UL']
    for directory in include_dirs:
        command.extend(['-I', str(Path(directory).resolve())])
    command.extend('-D' + str(define) for define in defines)
    command.extend(str(flag) for flag in extra_flags)
    command.extend(str(source) for source in source_paths)
    command.extend(['-o', str(elf)])
    # Keep failed builds from leaving a previous successful HEX available to load.
    elf.unlink(missing_ok=True)
    hex_file.unlink(missing_ok=True)
    compiled = _run(command)
    converted = _run([tools['objcopy'], '-O', 'ihex', '-R', '.eeprom', str(elf), str(hex_file)])
    size_command = [tools['size'], '--format=berkeley', str(elf)]
    sized = _run(size_command)
    row = re.search(r'^\s*(\d+)\s+(\d+)\s+(\d+)\s+\d+\s+[0-9a-fA-F]+\s+.+$',
                    sized.stdout, re.MULTILINE)
    if row is None:
        raise FirmwareError('Unrecognized avr-size output', stdout=sized.stdout,
                            stderr=sized.stderr, command=size_command)
    text, data, bss = map(int, row.groups())
    warnings = [line for result in (compiled, converted, sized)
                for line in result.stderr.splitlines() if line.strip()]
    return dict(elf=str(elf), hex=str(hex_file), flash_bytes=text + data,
                ram_bytes=data + bss, warnings=warnings, command=command)


def load_program(circuit_or_session, ref, hex_path, clock_hz=None):
    """Set PROGRAM and optional CLKFREQ/CLOCK; return a serializable summary.

    CLOCK is the actual AVR property verified in the samples above; CLKFREQ
    is not a verified AVR property. CLKFREQ is also written to preserve the
    requested interface, but CLOCK configures the sampled AVR models. Stop a
    live simulation before calling.
    Offline circuits still need saving; a live Session still needs saving if
    the configuration should persist. HEX contents/model suitability are not
    validated here.
    """
    program = Path(hex_path).resolve()
    if not program.is_file():
        raise FileNotFoundError(program)
    properties = dict(PROGRAM=str(program))
    if clock_hz is not None:
        if type(clock_hz) is not int or clock_hz <= 0:
            raise ValueError('clock_hz must be a positive integer in Hz')
        for unit, scale in [('MHz', 1_000_000), ('kHz', 1000), ('Hz', 1)]:
            if clock_hz >= scale:
                properties['CLKFREQ'] = properties['CLOCK'] = f'{clock_hz / scale:.12g}{unit}'
                break
    circuit_or_session.set_properties(ref, **properties)
    return dict(ref=ref, properties=properties)
