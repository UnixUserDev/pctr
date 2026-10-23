# pctr

**PCTR — Personal Computer Tactile Response**

Element-based UI automation CLI for AI agents. **Windows (UIA)** and **Linux (AT-SPI2), X11 and Wayland**.

Instead of screenshotting and guessing pixel coordinates, `pctr` finds controls
by their **name / control type / automation id** and acts on them. Window moves,
DPI changes, and layout shifts don't break your script.

On Windows it is built on `pywinauto` (UI Automation) with `pyautogui` /
`pydirectinput` for raw mouse and keyboard. On Linux it uses AT-SPI2 (over the
accessibility D-Bus, no `pyatspi` needed), XTest (`python-xlib`) for input,
`mss`/compositor capture for screenshots, and `rapidocr`/tesseract for OCR.
On top of that it offers **OCR** and **YOLO-World** open-vocabulary detection for
text and objects that aren't exposed as controls.

Linux notes (0.6.0):

* `pctr windows` lists real applications with real PIDs, including native
  Wayland clients that X cannot see — the window list comes from the
  accessibility bus, not from X atoms.
* The tree walk is breadth-first and concurrent: the walk for a 375-node Qt
  window takes ~0.4–1.0 s instead of ~5.5 s serially. End-to-end `pctr tree`
  is ~0.6–1.8 s on this machine, interpreter startup and window discovery
  included.
* `tree --json` carries `opaque` and `error`. `opaque: true` means the app
  answered but publishes no widgets (Electron without
  `--force-renderer-accessibility`, JVM without the ATK bridge) — that is a
  different problem from "the accessibility bus is down", and pctr says which.
* Act by *action*, not by coordinates: `pctr action --list`, `pctr action --do`,
  `pctr value --set`, `pctr select`. AT-SPI actions reach native Wayland
  windows; synthetic XTest clicks only reach XWayland clients.


![pctr driving by element name: enumerating windows, mapping virtual desktops, dumping the control tree, filling a field, then reading it back with OCR](docs/demo.gif)

*Driven entirely by pctr itself - list windows, map virtual desktops, dump a
control tree, `set` a field, read it back with OCR, screenshot it, and YOLO-World
detect. Recorded with OBS, which pctr also piloted.*

## Install

### Windows

```bash
pip install "pctr[windows]"   # UIA + input
pip install "pctr[vision]"    # + ultralytics, for `pctr look`
pip install "pctr[mcp]"       # + MCP SDK, for the MCP server
pip install "pctr[pad]"       # + vgamepad, for `pctr pad` (needs ViGEmBus)
pip install "pctr[serial]"    # + pyserial, for `pctr hid` / the serial-hid backend
pip install "pctr[all]"       # everything
```

### Linux (X11 and Wayland)

```bash
pip install "pctr[linux]"     # python-xlib + dbus-next + rapidocr
```

Requires the accessibility bus to be running (`at-spi2-core`; on most desktops
it already is). Element targeting uses AT-SPI2, so **apps must expose
accessibility** (GTK/Qt/Electron do; many X11 toolkits and games do not).
Screenshots use `mss`; under Wayland it falls back to `spectacle` (KDE) or
`grim`.

Elements, window discovery and AT-SPI actions work on **both display servers**:
`--backend wayland` is a real backend, not a stub. What still needs X is
synthetic input (XTest), screenshots and window geometry — those raise a clear
error instead of silently doing nothing when no X display exists. Native Wayland
clients cannot receive synthetic clicks (protocol design), so use
`pctr action` / `pctr value` / `pctr select` for those; coordinate clicks only
reach XWayland clients. Pure-Wayland input (libei / xdg-desktop-portal
RemoteDesktop) is not implemented yet. Use `--backend auto|windows|x11|wayland`
to pick explicitly.

### Both

```bash
pip install "pctr[ocr]"       # rapidocr + pytesseract
```

## MCP server

`pctr` ships a Model Context Protocol server so MCP clients (Claude Desktop,
Cursor, opencode, ...) can drive the desktop directly:

```bash
pip install "pctr[mcp]"
```

Add it to your client config (stdio):

```json
{ "mcpServers": { "pctr": { "command": "pctr-mcp" } } }
```

(or `"command": "python", "args": ["-m", "pctr.mcp_server"]`)

Tools exposed: `pctr_windows`, `pctr_tree`, `pctr_find`, `pctr_click`,
`pctr_set`, `pctr_type`, `pctr_keys`, `pctr_hotkey`, `pctr_focus`, `pctr_wait`,
`pctr_shot`, `pctr_ocr`, `pctr_ocrfind`, `pctr_ocrclick`, `pctr_look`,
`pctr_desktop`.

Or run/manage it from the CLI:

```bash
pctr mcp status                                          # SDK + server state
pctr mcp start --transport streamable-http --port 8765   # background HTTP server
pctr mcp restart | pctr mcp stop
pctr mcp help
```

`stdio` runs in the foreground (what a client spawns); `sse` / `streamable-http`
run in the background, reachable at `http://127.0.0.1:8765/mcp` (or `…/sse`).

## Commands

| Command | What it does |
|---------|--------------|
| `pctr windows [--filter RE] [--process PID]` | List top-level windows (`pid=… \| title`). |
| `pctr tree --title RE [--depth N] [--limit N]` | Dump the UIA control tree of a window. |
| `pctr find --title RE [--name RE] [--limit N] [--nth N]` | List matching elements. |
| `pctr click --title RE --name RE [--method auto\|invoke\|mouse] [--dbl]` | Click an element. |
| `pctr set --title RE --name RE --text S` | Set a field via the UIA ValuePattern (exact, instant). |
| `pctr action --title RE --name RE --list` / `--do "Show Menu"` / `--toggle` | List or invoke named AT-SPI actions (Linux). Works on native Wayland windows. |
| `pctr value --title RE --name RE [--set N]` | Read or set a slider/spinner/progress Value (Linux). |
| `pctr select --title RE --name RE [--deselect]` | Select a list item, table cell, or tab (Linux). |
| `pctr text --title RE --name RE [--offset N]` | Read text at a character offset (Linux). |
| `pctr scroll --title RE --name RE` | Scroll an element into view (Linux). |
| `pctr type [--title RE --name RE] --text S [--delay 0.03] [--chunk 1]` | Send keystrokes (see notes). |
| `pctr keys --keys "{ENTER}"` | Send a global key combo (pywinauto syntax). |
| `pctr hotkey --keys ctrl+s` | Send a modifier combo (prefer this over `keys "^s"`). |
| `pctr focus --title RE` | Bring a window to the foreground. |
| `pctr wait --title RE --name RE [--timeout S]` | Wait until an element exists. |
| `pctr shot [--title RE] --out PATH` | Screenshot the screen or a window. |
| `pctr size` | Print primary screen size as `WxH`. |
| `pctr desktop <action>` | Virtual desktops: `list` / `windows` / `where` / `on-current` / `next` / `prev` / `new` / `close` / `move-window`. |
| `pctr move --x N --y N` / `down` / `up` / `hold --ms N` | Raw mouse control (`--direct` for games). |
| `pctr drag --start x,y --end x,y [--duration S]` | Drag between two points. |
| `pctr keydown / keyup --key a` / `keyhold --key w --ms 1500` | Hold keyboard keys (`--direct` for games). |
| `pctr ocr [--title RE] [--lang TAG]` | OCR the screen/window; list words with boxes. |
| `pctr ocrfind --text RE [--lang TAG]` | OCR then list matches with click centers (single words). |
| `pctr ocrclick --text RE [--nth N] [--lang TAG]` | OCR then click a word. |
| `pctr look --for "a dog" [--title RE] [--conf X]` | YOLO-World detect objects by text prompt. |
| `pctr lookclick --for "a dog" [--nth N]` | Detect then click the top match. |
| `pctr pad <button\|stick\|trigger\|status>` | Virtual Xbox 360 gamepad via ViGEmBus. |
| `pctr hid <action> --port COMx` | Serial-HID bridge to a real USB-HID microcontroller. |
| `pctr mcp <start\|stop\|restart\|status\|help>` | Manage the MCP server (stdio or background http/sse). |
| `pctr skill [--install]` / `pctr setup` | Print/install the agent skill + an AGENTS.md section. |

Global flags: `--json` (machine-readable output) and `--file FILE` (run a batch
of commands from a file).

Common filters: `--title` (regex), `--name` (regex), `--control-type`
(`Button`, `Edit`, `Pane`, `MenuItem`, …), `--auto-id`, `--nth`, `--process`.

`click`, `set`, `wait` accept **any** selector - `--name` is not required.
`tree`/`find` require a window selector (`--title` or `--process`).

## The loop

```bash
pctr tree  --title "Notepad" --limit 60      # see what's clickable
pctr click --title "Notepad" --name "^File$"
pctr set   --title "Notepad" --name "Text Editor" --text "hello"
```

## JSON output

Add `--json` (global, or per-command) for machine-readable output from
`windows`, `tree`, `find`, `ocr`, `ocrfind`, `look`, and `desktop`:

```bash
pctr windows --json
# [{"hwnd":1234,"pid":5678,"title":"Untitled - Notepad"}, ...]

pctr desktop list --json
# [{"index":0,"guid":"ae37…","current":true,"windows":11}, ...]

pctr look --for "a car" --json
# [{"label":"a car","conf":0.86,"x":512,"y":300,"box":[400,220,620,380]}, ...]
```

Progress and warnings go to stderr, so stdout stays valid JSON.

## Batch files

```bash
pctr --file script.txt          # one command per line; # comments and blanks skipped
pctr --json --file script.txt   # --json applies to every line
```

Stops on the first failing line and reports its line number.

## Virtual desktops

```bash
pctr desktop list                       # desktops (#index, guid, [current], window count)
pctr desktop windows --desktop 1        # windows on desktop #1
pctr desktop where --title "App"        # which desktop a window is on
pctr desktop on-current --title "App"   # True / False
pctr desktop next | prev | new | close  # switch (moves the active desktop)
pctr desktop move-window --title RE --to N
```

- Switching uses the standard Win+Ctrl hotkeys and moves the **active** desktop
  (you included). Add `--direct` if a game has focus.
- **Cross-desktop control:** UIA actions (`click --method invoke`, `set`) reach
  windows on another desktop **without switching** (accessibility layer, not
  screen input). Raw mouse/keys only affect the active desktop.
- **`move-window` is blocked on Windows 11** (third-party `MoveWindowToDesktop`
  returns `Access denied`); it works on Windows 10.

## Raw input backends

The OS flags `SendInput` events as injected, so anything built on user32 is
detectable in principle. `pctr` can route raw mouse/keyboard through a different
backend with `--backend`:

| Backend | How | Detectable? |
|---|---|---|
| `pyautogui` (default / `sendinput`) | user32 `SendInput`, virtual keys | yes (`injected` flag) |
| `pydirectinput` (or `--direct`) | `SendInput` with **scancodes** — better for DirectInput games | yes |
| `serial-hid` | a **microcontroller presenting real USB HID** over serial | no — it *is* real hardware |

```bash
pctr move --backend pydirectinput --x 800 --y 400
pctr keyhold --key w --ms 400 --backend pydirectinput
pctr type --text "hi" --backend serial-hid --port COM5   # needs a board
```

Applies to `move` / `down` / `up` / `hold` / `keydown` / `keyup` / `keyhold` /
`hotkey` / `drag` / `type` / `desktop`.

### Gamepad

```bash
pip install "pctr[pad]"     # + the ViGEmBus driver
pctr pad button  --name A --ms 150
pctr pad button  --name DPAD_UP --ms 100
pctr pad stick   --x 1.0 --y -0.5 --ms 400
pctr pad trigger --right 1.0 --ms 300
```

### Serial-HID bridge

Firmware protocol, newline-terminated ASCII:
`MOVE x y` · `CLICK b` · `DOWN b` · `UP b` · `KEY k DOWN|UP` · `WRITE text` · `HOTKEY a+b`

```bash
pip install "pctr[serial]"
pctr hid ports                                  # list COM ports
pctr hid move --x 500 --y 300 --port COM5
pctr hid type --text "hello" --port COM5
```

## Exit codes

- `0` success, `1` no match / window not found, `2` usage/validation error.
- Errors print one `error: …` line - no tracebacks.

## Notes

- Prefer `set` (ValuePattern) over `type` for text fields - it can't drop or
  reorder characters. `type`'s element path uses pywinauto `type_keys`, which
  interprets `{ } + ^ % ~ ( )` as key syntax, so it is **not** literal.
- Global `type` / `keys` go to the OS-focused window - `pctr focus` first.
- Prefer `pctr hotkey --keys ctrl+s` over `keys "^s"`; the `^` modifier is
  fragile through shells.
- `ocrfind`/`ocrclick` match one **word** at a time (Windows OCR tokenizes words).
- `look` reads its model from `--model` or the `PCTR_YOLO_MODEL` env var; progress
  bars go to stderr so stdout stays parseable.
- Qt apps expose a rich tree including embedded webviews. Electron apps expose
  only the window frame (use `focus` + global keys, or drive them over CDP).
- Games ignore synthetic input - pass `--direct` to mouse/key commands and focus
  the game first.

## License

MIT

---

AI-generated with [OpenCode](https://opencode.ai) — primary model: DeepSeek V4.1 Flash.
