"""Linux X11 backend for pctr — AT-SPI2 elements, XTest input, EWMH windows, mss screenshots."""

from __future__ import annotations

import os
import re
import time

from .base import Backend, WindowInfo, ElementInfo
from .input import X11Input
from .ocr import RapidOCRBackend, TesseractOCR
from .atspi import AtspiClient


# --- AT-SPI2 role -> UIA control-type mapping ----------------------------------


def _fail(msg, code=2):
    print("error: %s" % msg, file=__import__("sys").stderr)
    raise SystemExit(code)


class X11Backend(Backend):
    """Linux X11 backend using python-xlib, mss, PIL, and rapidocr/tesseract."""

    def __init__(self):
        from Xlib import display, X
        from Xlib.protocol import rq

        self._display = display.Display()
        self._X = X
        self._root = self._display.screen().root
        self._NET_WM_NAME = self._display.intern_atom("_NET_WM_NAME")
        self._UTF8_STRING = self._display.intern_atom("UTF8_STRING")
        self._WM_NAME = self._display.intern_atom("WM_NAME")
        self._NET_WM_PID = self._display.intern_atom("_NET_WM_PID")
        self._NET_CLIENT_LIST = self._display.intern_atom("_NET_CLIENT_LIST")
        self._NET_ACTIVE_WINDOW = self._display.intern_atom("_NET_ACTIVE_WINDOW")
        self._NET_CURRENT_DESKTOP = self._display.intern_atom("_NET_CURRENT_DESKTOP")
        self._NET_NUMBER_OF_DESKTOPS = self._display.intern_atom("_NET_NUMBER_OF_DESKTOPS")
        self._NET_DESKTOP_NAMES = self._display.intern_atom("_NET_DESKTOP_NAMES")
        self._NET_WM_DESKTOP = self._display.intern_atom("_NET_WM_DESKTOP")
        self._NET_WM_WINDOW_TYPE = self._display.intern_atom("_NET_WM_WINDOW_TYPE")
        self._NET_WM_STATE = self._display.intern_atom("_NET_WM_STATE")
        self._NET_WM_STATE_MAXIMIZED_HORZ = self._display.intern_atom("_NET_WM_STATE_MAXIMIZED_HORZ")
        self._NET_WM_STATE_MAXIMIZED_VERT = self._display.intern_atom("_NET_WM_STATE_MAXIMIZED_VERT")

        self._input = X11Input()
        self._ocr_engine = None  # lazy
        self._atspi_client = AtspiClient()  # D-Bus AT-SPI (no pyatspi needed)

    # --- Window management ---

    def _get_window_title(self, win) -> str:
        # Try _NET_WM_NAME (UTF8) first, then WM_NAME
        try:
            prop = win.get_full_property(self._NET_WM_NAME, self._UTF8_STRING)
            if prop and prop.value:
                return prop.value.decode("utf-8", errors="replace")
        except Exception:
            pass
        try:
            prop = win.get_full_property(self._WM_NAME, self._X.AnyPropertyType)
            if prop and prop.value:
                if isinstance(prop.value, bytes):
                    return prop.value.decode("utf-8", errors="replace")
                return str(prop.value)
        except Exception:
            pass
        return ""

    def _get_window_pid(self, win) -> int:
        try:
            prop = win.get_full_property(self._NET_WM_PID, self._X.AnyPropertyType)
            if prop and prop.value:
                return int(prop.value[0])
        except Exception:
            pass
        return 0

    def _get_window_desktop(self, win) -> int:
        try:
            prop = win.get_full_property(self._NET_WM_DESKTOP, self._X.AnyPropertyType)
            if prop and prop.value:
                return int(prop.value[0])
        except Exception:
            pass
        return 0

    def _is_normal_window(self, win) -> bool:
        """Filter out dock/panel/desktop windows."""
        try:
            win_type = win.get_full_property(self._NET_WM_WINDOW_TYPE, self._X.AnyPropertyType)
            if win_type and win_type.value:
                type_atom = win_type.value[0]
                type_name = self._display.get_atom_name(type_atom)
                # Skip docks, panels, desktop, splash
                skip = {"_NET_WM_WINDOW_TYPE_DOCK", "_NET_WM_WINDOW_TYPE_DESKTOP",
                        "_NET_WM_WINDOW_TYPE_SPLASH", "_NET_WM_WINDOW_TYPE_NOTIFICATION",
                        "_NET_WM_WINDOW_TYPE_TOOLBAR"}
                if type_name in skip:
                    return False
        except Exception:
            pass
        return True

    def _all_windows(self) -> list:
        """Return all managed windows from _NET_CLIENT_LIST."""
        try:
            prop = self._root.get_full_property(self._NET_CLIENT_LIST, self._X.AnyPropertyType)
            if prop and prop.value:
                return list(prop.value)
        except Exception:
            pass
        # Fallback: query tree
        try:
            tree = self._root.query_tree()
            return [w.id for w in tree.children]
        except Exception:
            return []

    def list_windows(self, filter_re=None, process=None):
        rows = []
        for wid in self._all_windows():
            try:
                win = self._display.create_resource_object("window", wid)
                title = self._get_window_title(win)
                pid = self._get_window_pid(win)
                desktop = self._get_window_desktop(win)
                if not title and not pid:
                    continue
                if not self._is_normal_window(win):
                    continue
                if filter_re and filter_re.search(title) is None:
                    continue
                if process is not None and pid != process:
                    continue
                rows.append(WindowInfo(handle=wid, title=title, pid=pid, desktop=desktop))
            except Exception:
                continue
        return rows

    def find_window(self, title_re, process=None, timeout=5.0):
        rx = re.compile(title_re, re.I) if title_re else None
        deadline = time.time() + timeout
        last = 0
        while True:
            matches = [w for w in self.list_windows() if (rx is None or rx.search(w.title)) and (process is None or w.pid == process)]
            last = len(matches)
            if matches:
                return matches[0].handle
            if time.time() >= deadline:
                break
            time.sleep(0.25)
        raise SystemExit(
            "window not found: title_re=%r process=%r (saw %d match(es))" % (title_re, process, last)
        )

    def get_window_title(self, handle):
        win = self._display.create_resource_object("window", handle)
        return self._get_window_title(win)

    def get_window_pid(self, handle):
        win = self._display.create_resource_object("window", handle)
        return self._get_window_pid(win)

    def get_window_rect(self, handle):
        win = self._display.create_resource_object("window", handle)
        try:
            geom = win.get_geometry()
            # get_geometry() is parent-relative under a reparenting WM; translate
            # the window origin to root coordinates so screenshots/clicks line up.
            try:
                coords = win.translate_coords(self._root, 0, 0)
                x, y = int(coords.x), int(coords.y)
            except Exception:
                x, y = int(geom.x), int(geom.y)
            return (x, y, x + geom.width, y + geom.height)
        except Exception:
            raise SystemExit("cannot get geometry for window 0x%x" % handle)

    def focus_window(self, handle):
        win = self._display.create_resource_object("window", handle)
        try:
            # Try _NET_ACTIVE_WINDOW first (EWMH)
            win.change_property(self._NET_ACTIVE_WINDOW, self._X.XA_WINDOW, 32, [handle])
            self._display.sync()
        except Exception:
            pass
        try:
            win.set_input_focus(self._X.RevertToParent, self._X.CurrentTime)
            self._display.sync()
        except Exception:
            pass

    # --- Element tree (AT-SPI2 over D-Bus) ---

    def _atspi_title(self, handle) -> str:
        return self.get_window_title(handle)

    def get_elements(self, handle, max_depth=99, limit=400):
        """Walk the AT-SPI tree for the window. Returns list of (ElementInfo, depth)."""
        title = self._atspi_title(handle)
        if not title:
            return []
        rows = self._atspi_client.get_elements(title, max_depth=max_depth, limit=limit)
        return [(el, el.depth) for el in rows]

    def _reopen_atspi(self, handle, el):
        """Re-resolve an element ref by matching name+rect in the window tree."""
        return el.ref

    def find_elements(self, handle, name=None, control_type=None, auto_id=None):
        rx_name = re.compile(name, re.I) if name is not None else None
        out = []
        for el, _ in self.get_elements(handle):
            if rx_name is not None and rx_name.search(el.name) is None:
                continue
            if control_type is not None and el.control_type.lower() != control_type.lower():
                continue
            if auto_id is not None and el.automation_id != auto_id:
                continue
            out.append(el)
        return out

    # --- Element actions ---

    def click_element(self, handle, el, button="left", dbl=False):
        l, t, r, b = el.rect
        cx = (l + r) // 2
        cy = (t + b) // 2
        self._input.move_to(cx, cy)
        self._input.mouse_down(button)
        self._input.mouse_up(button)
        if dbl:
            time.sleep(0.05)
            self._input.mouse_down(button)
            self._input.mouse_up(button)

    def invoke_element(self, handle, el):
        # Try AT-SPI action first
        ref = self._reopen_atspi(handle, el)
        if ref and self._atspi_client.invoke(ref):
            return True
        # Fall back to a synthetic click at the element center
        self.click_element(handle, el)
        return True

    def set_element_text(self, handle, el, text):
        # Prefer AT-SPI EditableText (no focus/cursor fiddling)
        ref = self._reopen_atspi(handle, el)
        if ref and self._atspi_client.set_text(ref, text):
            return
        l, t, r, b = el.rect
        self._input.move_to((l + r) // 2, (t + b) // 2)
        self._input.mouse_down("left")
        self._input.mouse_up("left")
        time.sleep(0.05)
        # Select all and delete
        self._input.hotkey("ctrl", "a")
        time.sleep(0.02)
        self._input.key_down("delete")
        self._input.key_up("delete")
        time.sleep(0.02)
        self._input.write(text, interval=0.01)

    def type_element_text(self, handle, el, text, delay=0.0):
        # Focus the element, then APPEND literal text (do not replace).
        l, t, r, b = el.rect
        self._input.move_to((l + r) // 2, (t + b) // 2)
        self._input.mouse_down("left")
        self._input.mouse_up("left")
        time.sleep(0.05)
        self._input.write(text, interval=delay)

    # --- Input (global) ---

    def set_input(self, name=None, port=None):
        from .input import X11Input, SerialHID
        n = (name or "auto").lower()
        if n in ("serial", "serial-hid", "hid"):
            self._input = SerialHID(port)
        else:
            # pyautogui/pydirectinput are Windows-only; everything else is XTest
            self._input = X11Input()

    def write_text(self, text, interval=0.0):
        self._input.write(text, interval=interval)

    def move_mouse(self, x, y, duration=0.0):
        self._input.move_to(x, y, duration=duration)

    def mouse_down(self, button="left"):
        self._input.mouse_down(button=button)

    def mouse_up(self, button="left"):
        self._input.mouse_up(button=button)

    def key_down(self, key):
        self._input.key_down(key)

    def key_up(self, key):
        self._input.key_up(key)

    def send_keys(self, keys):
        """Parse {ENTER}, ^s, +shift, %alt syntax -> XTest key sequence."""
        # Tokenize: handle {KEY}, ^ctrl, +shift, %alt, literal text
        i = 0
        while i < len(keys):
            ch = keys[i]
            if ch == "{" and "}" in keys[i:]:
                j = keys.index("}", i)
                key = keys[i + 1:j]
                self._input.key_down(key)
                self._input.key_up(key)
                i = j + 1
            elif ch == "^":
                # ctrl
                if i + 1 < len(keys):
                    self._input.key_down("ctrl")
                    self._input.key_down(keys[i + 1])
                    self._input.key_up(keys[i + 1])
                    self._input.key_up("ctrl")
                i += 2
            elif ch == "+":
                # shift
                if i + 1 < len(keys):
                    self._input.key_down("shift")
                    self._input.key_down(keys[i + 1])
                    self._input.key_up(keys[i + 1])
                    self._input.key_up("shift")
                i += 2
            elif ch == "%":
                # alt
                if i + 1 < len(keys):
                    self._input.key_down("alt")
                    self._input.key_down(keys[i + 1])
                    self._input.key_up(keys[i + 1])
                    self._input.key_up("alt")
                i += 2
            elif ch == "~":
                self._input.key_down("return")
                self._input.key_up("return")
                i += 1
            elif ch == "(":
                # Group: (abc) — type literal until )
                j = keys.find(")", i)
                if j == -1:
                    j = len(keys)
                literal = keys[i + 1:j]
                self._input.write(literal, interval=0.01)
                i = j + 1
            else:
                self._input.write(ch, interval=0.01)
                i += 1

    # --- Screen ---

    def screen_size(self):
        import mss
        with mss.mss() as sct:
            mon = sct.monitors[0]
            return (mon["width"], mon["height"])

    def screenshot(self, region=None):
        import mss
        from PIL import Image
        img = None
        try:
            with mss.mss() as sct:
                if region:
                    l, t, r, b = region
                    monitor = {"top": t, "left": l, "width": r - l, "height": b - t}
                else:
                    monitor = sct.monitors[0]
                raw = sct.grab(monitor)
                img = Image.frombytes("RGB", raw.size, raw.rgb)
        except Exception:
            img = None
        # mss reads the XWayland root: under Wayland it usually comes back all
        # black. Detect that and fall back to the compositor's native capture.
        if img is not None and img.getbbox() is not None:
            return img
        fb = self._wayland_screenshot()
        if fb is not None:
            return fb.crop(region) if region else fb
        return img

    def _wayland_screenshot(self):
        """Full-screen capture via the compositor (Wayland), returns PIL.Image or None."""
        import tempfile
        import time as _time
        from PIL import Image

        if not os.environ.get("WAYLAND_DISPLAY"):
            return None

        def _try_spectacle(path):
            import shutil
            import subprocess
            if not shutil.which("spectacle"):
                return False
            try:
                subprocess.run(
                    ["spectacle", "-b", "-n", "-f", "-o", path],
                    capture_output=True, timeout=20,
                )
            except Exception:
                return False
            for _ in range(40):
                if os.path.exists(path) and os.path.getsize(path) > 0:
                    return True
                _time.sleep(0.1)
            return os.path.exists(path) and os.path.getsize(path) > 0

        def _try_grim(path):
            import shutil
            import subprocess
            if not shutil.which("grim"):
                return False
            try:
                return subprocess.run(["grim", path], capture_output=True, timeout=20).returncode == 0
            except Exception:
                return False

        path = os.path.join(tempfile.gettempdir(), "pctr_wayland_shot_%d.png" % os.getpid())
        try:
            if _try_spectacle(path) or _try_grim(path):
                return Image.open(path).convert("RGB")
        except Exception:
            pass
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        return None

    # --- OCR ---

    def _get_ocr(self):
        if self._ocr_engine is None:
            # RapidOCRBackend imports lazily, so probe the import here to decide
            # which engine actually works.
            try:
                import rapidocr_onnxruntime  # noqa: F401
                self._ocr_engine = RapidOCRBackend()
            except Exception:
                self._ocr_engine = TesseractOCR()
        return self._ocr_engine

    def ocr(self, image, lang=None):
        engine = self._get_ocr()
        if isinstance(engine, TesseractOCR) and lang:
            return engine.ocr(image, lang=lang)
        return engine.ocr(image)

    # --- Virtual desktops ---

    def list_desktops(self):
        try:
            prop = self._root.get_full_property(self._NET_DESKTOP_NAMES, self._UTF8_STRING)
            if prop and prop.value:
                names = prop.value.split(b"\x00")
                return [n.decode("utf-8", errors="replace") for n in names if n]
        except Exception:
            pass
        try:
            prop = self._root.get_full_property(self._NET_NUMBER_OF_DESKTOPS, self._X.AnyPropertyType)
            if prop and prop.value:
                n = int(prop.value[0])
                return ["Desktop %d" % i for i in range(n)]
        except Exception:
            pass
        return ["Desktop 0"]

    def get_window_desktop(self, handle):
        win = self._display.create_resource_object("window", handle)
        return self._get_window_desktop(win)

    def move_window_to_desktop(self, handle, desktop_idx):
        win = self._display.create_resource_object("window", handle)
        # Send _NET_WM_DESKTOP client message
        from Xlib.protocol import event
        ev = event.ClientMessage(
            window=win,
            client_type=self._NET_WM_DESKTOP,
            data=(32, [desktop_idx, 0, 0, 0, 0]),
        )
        self._root.send_event(ev, event_mask=self._X.SubstructureRedirectMask | self._X.SubstructureNotifyMask)
        self._display.sync()

    def current_desktop(self):
        try:
            prop = self._root.get_full_property(self._NET_CURRENT_DESKTOP, self._X.AnyPropertyType)
            if prop and prop.value:
                return int(prop.value[0])
        except Exception:
            pass
        return 0

    def switch_desktop(self, action):
        # Send _NET_CURRENT_DESKTOP client message to root
        from Xlib.protocol import event
        if action == "next":
            cur = self.current_desktop()
            total = len(self.list_desktops())
            target = (cur + 1) % total
        elif action == "prev":
            cur = self.current_desktop()
            total = len(self.list_desktops())
            target = (cur - 1) % total
        elif action == "new":
            # Request a new desktop (not all WMs support this)
            target = len(self.list_desktops())
        elif action == "close":
            cur = self.current_desktop()
            target = max(0, cur - 1)
        else:
            _fail("unknown desktop action %r" % action)

        ev = event.ClientMessage(
            window=self._root,
            client_type=self._NET_CURRENT_DESKTOP,
            data=(32, [target, 0, 0, 0, 0]),
        )
        self._root.send_event(ev, event_mask=self._X.SubstructureRedirectMask | self._X.SubstructureNotifyMask)
        self._display.sync()
