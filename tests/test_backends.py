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


def test_detect_wayland_with_xwayland_reports_wayland(monkeypatch):
    # Wayland is the truth even when XWayland sets DISPLAY: the accessibility
    # bus sees native Wayland windows that X cannot, and both platform keys
    # resolve to the same Linux backend anyway.
    from pctr.backends import detect_platform
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("DISPLAY", ":0")
    assert detect_platform() == "wayland"


def test_get_backend_unknown_raises():
    from pctr.backends import get_backend
    with pytest.raises(SystemExit):
        get_backend("nonsense")


def test_get_backend_wayland_returns_linux_backend():
    # 0.6.0: elements/actions run over AT-SPI on Wayland too, so `wayland` is a
    # real backend (X11 is only needed for input and screenshots).
    from pctr.backends import get_backend
    from pctr.backends.x11 import X11Backend
    be = get_backend("wayland")
    assert isinstance(be, X11Backend)
    assert be._atspi_client is not None


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



def test_ensure_x_interns_the_atoms_pctr_reads(monkeypatch):
    """0.6.0 shipped with the _ATOMS mapping inverted: titles came back empty."""
    import Xlib.display
    from pctr.backends import x11

    class FakeRoot:
        pass

    class FakeScreen:
        root = FakeRoot()

    class FakeDisplay:
        def screen(self):
            return FakeScreen()

        def intern_atom(self, name):
            return sum(bytearray(name.encode()))

    monkeypatch.setattr(Xlib.display, "Display", lambda: FakeDisplay())
    be = x11.X11Backend()
    for attr, atom in x11._ATOMS.items():
        assert attr.startswith("_"), attr
        assert getattr(be, attr) == sum(bytearray(atom.encode())), attr
    # the two that _get_window_title actually reads
    assert be._UTF8_STRING and be._WM_NAME and be._NET_WM_NAME


def test_set_element_text_refuses_non_editable(monkeypatch):
    from pctr.backends import x11
    from pctr.backends.base import ElementInfo

    import Xlib.display

    class FakeRoot:
        pass

    class FakeScreen:
        root = FakeRoot()

    class FakeDisplay:
        def screen(self):
            return FakeScreen()

        def intern_atom(self, name):
            return 1

    monkeypatch.setattr(Xlib.display, "Display", lambda: FakeDisplay())
    be = x11.X11Backend()
    called = []
    monkeypatch.setattr(be._atspi_client, "set_text", lambda ref, text: called.append(text) or True)

    el = ElementInfo(name="File", control_type="MenuItem", automation_id="",
                     rect=(0, 0, 10, 10), ref=("bus", "/x"), interfaces=())
    with pytest.raises(RuntimeError) as exc:
        be.set_element_text(1, el, "boom")
    assert "not editable" in str(exc.value)
    assert called == [], "wrote text into an element without EditableText"


def _fake_x_backend(monkeypatch):
    import Xlib.display
    from pctr.backends import x11

    class FakeRoot:
        pass

    class FakeScreen:
        root = FakeRoot()

    class FakeDisplay:
        def screen(self):
            return FakeScreen()

        def intern_atom(self, name):
            return 1

        def close(self):
            pass

    monkeypatch.setattr(Xlib.display, "Display", lambda: FakeDisplay())
    return x11.X11Backend()


def test_toggle_refuses_non_toggle_controls(monkeypatch):
    """--toggle on a MenuItem used to activate it (Press/Click fell through)."""
    from pctr.backends.base import ElementInfo

    be = _fake_x_backend(monkeypatch)
    fired = []
    monkeypatch.setattr(be._atspi_client, "action_names", lambda ref: ["Press"])
    monkeypatch.setattr(be._atspi_client, "invoke", lambda ref, name=None: fired.append(name) or True)

    menu_item = ElementInfo(name="New Window", control_type="MenuItem", automation_id="",
                            rect=(0, 0, 1, 1), ref=("bus", "/x"))
    assert be.toggle_element(1, menu_item) is False
    assert fired == [], "a menu item was activated through --toggle"

    button = ElementInfo(name="OK", control_type="Button", automation_id="",
                         rect=(0, 0, 1, 1), ref=("bus", "/x"))
    assert be.toggle_element(1, button) is False
    assert fired == []


def test_toggle_works_on_a_checkbox(monkeypatch):
    from pctr.backends.base import ElementInfo

    be = _fake_x_backend(monkeypatch)
    fired = []
    monkeypatch.setattr(be._atspi_client, "action_names", lambda ref: ["Toggle", "Press"])
    monkeypatch.setattr(be._atspi_client, "invoke", lambda ref, name=None: fired.append(name) or True)

    cb = ElementInfo(name="Show hidden files", control_type="CheckBox", automation_id="",
                     rect=(0, 0, 1, 1), ref=("bus", "/x"))
    assert be.toggle_element(1, cb) is True
    assert fired == ["toggle"]


def test_set_element_text_has_no_blind_typing_fallback(monkeypatch):
    """An element with no accessible node must not get ctrl+a/type at focus."""
    from pctr.backends.base import ElementInfo

    be = _fake_x_backend(monkeypatch)
    typed = []

    class FakeInput:
        def __getattr__(self, name):
            def record(*a, **k):
                typed.append(name)
            return record

    be._input_obj = FakeInput()

    el = ElementInfo(name="canvas", control_type="Pane", automation_id="",
                     rect=(0, 0, 10, 10), ref=None)
    with pytest.raises(RuntimeError) as exc:
        be.set_element_text(1, el, "secret")
    assert "refusing to type blindly" in str(exc.value)
    assert typed == [], "typed into whatever had focus"


def test_set_element_text_reports_refused_write(monkeypatch):
    from pctr.backends.base import ElementInfo

    be = _fake_x_backend(monkeypatch)
    monkeypatch.setattr(be._atspi_client, "set_text", lambda ref, text: False)
    el = ElementInfo(name="Search", control_type="Edit", automation_id="", rect=(0, 0, 1, 1),
                     ref=("bus", "/x"), interfaces=("org.a11y.atspi.EditableText",))
    with pytest.raises(RuntimeError) as exc:
        be.set_element_text(1, el, "x")
    assert "refused the write" in str(exc.value)


def test_resolve_window_literal_title_does_not_raise(monkeypatch):
    from pctr.backends.atspi import AppWindow

    wins = [AppWindow(title="Report (final) [2].txt", pid=5, bus=":1.1", path="/a")]
    from pctr.backends import atspi
    c = atspi.AtspiClient()
    monkeypatch.setattr(c, "windows", lambda: wins)
    got = c.resolve_window("Report (final) [2].txt", None, literal=True)
    assert got is not None and got.ref == (":1.1", "/a")
    # a pattern still works when asked for
    assert c.resolve_window("Report .*", None).ref == (":1.1", "/a")
