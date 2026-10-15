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
            # Under a Wayland session, XWayland is usually present (DISPLAY set).
            # Native Wayland windows are invisible to X11, but XWayland clients
            # (and everything driven through AT-SPI / XTest) still work. Prefer
            # x11 over an unimplemented wayland backend when DISPLAY is available.
            if os.environ.get("DISPLAY"):
                return "x11"
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
    if name == "x11":
        from .x11 import X11Backend
        return X11Backend()
    if name == "wayland":
        raise SystemExit(
            "wayland backend is not yet implemented (planned: libei + xdg-desktop-portal)"
        )
    raise SystemExit("unknown backend %r (auto | windows | x11 | wayland)" % name)
