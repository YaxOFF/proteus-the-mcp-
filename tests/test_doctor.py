"""Read-only diagnostics, using synthetic files and isolated discovery helpers."""
import importlib.metadata
import struct
import subprocess
from types import SimpleNamespace

import pytest

from proteus_mcp import doctor


def _pe(path, machine=0x14C):
    data = bytearray(134)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 128)
    data[128:132] = b"PE\0\0"
    struct.pack_into("<H", data, 132, machine)
    path.write_bytes(data)
    return path


def _missing(*args, **kwargs):
    raise FileNotFoundError("private diagnostic text must not be returned")


def _by_name(report):
    return {check["name"]: check for check in report["checks"]}


@pytest.fixture
def environment(tmp_path, monkeypatch):
    from proteus_mcp import bridge_client, engine, kicad_import, probe, server, templates
    from proteus_mcp.engine import proteus_paths

    home = tmp_path / "Proteus"
    exe = home / "BIN" / "PDS.EXE"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"must never be executed")
    data = home / "DATA"
    library = data / "LIBRARY"
    library.mkdir(parents=True)
    template = tmp_path / templates.TEMPLATE_NAME
    template.write_bytes(b"offline template stand-in")
    dll = _pe(tmp_path / "MCPBRIDGE.DLL")
    cli = tmp_path / "kicad-cli.exe"
    cli.write_bytes(b"must never be executed")
    monkeypatch.setattr(doctor.sys, "platform", "win32")
    monkeypatch.setattr(proteus_paths, "EXECUTABLE", exe)
    monkeypatch.setattr(proteus_paths, "LIBRARY", library)
    monkeypatch.setattr(proteus_paths, "home", lambda: home)
    monkeypatch.setattr(proteus_paths, "data", lambda: data)
    monkeypatch.setattr(server, "_pds_version", lambda path: "8.13.31525.0")
    monkeypatch.setattr(engine, "Library", lambda directory: SimpleNamespace(
        search=lambda query, limit: [{"name": "RESISTOR"}], skipped=[]))
    monkeypatch.setattr(templates, "cache_dir", lambda: tmp_path)
    monkeypatch.setattr(templates, "_usable", lambda path: True)
    monkeypatch.setattr(templates, "ensure_template", lambda *a, **k: pytest.fail("must not build cache"))
    monkeypatch.setattr(probe, "find_dll", lambda: str(dll))
    monkeypatch.setattr(kicad_import, "find_kicad_cli", lambda: cli)
    monkeypatch.setattr(doctor.shutil, "which", lambda command: str(tmp_path / "avr-gcc.exe"))
    monkeypatch.setattr(doctor.importlib.metadata, "version", lambda name: "2.3.0")
    monkeypatch.setattr(bridge_client.BridgeClient, "connect", lambda *a, **k: pytest.fail("must not open pipe"))
    monkeypatch.setattr(engine, "Session", lambda *a, **k: pytest.fail("must not launch Proteus"))

    def tasklist(command, **kwargs):
        assert command == ["tasklist", "/FI", "IMAGENAME eq PDS.EXE", "/FO", "CSV", "/NH"]
        assert 0 < kwargs["timeout"] <= 1
        assert kwargs["capture_output"]
        return subprocess.CompletedProcess(command, 0, "INFO: No tasks match.\n", "")

    monkeypatch.setattr(doctor.subprocess, "run", tasklist)
    return SimpleNamespace(home=home, exe=exe, dll=dll, template=template,
                           library=library, engine=engine, paths=proteus_paths,
                           probe=probe, kicad=kicad_import, server=server, templates=templates)


def test_missing_proteus_is_error(environment):
    environment.exe.unlink()
    report = doctor.diagnose()
    check = _by_name(report)["Proteus install"]
    assert check["status"] == "error" and not report["ok"]
    assert "PROTEUS_HOME" in check["fix"]


def test_missing_dll_is_warning(environment, monkeypatch):
    monkeypatch.setattr(environment.probe, "find_dll", _missing)
    report = doctor.diagnose()
    check = _by_name(report)["Probe DLL"]
    assert check["status"] == "warn" and report["ok"]
    assert "MCPBRIDGE.DLL" in check["fix"] and "/releases/" in check["fix"]
    assert "PROTEUS_MCP_BRIDGE_DLL" in check["fix"]
    assert "private diagnostic" not in str(report)


def test_missing_kicad_is_warning(environment, monkeypatch):
    monkeypatch.setattr(environment.kicad, "find_kicad_cli", _missing)
    report = doctor.diagnose()
    check = _by_name(report)["KiCad CLI"]
    assert check["status"] == "warn" and report["ok"]
    assert "KICAD_CLI" in check["fix"]


@pytest.mark.parametrize("machine,status", [(0x14C, "ok"), (0x8664, "error"), (0xAA64, "error")])
def test_pe_bitness(environment, machine, status):
    _pe(environment.dll, machine)
    assert doctor._pe_machine(environment.dll) == machine
    assert _by_name(doctor.diagnose())["Probe DLL"]["status"] == status


@pytest.mark.parametrize("damage", ["short", "dos", "offset", "signature"])
def test_invalid_pe_is_error(environment, damage):
    raw = bytearray(environment.dll.read_bytes())
    if damage == "short":
        raw = raw[:20]
    elif damage == "dos":
        raw[:2] = b"XX"
    elif damage == "offset":
        struct.pack_into("<I", raw, 0x3C, 0xFFFFFFFF)
    else:
        raw[128:132] = b"XXXX"
    environment.dll.write_bytes(raw)
    assert _by_name(doctor.diagnose())["Probe DLL"]["status"] == "error"


@pytest.mark.parametrize("version,status", [("8.13.1.0", "ok"), ("8.16.1.0", "ok"),
                                            ("8.15.1.0", "warn"), (None, "warn")])
def test_validated_builds(environment, monkeypatch, version, status):
    monkeypatch.setattr(environment.server, "_pds_version", lambda path: version)
    assert _by_name(doctor.diagnose())["Proteus install"]["status"] == status


def test_missing_and_unusable_template_warn(environment, monkeypatch):
    monkeypatch.setattr(environment.templates, "_usable", lambda path: False)
    assert _by_name(doctor.diagnose())["Template cache"]["status"] == "warn"
    environment.template.unlink()
    check = _by_name(doctor.diagnose())["Template cache"]
    assert check["status"] == "warn"
    assert check["fix"] == "it is created automatically on first project_new"


def test_library_load_failure_does_not_abort_other_checks(environment, monkeypatch):
    def broken(directory):
        raise ValueError("sensitive file contents")

    monkeypatch.setattr(environment.engine, "Library", broken)
    report = doctor.diagnose()
    assert _by_name(report)["Device library"]["status"] == "error"
    assert _by_name(report)["MCP SDK"]["status"] == "ok"
    assert "sensitive file contents" not in str(report)


def test_empty_library_is_error(environment, monkeypatch):
    monkeypatch.setattr(environment.engine, "Library", lambda directory: SimpleNamespace(
        search=lambda query, limit: [], skipped=[]))
    assert _by_name(doctor.diagnose())["Device library"]["status"] == "error"


def test_missing_library_is_error(environment):
    environment.library.rmdir()
    assert _by_name(doctor.diagnose())["Device library"]["status"] == "error"


def test_skipped_catalogues_are_informational(environment, monkeypatch):
    # Stock installs carry a few catalogues in other formats; devices still load, so this is ok.
    monkeypatch.setattr(environment.engine, "Library", lambda directory: SimpleNamespace(
        search=lambda query, limit: [{}], skipped=[{"path": "ignored"}]))
    check = _by_name(doctor.diagnose())["Device library"]
    assert check["status"] == "ok" and "1 catalogues in other formats ignored" in check["detail"]


def test_avr_bundled_and_path_discovery(environment, monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    assert _by_name(doctor.diagnose())["AVR toolchain"]["status"] == "warn"
    avr = environment.home / "Tools/ARDUINO/hardware/tools/avr/bin/avr-gcc.exe"
    avr.parent.mkdir(parents=True)
    avr.write_bytes(b"must never be executed")
    check = _by_name(doctor.diagnose())["AVR toolchain"]
    assert check["status"] == "ok" and check["detail"] == str(avr)


def test_tasklist_only_returns_pds_pids(environment, monkeypatch):
    output = ('"PDS.EXE","321","private-session","1","100 K"\n'
              '"other.exe","999","private-session","1","100 K"\n'
              '"pds.exe","123","private-session","1","100 K"\n')
    monkeypatch.setattr(doctor.subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(a, 0, output, "private stderr"))
    report = doctor.diagnose()
    check = _by_name(report)["Running Proteus"]
    assert check["status"] == "warn" and check["detail"] == "PDS.EXE PIDs: 123, 321"
    assert check["fix"] == "close_proteus or end leftover PDS.EXE"
    assert "private" not in str(report)


def test_tasklist_timeout_warns(environment, monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("tasklist", 1)

    monkeypatch.setattr(doctor.subprocess, "run", timeout)
    assert _by_name(doctor.diagnose())["Running Proteus"]["status"] == "warn"


def test_pipe_is_not_claimed_free(environment):
    check = _by_name(doctor.diagnose())["Default named pipe"]
    assert check["status"] == "warn"
    assert "//./pipe/proteus_mcp" in check["detail"]
    assert "unverified" in check["detail"]


def test_missing_sdk_is_error(environment, monkeypatch):
    def missing(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(doctor.importlib.metadata, "version", missing)
    assert _by_name(doctor.diagnose())["MCP SDK"]["status"] == "error"


@pytest.mark.parametrize("system,version", [("linux", (3, 10)), ("win32", (3, 9))])
def test_unsupported_runtime(environment, monkeypatch, system, version):
    monkeypatch.setattr(doctor.sys, "platform", system)
    monkeypatch.setattr(doctor.sys, "version_info", version)
    assert _by_name(doctor.diagnose())["Windows / Python"]["status"] == "error"


def test_cli_status_and_table(environment, capsys):
    assert doctor.main() == 0  # Warnings alone succeed.
    assert "CHECK" in capsys.readouterr().out
    environment.exe.unlink()
    assert doctor.main() == 1
    output = capsys.readouterr().out
    assert "error" in output and "Fix:" in output


def test_diagnose_local_structure():
    """Real local discovery; machine-specific statuses are deliberately unconstrained."""
    report = doctor.diagnose()
    assert isinstance(report, dict) and isinstance(report["ok"], bool)
    assert len(report["checks"]) == 10
    assert len({check["name"] for check in report["checks"]}) == 10
    for check in report["checks"]:
        assert set(check) == {"name", "status", "detail", "fix"}
        assert check["status"] in {"ok", "warn", "error"}
        assert all(isinstance(value, str) and value for value in check.values())
    assert report["ok"] == all(check["status"] != "error" for check in report["checks"])
