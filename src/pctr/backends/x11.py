"""Linux X11 backend for pctr — AT-SPI2 elements, XTest input, EWMH windows, mss screenshots."""

from __future__ import annotations

import os
import re
import time

from .base import Backend, WindowInfo, ElementInfo, TreeResult
from .input import X11Input
from .ocr import RapidOCRBackend, TesseractOCR
from .atspi import AppWindow, AtspiClient


# --- AT-SPI2 role -> UIA control-type mapping ----------------------------------


_ATOMS = {
    "_NET_WM_NAME": "_NET_WM_NAME",
    "_UTF8_STRING": "UTF8_STRING",
    "_WM_NAME": "WM_NAME",
    "_NET_WM_PID": "_NET_WM_PID",
    "_NET_CLIENT_LIST": "_NET_CLIENT_LIST",
    "_NET_ACTIVE_WINDOW": "_NET_ACTIVE_WINDOW",
    "_NET_CURRENT_DESKTOP": "_NET_CURRENT_DESKTOP",
    "_NET_NUMBER_OF_DESKTOPS": "_NET_NUMBER_OF_DESKTOPS",
    "_NET_DESKTOP_NAMES": "_NET_DESKTOP_NAMES",
    "_NET_WM_DESKTOP": "_NET_WM_DESKTOP",
    "_NET_WM_WINDOW_TYPE": "_NET_WM_WINDOW_TYPE",
    "_NET_WM_STATE": "_NET_WM_STATE",
    "_NET_WM_STATE_MAXIMIZED_HORZ": "_NET_WM_STATE_MAXIMIZED_HORZ",
    "_NET_WM_STATE_MAXIMIZED_VERT": "_NET_WM_STATE_MAXIMIZED_VERT",
}


def _fail(msg, code=2):
    print("error: %s" % msg, file=__import__("sys").stderr)
    raise SystemExit(code)


class X11Backend(Backend):
    """Linux backend: AT-SPI2 elements, XTest input, EWMH windows, mss screenshots.

    Elements come from the accessibility bus, which works under both X11 and
    Wayland. The X connection is optional and only used for input, screenshots
    and window geometry -- on a native Wayland session (no XWayland) those
    raise a clear error while element discovery and AT-SPI actions keep working.

    Windows that exist only on the accessibility bus (native Wayland clients)
    get synthesised negative handles so the rest of the stack, which speaks
    integer handles, keeps working unchanged.
    """

    def __init__(self):
        self._display = None
        self._X = None
        self._root = None
        self._input_obj = None
        self._ocr_engine = None  # lazy
        self._atspi_client = AtspiClient()  # D-Bus AT-SPI (no pyatspi needed)
        self._remote: dict[int, tuple[str, str]] = {}
        self._remote_info: dict[int, AppWindow] = {}
        self._next_remote = -1
        self.last_error: str | None = None
        self._ensure_x(silent=True)
        try:
            import atexit
            atexit.register(self.close)
        except Exception:
            pass

    def close(self) -> None:
        """Release the X connection and the shared accessibility session."""
        try:
            if self._display is not None:
                self._display.close()
        except Exception:
            pass
        self._display = None
        try:
            from .atspi import close_session
            close_session()
        except Exception:
            pass

    # --- X plumbing ---

    def _ensure_x(self, silent: bool = False):
        """Connect to X (XWayland counts); elements do not need it, input does."""
        if self._display is not None:
            return self._display
        try:
            from Xlib import display
            disp = display.Display()
            self._display = disp
            self._X = __import__("Xlib.X", fromlist=["X"])
            self._root = disp.screen().root
            for attr, atom in _ATOMS.items():
                setattr(self, attr, disp.intern_atom(atom))
            return disp
        except Exception as e:
            self._display = None
            if silent:
                return None
            raise SystemExit(
                "no X display available -- input, screenshots and window geometry need "
                "X11/XWayland (%s). Element discovery and AT-SPI actions still work." % e
            )

    @property
    def _input(self):
        if self._input_obj is None:
            self._ensure_x()
            self._input_obj = X11Input()
        return self._input_obj

    @_input.setter
    def _input(self, value):
        self._input_obj = value

    # --- AT-SPI-only windows (Wayland: no XID to key off) ---

    def _remote_handle(self, win: AppWindow) -> int:
        key = (win.bus, win.path)
        for handle, ref in self._remote.items():
            if ref == key:
                self._remote_info[handle] = win
                return handle
        handle = self._next_remote
        self._next_remote -= 1
        self._remote[handle] = key
        self._remote_info[handle] = win
        return handle


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
        if self._ensure_x(silent=True) is None:
            return []
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

    def _x11_windows(self, filter_re=None, process=None):
        """Windows known to the X server (X11 or XWayland clients only)."""
        if self._ensure_x(silent=True) is None:
            return []
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

    def _atspi_windows(self, filter_re=None, process=None):
        """Windows on the accessibility bus that X cannot see (native Wayland)."""
        try:
            wins = self._atspi_client.windows()
            self.last_error = None
        except Exception as e:
            # Remember it: an empty list must not look like an empty desktop.
            self.last_error = "accessibility bus: %s" % e
            return []
        rows = []
        seen: set[tuple[int, str, str]] = set()
        for w in wins:
            if filter_re and filter_re.search(w.title or "") is None and filter_re.search(w.app_name or "") is None:
                continue
            if process is not None and w.pid != process:
                continue
            # Panels and overlays expose several frames with the same label
            # ("plasmashell" x7); identical title+app entries are noise.
            key = (w.pid, w.title, w.app_name)
            if key in seen:
                continue
            seen.add(key)
            rows.append(WindowInfo(handle=self._remote_handle(w), title=w.title or w.app_name,
                                   pid=w.pid, desktop=0))
        return rows

    def list_windows(self, filter_re=None, process=None):
        """X windows plus accessibility-bus windows, de-duplicated by PID.

        Under a Wayland session X only sees XWayland clients, so the AT-SPI
        list is what makes `pctr windows` show the actual desktop.
        """
        rows = self._x11_windows(filter_re, process)
        seen_pids = {w.pid for w in rows if w.pid}
        live: set[int] = set()
        for w in self._atspi_windows(filter_re, process):
            if w.pid and w.pid in seen_pids:
                continue
            rows.append(w)
            live.add(w.handle)
        # Forget synthesised handles for windows that have closed.
        if not filter_re and process is None:
            for handle in list(self._remote):
                if handle not in live:
                    self._remote.pop(handle, None)
                    self._remote_info.pop(handle, None)
        return rows

    def find_window(self, title_re, process=None, timeout=5.0):
        try:
            rx = re.compile(title_re, re.I) if title_re else None
        except re.error as e:
            raise SystemExit("bad --title regex %r: %s" % (title_re, e))
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
        why = getattr(self, "last_error", None)
        raise SystemExit(
            "window not found: title_re=%r process=%r (saw %d match(es))%s" % (
                title_re, process, last, " -- %s" % why if why else ""
            )
        )

    def _remote_of(self, handle) -> AppWindow | None:
        return self._remote_info.get(handle)

    def get_window_title(self, handle):
        win = self._remote_of(handle)
        if win is not None:
            return win.title or win.app_name
        self._ensure_x()
        w = self._display.create_resource_object("window", handle)
        return self._get_window_title(w)

    def get_window_pid(self, handle):
        win = self._remote_of(handle)
        if win is not None:
            return win.pid
        self._ensure_x()
        w = self._display.create_resource_object("window", handle)
        return self._get_window_pid(w)

    def get_window_rect(self, handle):
        win = self._remote_of(handle)
        if win is not None:
            # No XID on Wayland: ask the accessibility bus where the frame is.
            for el, _ in self.get_elements(handle, max_depth=1, limit=1):
                return el.rect
            return (0, 0, 0, 0)
        self._ensure_x()
        w = self._display.create_resource_object("window", handle)
        try:
            geom = w.get_geometry()
            # get_geometry() is parent-relative under a reparenting WM; translate
            # the window origin to root coordinates so screenshots/clicks line up.
            try:
                coords = w.translate_coords(self._root, 0, 0)
                x, y = int(coords.x), int(coords.y)
            except Exception:
                x, y = int(geom.x), int(geom.y)
            return (x, y, x + geom.width, y + geom.height)
        except Exception:
            raise SystemExit("cannot get geometry for window 0x%x" % handle)

    def focus_window(self, handle):
        if self._remote_of(handle) is not None:
            # Wayland: no way to raise another app's window without the
            # compositor's cooperation. Invoking an element still works; say so.
            raise SystemExit(
                "cannot focus a Wayland window by handle -- use element actions "
                "(pctr click --title ...) instead of window focus"
            )
        self._ensure_x()
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

    def _atspi_ref(self, handle):
        """Resolve an integer window handle to an AT-SPI (bus, path) ref."""
        win = self._remote_of(handle)
        if win is not None:
            return win.ref
        title = self.get_window_title(handle)
        pid = self.get_window_pid(handle) or None
        if not title and pid is None:
            return None
        # The window title is data, not a pattern: a title like "Report (final)"
        # would otherwise raise or match the wrong window.
        found = self._atspi_client.resolve_window(title or None, pid, literal=True)
        if found is None and title:
            # window title and accessible frame name often differ slightly
            found = self._atspi_client.resolve_window(title[:40], pid, literal=False)
        return found.ref if found is not None else None

    def get_elements(self, handle, max_depth=99, limit=400):
        """Walk the AT-SPI tree for the window. Returns list of (ElementInfo, depth)."""
        ref = self._atspi_ref(handle)
        if ref is None:
            return []
        rows = self._atspi_client.get_elements(ref, max_depth=max_depth, limit=limit)
        return [(el, el.depth) for el in rows]

    def get_tree(self, handle, max_depth=99, limit=400):
        """Element tree plus "is this app actually automatable?" diagnostics."""
        ref = self._atspi_ref(handle)
        if ref is None:
            return TreeResult(
                elements=[],
                error="window is not on the accessibility bus -- the app may be "
                      "sandboxed, or at-spi2-core is not running",
                backend="atspi",
            )
        return self._atspi_client.get_tree(ref, max_depth=max_depth, limit=limit)

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

    # Control types that actually have a checked/unchecked state. A menu item
    # advertising "press" is NOT one of these: firing it via --toggle would
    # activate whatever it is (including "Delete", "Close Session").
    _TOGGLE_TYPES = {"checkbox", "radiobutton", "switch", "togglebutton",
                     "check box", "toggle button", "radio button", "check menuitem",
                     "check menu item", "radio menu item", "radiomenuitem"}

    def toggle_element(self, handle, el):
        """Check/uncheck a real toggle control (checkbox, radio, switch)."""
        ctype = (el.control_type or "").lower()
        if ctype not in self._TOGGLE_TYPES:
            return False
        ref = el.ref
        if not ref:
            return False
        names = [a.lower() for a in self._atspi_client.action_names(ref)]
        for name in ("toggle", "press", "click"):
            if name in names:
                return self._atspi_client.invoke(ref, name)
        return False

    def action_names(self, handle, el):
        return self._atspi_client.action_names(el.ref) if el.ref else []

    def action(self, handle, el, name):
        return self._atspi_client.invoke(el.ref, name) if el.ref else False

    def get_element_value(self, handle, el):
        return self._atspi_client.get_value(el.ref) if el.ref else None

    def set_element_value(self, handle, el, value):
        if not el.ref:
            return False
        return self._atspi_client.set_value(el.ref, float(value))

    def select_element(self, handle, el, deselect=False):
        return self._atspi_client.select(el.ref, deselect) if el.ref else False

    def element_text(self, handle, el, offset=0):
        return self._atspi_client.text_at(el.ref, offset) if el.ref else ""

    def scroll_element(self, handle, el):
        return self._atspi_client.scroll_to(el.ref) if el.ref else False

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
        """Replace an element's text through AT-SPI EditableText.

        There is deliberately no synthetic-input fallback: on Wayland the click
        never lands, so ctrl+a / delete / type would go to whatever window has
        focus. Blind typing is `pctr type`, and it has to be asked for.
        """
        ref = self._reopen_atspi(handle, el)
        if ref is None:
            raise RuntimeError(
                "element %r (type %s) has no accessible node, so pctr cannot "
                "tell where text would land -- refusing to type blindly"
                % (el.name, el.control_type)
            )
        if "org.a11y.atspi.EditableText" not in (el.interfaces or ()):
            raise RuntimeError(
                "element %r (type %s) is not editable -- it exposes no "
                "EditableText interface" % (el.name, el.control_type)
            )
        if not self._atspi_client.set_text(ref, text):
            raise RuntimeError(
                "element %r advertises EditableText but refused the write" % el.name
            )

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
            # pyautogui/pydirectinput are Windows-only; everything else is XTest.
            # Fail with pctr's own message, not Xlib's, when there is no X.
            self._ensure_x()
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
        if self._ensure_x(silent=True) is None:
            return ["Desktop 0"]
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
        if self._remote_of(handle) is not None:
            return 0
        self._ensure_x()
        win = self._display.create_resource_object("window", handle)
        return self._get_window_desktop(win)

    def move_window_to_desktop(self, handle, desktop_idx):
        if self._remote_of(handle) is not None:
            raise SystemExit("cannot move a Wayland window between desktops (no window handle)")
        self._ensure_x()
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
        if self._ensure_x(silent=True) is None:
            return 0
        try:
            prop = self._root.get_full_property(self._NET_CURRENT_DESKTOP, self._X.AnyPropertyType)
            if prop and prop.value:
                return int(prop.value[0])
        except Exception:
            pass
        return 0

    def switch_desktop(self, action):
        # Send _NET_CURRENT_DESKTOP client message to root
        self._ensure_x()
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
