"""Shared Win32 window/menu helpers for PID-scoped Proteus sessions."""
import argparse
import ctypes as c
import json
from ctypes import wintypes as w

u = c.WinDLL("user32", use_last_error=True)
k = c.WinDLL("kernel32", use_last_error=True)
CALLBACK = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
u.GetWindowThreadProcessId.argtypes = (w.HWND, c.POINTER(w.DWORD))
u.GetWindowTextLengthW.argtypes = (w.HWND,)
u.GetWindowTextW.argtypes = (w.HWND, w.LPWSTR, c.c_int)
u.GetClassNameW.argtypes = (w.HWND, w.LPWSTR, c.c_int)
u.GetDlgCtrlID.argtypes = (w.HWND,)
u.IsWindowVisible.argtypes = (w.HWND,)
u.IsWindowEnabled.argtypes = (w.HWND,)
u.GetMenu.argtypes = (w.HWND,)
u.GetMenu.restype = w.HMENU
u.GetSubMenu.argtypes = (w.HMENU, c.c_int)
u.GetSubMenu.restype = w.HMENU
u.GetMenuItemCount.argtypes = (w.HMENU,)
u.GetMenuItemID.argtypes = (w.HMENU, c.c_int)
u.GetMenuItemID.restype = w.UINT
u.GetMenuStringW.argtypes = (w.HMENU, w.UINT, w.LPWSTR, c.c_int, w.UINT)
u.GetMenuState.argtypes = (w.HMENU, w.UINT, w.UINT)
u.PostMessageW.argtypes = (w.HWND, w.UINT, w.WPARAM, w.LPARAM)
k.OpenProcess.argtypes = (w.DWORD, w.BOOL, w.DWORD)
k.OpenProcess.restype = w.HANDLE
k.QueryFullProcessImageNameW.argtypes = (w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD))
k.CloseHandle.argtypes = (w.HANDLE,)


def windows(pid):
    rows = []

    def describe(hwnd):
        text = c.create_unicode_buffer(u.GetWindowTextLengthW(hwnd) + 1)
        cls = c.create_unicode_buffer(256)
        u.GetWindowTextW(hwnd, text, len(text))
        u.GetClassNameW(hwnd, cls, len(cls))
        return dict(hwnd=hwnd, title=text.value, cls=cls.value,
                    control_id=u.GetDlgCtrlID(hwnd), enabled=bool(u.IsWindowEnabled(hwnd)))

    @CALLBACK
    def visit(hwnd, _):
        owner = w.DWORD()
        u.GetWindowThreadProcessId(hwnd, c.byref(owner))
        if owner.value == pid and u.IsWindowVisible(hwnd):
            row = describe(hwnd)
            children = []

            @CALLBACK
            def child(handle, _):
                if u.IsWindowVisible(handle):
                    children.append(describe(handle))
                return True

            u.EnumChildWindows(hwnd, child, 0)
            row["children"] = children
            rows.append(row)
        return True

    u.EnumWindows(visit, 0)
    if _desktop:
        u.EnumDesktopWindows(_desktop, visit, 0)
    return rows


# --- Hidden desktop -------------------------------------------------------
# Proteus shows its own main window and taskbar button even when started with
# SW_HIDE. Starting it on a separate (never switched-to) desktop of the same
# window station keeps it fully invisible, while PostMessage/SendMessage,
# menus, the clipboard and process-memory reads keep working as usual.
# Set PROTEUS_MCP_VISIBLE=1 to launch on the normal desktop for debugging.
DESKTOP_NAME = "proteus_mcp"
_desktop = None

u.CreateDesktopW.argtypes = (w.LPCWSTR, w.LPCWSTR, c.c_void_p, w.DWORD, w.DWORD, c.c_void_p)
u.CreateDesktopW.restype = w.HANDLE
u.EnumDesktopWindows.argtypes = (w.HANDLE, CALLBACK, w.LPARAM)


def hidden_desktop():
    """Create (or open) the private desktop once and keep its handle alive."""
    global _desktop
    if _desktop is None:
        handle = u.CreateDesktopW(DESKTOP_NAME, None, None, 0, 0x10000000, None)  # GENERIC_ALL
        if not handle:
            raise c.WinError(c.get_last_error())
        _desktop = handle
    return _desktop


class _STARTUPINFO(c.Structure):
    _fields_ = [("cb", w.DWORD), ("lpReserved", w.LPWSTR), ("lpDesktop", w.LPWSTR),
                ("lpTitle", w.LPWSTR), ("dwX", w.DWORD), ("dwY", w.DWORD),
                ("dwXSize", w.DWORD), ("dwYSize", w.DWORD), ("dwXCountChars", w.DWORD),
                ("dwYCountChars", w.DWORD), ("dwFillAttribute", w.DWORD), ("dwFlags", w.DWORD),
                ("wShowWindow", w.WORD), ("cbReserved2", w.WORD), ("lpReserved2", c.c_void_p),
                ("hStdInput", w.HANDLE), ("hStdOutput", w.HANDLE), ("hStdError", w.HANDLE)]


class _PROCESS_INFORMATION(c.Structure):
    _fields_ = [("hProcess", w.HANDLE), ("hThread", w.HANDLE),
                ("dwProcessId", w.DWORD), ("dwThreadId", w.DWORD)]


k.CreateProcessW.argtypes = (w.LPCWSTR, w.LPWSTR, c.c_void_p, c.c_void_p, w.BOOL, w.DWORD,
                             c.c_void_p, w.LPCWSTR, c.POINTER(_STARTUPINFO),
                             c.POINTER(_PROCESS_INFORMATION))
k.WaitForSingleObject.argtypes = (w.HANDLE, w.DWORD)
k.GetExitCodeProcess.argtypes = (w.HANDLE, c.POINTER(w.DWORD))
k.TerminateProcess.argtypes = (w.HANDLE, w.UINT)


class HiddenProcess:
    """Minimal subprocess.Popen look-alike for a process started on the hidden desktop."""

    def __init__(self, args, cwd=None):
        import subprocess
        self.args = [str(a) for a in args]
        info, proc = _STARTUPINFO(), _PROCESS_INFORMATION()
        info.cb = c.sizeof(info)
        info.lpDesktop = DESKTOP_NAME
        info.dwFlags = 1  # STARTF_USESHOWWINDOW
        info.wShowWindow = 0
        cmdline = c.create_unicode_buffer(subprocess.list2cmdline(self.args))
        hidden_desktop()
        if not k.CreateProcessW(None, cmdline, None, None, False, 0, None, cwd,
                                c.byref(info), c.byref(proc)):
            raise c.WinError(c.get_last_error())
        k.CloseHandle(proc.hThread)
        self._handle, self.pid, self.returncode = proc.hProcess, proc.dwProcessId, None

    def poll(self):
        if self.returncode is None and k.WaitForSingleObject(self._handle, 0) == 0:
            code = w.DWORD()
            k.GetExitCodeProcess(self._handle, c.byref(code))
            self.returncode = code.value
        return self.returncode

    def wait(self, timeout=None):
        import subprocess
        ms = 0xFFFFFFFF if timeout is None else int(timeout * 1000)
        if k.WaitForSingleObject(self._handle, ms) != 0:
            raise subprocess.TimeoutExpired(self.args, timeout)
        return self.poll()

    def terminate(self):
        if self.poll() is None:
            k.TerminateProcess(self._handle, 1)

    kill = terminate

    def __del__(self):
        if getattr(self, "_handle", None):
            k.CloseHandle(self._handle)
            self._handle = None


def menus(menu, prefix=""):
    rows = []
    for index in range(u.GetMenuItemCount(menu)):
        text = c.create_unicode_buffer(512)
        u.GetMenuStringW(menu, index, text, len(text), 0x400)
        label = text.value.replace("&", "").split("\t")[0].strip()
        if not label:
            continue
        path = f"{prefix}/{label}" if prefix else label
        sub = u.GetSubMenu(menu, index)
        if sub:
            rows.extend(menus(sub, path))
        else:
            rows.append(dict(path=path, command_id=u.GetMenuItemID(menu, index),
                             enabled=not bool(u.GetMenuState(menu, index, 0x400) & 3)))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pid", type=int)
    # ponytail: labels follow this installation's language; rediscover paths on other versions/locales.
    parser.add_argument("--menu", help="Exact discovered menu path to invoke; default is read-only.")
    args = parser.parse_args()
    process = k.OpenProcess(0x1000, False, args.pid)
    if not process:
        raise c.WinError(c.get_last_error())
    try:
        path = c.create_unicode_buffer(32768)
        size = w.DWORD(len(path))
        if not k.QueryFullProcessImageNameW(process, 0, path, c.byref(size)):
            raise c.WinError(c.get_last_error())
        if path.value.lower().rsplit("\\", 1)[-1] != "pds.exe":
            raise ValueError("The PID must belong to PDS.EXE.")
    finally:
        k.CloseHandle(process)
    rows = windows(args.pid)
    main_windows = [row for row in rows if u.GetMenu(row["hwnd"])]
    if len(main_windows) != 1:
        raise RuntimeError("Expected exactly one Proteus window with a menu.")
    window = main_windows[0]
    entries = menus(u.GetMenu(window["hwnd"]))
    if args.menu:
        matches = [entry for entry in entries if entry["path"] == args.menu]
        if len(matches) != 1 or not matches[0]["enabled"] or not window["enabled"]:
            raise ValueError("Menu is missing, ambiguous, disabled, or blocked by a dialog.")
        if not u.PostMessageW(window["hwnd"], 0x111, matches[0]["command_id"], 0):
            raise c.WinError(c.get_last_error())
        print(json.dumps({"sent": matches[0], "pid": args.pid}))
    else:
        print(json.dumps({"pid": args.pid, "windows": rows, "menus": entries}, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
