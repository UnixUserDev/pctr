"""pctr backend layer — platform abstraction for Windows / Linux (X11/Wayland)."""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import Backend

__all__ = ["get_backend", "detect_platform"]


def detect_platform() -> str:
    """Return the platform key: 'windows', 'x11', 'wayland', or 'unknown'."""
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "linux":
        session = os.environ.get("XDG_SESSION_TYPE", "").lower()
        if session == "wayland" or os.environ.get("WAYLAND_DISPLAY"):
            # Elements, actions and window discovery run over AT-SPI and work
            # identically on Wayland; XWayland only adds pointer input,
            # screenshots and window geometry. Same backend either way.
            return "wayland"
        return "x11"
    return "unknown"


def get_backend(name: str = "auto") -> "Backend":
    """Resolve and instantiate a backend by name (or auto-detect)."""
    if name == "auto":
        name = detect_platform()
    if name == "windows":
        from .windows import WindowsBackend
        return WindowsBackend()
    if name in ("x11", "wayland"):
        # One Linux backend: AT-SPI for elements (works on both display
        # servers) plus XTest/mss/EWMH where an X display exists. On a pure
        # Wayland session input and screenshots raise a clear error instead of
        # pretending the window layer exists.
        from .x11 import X11Backend
        return X11Backend()
    raise SystemExit("unknown backend %r (auto | windows | x11 | wayland)" % name)
