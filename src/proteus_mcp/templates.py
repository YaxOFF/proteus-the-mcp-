"""A Proteus 8.x-format template project for ``Circuit(template_project=...)``.

``Circuit`` needs an existing project that already contains the RESISTOR and CAPACITOR
device definitions saved in the format of the installed Proteus. Labcenter's own sample
``DATA\\SAMPLES\\Graph Based Simulation\\Rescap.pdsprj`` has them, but it is saved in an older
file format on some builds. ``ensure_template()`` therefore builds a template once: it
copies that sample into ``%LOCALAPPDATA%\\proteus-mcp\\template.pdsprj``, opens the copy in a
hidden Proteus, saves it once (which rewrites it in the installed version's format) and
caches it. No Labcenter file is shipped in this repository; the template is made from the
user's own installation.
"""
import os
import shutil
import tempfile
from pathlib import Path

REQUIRED_DEVICES = ("RESISTOR", "CAPACITOR")
_SAMPLE = ("Graph Based Simulation", "Rescap.pdsprj")


def cache_dir():
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "proteus-mcp"


def sample_path():
    """The user's own Rescap.pdsprj inside the Proteus DATA\\SAMPLES folder."""
    from .engine.proteus_paths import SAMPLES
    return Path(SAMPLES).joinpath(*_SAMPLE)


def _usable(path):
    """True if ``path`` loads as a Circuit template with the required devices."""
    from .engine import Circuit
    try:
        templates = Circuit(template_project=path).templates
    except Exception:
        return False
    return all(name in templates for name in REQUIRED_DEVICES)


def _resave(project):
    """Open ``project`` in a hidden Proteus, save it once, exit without further prompts."""
    from .engine import Session
    session = Session(project)
    try:
        session.save()
    finally:
        session.close(discard=True)


def ensure_template(directory=None, sample=None):
    """Return the path of the cached template, creating it on first use.

    ``directory`` overrides the cache folder; ``sample`` overrides the source project
    (both mainly for tests). Raises FileNotFoundError if the sample is missing and
    RuntimeError if the re-saved copy still lacks the required devices.
    """
    target = Path(directory) / "template.pdsprj" if directory else cache_dir() / "template.pdsprj"
    if target.exists() and _usable(target):
        return target
    source = Path(sample) if sample else sample_path()
    if not source.is_file():
        raise FileNotFoundError(f"Proteus sample project not found: {source}. "
                                "Set PROTEUS_HOME/PROTEUS_DATA if Proteus is installed elsewhere.")
    target.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="template-", dir=target.parent))
    try:
        building = work / "template.pdsprj"
        shutil.copyfile(source, building)
        _resave(building)
        if not _usable(building):
            raise RuntimeError(f"{source} re-saved by Proteus still lacks {REQUIRED_DEVICES}")
        os.replace(building, target)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return target
