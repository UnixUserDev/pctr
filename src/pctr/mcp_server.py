"""pctr MCP server - exposes pctr's element-based UI automation as MCP tools.

Run over stdio (default). Add it to an MCP client (Claude Desktop, Cursor,
opencode, ...) as:

    { "command": "pctr-mcp" }

or:

    { "command": "python", "args": ["-m", "pctr.mcp_server"] }

Needs the MCP SDK:  pip install "pctr[mcp]"
"""

import subprocess
import sys

try:
    from mcp.server.mcpserver import MCPServer
except Exception as e:  # pragma: no cover
    raise SystemExit('pctr-mcp needs the MCP SDK: pip install "pctr[mcp]" (%s)' % e)

try:
    from pctr import __version__ as _VERSION
except Exception:  # pragma: no cover
    _VERSION = ""

server = MCPServer(
    "pctr",
    version=_VERSION or "0.0.0",
    instructions=(
        "Element-based UI automation. Find controls by name / control "
        "type / automation id instead of pixel coordinates, then click/type/etc. "
        "Also: OCR (ocr/ocrfind/ocrclick), YOLO-World (look), and virtual desktops "
        "(desktop). Supports Windows (UIA) and Linux (AT-SPI2) on both X11 and "
        "Wayland. On Linux, tree output carries opaque=true when an application "
        "publishes no accessibility tree (Electron/JVM) -- that is not an empty "
        "window, and clicking its elements by coordinates will not work. Prefer "
        "action/value/select over mouse clicks: they go through the accessibility "
        "API and work on native Wayland windows too."
    ),
)


def _run(args):
    """Run the pctr CLI and return stdout (+ stderr and exit code on failure)."""
    cmd = [sys.executable, "-m", "pctr"] + [str(a) for a in args]
    p = subprocess.run(cmd, capture_output=True, text=True)
    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()
    if p.returncode != 0:
        parts = ["exit=%d" % p.returncode]
        if out:
            parts.append(out)
        if err:
            parts.append(err)
        return "\n".join(parts)
    return out or "(no output)"


def _selectors(title=None, process=None, name=None, control_type=None, auto_id=None, nth=None):
    a = []
    if title:
        a += ["--title", title]
    if process is not None:
        a += ["--process", str(process)]
    if name:
        a += ["--name", name]
    if control_type:
        a += ["--control-type", control_type]
    if auto_id:
        a += ["--auto-id", auto_id]
    if nth is not None:
        a += ["--nth", str(nth)]
    return a


@server.tool(description="List top-level windows (pid | title). Filter by --filter regex or process id.")
def pctr_windows(filter: str | None = None, process: int | None = None) -> str:
    a = []
    if filter:
        a += ["--filter", filter]
    if process is not None:
        a += ["--process", str(process)]
    return _run(["windows"] + a)


@server.tool(description="Dump the UIA control tree of a window as JSON (carries opaque/error/backend). Provide --title (or process).")
def pctr_tree(title: str | None = None, process: int | None = None, depth: int | None = None, limit: int | None = None) -> str:
    a = ["tree", "--json"] + _selectors(title=title, process=process)
    if depth is not None:
        a += ["--depth", str(depth)]
    if limit is not None:
        a += ["--limit", str(limit)]
    return _run(a)


@server.tool(description="Find elements in a window and print name/type/id/rect. Any selector works; tree/find need title or process.")
def pctr_find(title: str | None = None, process: int | None = None, name: str | None = None,
              control_type: str | None = None, auto_id: str | None = None,
              nth: int | None = None, limit: int | None = None) -> str:
    a = ["find"] + _selectors(title=title, process=process, name=name,
                              control_type=control_type, auto_id=auto_id, nth=nth)
    if limit is not None:
        a += ["--limit", str(limit)]
    return _run(a)


@server.tool(description="Click an element. method: auto|invoke|mouse (invoke works cross-desktop).")
def pctr_click(title: str | None = None, process: int | None = None, name: str | None = None,
               control_type: str | None = None, auto_id: str | None = None, nth: int | None = None,
               method: str = "auto", button: str = "left", dbl: bool = False) -> str:
    a = ["click"] + _selectors(title=title, process=process, name=name,
                               control_type=control_type, auto_id=auto_id, nth=nth)
    a += ["--method", method, "--button", button]
    if dbl:
        a.append("--dbl")
    return _run(a)


@server.tool(description="Set a field's text via the UIA ValuePattern (exact; prefer over type).")
def pctr_set(text: str, title: str | None = None, process: int | None = None, name: str | None = None,
             control_type: str | None = None, auto_id: str | None = None, nth: int | None = None) -> str:
    a = ["set"] + _selectors(title=title, process=process, name=name,
                             control_type=control_type, auto_id=auto_id, nth=nth)
    a += ["--text", text]
    return _run(a)


@server.tool(description="List or invoke named element actions (AT-SPI: show menu, press, toggle, expand, ...). Use --list first.")
def pctr_action(title: str | None = None, process: int | None = None, name: str | None = None,
                control_type: str | None = None, auto_id: str | None = None, nth: int | None = None,
                do: str | None = None, list_actions: bool = False, toggle: bool = False) -> str:
    a = ["action"] + _selectors(title=title, process=process, name=name,
                                control_type=control_type, auto_id=auto_id, nth=nth)
    if do:
        a += ["--do", do]
    if list_actions:
        a.append("--list")
    if toggle:
        a.append("--toggle")
    return _run(a)


@server.tool(description="Read or set an element's Value (slider, spinner, progress bar).")
def pctr_value(title: str | None = None, process: int | None = None, name: str | None = None,
               control_type: str | None = None, auto_id: str | None = None, nth: int | None = None,
               set_to: float | None = None) -> str:
    a = ["value"] + _selectors(title=title, process=process, name=name,
                               control_type=control_type, auto_id=auto_id, nth=nth)
    if set_to is not None:
        a += ["--set", str(set_to)]
    return _run(a)


@server.tool(description="Select or deselect a list item, table cell, or tab.")
def pctr_select(title: str | None = None, process: int | None = None, name: str | None = None,
                control_type: str | None = None, auto_id: str | None = None, nth: int | None = None,
                deselect: bool = False) -> str:
    a = ["select"] + _selectors(title=title, process=process, name=name,
                                control_type=control_type, auto_id=auto_id, nth=nth)
    if deselect:
        a.append("--deselect")
    return _run(a)


@server.tool(description="Send keystrokes (global or to an element). NOTE: pywinauto key syntax - { } + ^ % ~ ( ) are interpreted, not literal.")
def pctr_type(text: str, title: str | None = None, name: str | None = None,
              delay: float = 0.03, chunk: int = 1) -> str:
    a = ["type", "--text", text, "--delay", str(delay), "--chunk", str(chunk)]
    if title:
        a += ["--title", title]
    if name:
        a += ["--name", name]
    return _run(a)


@server.tool(description="Send a global key combo in pywinauto syntax, e.g. {ENTER}. Prefer hotkey for modifiers.")
def pctr_keys(keys: str) -> str:
    return _run(["keys", "--keys", keys])


@server.tool(description="Send a modifier combo, e.g. ctrl+s, win+shift+s.")
def pctr_hotkey(keys: str) -> str:
    return _run(["hotkey", "--keys", keys])


@server.tool(description="Bring a window to the foreground.")
def pctr_focus(title: str | None = None, process: int | None = None) -> str:
    return _run(["focus"] + _selectors(title=title, process=process))


@server.tool(description="Wait until an element exists.")
def pctr_wait(title: str | None = None, process: int | None = None, name: str | None = None,
              control_type: str | None = None, auto_id: str | None = None, timeout: float = 10.0) -> str:
    a = ["wait"] + _selectors(title=title, process=process, name=name,
                              control_type=control_type, auto_id=auto_id)
    a += ["--timeout", str(timeout)]
    return _run(a)


@server.tool(description="Screenshot the screen (or a window) to a file path.")
def pctr_shot(out: str, title: str | None = None, process: int | None = None) -> str:
    a = ["shot", "--out", out] + _selectors(title=title, process=process)
    return _run(a)


@server.tool(description="OCR the screen (or a window); list recognised words with boxes.")
def pctr_ocr(title: str | None = None, process: int | None = None) -> str:
    return _run(["ocr"] + _selectors(title=title, process=process))


@server.tool(description="OCR then list words matching --text (single words).")
def pctr_ocrfind(text: str, title: str | None = None, process: int | None = None) -> str:
    return _run(["ocrfind", "--text", text] + _selectors(title=title, process=process))


@server.tool(description="OCR then click the word matching --text.")
def pctr_ocrclick(text: str, title: str | None = None, process: int | None = None, nth: int = 0) -> str:
    return _run(["ocrclick", "--text", text, "--nth", str(nth)] + _selectors(title=title, process=process))


@server.tool(description="YOLO-World open-vocabulary detection by text prompt (e.g. 'a blue folder icon').")
def pctr_look(for_: str, title: str | None = None, process: int | None = None, conf: float | None = None) -> str:
    a = ["look", "--for", for_] + _selectors(title=title, process=process)
    if conf is not None:
        a += ["--conf", str(conf)]
    return _run(a)


@server.tool(description="Virtual desktops: action = list | windows | where | on-current | next | prev | new | close | move-window.")
def pctr_desktop(action: str, title: str | None = None, process: int | None = None,
                 desktop: int | None = None, to: int | None = None) -> str:
    a = ["desktop", action] + _selectors(title=title, process=process)
    if desktop is not None:
        a += ["--desktop", str(desktop)]
    if to is not None:
        a += ["--to", str(to)]
    return _run(a)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(
        prog="pctr-mcp",
        description="pctr MCP server - element-based UI automation over MCP.",
    )
    ap.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default="stdio",
                    help="stdio (default, for MCP clients) or an HTTP transport")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args(argv)
    if a.transport == "streamable-http":
        server.run(transport="streamable-http", host=a.host, port=a.port)
    elif a.transport == "sse":
        server.run(transport="sse", host=a.host, port=a.port)
    else:
        server.run(transport="stdio")


if __name__ == "__main__":
    main()
