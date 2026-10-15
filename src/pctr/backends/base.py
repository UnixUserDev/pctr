"""Backend abstraction for pctr — defines the interface all platform backends implement."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class WindowInfo:
    """A top-level window."""
    handle: int          # HWND on Windows, XID on X11
    title: str
    pid: int
    desktop: int = 0     # virtual desktop index


@dataclass
class ElementInfo:
    """An automation element (control)."""
    name: str
    control_type: str    # "Button", "Edit", etc. (role on X11)
    automation_id: str   # control-id on X11
    rect: tuple[int, int, int, int]  # left, top, right, bottom
    depth: int = 0
    ref: tuple[str, str] | None = None  # (dbus bus name, object path) for AT-SPI


@dataclass
class OCRWord:
    """A single OCR result."""
    text: str
    x: int
    y: int
    w: int
    h: int


class Backend(ABC):
    """Platform backend — every pctr command dispatches through this interface."""

    # --- Window management ---
    @abstractmethod
    def list_windows(self, filter_re: re.Pattern | None = None,
                     process: int | None = None) -> list[WindowInfo]: ...

    @abstractmethod
    def find_window(self, title_re: str | None, process: int | None,
                    timeout: float = 5.0) -> int: ...

    @abstractmethod
    def get_window_title(self, handle: int) -> str: ...

    @abstractmethod
    def get_window_pid(self, handle: int) -> int: ...

    @abstractmethod
    def get_window_rect(self, handle: int) -> tuple[int, int, int, int]: ...

    @abstractmethod
    def focus_window(self, handle: int) -> None: ...

    # --- Element tree ---
    @abstractmethod
    def get_elements(self, handle: int, max_depth: int = 99,
                     limit: int = 400) -> list[tuple[ElementInfo, int]]: ...

    @abstractmethod
    def find_elements(self, handle: int, name: str | None = None,
                      control_type: str | None = None,
                      auto_id: str | None = None) -> list[ElementInfo]: ...

    # --- Element actions ---
    @abstractmethod
    def click_element(self, handle: int, el: ElementInfo,
                      button: str = "left", dbl: bool = False) -> None: ...

    @abstractmethod
    def invoke_element(self, handle: int, el: ElementInfo) -> bool: ...

    @abstractmethod
    def set_element_text(self, handle: int, el: ElementInfo, text: str) -> None: ...

    @abstractmethod
    def type_element_text(self, handle: int, el: ElementInfo,
                          text: str, delay: float = 0.0) -> None: ...

    # --- Input (global) ---
    @abstractmethod
    def move_mouse(self, x: int, y: int, duration: float = 0.0) -> None: ...

    @abstractmethod
    def mouse_down(self, button: str = "left") -> None: ...

    @abstractmethod
    def mouse_up(self, button: str = "left") -> None: ...

    @abstractmethod
    def key_down(self, key: str) -> None: ...

    @abstractmethod
    def key_up(self, key: str) -> None: ...

    @abstractmethod
    def send_keys(self, keys: str) -> None: ...

    # --- Screen ---
    @abstractmethod
    def screen_size(self) -> tuple[int, int]: ...

    @abstractmethod
    def screenshot(self, region: tuple[int, int, int, int] | None = None) -> Any: ...

    # --- OCR ---
    @abstractmethod
    def ocr(self, image: Any, lang: str | None = None) -> list[OCRWord]: ...

    # --- Virtual desktops ---
    @abstractmethod
    def list_desktops(self) -> list[str]: ...

    @abstractmethod
    def get_window_desktop(self, handle: int) -> int: ...

    @abstractmethod
    def move_window_to_desktop(self, handle: int, desktop_idx: int) -> None: ...

    @abstractmethod
    def current_desktop(self) -> int: ...

    @abstractmethod
    def switch_desktop(self, action: str) -> None: ...
