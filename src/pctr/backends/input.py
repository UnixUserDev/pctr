"""Input backends for pctr — Windows (pyautogui/pydirectinput) and Linux X11 (XTest)."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod


class InputBackend(ABC):
    """Synthetic input backend."""

    @abstractmethod
    def move_to(self, x: int, y: int, duration: float = 0.0) -> None: ...

    @abstractmethod
    def mouse_down(self, button: str = "left") -> None: ...

    @abstractmethod
    def mouse_up(self, button: str = "left") -> None: ...

    @abstractmethod
    def key_down(self, key: str) -> None: ...

    @abstractmethod
    def key_up(self, key: str) -> None: ...

    @abstractmethod
    def write(self, text: str, interval: float = 0.0) -> None: ...

    @abstractmethod
    def hotkey(self, *keys: str) -> None: ...


class SerialHID(InputBackend):
    """Raw input via a microcontroller presenting a REAL USB HID device.

    Talks newline-terminated ASCII over a serial port. Firmware protocol:
      MOVE x y ; CLICK b ; DOWN b ; UP b ; KEY k DOWN|UP ; WRITE text ; HOTKEY a+b
    Port from --port or the PCTR_SERIAL_PORT env var.
    """

    def __init__(self, port=None, baud=115200):
        try:
            import serial
        except Exception as e:
            raise RuntimeError('serial-hid needs pyserial: pip install "pctr[serial]" (%s)' % e)
        port = port or __import__("os").environ.get("PCTR_SERIAL_PORT")
        if not port:
            raise RuntimeError("serial-hid needs a port: pass --port /dev/ttyUSB0 or set PCTR_SERIAL_PORT")
        self._ser = serial.Serial(port, baud, timeout=1)

    def _send(self, line):
        self._ser.write((line + "\n").encode("utf-8"))

    def move_to(self, x, y, duration=0.0):
        self._send("MOVE %d %d" % (int(x), int(y)))

    def mouse_down(self, button="left"):
        self._send("DOWN %s" % button)

    def mouse_up(self, button="left"):
        self._send("UP %s" % button)

    def key_down(self, key):
        self._send("KEY %s DOWN" % key)

    def key_up(self, key):
        self._send("KEY %s UP" % key)

    def write(self, text, interval=0.0):
        self._send("WRITE %s" % text.replace("\r", "").replace("\n", "\\n"))

    def hotkey(self, *keys):
        self._send("HOTKEY %s" % "+".join(keys))


class WindowsInput(InputBackend):
    """Windows input via pyautogui / pydirectinput."""

    def __init__(self, backend: str = "auto", port: str | None = None):
        self._backend_name = backend
        self._port = port
        self._mod = None
        self._resolve()

    def _resolve(self):
        name = (self._backend_name or "auto").lower()
        if name in ("", "auto", "pyautogui", "sendinput"):
            import pyautogui
            pyautogui.FAILSAFE = False
            pyautogui.PAUSE = 0.01
            self._mod = pyautogui
            return
        if name in ("pydirectinput", "direct"):
            import pydirectinput
            pydirectinput.FAILSAFE = False
            try:
                pydirectinput.PAUSE = 0.01
            except Exception:
                pass
            self._mod = pydirectinput
            return
        raise RuntimeError(
            "unknown Windows input backend %r (pyautogui | pydirectinput)" % name
        )

    def move_to(self, x: int, y: int, duration: float = 0.0) -> None:
        self._mod.moveTo(x, y, duration=duration)

    def mouse_down(self, button: str = "left") -> None:
        self._mod.mouseDown(button=button)

    def mouse_up(self, button: str = "left") -> None:
        self._mod.mouseUp(button=button)

    def key_down(self, key: str) -> None:
        self._mod.keyDown(key)

    def key_up(self, key: str) -> None:
        self._mod.keyUp(key)

    def write(self, text: str, interval: float = 0.0) -> None:
        self._mod.write(text, interval=interval)

    def hotkey(self, *keys: str) -> None:
        self._mod.hotkey(*keys)


class X11Input(InputBackend):
    """Linux X11 input via XTest extension."""

    # Key name -> keysym mapping for common keys
    _KEYSYM_MAP = {
        "enter": "Return", "return": "Return",
        "tab": "Tab", "space": "space", "spacebar": "space",
        "backspace": "BackSpace", "delete": "Delete",
        "escape": "Escape", "esc": "Escape",
        "up": "Up", "down": "Down", "left": "Left", "right": "Right",
        "home": "Home", "end": "End", "pageup": "Prior", "pagedown": "Next",
        "insert": "Insert", "menu": "Menu",
        "f1": "F1", "f2": "F2", "f3": "F3", "f4": "F4",
        "f5": "F5", "f6": "F6", "f7": "F7", "f8": "F8",
        "f9": "F9", "f10": "F10", "f11": "F11", "f12": "F12",
        "shift": "Shift_L", "ctrl": "Control_L", "alt": "Alt_L",
        "win": "Super_L", "super": "Super_L", "cmd": "Super_L",
    }

    def __init__(self):
        from Xlib import display, X
        from Xlib.ext import xtest
        import Xlib.XK

        self._display = display.Display()
        self._xtest = xtest
        self._X = X
        self._XK = Xlib.XK

        if not self._display.has_extension("XTEST"):
            raise RuntimeError("X11 XTest extension not available")

    def _keysym(self, key: str):
        if len(key) == 1:
            return self._XK.string_to_keysym(key)
        return self._XK.string_to_keysym(self._KEYSYM_MAP.get(key.lower(), key))

    def _keycode(self, keysym) -> int:
        return self._display.keysym_to_keycode(keysym) or 0

    def move_to(self, x: int, y: int, duration: float = 0.0) -> None:
        if duration > 0:
            # Simple linear interpolation
            start_x, start_y = self._get_cursor_pos()
            steps = max(1, int(duration / 0.02))
            for i in range(1, steps + 1):
                t = i / steps
                cx = int(start_x + (x - start_x) * t)
                cy = int(start_y + (y - start_y) * t)
                self._xtest.fake_input(self._display, self._X.MotionNotify, x=cx, y=cy)
                self._display.sync()
                time.sleep(0.02)
        else:
            self._xtest.fake_input(self._display, self._X.MotionNotify, x=x, y=y)
            self._display.sync()

    def _get_cursor_pos(self) -> tuple[int, int]:
        root = self._display.screen().root
        pos = root.query_pointer()
        return pos.root_x, pos.root_y

    def mouse_down(self, button: str = "left") -> None:
        btn = {"left": 1, "middle": 2, "right": 3}.get(button.lower(), 1)
        self._xtest.fake_input(self._display, self._X.ButtonPress, btn)
        self._display.sync()

    def mouse_up(self, button: str = "left") -> None:
        btn = {"left": 1, "middle": 2, "right": 3}.get(button.lower(), 1)
        self._xtest.fake_input(self._display, self._X.ButtonRelease, btn)
        self._display.sync()

    def key_down(self, key: str) -> None:
        keysym = self._keysym(key)
        kc = self._keycode(keysym)
        if kc:
            self._xtest.fake_input(self._display, self._X.KeyPress, kc)
            self._display.sync()

    def key_up(self, key: str) -> None:
        keysym = self._keysym(key)
        kc = self._keycode(keysym)
        if kc:
            self._xtest.fake_input(self._display, self._X.KeyRelease, kc)
            self._display.sync()

    _SHIFT_MAP = {
        "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6",
        "&": "7", "*": "8", "(": "9", ")": "0", "_": "-", "+": "=",
        "{": "[", "}": "]", "|": "\\", ":": ";", '"': "'", "<": ",",
        ">": ".", "?": "/", "~": "`",
    }

    def _tap(self, ch: str) -> None:
        """Press+release one character, synthesizing Shift when needed."""
        shift = False
        key = ch
        if ch.isalpha() and ch.isupper():
            shift = True
            key = ch.lower()
        elif ch in self._SHIFT_MAP:
            shift = True
            key = self._SHIFT_MAP[ch]
        keysym = self._XK.string_to_keysym(key)
        if not keysym:
            return
        kc = self._keycode(keysym)
        if not kc:
            return
        if shift:
            self._xtest.fake_input(self._display, self._X.KeyPress, self._keycode(self._XK.string_to_keysym("Shift_L")))
        self._xtest.fake_input(self._display, self._X.KeyPress, kc)
        self._xtest.fake_input(self._display, self._X.KeyRelease, kc)
        if shift:
            self._xtest.fake_input(self._display, self._X.KeyRelease, self._keycode(self._XK.string_to_keysym("Shift_L")))
        self._display.sync()

    def write(self, text: str, interval: float = 0.0) -> None:
        for ch in text:
            if ch == "\n":
                self.key_down("return")
                self.key_up("return")
            elif ch == "\t":
                self.key_down("tab")
                self.key_up("tab")
            else:
                self._tap(ch)
            if interval > 0:
                time.sleep(interval)

    def hotkey(self, *keys: str) -> None:
        keycodes = []
        for k in keys:
            keysym = self._keysym(k)
            kc = self._keycode(keysym)
            if kc:
                self._xtest.fake_input(self._display, self._X.KeyPress, kc)
                keycodes.append(kc)
        self._display.sync()
        for kc in reversed(keycodes):
            self._xtest.fake_input(self._display, self._X.KeyRelease, kc)
        self._display.sync()
