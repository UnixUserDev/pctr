"""Command dispatch tests with a fake backend (no display required)."""

import pytest


class FakeBackend:
    """Records the calls a cmd_* makes, so dispatch can be tested headlessly."""

    def __init__(self):
        self.calls = []

    def set_input(self, name=None, port=None):
        self.calls.append(("set_input", name, port))

    def key_down(self, k):
        self.calls.append(("key_down", k))

    def key_up(self, k):
        self.calls.append(("key_up", k))

    def move_mouse(self, x, y, duration=0.0):
        self.calls.append(("move_mouse", x, y))

    def mouse_down(self, button="left"):
        self.calls.append(("mouse_down", button))

    def mouse_up(self, button="left"):
        self.calls.append(("mouse_up", button))

    def write_text(self, text, interval=0.0):
        self.calls.append(("write_text", text, interval))


def _args(argv, backend):
    import pctr.cli as cli
    a = cli.build_parser().parse_args(argv)
    a._backend = backend
    return a


def test_hotkey_dispatch_no_nameerror():
    import pctr.cli as cli
    be = FakeBackend()
    args = _args(["hotkey", "--keys", "ctrl+s"], be)
    cli.cmd_hotkey(args)  # used to raise NameError: X11Input
    assert [c for c in be.calls if c[0] in ("key_down", "key_up")] == [
        ("key_down", "ctrl"), ("key_down", "s"),
        ("key_up", "s"), ("key_up", "ctrl"),
    ]


def test_global_type_uses_literal_write():
    import pctr.cli as cli
    be = FakeBackend()
    args = _args(["type", "--text", "a{b}c^d"], be)
    cli.cmd_type(args)
    assert ("write_text", "a{b}c^d", 0.03) in be.calls


def test_move_honors_input_backend():
    import pctr.cli as cli
    be = FakeBackend()
    args = _args(["move", "--x", "5", "--y", "6", "--input-backend", "serial-hid", "--port", "/dev/ttyUSB0"], be)
    cli.cmd_move(args)
    assert ("set_input", "serial-hid", "/dev/ttyUSB0") in be.calls


def test_move_direct_maps_to_pydirectinput():
    import pctr.cli as cli
    be = FakeBackend()
    args = _args(["move", "--x", "1", "--y", "2", "--direct"], be)
    cli.cmd_move(args)
    assert ("set_input", "pydirectinput", None) in be.calls
