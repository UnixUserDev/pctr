---
name: pctr
description: Drive native desktop apps (not browsers) with the pctr CLI - Windows via pywinauto/UIA, Linux via AT-SPI2 on X11 and Wayland - with OCR and YOLO-World fallbacks. Use whenever you must control or inspect a desktop GUI - list windows, dump a control tree, click buttons, type into fields, read on-screen text by OCR, find objects by description, send hotkeys, hold keys, drag, or screenshot an app. Element-based, no pixel hunting. Triggers: pctr, UIA, AT-SPI, UI automation, pywinauto, desktop automation, click a button in an app, type into an app, drive a GUI, control a desktop application, usecomputer.
---

# pctr - desktop control

**PCTR — Personal Computer Tactile Response.** Element-based automation.
Find a control by its name / control type / automation id and act on it, so
window moves, DPI, and layout changes don't break you. Windows uses UI
Automation (`pywinauto` + `uiautomation`); Linux uses AT-SPI2 (accessibility
D-Bus) on both X11 and Wayland, plus XTest where an X display exists. Pick the
platform with `--backend auto|windows|x11|wayland` (`auto` is correct almost
always).

On Linux, act through the accessibility API when you can: `action` / `value` /
`select` reach native Wayland windows, while synthetic mouse clicks only reach
XWayland clients. If `tree --json` says `"opaque": true`, the app publishes no
widgets at all - stop hunting for element names and fall back to `ocr`,
`look`, or a keyboard shortcut.

## Three ways to find a target

1. **UIA elements** (best) - `tree` / `find` / `click` by control name/id.
2. **OCR** - `ocr` / `ocrfind` / `ocrclick` (Windows built-in `Windows.Media.Ocr`, no deps) reads on-screen text that isn't exposed as a control.
3. **YOLO-World** - `look --for "..."` / `lookclick --for "..."` open-vocabulary object detection by natural-language prompt (canvas, games, custom-drawn UI).

## The loop

```bash
pctr windows                                   # top-level windows + pids
pctr tree   --title "Notepad" --limit 120      # dump the control tree
pctr find   --title "Notepad" --name "Submit"
pctr click  --title "Notepad" --name "^File$" --control-type MenuItem
pctr shot   --title "Notepad" --out "C:\temp\np.png"
```

Look at `tree`/`find` output first - the name/type/id it prints is what you pass
back as `--name`, `--control-type`, `--auto-id`.

## Commands

| Command | What it does |
|---------|--------------|
| `windows [--filter RE] [--process PID]` | List top-level windows. |
| `tree --title RE [--depth N] [--limit N]` | Dump the UIA control tree. |
| `find --title RE [--name RE] [--limit N] [--nth N]` | List matching elements. |
| `click --title RE --name RE [--method auto\|invoke\|mouse] [--dbl]` | Click an element. |
| `set --title RE --name RE --text S` | Set a field via the UIA ValuePattern (exact, instant). |
| `action --title RE --name RE --list` / `--do NAME` / `--toggle` | Named AT-SPI actions (Linux): show menu, press, expand, toggle. |
| `value --title RE --name RE [--set N]` | Read/set a slider, spinner, or progress value (Linux). |
| `select --title RE --name RE [--deselect]` | Select a list item, table cell, or tab (Linux). |
| `text --title RE --name RE [--offset N]` | Read text at an offset (Linux). |
| `scroll --title RE --name RE` | Scroll an element into view (Linux). |
| `type [--title RE --name RE] --text S [--delay 0.03] [--chunk 1]` | Send keystrokes (see caveats). |
| `keys --keys "{ENTER}"` | Global combo via pywinauto send_keys syntax. |
| `hotkey --keys ctrl+s` | Modifier combo (prefer this over `keys "^s"`). |
| `focus --title RE` | Bring a window to the foreground. |
| `wait --title RE --name RE [--timeout S]` | Wait for an element. |
| `shot [--title RE] --out PATH` | Screenshot the screen or a window. |
| `size` | Print primary screen size as WxH. |
| `desktop <action>` | Windows virtual desktops: `list` / `windows` / `where` / `on-current` / `next` / `prev` / `new` / `close` / `move-window`. |
| `move --x N --y N` / `down` / `up` / `hold --ms N` | Raw mouse control (`--direct` for games). |
| `drag --start x,y --end x,y [--duration S]` | Drag between points. |
| `keydown / keyup --key a` / `keyhold --key w --ms 1500` | Hold keyboard keys (`--direct` for games). |
| `ocr [--title RE] [--lang TAG]` | OCR the screen (or window); list words with boxes. |
| `ocrfind --text RE [--lang TAG]` | OCR then list matches with click centers. Matches **single words**. |
| `ocrclick --text RE [--nth N] [--lang TAG]` | OCR then click a word. |
| `look --for "a dog" [--title RE] [--conf X]` | YOLO-World detect objects by text prompt. |
| `lookclick --for "a dog" [--nth N]` | Detect then click the top match. |
| `pad <button\|stick\|trigger\|status>` | Virtual Xbox 360 gamepad via ViGEmBus (`pctr[pad]`). |
| `hid <action> --port COMx` | Serial-HID bridge to a microcontroller that presents real USB HID. |
| `mcp <start\|stop\|restart\|status\|help>` | Manage the MCP server (stdio or background http/sse). |
| `skill [--install]` / `setup` | Print/install this skill + an AGENTS.md section. |

Global flags: `--json` (machine-readable output) and `--file FILE` (batch).

Filters: `--title` (regex), `--name` (regex), `--control-type`, `--auto-id`,
`--nth`, `--process`, `--timeout`.

### Selectors

`click`, `set`, `wait` accept **any** selector - you do **not** have to pass
`--name`. Give at least one of `--title` / `--process` / `--name` /
`--control-type` / `--auto-id`. `tree` and `find` require a *window* selector
(`--title` or `--process`) so they never silently grab the wrong window.

### Exit codes

- `0` - success (and for `ocr`).
- `1` - no match: `find`/`ocrfind` found nothing, or the window wasn't found.
- `2` - usage/validation error: bad regex, bad `x,y` pair, unwritable `--out`, missing selector.

Errors print a single `error: ...` line (no tracebacks).

### JSON output

Add `--json` (global, or per-command) to get machine-readable output from
`windows`, `tree`, `find`, `ocr`, `ocrfind`, `look`, and `desktop`:

```bash
pctr windows --json           # [{"hwnd":..,"pid":..,"title":".."}, ...]
pctr find --title App --json  # {"window":"..","count":N,"matches":[{"name":..,"rect":[l,t,r,b]}, ..]}
pctr desktop list --json      # [{"index":0,"guid":"..","current":true,"windows":11}, ..]
pctr look --for "a car" --json   # [{"label":"..","conf":0.86,"x":..,"y":..,"box":[..]}, ..]
```

Human-readable progress goes to stderr, so JSON on stdout stays clean.

### Batch files

Run many commands from a file - one per line, `#` comments allowed, blank lines
skipped:

```bash
pctr --file script.txt        # e.g. lines:  windows --filter Notepad
pctr --json --file script.txt # --json applies to every command in the file
```

Stops on the first failing line and reports its line number. Each line is parsed
exactly like a normal `pctr ...` invocation (same flags, same quoting rules).

### Input backends

Raw mouse/keyboard commands take `--backend`:

- `pyautogui` (default) / `sendinput` — virtual keys via user32 `SendInput`.
- `pydirectinput` (also `--direct`) — scancodes; better for DirectInput games.
- `serial-hid` — talk to a **microcontroller presenting a real USB HID device**
  (`--port COMx` or `PCTR_SERIAL_PORT`). Rawest option: the OS sees a genuine
  keyboard/mouse, so there's no "injected" flag to detect.

Applies to `move` / `down` / `up` / `hold` / `keydown` / `keyup` / `keyhold` /
`hotkey` / `drag` / `type` / `desktop`.

```bash
pctr move --backend pydirectinput --x 800 --y 400
pctr keyhold --key w --ms 400 --backend pydirectinput
pctr type --text "hello" --backend serial-hid --port COM5
```

Gamepads (virtual Xbox 360 pad, needs the ViGEmBus driver):

```bash
pip install "pctr[pad]"
pctr pad button  --name A --ms 150
pctr pad button  --name DPAD_UP --ms 100
pctr pad stick   --x 1.0 --y -0.5 --ms 400
pctr pad trigger --right 1.0 --ms 300
pctr pad status
```

Serial-HID firmware protocol (newline-terminated ASCII on the board):
`MOVE x y` · `CLICK b` · `DOWN b` · `UP b` · `KEY k DOWN|UP` · `WRITE text` · `HOTKEY a+b`

```bash
pip install "pctr[serial]"
pctr hid ports                              # list COM ports
pctr hid move --x 500 --y 300 --port COM5
pctr hid key  --key a --down --port COM5
pctr hid type --text "hello" --port COM5
pctr hid raw  --line "..." --port COM5
```

## Patterns

- **Text fields -> use `set`, not `type`.** `set` uses the ValuePattern: atomic, exact, can't drop chars. `type`'s element path goes through pywinauto `type_keys`, which interprets `{ } + ^ % ~ ( )` as key syntax - so `--text "a{b}"` is **not** literal and an unmatched `{` fails. Prefer `set`; use `type` only for plain text on fields with no ValuePattern.
- **Global `type`/`keys` go to the OS-focused window** - `pctr focus --title ...` first.
- **Prefer `hotkey ctrl+s` over `keys "^s"`.** The `^` modifier in `keys` is fragile through shells; `hotkey` takes `ctrl+alt+x` form and is reliable.
- Click falls back to a real mouse click when a control has no invoke pattern.
- Disambiguate with `--control-type` / `--auto-id` / `--nth`.
- **Quote regex args.** `$`, `^`, `|` are regex metachars and can be mangled by your shell; single-quote them, or use plain substrings.

## App notes

- **Qt apps** expose a rich tree including embedded webviews.
- **Electron apps** (Discord, VS Code, OpenCode) hide web content behind the frame - the tree shows only window buttons. Use `focus` + global `type`/`keys`, verify with `shot`, and fall back to `ocr`/`look`. For deep control, drive them over CDP instead.
- **Canvas / games / custom UI**: no tree - use `ocr` for text and `look` for objects.
- **Games** (Minecraft, Trailmakers, ...) ignore synthetic input from `pyautogui`; pass `--direct` to mouse/key commands to use `pydirectinput`, and `pctr focus` the game first.

## Virtual desktops

pctr can see and drive windows across Windows virtual desktops.

```bash
pctr desktop list                       # desktops (#index, guid, [current], window count)
pctr desktop windows --desktop 1        # windows living on desktop #1
pctr desktop where --title "App"        # which desktop a window is on
pctr desktop on-current --title "App"   # True / False
pctr desktop next | prev | new | close  # switch (moves the active desktop)
pctr desktop move-window --title RE --to N
```

- **Switching** (`next`/`prev`/`new`/`close`) uses the standard Win+Ctrl hotkeys and moves the **active** desktop (you included). Add `--direct` if a game has focus.
- **Cross-desktop control:** UIA element actions (`click --method invoke`, `set`) reach windows on *another* desktop **without switching**, because they go through the accessibility layer, not screen input. Raw mouse/keys (mouse-fallback click, global `type`/`keys`, `--direct`) only affect the active desktop.
- `list` / `windows` / `where` / `on-current` are read-only.
- **`move-window` is blocked on Windows 11** - third-party `MoveWindowToDesktop` returns `Access denied` (works on Windows 10). Reads and UIA actions still work cross-desktop.

## OCR / YOLO notes

- `ocrfind`/`ocrclick` match **one word at a time** (Windows OCR tokenizes into words) - match a single distinctive word, not a phrase.
- OCR needs the window visible/foreground; accuracy varies with font/scale.
- `look` needs `pip install "pctr[vision]"`. Point at a local model with the
  `PCTR_YOLO_MODEL` env var to avoid the first-run download. Progress bars are
  sent to stderr, so `look`'s stdout stays parseable.

## MCP server

`pip install "pctr[mcp]"` then register `pctr-mcp` as a stdio MCP server
(Claude Desktop / Cursor / opencode). Exposes `pctr_windows`, `pctr_tree`,
`pctr_find`, `pctr_click`, `pctr_set`, `pctr_type`, `pctr_keys`, `pctr_hotkey`,
`pctr_focus`, `pctr_wait`, `pctr_shot`, `pctr_ocr`, `pctr_ocrfind`,
`pctr_ocrclick`, `pctr_look`, `pctr_desktop`.

Manage or run it from the CLI:

```bash
pctr mcp status                                          # SDK + server state
pctr mcp start --transport streamable-http --port 8765   # background HTTP server
pctr mcp restart | pctr mcp stop
pctr mcp help
```

`stdio` runs in the foreground (what an MCP client spawns); `sse` /
`streamable-http` run in the background. HTTP endpoint:
`http://127.0.0.1:8765/mcp` (streamable-http) or `…/sse` (sse).

## Setup

```bash
pip install pctr             # core (UIA + OCR)
pip install "pctr[vision]"   # + ultralytics, for `pctr look`
pip install "pctr[mcp]"      # + MCP SDK, for `pctr-mcp` / `pctr mcp`
pip install "pctr[pad]"      # + vgamepad, for `pctr pad` (needs ViGEmBus)
pip install "pctr[serial]"   # + pyserial, for `pctr hid` / the serial-hid backend
pip install "pctr[all]"      # everything
pctr setup                   # installs this skill for opencode / claude / agents + appends to ./AGENTS.md
```
