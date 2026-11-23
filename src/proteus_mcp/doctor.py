"""Fast, read-only local diagnostics: ``py -3 -m proteus_mcp.doctor``.

No Proteus process, compiler or pipe connection is started. Only tasklist is
executed, with a short timeout. Exception messages and process metadata are
deliberately omitted so reports contain no account or project information.
"""
from __future__ import annotations

import csv
import importlib.metadata
import io
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys


_DLL_FIX = (
    "Download MCPBRIDGE.DLL from "
    "https://github.com/mahmoud-elghamry/proteus-mcp/releases/latest "
    "and set PROTEUS_MCP_BRIDGE_DLL to its path."
)


def _result(name, status, detail, fix):
    return {"name": name, "status": status, "detail": detail, "fix": fix}


def _runtime():
    supported = sys.platform == "win32" and sys.version_info >= (3, 10)
    # platform.platform() can include a network node name on some platforms.
    detail = (f"{'Windows' if sys.platform == 'win32' else 'Non-Windows'}; "
              f"Python {platform.python_version()} ({struct.calcsize('P') * 8}-bit)")
    return _result("Windows / Python", "ok" if supported else "error", detail,
                   "Run with Python 3.10 or newer on Windows.")


def _proteus():
    from .engine import proteus_paths

    exe = Path(proteus_paths.EXECUTABLE)
    fix = "Set PROTEUS_HOME to the Proteus installation folder containing BIN/PDS.EXE."
    if not exe.is_file():
        return _result("Proteus install", "error", f"PDS.EXE not found: {exe}", fix)
    # Share the server's Windows version-resource reader; never execute PDS.EXE.
    try:
        from .server import _pds_version
        version = _pds_version(exe)
    except Exception:
        version = None
    validated = version and tuple(version.split(".")[:2]) in (("8", "13"), ("8", "16"))
    return _result("Proteus install", "ok" if validated else "warn",
                   f"{exe}; version {version or 'unavailable'}",
                   "Use a validated Proteus 8.13 or 8.16 installation.")


def _library():
    from .engine import Library, proteus_paths

    data, directory = Path(proteus_paths.data()), Path(proteus_paths.LIBRARY)
    fix = "Set PROTEUS_DATA to a readable Proteus DATA folder containing LIBRARY."
    if not all(path.is_dir() and os.access(path, os.R_OK) for path in (data, directory)):
        return _result("Device library", "error", f"DATA/LIBRARY unreadable: {directory}", fix)
    library = Library(directory)
    if not library.search("", 1):
        return _result("Device library", "error", f"No devices loaded: {directory}", fix)
    skipped = len(library.skipped)
    # A few catalogues in other formats are normal on stock installs; devices still load.
    note = f"; {skipped} catalogues in other formats ignored (normal)" if skipped else ""
    return _result("Device library", "ok", f"{directory}; devices loaded{note}", fix)


def _template():
    from . import templates

    path = templates.cache_dir() / templates.TEMPLATE_NAME
    if not path.is_file():
        return _result("Template cache", "warn", f"Missing: {path}",
                       "it is created automatically on first project_new")
    usable = templates._usable(path)
    return _result("Template cache", "ok" if usable else "warn",
                   f"{path}; {'usable' if usable else 'unusable'}",
                   "Remove the unusable cached template and call project_new to rebuild it.")


def _pe_machine(path):
    """Read only the DOS/PE header; return the COFF machine or reject invalid PE."""
    with Path(path).open("rb") as stream:
        dos = stream.read(64)
        if len(dos) != 64 or dos[:2] != b"MZ":
            raise ValueError("Invalid DOS header")
        offset = struct.unpack_from("<I", dos, 0x3C)[0]
        if offset < 64 or offset > os.fstat(stream.fileno()).st_size - 6:
            raise ValueError("Invalid PE offset")
        stream.seek(offset)
        header = stream.read(6)
        if len(header) != 6 or header[:4] != b"PE\0\0":
            raise ValueError("Invalid PE signature")
        return struct.unpack_from("<H", header, 4)[0]


def _probe():
    from . import probe

    try:
        path = Path(probe.find_dll())
    except FileNotFoundError:
        return _result("Probe DLL", "warn", "MCPBRIDGE.DLL not found", _DLL_FIX)
    try:
        machine = _pe_machine(path)
    except (OSError, ValueError):
        return _result("Probe DLL", "error", f"Invalid or unreadable PE: {path}", _DLL_FIX)
    return _result("Probe DLL", "ok" if machine == 0x14C else "error",
                   f"{path}; PE machine 0x{machine:04x}"
                   + (" (32-bit x86)" if machine == 0x14C else " (expected 32-bit x86)"),
                   _DLL_FIX)


def _kicad():
    from .kicad_import import find_kicad_cli

    fix = "Install KiCad and set KICAD_CLI to its kicad-cli.exe path."
    try:
        path = find_kicad_cli()
    except FileNotFoundError:
        return _result("KiCad CLI", "warn", "kicad-cli not found", fix)
    return _result("KiCad CLI", "ok", str(path), fix)


def _avr():
    from .engine.proteus_paths import home

    bundled = home() / "Tools" / "ARDUINO" / "hardware" / "tools" / "avr" / "bin" / "avr-gcc.exe"
    path = str(bundled) if bundled.is_file() else shutil.which("avr-gcc")
    return _result("AVR toolchain", "ok" if path else "warn", path or "avr-gcc not found",
                   "Add the AVR toolchain bin folder containing avr-gcc.exe to PATH.")


def _processes():
    fix = "close_proteus or end leftover PDS.EXE"
    if sys.platform != "win32":
        return _result("Running Proteus", "warn", "tasklist requires Windows", fix)
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq PDS.EXE", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, errors="replace", timeout=1.0,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return _result("Running Proteus", "warn", "tasklist unavailable or timed out", fix)
    if result.returncode:
        return _result("Running Proteus", "warn", "tasklist failed", fix)
    # Never include raw tasklist output (session names, usernames, etc.).
    pids = sorted({int(row[1]) for row in csv.reader(io.StringIO(result.stdout))
                   if len(row) >= 2 and row[0].casefold() == "pds.exe" and row[1].isascii()
                   and row[1].isdigit()})
    return _result("Running Proteus", "warn" if pids else "ok",
                   "PDS.EXE PIDs: " + ", ".join(map(str, pids)) if pids else "No PDS.EXE processes",
                   fix)


def _pipe():
    from .bridge_client import BridgeClient

    return _result("Default named pipe", "warn",
                   f"{BridgeClient().path}; availability unverified (pipe not opened)",
                   "Use a unique pipe name when running concurrent bridge sessions.")


def _sdk():
    try:
        version = importlib.metadata.version("mcp")
    except importlib.metadata.PackageNotFoundError:
        return _result("MCP SDK", "error", "mcp distribution not installed",
                       "Run py -3 -m pip install \"mcp>=1.2,<3\" in the server environment.")
    return _result("MCP SDK", "ok", f"mcp {version}",
                   "Use the environment where the mcp package is installed.")


def diagnose() -> dict:
    """Return independent checks and aggregate success without modifying files.

    Warnings do not fail the CLI. In particular, pipe availability is explicitly
    unverified: checking the configured name must never connect to a live probe.
    """
    checks = []
    for name, check, fix in (
        ("Windows / Python", _runtime, "Run with Python 3.10 or newer on Windows."),
        ("Proteus install", _proteus, "Set PROTEUS_HOME to the Proteus installation folder."),
        ("Device library", _library, "Set PROTEUS_DATA to a readable Proteus DATA folder."),
        ("Template cache", _template, "Call project_new to rebuild the template cache."),
        ("Probe DLL", _probe, _DLL_FIX),
        ("KiCad CLI", _kicad, "Set KICAD_CLI to the kicad-cli.exe path."),
        ("AVR toolchain", _avr, "Add the AVR toolchain bin folder to PATH."),
        ("Running Proteus", _processes, "close_proteus or end leftover PDS.EXE"),
        ("Default named pipe", _pipe, "Use a unique pipe name for concurrent sessions."),
        ("MCP SDK", _sdk, "Run py -3 -m pip install \"mcp>=1.2,<3\"."),
    ):
        try:
            checks.append(check())
        except Exception as exc:
            # Exception text can contain arbitrary library strings or user data.
            checks.append(_result(name, "error", f"Check failed ({type(exc).__name__})", fix))
    return {"ok": all(check["status"] != "error" for check in checks), "checks": checks}


def main() -> int:
    """Print a readable diagnostic table and return a shell exit status."""
    report = diagnose()
    print(f"{'CHECK':<20} {'STATUS':<7} DETAIL / FIX")
    print("-" * 78)
    for check in report["checks"]:
        print(f"{check['name']:<20} {check['status']:<7} {check['detail']}")
        if check["status"] != "ok":
            print(f"{'':28} Fix: {check['fix']}")
    return int(any(check["status"] == "error" for check in report["checks"]))


if __name__ == "__main__":
    raise SystemExit(main())
