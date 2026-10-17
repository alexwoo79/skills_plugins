# skills_plugins

Backup of the Codex skills, Codex plugin skills and Omarchy shell plugins
installed on this machine (Omarchy / Arch Linux, Hyprland), so they can be
restored on a fresh system.

## Platform: Linux only

Everything in this repo is written for **Linux** and has only been tested on
this machine's setup — Omarchy (Arch Linux) with Hyprland and Quickshell, the
local MCP binaries, and tools under `~/.local/bin`.

The scripts assume Linux paths, Wayland/Hyprland APIs, systemd user services,
and Omarchy-specific files (`~/.config/omarchy/`, `omarchy-*` commands), so
they will not run unmodified on macOS, Windows, or a plain Linux distribution.

**If you want to use any of these skills or plugins on another system, please
contact the author first: [@alexwoo79](https://github.com/alexwoo79).**

## What is here

### `codex-skills/` — skills installed in `~/.codex/skills/`

| Skill | What it does |
| --- | --- |
| `data-clean/` | Declarative data cleaning through the local `dataflow-mcp` binary (profile → plan → pipeline → export) |
| `data-workflow/` | Parse, validate and build dataflow-studio workflow JSON, backed by real data profiles |
| `markdown-ppt/` | Markdown → PPTX/SVG/HTML/Reveal.js/Word decks via the `markdown-ppt` CLI |
| `omarchy-proxyctl/` | Manage the system proxy on Omarchy/Hyprland with `proxyctl`: inspect, verify an endpoint, on/off/restore, address discovery, TUN, troubleshooting |
| `print-sheet/` | Typeset Markdown, source code or text files into a printable A4 sheet (HTML + PDF + per-page PNG preview), optionally sent straight to a CUPS printer |
| `smartboard-analysis/` | Smartboard data dashboards and six-part insight reports through the local `smartboard-mcp` binary |

### `codex-plugins/` — skills that arrive through a Codex plugin

| Plugin | What it does |
| --- | --- |
| `design-metrics-skills/` | Personal plugin with two building-design skills — `building-metrics-calculation` (city-rule metric calculation) and `keyan-calculation` (feasibility-stage measurement) — plus the shared Python `engine/` |

### `cad-skill/` — natural-language CAD drafting (not installed yet)

Skill for driving a real CAD session **directly over Windows COM** (pywin32):
architectural plan drafting (grid, walls, doors/windows, furniture, text, dimensions, layers)
with a design-first, step-by-step workflow. `scripts/wall_plan.py` turns a straight-wall +
opening spec into a drawing call sequence; `scripts/plan_render.py` emits calls, an AutoCAD
`.scr`, DXF and an SVG preview from that same geometry; `scripts/plan_to_scr.py` compiles any
call sequence into an AutoCAD `.scr` for one-shot batch drafting (an order of magnitude
faster than per-entity calls); `references/performance.md` explains the cost model and
when a rewrite is — or is not — worth it. Copy it into `~/.codex/skills/` like the other
skills; unlike everything else in this repo it targets **Windows** (AutoCAD / GstarCAD /
ZWCAD over COM), with Codex and CAD in the same desktop session.

The COM driver is inlined at `cad-skill/vendor/cad_core.py`, and `scripts/cad_direct.py`
exposes it as a CLI (`layers`, `draw --calls`, `script --file`, `shot`, `save`, handle-based
edits …), so a single skill folder is enough. There is no server, port or client registration;
`draw --calls … --dry-run` validates geometry without pywin32 or CAD. 78 offline tests
(`python3 -m pytest cad-skill/tests`) drive the CLI end to end against fake COM.
`cad-skill/assets/example-two-bedroom/` holds a ready-to-run 96 m² two-bedroom example
(8 walls, 10 openings → 63 entities) with a runbook that goes all the way to DWG.
`scripts/image_to_calls.py` traces a raster line drawing into that same `plan.json`
(wall centrelines + thickness + openings) or into a DXF, with measured accuracy locked
by tests: 8/8 walls and 10/10 openings on a clean render, 7/8 and 8/10 on a noisy,
0.5°-rotated scan. It also infers each door's hinge side and swing from the drawn arc, and
detects straight-run stairs with `--stairs`.

`scripts/image_to_vector.py` covers the other half of raster work — solid shapes, logos and
stroke text — by tracing outer contours plus holes into an even-odd filled SVG or a DXF
with island-aware hatches, reporting area ratio and IoU against the source image.

`scripts/image_to_walls.py` handles furnished plans drawn with **solid thick walls**:
it separates walls by thickness (thin furniture strokes and label glyphs drop out), thins
them to centre lines, measures each wall's thickness from the distance transform and emits
a DXF with walls on `A-WALL` and everything else on `A-TRACE`. On the sample plan: 63 wall
segments in two thickness classes (18.8 / 10.2 px), 98.7% wall-pixel coverage, 0.8 s.

### `omarchy-plugins/` — Omarchy shell plugins

| Plugin | What it does |
| --- | --- |
| `io.github.alexwoo79.proxy/` | Bar widget: proxy state in one line (glyph + `off` / `on 87ms` / `on !`) with a details panel — editable endpoint, system proxy and TUN switches, live health and exit IP, and the state of every layer `proxyctl` writes |
| `io.github.alexwoo79.codepad/` | Bar widget: the codePad print-sheet builder reachable from the bar — queue the files to typeset, open them in the real codePad window, or export PDF / HTML / DOCX in place. Exports run headless, so no window flashes while a sheet renders |

The proxy skill and the proxy widget both drive
[`proxyctl`](https://github.com/alexwoo79/go_coding/tree/main/proxyctl), which
owns the proxy state for the desktop session, the systemd user session, the
browser flags, git and the dev tools.

The codePad widget drives the `codepad` binary — the print-sheet builder that
typesets Markdown, source code or plain text into a printable A4 sheet. It only
launches and exports; the preview, the themes and the print dialog stay in
codePad's own window, because QML cannot host another process's window.

### Install proxyctl (required by both proxy pieces)

```sh
go install github.com/alexwoo79/go_coding/proxyctl@latest
```

The binary lands in `$(go env GOPATH)/bin` (usually `~/go/bin`). Put that
directory on `PATH`, or copy the binary to `~/.local/bin/proxyctl`, which is
where the widget looks by default; the plugin's `proxyctlPath` setting
overrides that path.

## Restore

```sh
# every Codex skill
for d in codex-skills/*/; do cp -a "$d" ~/.codex/skills/; done

# the Omarchy bar plugin
cp -a omarchy-plugins/io.github.alexwoo79.proxy ~/.config/omarchy/plugins/
omarchy plugin enable io.github.alexwoo79.proxy --section right
```

Codex picks skills up from `~/.codex/skills/`; the proxy skill is invoked as
`$omarchy-proxyctl`. It ships three helper scripts under `scripts/`:

```sh
verify-proxy.sh HOST:PORT     # usable / unreliable / dead, with exit codes 0/3/1
find-lan-proxy.sh             # sweep the local subnet for a running proxy
check-browser-proxy.sh        # prove Chromium really uses the proxy
```

`codex-plugins/design-metrics-skills/` is a copy of the installed plugin
payload (manifest, engine, skills). Restore it by pointing the personal
marketplace at the plugin source again, or by copying it back into
`~/.codex/plugins/cache/personal/design-metrics-skills/<version>/`.

## Deliberately not stored

| Missing piece | How to get it back |
| --- | --- |
| `markdown-ppt/bin/markdown-ppt` (22 MB build artifact) | Rebuild with `make cli` in the source repo recorded in `bin/ENGINE_VERSION.json`, or run that package's `install.sh` |
| `design-metrics-skills/.venv` | `python -m venv .venv && .venv/bin/pip install -r engine/requirements.txt` |
| `~/.codex/skills/.system/` | Ships with Codex |
| `~/.codex/skills/omarchy`, `~/.codex/skills/diagnose-crash` | Symlinks into the Omarchy package: `ln -s /usr/share/omarchy/default/agents/skills/omarchy ~/.codex/skills/omarchy` (same for `diagnose-crash`) |

## Requirements per skill

| Skill | Needs |
| --- | --- |
| `data-clean`, `data-workflow` | local `dataflow-mcp` binary |
| `smartboard-analysis` | local `smartboard-mcp` binary |
| `design-metrics-skills` | Python plus `engine/requirements.txt` |
| `markdown-ppt` | the `markdown-ppt` CLI engine |
| `omarchy-proxyctl`, proxy plugin | `proxyctl` on PATH or `~/.local/bin/proxyctl` |
| codePad widget | the `codepad` binary — on `PATH`, or set `binary` in the widget's entry in `~/.config/omarchy/shell.json` (leave it empty to resolve `codepad` through `PATH`) |
| `cad-skill` | Windows + `pywin32` (Pillow for screenshots); `opencv-python-headless` + `ezdxf` for raster tracing |

## Install the Omarchy plugin

The paths below are relative to this repository, so start in the clone:

```sh
git clone https://github.com/alexwoo79/skills_plugins.git
cd skills_plugins

./omarchy-plugins/io.github.alexwoo79.proxy/install.sh              # right section
./omarchy-plugins/io.github.alexwoo79.proxy/install.sh --section center
./omarchy-plugins/io.github.alexwoo79.codepad/install.sh            # right section
```

`install.sh` copies the folder into `~/.config/omarchy/plugins/`, asks the
running shell to rescan, enables the widget and prints the result. Re-running
it just refreshes the installed copy.

Each plugin has its own `Model.js` unit test, runnable without Omarchy:

```sh
node omarchy-plugins/io.github.alexwoo79.proxy/test-model.js
node omarchy-plugins/io.github.alexwoo79.codepad/test-model.js
```

By hand it is three steps, and the middle one is the step that is easy to miss:

```sh
cp -a omarchy-plugins/io.github.alexwoo79.proxy ~/.config/omarchy/plugins/
omarchy-shell shell rescanPlugins      # or: omarchy restart shell
omarchy plugin enable io.github.alexwoo79.proxy --section right
omarchy plugin list | grep alexwoo79   # verify
```

### If the install does not work

- `plugin 'io.github.alexwoo79.proxy' is not known` — `omarchy plugin enable`
  asks the **running shell**, which only knows plugins it has already
  discovered. Run `omarchy-shell shell rescanPlugins` (or `omarchy restart
  shell`) after copying and try again. This is the usual cause.
- `cp: cannot stat 'omarchy-plugins/...'` — the command was run outside the
  clone. `cd` into the repository first, or use absolute paths.
- `omarchy plugin add <git-url>` will not help here: it expects the repository
  root to *be* a single plugin, and this repo holds a skill set plus a plugin,
  so the folder is copied instead.
- Remove the widget again with `omarchy plugin disable io.github.alexwoo79.proxy`
  or, including its folder, `omarchy plugin remove io.github.alexwoo79.proxy`.

### Plugin behaviour worth knowing

- Clicking the bar widget only opens the panel — proxy state changes happen
  exclusively from the switches inside it, never from a stray bar click.
- The panel's `Endpoint` field takes `host:port`; Enter applies it (re-pointing
  a running proxy) and saves it into the widget's `shell.json` entry via
  Omarchy's `updateEntryInline`. A malformed address is refused and the field
  turns urgent.
- **TUN mode** routes every app through the local mihomo using the same
  endpoint. It needs `~/.local/bin/mihomo` (proxyctl generates and validates
  the config), and a dead upstream cuts the machine off — verify the endpoint
  first, and the switch (or `proxyctl tun off`) is the escape hatch.
- The plugin never opens a window of its own: Omarchy's presentation terminal
  holds itself open until a key is pressed, so the full `proxyctl status`
  output stays in the panel instead.

## Tests

```sh
cd omarchy-plugins/io.github.alexwoo79.proxy
node test-model.js              # pure logic: parsing, argv building, display
omarchy plugin validate .
```
