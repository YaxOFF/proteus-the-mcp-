"""Template helper: offline logic with a stand-in Proteus, plus one real-Proteus build."""
import shutil
from pathlib import Path

import pytest

from proteus_mcp import templates

ROOT = Path(__file__).resolve().parents[1]
PREMADE = ROOT / "work" / "template813.pdsprj"       # a template already saved by Proteus 8.13


def test_cached_template_is_reused_without_proteus(tmp_path, monkeypatch):
    if not PREMADE.is_file():
        pytest.skip("work/template813.pdsprj not present")
    shutil.copyfile(PREMADE, tmp_path / templates.TEMPLATE_NAME)
    monkeypatch.setattr(templates, "_resave", lambda project: pytest.fail("must not launch Proteus"))
    assert templates.ensure_template(directory=tmp_path) == tmp_path / templates.TEMPLATE_NAME


def test_first_use_copies_sample_and_resaves(tmp_path, monkeypatch):
    if not PREMADE.is_file():
        pytest.skip("work/template813.pdsprj not present")
    sample = tmp_path / "sample" / "Rescap.pdsprj"
    sample.parent.mkdir()
    shutil.copyfile(PREMADE, sample)
    resaved = []
    monkeypatch.setattr(templates, "_resave", lambda project: resaved.append(Path(project)))
    target = templates.ensure_template(directory=tmp_path / "cache", sample=sample)
    assert target == tmp_path / "cache" / templates.TEMPLATE_NAME and target.is_file()
    assert len(resaved) == 1 and resaved[0].name == "template.pdsprj" and resaved[0] != target
    assert list((tmp_path / "cache").iterdir()) == [target]      # scratch folder cleaned up


def test_unusable_result_is_rejected(tmp_path, monkeypatch):
    sample = tmp_path / "Rescap.pdsprj"
    sample.write_bytes(b"not a project")
    monkeypatch.setattr(templates, "_resave", lambda project: None)
    with pytest.raises(RuntimeError, match="lacks"):
        templates.ensure_template(directory=tmp_path / "cache", sample=sample)
    assert not (tmp_path / "cache" / templates.TEMPLATE_NAME).exists()


def test_old_cache_is_preserved_and_rebuilt(tmp_path, monkeypatch):
    """The old template contains the sample's master artwork and must not be reused."""
    if not PREMADE.is_file():
        pytest.skip("work/template813.pdsprj not present")
    old = tmp_path / "template.pdsprj"
    shutil.copyfile(PREMADE, old)
    original = old.read_bytes()
    resaved = []
    monkeypatch.setattr(templates, "_resave", lambda project: resaved.append(project))
    target = templates.ensure_template(directory=tmp_path, sample=PREMADE)
    assert target.name == templates.TEMPLATE_NAME and target != old
    assert len(resaved) == 1 and old.read_bytes() == original


def test_missing_sample(tmp_path):
    with pytest.raises(FileNotFoundError, match="sample project not found"):
        templates.ensure_template(directory=tmp_path, sample=tmp_path / "nope.pdsprj")


def test_cache_dir_uses_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert templates.cache_dir() == tmp_path / "proteus-mcp"


@pytest.mark.proteus
def test_real_template_build(tmp_path, request):
    """Builds the template from the installed Proteus' own Rescap sample (opens Proteus)."""
    if "proteus" not in (request.config.getoption("-m") or ""):
        pytest.skip("opens Proteus; run with -m proteus")
    from proteus_mcp.engine import Circuit
    source = templates.sample_path()
    original = source.read_bytes()
    target = templates.ensure_template(directory=tmp_path)
    circuit = Circuit(template_project=target)
    assert {"RESISTOR", "CAPACITOR"} <= set(circuit.templates)
    # The master-sheet object list is empty, rather than just hidden under a view.
    master_body = circuit._dsn.index(b"OBJECT DATA\0", circuit._default) + 12
    assert circuit._dsn[master_body] == 0xFF
    assert source.read_bytes() == original
    assert templates.ensure_template(directory=tmp_path) == target     # cached the second time
