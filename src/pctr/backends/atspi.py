"""AT-SPI2 client over D-Bus (dbus-next) — no pyatspi / PyGObject needed.

Talks the accessibility bus directly with low-level D-Bus messages, so element
automation works on stock Linux installs that only have at-spi2-core.

Design notes:

* One persistent session per process. A background asyncio loop owns both the
  session bus and the accessibility bus, so calls stop paying reconnect costs
  and can overlap.
* The tree walk is breadth-first and **concurrent**: every node in a level is
  queried in parallel. Measured on a Qt window with 375 nodes: 5.4 s serial
  (the old per-call ``asyncio.run`` + reconnect path) vs ~0.3 s here.
* Windows are resolved by role (frame/window/dialog) *and* PID, not by exact
  title matching. That fixes duplicate titles and works on Wayland, where
  there is no window handle to look up.
* Failures are reported, never swallowed into "zero elements". An app that
  exposes no widgets (Electron without accessibility enabled, some JVM/SDL
  apps) is reported as *opaque* with a hint, which is different from
  "D-Bus is unavailable" and different from "the window has no children".
"""

from __future__ import annotations

import asyncio
import atexit
import time
import re
import threading
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass, field
from typing import Callable

from .base import ElementInfo, TreeResult

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

# Roles that mean "this app actually exposes widgets". If a tree has none of
# these, the app is almost certainly not publishing its a11y tree.
_INTERACTIVE_ROLES = {
    "Button", "CheckBox", "RadioButton", "Edit", "ComboBox", "MenuItem",
    "ListItem", "TabItem", "TreeItem", "Slider", "Hyperlink", "Spinner",
    "DataItem", "DataGrid",
}

# Control types that are pure containers: a tree of only these publishes nothing.
_CONTAINER_TYPES = {"application", "Pane", "Unknown", "ToolBar", "MenuBar",
                    "StatusBar", "Separator"}

_OPAQUE_HINT = (
    "application exposes no accessibility tree (Electron/Chromium: launch with "
    "--force-renderer-accessibility; Java: -Djavax.accessibility.assistive_technologies="
    "org.GNOME.Accessibility.AtkWrapper; Qt: QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1)"
)

# Window discovery is a full bus sweep; a CLI run asks for it 2-3 times.
_WINDOW_CACHE_TTL = 2.0

_DESKTOP_REF = ("org.a11y.atspi.Registry", "/org/a11y/atspi/accessible/root")
_DBUS_DRIVER = ("org.freedesktop.DBus", "/org/freedesktop/DBus")

# D-Bus errors that mean "the shared connection is gone, rebuild it".
_DISCONNECT_HINTS = ("connection", "disconnected", "broken pipe", "eof", "not connected")


def _normalize_role(role: str) -> str:
    return (role or "").strip().lower().replace("_", " ")


def _reraise_if_disconnect(exc: BaseException) -> None:
    """A dead bus must not look like an empty tree: let _Session rebuild it."""
    if _is_disconnect(exc):
        raise exc


@dataclass
class AppInfo:
    """One application on the accessibility bus."""

    name: str
    pid: int
    bus: str
    path: str

    @property
    def ref(self) -> tuple[str, str]:
        return (self.bus, self.path)


@dataclass
class AppWindow:
    """A top-level window/ frame/ dialog owned by an application."""

    title: str
    pid: int
    bus: str
    path: str
    app_name: str = ""

    @property
    def ref(self) -> tuple[str, str]:
        return (self.bus, self.path)


@dataclass
class _Node:
    """Raw facts about one accessible node, fetched concurrently."""

    ref: tuple[str, str]
    name: str = ""
    role: str = ""
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    interfaces: tuple[str, ...] = ()
    children: list[tuple[str, str]] = field(default_factory=list)


# --- low-level D-Bus helpers ---------------------------------------------------


async def _close_both(bus, abus) -> None:
    await _close(abus)
    await _close(bus)


async def _close(bus):
    if bus is None:
        return
    try:
        r = bus.disconnect()
        if asyncio.iscoroutine(r):
            await r
    except Exception:
        pass
    # dbus-next's disconnect() only shuts the socket down; it never closes the
    # fd, so the process leaks a socket per bus unless we do it.
    # _setup_socket() also keeps a makefile() stream, which holds an io ref on
    # the same fd: closing the socket alone leaves the fd open.
    for attr in ("_stream", "_sock"):
        try:
            obj = getattr(bus, attr, None)
            if obj is not None:
                obj.close()
        except Exception:
            pass


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
        await _close(bus)
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
    except Exception as e:
        _reraise_if_disconnect(e)
        return ""


async def _get_role(abus, ref) -> str:
    try:
        body = await _call(abus, ref, "org.a11y.atspi.Accessible", "GetRoleName")
        return str(body[0])
    except Exception as e:
        _reraise_if_disconnect(e)
        return ""


async def _get_children(abus, ref) -> list[tuple[str, str]]:
    try:
        body = await _call(abus, ref, "org.a11y.atspi.Accessible", "GetChildren")
        out = []
        for c in body[0]:
            out.append((str(c[0]), str(c[1])))
        return out
    except Exception as e:
        _reraise_if_disconnect(e)
        return []


async def _get_extents(abus, ref) -> tuple[int, int, int, int]:
    try:
        body = await _call(abus, ref, "org.a11y.atspi.Component", "GetExtents", "u", [0])
        # GetExtents returns one struct (iiii); dbus-next hands it back
        # wrapped: body == [[x, y, w, h]].
        x, y, w, h = body[0]
        return (int(x), int(y), int(x) + int(w), int(y) + int(h))
    except Exception as e:
        _reraise_if_disconnect(e)
        return (0, 0, 0, 0)


async def _get_actions(abus, ref) -> list:
    try:
        body = await _call(abus, ref, "org.a11y.atspi.Action", "GetActions")
        return list(body[0])
    except Exception as e:
        _reraise_if_disconnect(e)
        return []


async def _get_interfaces(abus, ref) -> tuple[str, ...]:
    try:
        body = await _call(
            abus, ref, "org.freedesktop.DBus.Properties", "Get",
            "ss", ["org.a11y.atspi.Accessible", "Interfaces"],
        )
        v = body[0]
        items = v.value if hasattr(v, "value") else v
        return tuple(str(i) for i in items)
    except Exception as e:
        _reraise_if_disconnect(e)
        return ()


async def _get_parent(abus, ref) -> tuple[str, str] | None:
    try:
        body = await _call(
            abus, ref, "org.freedesktop.DBus.Properties", "Get",
            "ss", ["org.a11y.atspi.Accessible", "Parent"],
        )
        v = body[0]
        val = v.value if hasattr(v, "value") else v
        return (str(val[0]), str(val[1]))
    except Exception as e:
        _reraise_if_disconnect(e)
        return None


async def _get_index_in_parent(abus, ref) -> int:
    try:
        body = await _call(
            abus, ref, "org.freedesktop.DBus.Properties", "Get",
            "ss", ["org.a11y.atspi.Accessible", "IndexInParent"],
        )
        v = body[0]
        return int(v.value if hasattr(v, "value") else v)
    except Exception as e:
        _reraise_if_disconnect(e)
        return -1


async def _pid_of(abus, bus_name: str) -> int:
    """PID of the process owning a unique name on the accessibility bus."""
    try:
        body = await _call(
            abus, _DBUS_DRIVER, "org.freedesktop.DBus",
            "GetConnectionUnixProcessID", "s", [bus_name],
        )
        return int(body[0])
    except Exception as e:
        _reraise_if_disconnect(e)
        return 0


async def _node(abus, ref, want_extents: bool = True) -> _Node | None:
    """Fetch everything pctr needs about one node, all in flight at once."""
    calls = [
        _get_name(abus, ref),
        _get_role(abus, ref),
        _get_interfaces(abus, ref),
        _get_children(abus, ref),
    ]
    if want_extents:
        calls.append(_get_extents(abus, ref))
    try:
        # return_exceptions: a node that errors must not leave its siblings
        # running as orphan tasks (which then print "Future exception was
        # never retrieved" at exit).
        res = await asyncio.gather(*calls, return_exceptions=True)
    except Exception:
        return None
    name, role, ifaces, kids = res[0], res[1], res[2], res[3]
    rect = res[4] if want_extents else (0, 0, 0, 0)
    for r in res:
        if isinstance(r, BaseException):
            _reraise_if_disconnect(r)
    if isinstance(name, BaseException):
        name = ""
    if isinstance(role, BaseException):
        role = ""
    if not isinstance(ifaces, tuple):
        ifaces = ()
    if not isinstance(kids, list):
        kids = []
    if not (isinstance(rect, tuple) and len(rect) == 4):
        rect = (0, 0, 0, 0)
    return _Node(ref=ref, name=name, role=role, rect=rect, interfaces=ifaces, children=kids)


def _control_type(role: str) -> str:
    return ROLE_MAP.get(_normalize_role(role), role or "Unknown")


async def _walk(abus, root_ref, max_depth: int = 99, limit: int = 400,
                concurrency: int = 32) -> list[ElementInfo]:
    """Breadth-first walk where each level is fetched concurrently.

    Concurrency is capped: dbus-next's writer raises EAGAIN (and leaks the
    exception on an unawaited future) once thousands of messages are in flight
    at once. 32 keeps ~8x over the old serial walk with headroom.
    """
    rows: list[ElementInfo] = []
    level: list[tuple[tuple[str, str], int]] = [(root_ref, 1)]
    sem = asyncio.Semaphore(concurrency)

    async def fetch(ref):
        async with sem:
            return await _node(abus, ref)

    while level and len(rows) < limit:
        nodes = await asyncio.gather(*[fetch(r) for r, _ in level], return_exceptions=True)
        nxt: list[tuple[tuple[str, str], int]] = []
        for (ref, depth), n in zip(level, nodes):
            if isinstance(n, BaseException):
                _reraise_if_disconnect(n)
                continue
            if n is None:
                continue
            ctype = _control_type(n.role)
            if n.name or ctype != "Unknown":
                rows.append(ElementInfo(
                    name=n.name,
                    control_type=ctype,
                    automation_id="",
                    rect=n.rect,
                    depth=depth,
                    ref=ref,
                    interfaces=n.interfaces,
                ))
            if depth < max_depth:
                nxt.extend((c, depth + 1) for c in n.children)
        level = nxt
    return rows[:limit]


async def _app_infos(abus) -> list[AppInfo]:
    apps = await _get_children(abus, _DESKTOP_REF)
    infos = await asyncio.gather(*[_app_info(abus, a) for a in apps])
    return [i for i in infos if i is not None]


async def _app_info(abus, ref) -> AppInfo | None:
    name, pid = await asyncio.gather(_get_name(abus, ref), _pid_of(abus, ref[0]))
    if not name and not pid:
        return None
    return AppInfo(name=name, pid=pid, bus=ref[0], path=ref[1])


async def _app_windows(abus) -> list[AppWindow]:
    """Every frame/window/dialog node up to depth 3 under every application."""
    apps = await _app_infos(abus)
    results = await asyncio.gather(
        *[_windows_under(abus, app.ref, app, 0) for app in apps],
        return_exceptions=True,
    )
    out: list[AppWindow] = []
    for r in results:
        if isinstance(r, BaseException):
            _reraise_if_disconnect(r)
            continue
        out.extend(r)
    return out


async def _windows_under(abus, ref, app: AppInfo, depth: int) -> list[AppWindow]:
    if depth > 2:
        return []
    kids = await _get_children(abus, ref)
    found: list[AppWindow] = []
    nested: list[tuple[tuple[str, str], int]] = []
    for kid in kids:
        role = _normalize_role(await _get_role(abus, kid))
        if role in _WINDOW_ROLES:
            title = await _get_name(abus, kid)
            found.append(AppWindow(title=title or app.name, pid=app.pid,
                                   bus=kid[0], path=kid[1], app_name=app.name))
            nested.append((kid, depth + 1))
        elif depth == 0:
            # panels/overlays can host real dialogs one level down
            nested.append((kid, depth + 1))
    for kid, d in nested:
        found.extend(await _windows_under(abus, kid, app, d))
    return found


# --- persistent session --------------------------------------------------------


def _is_disconnect(exc: BaseException) -> bool:
    text = str(exc).lower()
    return isinstance(exc, (ConnectionError, EOFError, asyncio.IncompleteReadError)) or any(
        h in text for h in _DISCONNECT_HINTS
    )


class _Session:
    """One long-lived AT-SPI connection, driven from a background event loop."""

    def __init__(self):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._bus = None
        self._abus = None
        self._conn_lock = None
        self._lock = threading.Lock()

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is not None and self._loop.is_running():
                return self._loop
            loop = asyncio.new_event_loop()
            t = threading.Thread(target=loop.run_forever, name="pctr-atspi", daemon=True)
            t.start()
            # run_forever() is not instantaneous; wait so a racing caller cannot
            # see is_running() == False and spawn a second loop.
            for _ in range(200):
                if loop.is_running():
                    break
                time.sleep(0.001)
            self._loop, self._thread = loop, t
            return loop

    def _drop(self) -> None:
        bus, abus, loop = self._bus, self._abus, self._loop
        self._bus = self._abus = None
        if bus is None or loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(_close_both(bus, abus), loop).result(2)
        except Exception:
            pass

    async def _run_once(self, fn: Callable):
        # Atomic on this loop: two concurrent first calls must not both connect.
        if self._conn_lock is None:
            self._conn_lock = asyncio.Lock()
        async with self._conn_lock:
            if self._abus is None:
                self._bus, self._abus = await _open()
        return await fn(self._bus, self._abus)

    def run(self, fn: Callable, timeout: float = 60.0):
        """Run ``await fn(bus, abus)`` on the shared loop; reconnect once if dropped."""
        loop = self._ensure_loop()
        last: BaseException | None = None
        for attempt in (0, 1):
            fut = asyncio.run_coroutine_threadsafe(self._run_once(fn), loop)
            try:
                return fut.result(timeout)
            except FuturesTimeout:
                fut.cancel()
                raise RuntimeError("AT-SPI call timed out after %.0fs" % timeout)
            except Exception as e:  # noqa: BLE001 - re-raised below
                last = e
                if attempt == 0 and _is_disconnect(e):
                    self._drop()
                    continue
                raise
        raise last  # pragma: no cover - loop always raises or returns

    def close(self) -> None:
        """Drain and stop the background loop without leaking task exceptions."""
        loop = self._loop
        self._drop()
        if loop is None or not loop.is_running():
            return

        async def _shutdown():
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for t in tasks:
                t.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        try:
            asyncio.run_coroutine_threadsafe(_shutdown(), loop).result(3)
        except Exception:
            pass
        loop.call_soon_threadsafe(loop.stop)


_SESSION: _Session | None = None
_SESSION_LOCK = threading.Lock()


def _session() -> _Session:
    global _SESSION
    with _SESSION_LOCK:
        if _SESSION is None:
            _SESSION = _Session()
        return _SESSION


def close_session() -> None:
    """Drop the shared AT-SPI connection (process shutdown / tests)."""
    global _SESSION
    with _SESSION_LOCK:
        s, _SESSION = _SESSION, None
    if s is not None:
        s.close()


atexit.register(close_session)


# --- public client -------------------------------------------------------------


class AtspiClient:
    """Synchronous facade over the AT-SPI D-Bus client."""

    def __init__(self):
        self._available: bool | None = None
        self._win_cache: tuple[float, list[AppWindow]] | None = None

    # -- discovery --

    def available(self) -> bool:
        if self._available is None:
            try:
                self._available = bool(self.apps())
            except Exception:
                self._available = False
        return self._available

    def apps(self) -> list[AppInfo]:
        """Applications currently on the accessibility bus."""
        return _session().run(lambda bus, abus: _app_infos(abus))

    def windows(self) -> list[AppWindow]:
        """Top-level windows across every application, with real PIDs."""
        now = time.monotonic()
        if self._win_cache is not None and now - self._win_cache[0] < _WINDOW_CACHE_TTL:
            return self._win_cache[1]
        wins = _session().run(lambda bus, abus: _app_windows(abus))
        self._win_cache = (now, wins)
        return wins

    def resolve_window(self, title: str | None = None, process: int | None = None,
                       literal: bool = False) -> AppWindow | None:
        """Find a window by title regex and/or PID.

        Resolution order: title+PID, title only, PID only. Falls back to
        matching the application name, which is what you want for apps that
        name their frame something unrelated to the app.
        """
        try:
            wins = self.windows()
        except Exception:
            return None
        try:
            # literal=True is the common case: a real window title, not a pattern.
            rx = re.compile(re.escape(title) if literal else title, re.I) if title else None
        except re.error:
            rx = re.compile(re.escape(title), re.I) if title else None

        def match(w: AppWindow) -> bool:
            return (rx is None or rx.search(w.title) is not None) and (process is None or w.pid == process)

        hits = [w for w in wins if match(w)]
        if hits:
            # First match in bus enumeration order (apps are listed parent-first,
            # so this is the app's primary frame in practice).
            return hits[0]
        if rx is not None:
            by_app = [w for w in wins if rx.search(w.app_name or "") is not None
                      and (process is None or w.pid == process)]
            if by_app:
                return by_app[0]
        if process is not None:
            owned = [w for w in wins if w.pid == process]
            if owned:
                return owned[0]
        return None

    # -- element tree --

    def get_elements(self, ref_or_title, max_depth: int = 12, limit: int = 400,
                     process: int | None = None) -> list[ElementInfo]:
        """Element rows for an AT-SPI ref tuple, or for a window title."""
        ref = None
        if isinstance(ref_or_title, tuple):
            ref = ref_or_title
        else:
            win = self.resolve_window(ref_or_title, process)
            if win is not None:
                ref = win.ref
        if ref is None:
            return []
        try:
            return _session().run(lambda bus, abus: _walk(abus, ref, max_depth, limit))
        except Exception as e:
            raise SystemExit("accessibility bus error: %s" % e)

    def get_tree(self, ref_or_title, max_depth: int = 99, limit: int = 400,
                 process: int | None = None) -> TreeResult:
        """Element rows plus an honest verdict on whether the app is automatable."""
        ref = None
        if isinstance(ref_or_title, tuple):
            ref = ref_or_title
        else:
            win = self.resolve_window(ref_or_title, process)
            if win is not None:
                ref = win.ref
        if ref is None:
            return TreeResult(elements=[], error="window not present on the accessibility bus",
                              backend="atspi")
        try:
            rows = _session().run(lambda bus, abus: _walk(abus, ref, max_depth, limit))
        except Exception as e:
            return TreeResult(elements=[], error="AT-SPI: %s" % e, backend="atspi")

        # A depth-limited or limit-capped walk only proves the nodes we saw
        # exist: never call an app opaque because the caller asked for --depth 1.
        if max_depth < 99 or len(rows) >= limit:
            return TreeResult(
                elements=[(el, el.depth) for el in rows],
                opaque=False,
                error=None,
                backend="atspi",
            )
        interactive = any(r.control_type in _INTERACTIVE_ROLES for r in rows)
        # A tree made only of containers and the window title, with nothing
        # named inside it, is an app that never published its widgets. Nested
        # pane-only shells (Electron) count; a window with real labelled text
        # does not, so a plain label dialog is not mislabelled "opaque".
        skin_deep = all(r.control_type in _CONTAINER_TYPES for r in rows)
        title = rows[0].name if rows else ""
        unnamed = all(not r.name or r.name == title for r in rows)
        opaque = bool(rows) and not interactive and skin_deep and unnamed and len(rows) <= 24
        error = _OPAQUE_HINT if opaque else None
        if not rows:
            error = "window exposes no accessible children"
        return TreeResult(
            elements=[(el, el.depth) for el in rows],
            opaque=opaque,
            error=error,
            backend="atspi",
        )

    # -- actions --

    def action_names(self, ref: tuple[str, str]) -> list[str]:
        """Names of the invokable actions an element advertises."""
        try:
            actions = _session().run(lambda bus, abus: _get_actions(abus, ref))
        except Exception:
            return []
        out = []
        for a in actions:
            # AT-SPI hands back (name, description, keybinding) triples.
            try:
                out.append(str(a[0]))
            except Exception:
                out.append(str(a))
        return out

    def invoke(self, ref: tuple[str, str], action: str | None = None) -> bool:
        """Do an AT-SPI action by name (default: the first one)."""

        async def _go(bus, abus):
            actions = await _get_actions(abus, ref)
            if not actions:
                return False
            idx = 0
            if action is not None:
                idx = -1
                for i, a in enumerate(actions):
                    try:
                        if str(a[0]).lower() == action.lower():
                            idx = i
                            break
                    except Exception:
                        continue
                if idx < 0:
                    return False  # never fall through to action 0
            await _call(abus, ref, "org.a11y.atspi.Action", "DoAction", "i", [idx])
            return True

        try:
            return _session().run(_go)
        except Exception:
            return False

    def do_action_by_name(self, ref: tuple[str, str], name: str) -> bool:
        """Invoke a specific named action (toggle, expand, press, ...)."""
        return self.invoke(ref, name)

    def set_text(self, ref: tuple[str, str], text: str) -> bool:
        async def _go(bus, abus):
            await _call(abus, ref, "org.a11y.atspi.EditableText", "SetTextContents", "s", [text])
            return True

        try:
            return _session().run(_go)
        except Exception:
            return False

    def get_text(self, ref: tuple[str, str]) -> str:
        async def _go(bus, abus):
            body = await _call(abus, ref, "org.freedesktop.DBus.Properties", "Get",
                               "ss", ["org.a11y.atspi.Text", "Text"])
            v = body[0]
            return str(v.value) if hasattr(v, "value") else str(v)

        try:
            return _session().run(_go)
        except Exception:
            return ""

    def get_value(self, ref: tuple[str, str]) -> float | str | None:
        """Current value of a Value/Slider/SpinButton, if it has one."""
        async def _go(bus, abus):
            body = await _call(abus, ref, "org.freedesktop.DBus.Properties", "Get",
                               "ss", ["org.a11y.atspi.Value", "CurrentValue"])
            v = body[0]
            return v.value if hasattr(v, "value") else v

        try:
            return _session().run(_go)
        except Exception:
            return None

    def set_value(self, ref: tuple[str, str], value: float) -> bool:
        """Set a Value/Slider/SpinButton and report whether the app accepted it."""

        async def _go(bus, abus):
            body = await _call(abus, ref, "org.a11y.atspi.Value", "SetCurrentValue", "d", [float(value)])
            return bool(body[0]) if body else False

        try:
            return _session().run(_go)
        except Exception:
            return False

    def select(self, ref: tuple[str, str], deselect: bool = False) -> bool:
        """Select/deselect a list item, table cell, or tab.

        AT-SPI's Selection.SelectChild(index) lives on the *container*, and the
        index is the child's position in it -- so resolve Parent + IndexInParent
        first instead of guessing index 0.
        """

        async def _go(bus, abus):
            parent, index = await asyncio.gather(_get_parent(abus, ref),
                                                _get_index_in_parent(abus, ref))
            method = "DeselectChild" if deselect else "SelectChild"
            if parent is not None and index >= 0:
                await _call(abus, parent, "org.a11y.atspi.Selection", method, "i", [index])
                return True
            # Containers that expose Selection directly on the item (rare).
            await _call(abus, ref, "org.a11y.atspi.Selection", method, "i", [0])
            return True

        try:
            return _session().run(_go)
        except Exception:
            return False

    def text_at(self, ref: tuple[str, str], offset: int = 0) -> str:
        """Text around a character offset (Text interface)."""
        async def _go(bus, abus):
            body = await _call(abus, ref, "org.a11y.atspi.Text", "GetTextAtOffset", "iu",
                               [int(offset), 0])
            return str(body[0])

        try:
            return _session().run(_go)
        except Exception:
            return ""

    def scroll_to(self, ref: tuple[str, str]) -> bool:
        """Scroll the target into view (Component.ScrollTo)."""

        async def _go(bus, abus):
            await _call(abus, ref, "org.a11y.atspi.Component", "ScrollTo", "u", [0])
            return True

        try:
            return _session().run(_go)
        except Exception:
            return False
