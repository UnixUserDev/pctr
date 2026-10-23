"""AT-SPI client tests — no live accessibility bus required.

These cover the parts that used to be wrong in 0.5.0: serial tree walks,
title-exact window lookup, and failures silently turning into "zero elements".
"""

import asyncio

import pytest


def _fake_tree(monkeypatch, atspi, tree, calls=None):
    """Patch atspi._get_* so _walk sees a canned dict of ref -> (name, role, kids)."""
    async def fake_get_name(abus, ref):
        return tree[ref][0]

    async def fake_get_role(abus, ref):
        return tree[ref][1]

    async def fake_get_children(abus, ref):
        return tree[ref][2]

    async def fake_get_interfaces(abus, ref):
        return ()

    async def fake_get_extents(abus, ref):
        return (0, 0, 10, 10)

    monkeypatch.setattr(atspi, "_get_name", fake_get_name)
    monkeypatch.setattr(atspi, "_get_role", fake_get_role)
    monkeypatch.setattr(atspi, "_get_children", fake_get_children)
    monkeypatch.setattr(atspi, "_get_interfaces", fake_get_interfaces)
    monkeypatch.setattr(atspi, "_get_extents", fake_get_extents)


def test_walk_breadth_first_with_depths(monkeypatch):
    from pctr.backends import atspi

    root = ("bus", "/root")
    a = ("bus", "/a")
    b = ("bus", "/b")
    tree = {
        root: ("win", "frame", [a, b]),
        a: ("OK", "push button", []),
        b: ("Group", "panel", []),
    }
    _fake_tree(monkeypatch, atspi, tree)
    rows = asyncio.run(atspi._walk(None, root, max_depth=99, limit=100))
    assert [r.name for r in rows] == ["win", "OK", "Group"]
    assert [r.depth for r in rows] == [1, 2, 2]
    assert [r.control_type for r in rows] == ["Pane", "Button", "Pane"]


def test_walk_queries_a_level_concurrently(monkeypatch):
    """Siblings must be in flight together, not one round-trip after another."""
    from pctr.backends import atspi

    root = ("bus", "/root")
    kids = [("bus", "/%d" % i) for i in range(8)]
    tree = {root: ("win", "frame", kids)}
    for k in kids:
        tree[k] = ("B", "push button", [])

    _fake_tree(monkeypatch, atspi, tree)

    state = {"now": 0, "peak": 0}
    orig = atspi._get_name

    async def slow_get_name(abus, ref):
        state["now"] += 1
        state["peak"] = max(state["peak"], state["now"])
        await asyncio.sleep(0.01)
        state["now"] -= 1
        return await orig(abus, ref)

    monkeypatch.setattr(atspi, "_get_name", slow_get_name)
    asyncio.run(atspi._walk(None, root, max_depth=99, limit=100))
    assert state["peak"] > 1, "siblings were fetched serially"


def test_walk_respects_limit(monkeypatch):
    from pctr.backends import atspi

    root = ("bus", "/root")
    kids = [("bus", "/%d" % i) for i in range(20)]
    tree = {root: ("win", "frame", kids)}
    for k in kids:
        tree[k] = ("B", "push button", [])
    _fake_tree(monkeypatch, atspi, tree)
    rows = asyncio.run(atspi._walk(None, root, max_depth=99, limit=5))
    assert len(rows) == 5


def test_control_type_mapping():
    from pctr.backends import atspi
    assert atspi._control_type("push button") == "Button"
    assert atspi._control_type("Push_Button") == "Button"
    assert atspi._control_type("slide ruler") == "slide ruler"  # unknown roles pass through


def _client_with_windows(monkeypatch, windows):
    from pctr.backends import atspi

    client = atspi.AtspiClient()
    monkeypatch.setattr(client, "windows", lambda: windows)
    return client


def test_resolve_window_by_pid_disambiguates_duplicate_titles(monkeypatch):
    from pctr.backends.atspi import AppWindow

    wins = [
        AppWindow(title="Dolphin", pid=100, bus=":1.1", path="/a"),
        AppWindow(title="Dolphin", pid=200, bus=":1.2", path="/b"),
    ]
    client = _client_with_windows(monkeypatch, wins)
    assert client.resolve_window("Dolphin", 100).ref == (":1.1", "/a")
    assert client.resolve_window("Dolphin", 200).ref == (":1.2", "/b")


def test_resolve_window_falls_back_to_app_name(monkeypatch):
    from pctr.backends.atspi import AppWindow

    # The frame is named after the document, not the app.
    wins = [AppWindow(title="Home — Dolphin", pid=100, bus=":1.1", path="/a", app_name="dolphin")]
    client = _client_with_windows(monkeypatch, wins)
    assert client.resolve_window("dolphin", None).ref == (":1.1", "/a")


def test_resolve_window_by_pid_only(monkeypatch):
    from pctr.backends.atspi import AppWindow

    wins = [AppWindow(title="Whatever", pid=42, bus=":1.9", path="/x")]
    client = _client_with_windows(monkeypatch, wins)
    assert client.resolve_window(None, 42).ref == (":1.9", "/x")
    assert client.resolve_window(None, 43) is None


def test_get_tree_marks_electron_like_apps_opaque(monkeypatch):
    """Two nodes and no interactive control == "app is closed", not "empty"."""
    from pctr.backends import atspi

    client = atspi.AtspiClient()
    monkeypatch.setattr(atspi, "_session", lambda: _RunFake())
    monkeypatch.setattr(atspi, "ElementInfo", atspi.ElementInfo)
    rows = [
        atspi.ElementInfo(name="Discord", control_type="application", automation_id="",
                          rect=(0, 0, 0, 0), depth=1),
        atspi.ElementInfo(name="Discord", control_type="Pane", automation_id="",
                          rect=(0, 0, 100, 100), depth=2),
    ]
    monkeypatch.setattr(atspi, "_walk", _fake_walk(rows))
    tree = client.get_tree(("bus", "/x"))
    assert tree.opaque is True
    assert "force-renderer-accessibility" in tree.error
    assert len(tree.elements) == 2


def test_get_tree_not_opaque_when_widgets_exist(monkeypatch):
    from pctr.backends import atspi

    client = atspi.AtspiClient()
    monkeypatch.setattr(atspi, "_session", lambda: _RunFake())
    rows = [
        atspi.ElementInfo(name="win", control_type="Pane", automation_id="", rect=(0, 0, 0, 0), depth=1),
        atspi.ElementInfo(name="Group", control_type="Pane", automation_id="", rect=(0, 0, 0, 0), depth=2),
        atspi.ElementInfo(name="OK", control_type="Button", automation_id="", rect=(0, 0, 1, 1), depth=3),
    ]
    monkeypatch.setattr(atspi, "_walk", _fake_walk(rows))
    tree = client.get_tree(("bus", "/x"))
    assert tree.opaque is False
    assert tree.error is None


def test_get_tree_reports_bus_errors(monkeypatch):
    from pctr.backends import atspi

    client = atspi.AtspiClient()
    monkeypatch.setattr(atspi, "_session", lambda: _RunFake(boom=True))
    tree = client.get_tree(("bus", "/x"))
    assert tree.elements == []
    assert tree.opaque is False
    assert "AT-SPI" in tree.error


class _RunFake:
    """Stand-in for _Session.run(): calls the coroutine on a throwaway loop."""

    def __init__(self, boom=False):
        self.boom = boom

    def run(self, fn, timeout=10):
        if self.boom:
            raise RuntimeError("connection reset")
        return asyncio.run(fn(None, None))


def _fake_walk(rows):
    async def _go(abus, ref, max_depth=99, limit=400):
        return rows
    return _go


# --- regressions from the 0.6.0 review ----------------------------------------


def test_invoke_does_not_fall_back_to_action_zero(monkeypatch):
    """A typo'd --do must fail, not silently press the first action."""
    from pctr.backends import atspi

    calls = []

    async def fake_get_actions(abus, ref):
        return [["press", "Presses it", ""], ["expand", "Expands it", ""]]

    async def fake_call(abus, ref, iface, member, signature="", body=None):
        calls.append((member, body))
        return []

    monkeypatch.setattr(atspi, "_get_actions", fake_get_actions)
    monkeypatch.setattr(atspi, "_call", fake_call)
    monkeypatch.setattr(atspi, "_session", lambda: _RunFake())

    client = atspi.AtspiClient()
    assert client.invoke(("bus", "/x"), "does-not-exist") is False
    assert calls == [], "DoAction ran even though the action name did not match"

    assert client.invoke(("bus", "/x"), "expand") is True
    assert calls == [("DoAction", [1])]


def test_get_tree_flags_nested_pane_only_shell(monkeypatch):
    """Electron apps can expose a few nested panes and still publish nothing."""
    from pctr.backends import atspi

    client = atspi.AtspiClient()
    monkeypatch.setattr(atspi, "_session", lambda: _RunFake())
    rows = [
        atspi.ElementInfo(name="Slack", control_type="application", automation_id="", rect=(0, 0, 0, 0), depth=1),
        atspi.ElementInfo(name="Slack", control_type="Pane", automation_id="", rect=(0, 0, 900, 600), depth=2),
        atspi.ElementInfo(name="", control_type="Pane", automation_id="", rect=(0, 0, 0, 0), depth=3),
        atspi.ElementInfo(name="", control_type="Pane", automation_id="", rect=(0, 0, 0, 0), depth=4),
    ]
    monkeypatch.setattr(atspi, "_walk", _fake_walk(rows))
    assert client.get_tree(("bus", "/x")).opaque is True


def test_get_tree_does_not_flag_labelled_static_window(monkeypatch):
    from pctr.backends import atspi

    client = atspi.AtspiClient()
    monkeypatch.setattr(atspi, "_session", lambda: _RunFake())
    rows = [
        atspi.ElementInfo(name="Dialog", control_type="Pane", automation_id="", rect=(0, 0, 0, 0), depth=1),
        atspi.ElementInfo(name="Your export finished.", control_type="Text", automation_id="", rect=(0, 0, 0, 0), depth=2),
    ]
    monkeypatch.setattr(atspi, "_walk", _fake_walk(rows))
    assert client.get_tree(("bus", "/x")).opaque is False


def test_get_tree_skips_opaque_verdict_on_truncated_walks(monkeypatch):
    """--depth 3..12 on a shallow shell must not be called opaque."""
    from pctr.backends import atspi

    client = atspi.AtspiClient()
    monkeypatch.setattr(atspi, "_session", lambda: _RunFake())
    rows = [
        atspi.ElementInfo(name="Konsole", control_type="Pane", automation_id="", rect=(0, 0, 0, 0), depth=1),
    ]
    monkeypatch.setattr(atspi, "_walk", _fake_walk(rows))
    assert client.get_tree(("bus", "/x"), max_depth=8).opaque is False
    assert client.get_tree(("bus", "/x"), max_depth=99, limit=1).opaque is False
