"""Windows backend for pctr — wraps pywinauto / UIA / Win32 / COM virtual desktops.

This is a pure code-move from pctr 0.4.0 cli.py: no behavior changes.
"""

from __future__ import annotations

import ctypes
import re
import sys
import time
import uuid
from ctypes import wintypes
from typing import Any, NoReturn, cast

from .base import Backend, WindowInfo, ElementInfo, OCRWord
from .input import WindowsInput
from .ocr import WindowsOCR


def _fail(msg, code=2) -> NoReturn:
    print("error: %s" % msg, file=sys.stderr)
    raise SystemExit(code)


def _compile(pattern, what, flags=re.I):
    try:
        return re.compile(pattern, flags)
    except re.error as e:
        _fail("invalid %s regex %r: %s" % (what, pattern, e))


class WindowsBackend(Backend):
    def __init__(self):
        from pywinauto import Desktop
        self._Desktop = Desktop
        self._ocr = WindowsOCR()

    # --- Window management ---

    def list_windows(self, filter_re=None, process=None):
        rows = []
        for hwnd, title, pid in self._enum_top_windows():
            if filter_re and filter_re.search(title) is None:
                continue
            if process is not None and pid != process:
                continue
            rows.append(WindowInfo(handle=hwnd, title=title, pid=pid))
        return rows

    def find_window(self, title_re, process=None, timeout=5.0):
        return self._find_hwnd(title_re, process, timeout)

    def get_window_title(self, handle):
        win = self._Desktop(backend="uia").window(handle=handle)
        return win.window_text()

    def get_window_pid(self, handle):
        win = self._Desktop(backend="uia").window(handle=handle)
        return int(win.process_id())

    def get_window_rect(self, handle):
        win = self._Desktop(backend="uia").window(handle=handle)
        r = win.rectangle()
        return (r.left, r.top, r.right, r.bottom)

    def focus_window(self, handle):
        win = self._Desktop(backend="uia").window(handle=handle)
        win.set_focus()

    # --- Element tree ---

    def get_elements(self, handle, max_depth=99, limit=400):
        win = self._Desktop(backend="uia").window(handle=handle)
        rows = []
        truncated = False
        for el, d in self._walk(win, max_depth):
            try:
                info = el.element_info
            except Exception:
                continue
            r = info.rectangle
            rows.append((ElementInfo(
                name=info.name,
                control_type=info.control_type or "",
                automation_id=info.automation_id or "",
                rect=(r.left, r.top, r.right, r.bottom),
                depth=d,
            ), d))
            if len(rows) >= limit:
                truncated = True
                break
        return rows

    def find_elements(self, handle, name=None, control_type=None, auto_id=None):
        win = self._Desktop(backend="uia").window(handle=handle)
        rx_name = _compile(name, "name") if name is not None else None
        out = []
        for el in win.descendants():
            try:
                info = el.element_info
            except Exception:
                continue
            if rx_name is not None:
                n = info.name or ""
                if rx_name.search(n) is None:
                    continue
            if control_type is not None:
                c = (info.control_type or "")
                if c.lower() != control_type.lower():
                    continue
            if auto_id is not None:
                a = (info.automation_id or "")
                if a != auto_id:
                    continue
            r = info.rectangle
            out.append(ElementInfo(
                name=info.name,
                control_type=info.control_type or "",
                automation_id=info.automation_id or "",
                rect=(r.left, r.top, r.right, r.bottom),
            ))
        return out

    # --- Element actions ---

    def click_element(self, handle, el, button="left", dbl=False):
        win = self._Desktop(backend="uia").window(handle=handle)
        # Re-find the element by matching its properties
        target = None
        for e in win.descendants():
            try:
                info = e.element_info
            except Exception:
                continue
            r = info.rectangle
            if (r.left, r.top, r.right, r.bottom) == el.rect and \
               (info.name or "") == el.name and \
               (info.control_type or "") == el.control_type:
                target = e
                break
        if target is None:
            raise SystemExit("element not found for click")
        target.click_input(button=button)
        if dbl:
            target.click_input(button=button)

    def invoke_element(self, handle, el):
        win = self._Desktop(backend="uia").window(handle=handle)
        for e in win.descendants():
            try:
                info = e.element_info
            except Exception:
                continue
            r = info.rectangle
            if (r.left, r.top, r.right, r.bottom) == el.rect and \
               (info.name or "") == el.name:
                try:
                    e.invoke()
                    return True
                except Exception:
                    return False
        return False

    def set_element_text(self, handle, el, text):
        win = self._Desktop(backend="uia").window(handle=handle)
        for e in win.descendants():
            try:
                info = e.element_info
            except Exception:
                continue
            r = info.rectangle
            if (r.left, r.top, r.right, r.bottom) == el.rect and \
               (info.name or "") == el.name:
                try:
                    e.set_edit_text(text)
                    return
                except Exception:
                    pass
                # fallback: click, select all, type
                e.click_input()
                e.type_keys("^a{BACKSPACE}")
                e.type_keys(text, with_spaces=True, with_newlines=True)
                return
        raise SystemExit("element not found for set")

    def type_element_text(self, handle, el, text, delay=0.0):
        # Element-level typing uses pywinauto's type_keys via set path
        self.set_element_text(handle, el, text)

    # --- Input (global) ---

    def _input_backend(self, name=None, port=None):
        return WindowsInput(name or "auto", port)

    def move_mouse(self, x, y, duration=0.0):
        m = self._input_backend()
        m.move_to(x, y, duration=duration)

    def mouse_down(self, button="left"):
        m = self._input_backend()
        m.mouse_down(button=button)

    def mouse_up(self, button="left"):
        m = self._input_backend()
        m.mouse_up(button=button)

    def key_down(self, key):
        m = self._input_backend()
        m.key_down(key)

    def key_up(self, key):
        m = self._input_backend()
        m.key_up(key)

    def send_keys(self, keys):
        from pywinauto.keyboard import send_keys
        send_keys(keys, with_spaces=True)

    # --- Screen ---

    def screen_size(self):
        import pyautogui
        return pyautogui.size()

    def screenshot(self, region=None):
        import pyautogui
        if region:
            return pyautogui.screenshot(region=region)
        return pyautogui.screenshot()

    # --- OCR ---

    def ocr(self, image, lang=None):
        # image is a PIL Image; WindowsOCR expects a path
        import os
        import tempfile
        path = os.path.join(tempfile.gettempdir(), "pctr", "ocr_win_%d.png" % os.getpid())
        os.makedirs(os.path.dirname(path), exist_ok=True)
        image.save(path)
        try:
            return self._ocr._run(path, lang)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    # --- Virtual desktops ---

    def list_desktops(self):
        return self._vdesk_guids()

    def get_window_desktop(self, handle):
        guids = self._vdesk_guids()
        vdm = self._vdm()
        hwnd = wintypes.HWND(int(handle))
        try:
            d = self._guid_norm(vdm.GetWindowDesktopId(hwnd))
        except Exception:
            return -1
        return guids.index(d) if d in guids else -1

    def move_window_to_desktop(self, handle, desktop_idx):
        import comtypes
        guids = self._vdesk_guids()
        n = desktop_idx
        if n < 0 or n >= len(guids):
            _fail("desktop #%d out of range (0-%d)" % (n, len(guids) - 1))
        vdm = self._vdm()
        hwnd = wintypes.HWND(int(handle))
        try:
            vdm.MoveWindowToDesktop(hwnd, comtypes.GUID("{" + guids[n] + "}"))
        except Exception as e:
            msg = str(e).lower()
            if "denied" in msg or "0x80070005" in msg or "access" in msg:
                _fail(
                    "Windows 11 blocks third-party MoveWindowToDesktop (Access denied). "
                    "read (list/where/windows) and UIA actions still work cross-desktop; "
                    "moving windows between desktops works on Windows 10."
                )
            _fail("move-window failed: %s" % e)

    def current_desktop(self):
        import ctypes as _ct
        vdm = self._vdm()
        fg = _ct.windll.user32.GetForegroundWindow()
        try:
            cur = self._guid_norm(vdm.GetWindowDesktopId(wintypes.HWND(fg)))
        except Exception:
            return 0
        guids = self._vdesk_guids()
        return guids.index(cur) if cur in guids else 0

    def switch_desktop(self, action):
        combos = {
            "next": ("win", "ctrl", "right"),
            "prev": ("win", "ctrl", "left"),
            "new": ("win", "ctrl", "d"),
            "close": ("win", "ctrl", "f4"),
        }
        combo = combos[action]
        import pyautogui
        pyautogui.FAILSAFE = False
        pyautogui.hotkey(*combo)

    # --- Internal helpers (moved from cli.py) ---

    def _enum_top_windows(self):
        user32 = ctypes.windll.user32
        out = []
        proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def cb(hwnd, lp):
            n = user32.GetWindowTextLengthW(hwnd)
            if n and user32.IsWindowVisible(hwnd):
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                out.append((int(hwnd), buf.value, int(pid.value)))
            return True

        user32.EnumWindows(proc(cb), 0)
        return out

    def _find_hwnd(self, title_re, process=None, timeout=5.0):
        rx = _compile(title_re, "title") if title_re else None
        deadline = time.time() + timeout
        last = 0
        while True:
            matches = [
                w for w in self._enum_top_windows()
                if (rx is None or rx.search(w[1])) and (process is None or w[2] == process)
            ]
            last = len(matches)
            if matches:
                return matches[0][0]
            if time.time() >= deadline:
                break
            time.sleep(0.25)
        raise SystemExit(
            "window not found: title_re=%r process=%r (saw %d match(es))" % (title_re, process, last)
        )

    def _walk(self, root, max_depth):
        stack = []
        try:
            kids = root.children()
        except Exception:
            kids = []
        for k in reversed(kids):
            stack.append((k, 1))
        while stack:
            node, d = stack.pop()
            yield node, d
            if d >= max_depth:
                continue
            try:
                kids = node.children()
            except Exception:
                kids = []
            for k in reversed(kids):
                stack.append((k, d + 1))

    def _vdm(self) -> Any:
        from ctypes import POINTER
        from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

        class IVirtualDesktopManager(IUnknown):
            _iid_ = GUID("{A5CD92FF-29BE-454C-8D04-D82879FB3F1B}")
            _methods_ = [
                COMMETHOD([], HRESULT, "IsWindowOnCurrentVirtualDesktop",
                          (["in"], wintypes.HWND, "w"), (["out"], POINTER(wintypes.BOOL), "on")),
                COMMETHOD([], HRESULT, "GetWindowDesktopId",
                          (["in"], wintypes.HWND, "w"), (["out"], POINTER(GUID), "did")),
                COMMETHOD([], HRESULT, "MoveWindowToDesktop",
                          (["in"], wintypes.HWND, "w"), (["in"], POINTER(GUID), "did")),
            ]

        return cast(Any, comtypes.CoCreateInstance(
            GUID("{AA509086-5CA9-4C25-8F95-589D3C07B48A}"),
            interface=IVirtualDesktopManager,
        ))

    def _guid_norm(self, g):
        return str(g).strip("{}").lower()

    def _vdesk_guids(self):
        import winreg
        k = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\VirtualDesktops",
        )
        try:
            raw = winreg.QueryValueEx(k, "VirtualDesktopIDs")[0]
        finally:
            winreg.CloseKey(k)
        return [_guid_norm(uuid.UUID(bytes_le=raw[i:i + 16])) for i in range(0, len(raw) - 15, 16)]
