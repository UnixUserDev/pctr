"""Platform detection + backend selection tests (no display required)."""

import os
import sys

import pytest


def test_detect_windows(monkeypatch):
    from pctr.backends import detect_platform
    monkeypatch.setattr(sys, "platform", "win32")
    assert detect_platform() == "windows"


def test_detect_x11_linux(monkeypatch):
    from pctr.backends import detect_platform
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    assert detect_platform() == "x11"


def test_detect_wayland_without_xwayland(monkeypatch):
    from pctr.backends import detect_platform
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.delenv("DISPLAY", raising=False)
    assert detect_platform() == "wayland"


def test_detect_wayland_with_xwayland_prefers_x11(monkeypatch):
    from pctr.backends import detect_platform
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("DISPLAY", ":0")
    assert detect_platform() == "x11"


def test_get_backend_unknown_raises():
    from pctr.backends import get_backend
    with pytest.raises(SystemExit):
        get_backend("nonsense")


def test_get_backend_wayland_not_implemented():
    from pctr.backends import get_backend
    with pytest.raises(SystemExit):
        get_backend("wayland")


def test_role_map_is_lowercase_keys():
    from pctr.backends.atspi import ROLE_MAP, _normalize_role
    for k in ROLE_MAP:
        assert k == k.lower()
    assert _normalize_role("Push_Button") == "push button"
    assert ROLE_MAP["push button"] == "Button"


def test_get_extents_unwraps_struct(monkeypatch):
    """GetExtents comes back wrapped: body == [[x, y, w, h]] -> (x, y, x+w, y+h)."""
    import asyncio
    from pctr.backends import atspi

    async def fake_call(abus, ref, iface, member, signature="", body=None):
        assert member == "GetExtents"
        return [[10, 20, 30, 40]]

    monkeypatch.setattr(atspi, "_call", fake_call)
    ext = asyncio.run(atspi._get_extents(None, ("bus", "/obj")))
    assert ext == (10, 20, 40, 60)


def test_get_extents_swallows_errors(monkeypatch):
    import asyncio
    from pctr.backends import atspi

    async def boom(*a, **k):
        raise RuntimeError("no component")

    monkeypatch.setattr(atspi, "_call", boom)
    assert asyncio.run(atspi._get_extents(None, ("bus", "/obj"))) == (0, 0, 0, 0)

