"""AT-SPI2 client over D-Bus (dbus-next) — no pyatspi / PyGObject needed.

Talks the accessibility bus directly with low-level D-Bus messages, so element
automation works on stock Linux installs that only have at-spi2-core.
"""

from __future__ import annotations

import asyncio
from typing import Any

from .base import ElementInfo

# AT-SPI role name -> UIA-ish control type
ROLE_MAP = {
    "push button": "Button",
    "toggle button": "CheckBox",
    "check box": "CheckBox",
    "radio button": "RadioButton",
    "text": "Edit",
    "entry": "Edit",
    "password text": "Edit",
    "combo box": "ComboBox",
    "menu": "Menu",
    "menu item": "MenuItem",
    "menu bar": "MenuBar",
    "tool tip": "ToolTip",
    "label": "Text",
    "static": "Text",
    "panel": "Pane",
    "frame": "Pane",
    "dialog": "Pane",
    "window": "Pane",
    "table": "DataGrid",
    "table cell": "DataItem",
    "list": "List",
    "list item": "ListItem",
    "scroll bar": "ScrollBar",
    "slider": "Slider",
    "progress bar": "ProgressBar",
    "tool bar": "ToolBar",
    "page tab": "TabItem",
    "page tab list": "Tab",
    "tree": "Tree",
    "tree item": "TreeItem",
    "separator": "Separator",
    "link": "Hyperlink",
    "image": "Image",
    "icon": "Image",
    "header": "Header",
    "status bar": "StatusBar",
    "calendar": "Calendar",
    "spin button": "Spinner",
    "document": "Pane",
    "filler": "Pane",
    "canvas": "Pane",
}

# Roles worth treating as a top-level window when matching a title
_WINDOW_ROLES = {"frame", "dialog", "window", "application", "alert"}

_DESKTOP_REF = ("org.a11y.atspi.Registry", "/org/a11y/atspi/accessible/root")


def _normalize_role(role: str) -> str:
    return (role or "").strip().lower().replace("_", " ")


async def _open():
    """Connect to the session bus and the accessibility bus."""
    from dbus_next.aio import MessageBus
    from dbus_next import Message

    bus = await MessageBus().connect()
    reply = await bus.call(Message(
        destination="org.a11y.Bus",
        path="/org/a11y/bus",
        interface="org.a11y.Bus",
        member="GetAddress",
    ))
    if reply is None or not reply.body:
        raise RuntimeError("AT-SPI bus not available (org.a11y.Bus.GetAddress failed)")
    addr = reply.body[0]
    abus = await MessageBus(bus_address=addr).connect()
    return bus, abus


async def _call(abus, ref, iface, member, signature="", body=None):
    from dbus_next import Message, MessageType

    bn, path = ref
    msg = Message(
        destination=bn,
        path=path,
        interface=iface,
        member=member,
        signature=signature,
        body=list(body or []),
    )
    reply = await abus.call(msg)
    if reply is None:
        raise RuntimeError("no reply from %s %s.%s" % (path, iface, member))
    if reply.message_type == MessageType.ERROR:
        raise RuntimeError("%s.%s: %s" % (iface, member, reply.body[0] if reply.body else "error"))
    return reply.body


async def _get_name(abus, ref) -> str:
    try:
        body = await _call(
            abus, ref, "org.freedesktop.DBus.Properties", "Get",
            "ss", ["org.a11y.atspi.Accessible", "Name"],
        )
        v = body[0]
        return str(v.value) if hasattr(v, "value") else str(v)
    except Exception:
        return ""


async def _get_role(abus, ref) -> str:
    try:
        body = await _call(abus, ref, "org.a11y.atspi.Accessible", "GetRoleName")
        return str(body[0])
    except Exception:
        return ""


async def _get_children(abus, ref) -> list[tuple[str, str]]:
    try:
        body = await _call(abus, ref, "org.a11y.atspi.Accessible", "GetChildren")
        out = []
        for c in body[0]:
            out.append((str(c[0]), str(c[1])))
        return out
    except Exception:
        return []


async def _get_extents(abus, ref) -> tuple[int, int, int, int]:
    try:
        body = await _call(abus, ref, "org.a11y.atspi.Component", "GetExtents", "u", [0])
        x, y, w, h = (int(body[0]), int(body[1]), int(body[2]), int(body[3]))
        return (x, y, x + w, y + h)
    except Exception:
        return (0, 0, 0, 0)


async def _get_actions(abus, ref) -> list:
    try:
        body = await _call(abus, ref, "org.a11y.atspi.Action", "GetActions")
        return list(body[0])
    except Exception:
        return []


async def _walk(abus, ref, rows, depth, max_depth, limit, collect=True):
    """Depth-first walk from ref, appending ElementInfo rows."""
    if len(rows) >= limit:
        return
    if collect:
        name = await _get_name(abus, ref)
        role = await _get_role(abus, ref)
        rect = await _get_extents(abus, ref)
        ctype = ROLE_MAP.get(_normalize_role(role), role or "Unknown")
        if name or ctype != "Unknown":
            rows.append(ElementInfo(
                name=name,
                control_type=ctype,
                automation_id="",
                rect=rect,
                depth=depth,
                ref=ref,
            ))
    if depth >= max_depth:
        return
    for child in await _get_children(abus, ref):
        if len(rows) >= limit:
            return
        await _walk(abus, child, rows, depth + 1, max_depth, limit, collect=True)


async def _find_window_ref(abus, title: str, max_depth: int = 8) -> tuple[str, str] | None:
    """Find the accessible node matching a window title."""
    # Search apps under the desktop root
    for app in await _get_children(abus, _DESKTOP_REF):
        found = await _find_by_title(abus, app, title, 0, max_depth)
        if found is not None:
            return found
    return None


async def _find_by_title(abus, ref, title, depth, max_depth):
    role = _normalize_role(await _get_role(abus, ref))
    name = await _get_name(abus, ref)
    if name == title and (role in _WINDOW_ROLES or depth <= 2):
        return ref
    if depth >= max_depth:
        return None
    for child in await _get_children(abus, ref):
        found = await _find_by_title(abus, child, title, depth + 1, max_depth)
        if found is not None:
            return found
    return None


class AtspiClient:
    """Synchronous facade over the async AT-SPI D-Bus client."""

    def __init__(self):
        self._available = None

    def available(self) -> bool:
        if self._available is None:
            try:
                self._available = bool(asyncio.run(self._ping()))
            except Exception:
                self._available = False
        return self._available

    async def _ping(self):
        bus, abus = await _open()
        kids = await _get_children(abus, _DESKTOP_REF)
        return kids

    def apps(self) -> list[tuple[str, str]]:
        """List application refs on the accessibility bus."""

        async def _go():
            bus, abus = await _open()
            out = []
            for app in await _get_children(abus, _DESKTOP_REF):
                out.append((await _get_name(abus, app), app[0], app[1]))
            return out

        return asyncio.run(_go())

    def get_elements(self, title: str, max_depth: int = 12, limit: int = 400) -> list[ElementInfo]:
        """Return ElementInfo rows for the accessible subtree of a window title."""

        async def _go():
            bus, abus = await _open()
            ref = await _find_window_ref(abus, title)
            if ref is None:
                return []
            rows: list[ElementInfo] = []
            await _walk(abus, ref, rows, 1, max_depth, limit)
            return rows

        try:
            return asyncio.run(_go())
        except Exception:
            return []

    def invoke(self, ref: tuple[str, str]) -> bool:
        async def _go():
            bus, abus = await _open()
            actions = await _get_actions(abus, ref)
            if not actions:
                return False
            await _call(abus, ref, "org.a11y.atspi.Action", "DoAction", "i", [0])
            return True

        try:
            return asyncio.run(_go())
        except Exception:
            return False

    def set_text(self, ref: tuple[str, str], text: str) -> bool:
        async def _go():
            bus, abus = await _open()
            await _call(abus, ref, "org.a11y.atspi.EditableText", "SetTextContents", "s", [text])
            return True

        try:
            return asyncio.run(_go())
        except Exception:
            return False

    def get_text(self, ref: tuple[str, str]) -> str:
        async def _go():
            bus, abus = await _open()
            body = await _call(abus, ref, "org.freedesktop.DBus.Properties", "Get",
                               "ss", ["org.a11y.atspi.Text", "Text"])
            v = body[0]
            return str(v.value) if hasattr(v, "value") else str(v)

        try:
            return asyncio.run(_go())
        except Exception:
            return ""
