"""A Proteus 8.x-format template project for ``Circuit(template_project=...)``.

``Circuit`` needs an existing project that already contains the RESISTOR and CAPACITOR
device definitions saved in the format of the installed Proteus. Labcenter's own sample
``DATA\\SAMPLES\\Graph Based Simulation\\Rescap.pdsprj`` has them, but it is saved in an older
file format on some builds. ``ensure_template()`` therefore builds a template once: it
copies that sample into a versioned cache under ``%LOCALAPPDATA%\\proteus-mcp``, opens
the copy in a hidden Proteus, clears its master-sheet artwork and saves it in the installed
version's format. No Labcenter file is shipped in this repository; the template is made from the
user's own installation.
"""
import os
import shutil
import tempfile
from pathlib import Path

REQUIRED_DEVICES = ("RESISTOR", "CAPACITOR")
TEMPLATE_NAME = "template-v2.pdsprj"
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


def _post_menu(session, path):
    """Post a menu command even if Proteus has not refreshed its enabled flag yet."""
    import time
    from .engine import proteus_native as native
    window = session._wait(session._ready)
    items = [m for m in native.menus(native.u.GetMenu(window["hwnd"])) if m["path"] == path]
    if len(items) != 1:
        raise RuntimeError(f"Proteus menu not found: {path}")
    if not native.u.PostMessageW(window["hwnd"], 0x111, items[0]["command_id"], 0):
        import ctypes
        raise ctypes.WinError(ctypes.get_last_error())
    time.sleep(1.2)


def _resave(project):
    """Open ``project`` in a hidden Proteus, clear its master sheet, save, exit.

    The sample's master sheet carries Labcenter's logo and title block, which would
    otherwise appear under every generated circuit. Clearing it (Goto Master Sheet,
    Select All, Delete) keeps the device definitions on the root sheet intact and
    leaves the user's clipboard alone.
    """
    from .engine import Session
    from .engine import proteus_native as native
    import ctypes
    import time
    session = Session(project)
    try:
        for path in ("Template/Goto Master Sheet", "Edit/Select All Objects"):
            _post_menu(session, path)
        hwnd = session._wait(session._ready)["hwnd"]
        if not (native.u.PostMessageW(hwnd, 0x100, 0x2E, 0) and
                native.u.PostMessageW(hwnd, 0x101, 0x2E, 0)):
            raise ctypes.WinError(ctypes.get_last_error())
        time.sleep(1.2)
        _post_menu(session, "Template/Goto Master Sheet")
        session.save()
    finally:
        session.close(discard=True)


def ensure_template(directory=None, sample=None):
    """Return the path of the cached template, creating it on first use.

    ``directory`` overrides the cache folder; ``sample`` overrides the source project
    (both mainly for tests). Raises FileNotFoundError if the sample is missing and
    RuntimeError if the re-saved copy still lacks the required devices.
    """
    target = (Path(directory) if directory else cache_dir()) / TEMPLATE_NAME
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
