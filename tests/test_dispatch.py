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


class TreeBackend(FakeBackend):
    """Backend stub for cmd_tree: returns a canned TreeResult."""

    def __init__(self, result):
        super().__init__()
        self._result = result

    def find_window(self, title_re, process=None, timeout=5.0):
        return 1

    def get_tree(self, handle, max_depth=99, limit=400):
        return self._result

    def get_window_title(self, handle):
        return "win"

    def get_window_pid(self, handle):
        return 7


def _tree_backend(rows, opaque=False, error=None):
    from pctr.backends.base import ElementInfo, TreeResult
    els = [(ElementInfo(name=n, control_type=t, automation_id="", rect=(0, 0, 1, 1), depth=d), d)
           for n, t, d in rows]
    return TreeBackend(TreeResult(elements=els, opaque=opaque, error=error, backend="atspi"))


def test_tree_json_reports_opaque_and_keeps_exit_zero(capsys):
    import json
    import pctr.cli as cli
    be = _tree_backend([("win", "Pane", 1)], opaque=True, error="no widgets")
    args = _args(["tree", "--title", "x", "--json"], be)
    cli.cmd_tree(args)
    out = json.loads(capsys.readouterr().out)
    assert out["opaque"] is True
    assert out["error"] == "no widgets"
    assert out["backend"] == "atspi"
    assert len(out["elements"]) == 1


def test_tree_json_empty_keeps_exit_zero(capsys):
    import json
    import pctr.cli as cli
    be = _tree_backend([], error="bus unavailable")
    args = _args(["tree", "--title", "x", "--json"], be)
    cli.cmd_tree(args)  # must not raise
    out = json.loads(capsys.readouterr().out)
    assert out["elements"] == []
    assert out["error"] == "bus unavailable"


def test_tree_human_empty_exits_nonzero(capsys):
    import pytest as _pytest
    import pctr.cli as cli
    be = _tree_backend([], error="bus unavailable")
    args = _args(["tree", "--title", "x"], be)
    with _pytest.raises(SystemExit) as exc:
        cli.cmd_tree(args)
    assert exc.value.code == 1


def test_tree_depth_zero_is_not_promoted_to_full_tree(capsys):
    import json
    import pctr.cli as cli

    seen = {}

    class Be(TreeBackend):
        def get_tree(self, handle, max_depth=99, limit=400):
            seen["max_depth"] = max_depth
            return self._result

    from pctr.backends.base import TreeResult
    be = Be(TreeResult(elements=[], backend="atspi"))
    args = _args(["tree", "--title", "x", "--depth", "0", "--json"], be)
    cli.cmd_tree(args)
    assert seen["max_depth"] == 0
