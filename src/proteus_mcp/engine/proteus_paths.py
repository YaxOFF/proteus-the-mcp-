"""Locate the installed Proteus 8 tree instead of hard-coding one machine's paths.

Order: PROTEUS_HOME env var (folder that holds BIN and DATA), then the standard
Program Files / ProgramData locations. DATA may live under the install folder
(Proteus 8.13 default) or under ProgramData (8.16 style installs).
"""
import os
from pathlib import Path

_SYSTEM = Path(os.environ.get("SystemDrive", "C:") + os.sep)
_VENDOR = Path("Labcenter Electronics") / "Proteus 8 Professional"
_CANDIDATES = [Path(os.environ.get(key, str(_SYSTEM / folder))) / _VENDOR
               for key, folder in (("ProgramFiles(x86)", "Program Files (x86)"),
                                   ("ProgramFiles", "Program Files"))]
_DATA_CANDIDATES = [Path(os.environ.get("ProgramData", str(_SYSTEM / "ProgramData"))) / _VENDOR]


def home():
    env = os.environ.get("PROTEUS_HOME")
    for root in ([env] if env else []) + _CANDIDATES:
        if root and (Path(root) / "BIN" / "PDS.EXE").exists():
            return Path(root)
    return Path(_CANDIDATES[0])


def data():
    env = os.environ.get("PROTEUS_DATA")
    roots = ([env] if env else []) + [str(home() / "DATA")] + _DATA_CANDIDATES
    for root in roots:
        if root and (Path(root) / "LIBRARY").is_dir():
            return Path(root)
    return home() / "DATA"


EXECUTABLE = home() / "BIN" / "PDS.EXE"
LIBRARY = data() / "LIBRARY"
SAMPLES = data() / "SAMPLES"
