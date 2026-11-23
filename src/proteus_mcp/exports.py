"""Schematic image export (SVG/PDF/PNG) and Electrical Rules Check through Proteus's own dialogs.

Everything runs in the live hidden Proteus session (``open_in_proteus``). The dialogs are driven by
control IDs observed on Proteus 8.13: no screen capture, no clipboard.
"""
import ctypes as c
import struct
import time
import zlib
from pathlib import Path

from .engine import proteus_native as native

BM_GETCHECK, BM_CLICK, WM_COMMAND = 0xF0, 0xF5, 0x111

# format -> (menu path, main dialog title, file dialog title, scope control, viewer checkbox)
_EXPORTS = {
    "svg": ("File/Export Graphics/Export SVG File", "Export SVG File", "Export SVG File To", 770, 1792),
    "pdf": ("File/Export Graphics/Export Adobe PDF File", "Export PDF File", "Export PDF File To", 771, 1792),
    "bmp": ("File/Export Graphics/Export Bitmap", "Export Bitmap", None, 514, None),
}


def _child(dialog, control_id):
    found = [x for x in dialog["children"] if x["control_id"] == control_id]
    if len(found) != 1:
        raise RuntimeError(f"Proteus dialog control {control_id} not found in {dialog['title']!r}")
    return found[0]


def _checked(hwnd):
    result = c.c_size_t()
    native.u.SendMessageTimeoutW(hwnd, BM_GETCHECK, 0, 0, 2, 3000, c.byref(result))
    return result.value == 1


def _click(hwnd):
    if not native.u.PostMessageW(hwnd, BM_CLICK, 0, 0):
        raise c.WinError(c.get_last_error())
    time.sleep(.2)


def _set_check(dialog, control_id, state):
    hwnd = _child(dialog, control_id)["hwnd"]
    if _checked(hwnd) != state:
        _click(hwnd)


def _wait_file(session, path, before, timeout=60):
    def done():
        if path.exists() and path.stat().st_size > 0 and path.stat().st_mtime_ns != before:
            size = path.stat().st_size
            time.sleep(.3)
            return path.stat().st_size == size and session._ready()
        return None
    session._wait(done, timeout=timeout)


def export_graphics(session, destination, fmt="svg", all_sheets=False, fit=True, dpi=100):
    """Export the current sheet (PDF: optionally all sheets) to ``destination``.

    fit=True selects every object first and exports the marked area, so parts placed outside the
    sheet border (imported designs) are still in the picture.
    PNG is produced from Proteus's bitmap export (Display colours, ``dpi`` 100/200/300) converted
    in-process; 100 DPI keeps an A0 sheet near 4700 px wide.
    Returns the written path.
    """
    fmt = fmt.lower()
    destination = Path(destination).resolve()
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "png":
        bmp = destination.with_suffix(".tmp.bmp")
        try:
            _export(session, "bmp", bmp, False, fit, dpi)
            destination.write_bytes(bmp_to_png(bmp.read_bytes()))
        finally:
            bmp.unlink(missing_ok=True)
        return destination
    if fmt not in ("svg", "pdf"):
        raise ValueError("format must be svg, pdf or png")
    _export(session, fmt, destination, all_sheets, fit, 100)
    return destination


def _export(session, fmt, destination, all_sheets, fit, dpi):
    menu, title, file_title, scope, viewer = _EXPORTS[fmt]
    marked = fit and not (all_sheets and fmt == "pdf")
    if marked:
        session.menu("Edit/Select All Objects")
        time.sleep(.5)
    session.menu(menu)
    dialog = session._dialog(title)
    if fmt == "bmp":
        _click(_child(dialog, 512 if marked else 514)["hwnd"])    # Marked Area / Current Sheet
        _click(_child(dialog, {100: 1024, 200: 1025, 300: 1026}[int(dpi)])["hwnd"])  # resolution
        _click(_child(dialog, 1539)["hwnd"])   # Display colours
        _set_check(dialog, 3584, True)          # Output To File
        _click(_child(dialog, 157)["hwnd"])
        session._file_dialog("Export Bitmap To", destination)
    else:
        _click(_child(dialog, 768 if marked else (771 if (all_sheets and fmt == "pdf") else 770))["hwnd"])
        _set_check(dialog, 3072, True)           # Output To File
        _set_check(dialog, 1280, True)           # Colour output
        _set_check(dialog, viewer, False)        # never launch a viewer
        _click(_child(dialog, 157)["hwnd"])
        session._file_dialog(file_title, destination)
    dialog = session._dialog(title)
    if fmt != "bmp":
        _set_check(dialog, viewer, False)
    native.u.PostMessageW(dialog["hwnd"], WM_COMMAND, 1, 0)
    _wait_file(session, destination, None)


def bmp_to_png(data):
    """Convert an uncompressed 24/32-bit (or 8-bit palettised) Windows BMP to PNG bytes."""
    if data[:2] != b"BM":
        raise ValueError("Not a BMP file")
    offset, = struct.unpack_from("<I", data, 10)
    header_size, width, height, _, bpp, compression = struct.unpack_from("<IiiHHI", data, 14)
    if compression not in (0, 3) or bpp not in (8, 24, 32):
        raise ValueError(f"Unsupported BMP ({bpp} bpp, compression {compression})")
    flip = height > 0
    height = abs(height)
    stride = (width * bpp + 31) // 32 * 4
    palette = []
    if bpp == 8:
        colours, = struct.unpack_from("<I", data, 46)
        base = 14 + header_size
        palette = [data[base + 4 * i: base + 4 * i + 3][::-1] for i in range(colours or 256)]
    rows = []
    for y in range(height):
        row = data[offset + (height - 1 - y if flip else y) * stride:][:stride]
        if bpp == 8:
            pixels = b"".join(palette[i] for i in row[:width])
        else:
            step = bpp // 8
            pixels = bytearray(width * 3)
            pixels[0::3] = row[2:width * step:step]
            pixels[1::3] = row[1:width * step:step]
            pixels[2::3] = row[0:width * step:step]
        rows.append(b"\x00" + bytes(pixels))

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 6)) + chunk(b"IEND", b""))


def erc(session, report_path):
    """Run Proteus's Electrical Rules Check, save its report to ``report_path`` and parse it."""
    report_path = Path(report_path).resolve()
    if report_path.exists():
        raise FileExistsError(report_path)
    session.menu("Tool/Electrical Rules Check")

    def window():
        rows = [r for r in native.windows(session.pid) if r["title"].startswith("ELECTRICAL RULES CHECK")]
        return rows[0] if len(rows) == 1 else None
    report = session._wait(window, timeout=60)
    save = [x for x in report["children"] if x["control_id"] == 144]
    if len(save) != 1:
        raise RuntimeError("ERC window has no Save As button")
    _click(save[0]["hwnd"])
    file_dialog = session._wait(lambda: next((r for r in native.windows(session.pid)
                                               if r["cls"] == "#32770" and r["enabled"]
                                               and any(x["control_id"] == 1148 for x in r["children"])), None))
    session._file_dialog(file_dialog["title"], report_path)
    session._wait(lambda: report_path.exists() and report_path.stat().st_size > 0, timeout=30)
    time.sleep(.3)
    close = [x for x in session._wait(window)["children"] if x["control_id"] == 145]
    if close:
        _click(close[0]["hwnd"])
    session._wait(session._ready)
    return parse_erc(report_path.read_text(encoding="cp1252", errors="replace"))


def parse_erc(text):
    """Split an ERC report into errors/warnings; keep the raw text for anything unrecognised."""
    lines = [line.rstrip() for line in text.splitlines()]
    errors = [x.strip() for x in lines if x.strip().upper().startswith("ERROR")]
    warnings = [x.strip() for x in lines if x.strip().upper().startswith("WARNING")]
    return {"errors": errors, "warnings": warnings, "ok": not errors, "text": text}


SHEET_SIZES = {"A4": 256, "A3": 257, "A2": 258, "A1": 259, "A0": 260}


def set_sheet_size(session, size):
    """Select the current sheet's size (A4..A0) via System > Set Sheet Sizes.

    Only the radio choice for this sheet changes; the size definitions (a Proteus-wide setting)
    are left untouched."""
    size = size.upper()
    if size not in SHEET_SIZES:
        raise ValueError(f"size must be one of {sorted(SHEET_SIZES)}")
    session.menu("System/Set Sheet Sizes")
    dialog = session._dialog("Sheet Size Configuration")
    _click(_child(dialog, SHEET_SIZES[size])["hwnd"])
    native.u.PostMessageW(dialog["hwnd"], WM_COMMAND, 1, 0)
    session._wait(session._ready)
    time.sleep(.5)
    return size
