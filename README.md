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
| `smartboard-analysis/` | Smartboard data dashboards and six-part insight reports through the local `smartboard-mcp` binary |

### `codex-plugins/` — skills that arrive through a Codex plugin

| Plugin | What it does |
| --- | --- |
| `design-metrics-skills/` | Personal plugin with two building-design skills — `building-metrics-calculation` (city-rule metric calculation) and `keyan-calculation` (feasibility-stage measurement) — plus the shared Python `engine/` |

### `omarchy-plugins/` — Omarchy shell plugins

| Plugin | What it does |
| --- | --- |
| `io.github.alexwoo79.proxy/` | Bar widget: proxy state in one line (glyph + `off` / `on 87ms` / `on !`) with a details panel — editable endpoint, system proxy and TUN switches, live health and exit IP, and the state of every layer `proxyctl` writes |

The proxy skill and the proxy widget both drive
[`proxyctl`](https://github.com/alexwoo79/go_coding/tree/main/proxyctl), which
owns the proxy state for the desktop session, the systemd user session, the
browser flags, git and the dev tools.

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

## Install the Omarchy plugin (details)

```sh
cp -a omarchy-plugins/io.github.alexwoo79.proxy ~/.config/omarchy/plugins/
omarchy plugin enable io.github.alexwoo79.proxy --section right
```

The Omarchy shell hot-reloads plugins from `~/.config/omarchy/plugins/`, so the
widget appears immediately; `omarchy plugin disable io.github.alexwoo79.proxy`
removes it from the bar again.

`omarchy plugin add <git-url>` expects the repository root to *be* the plugin,
so this repo is installed by copying the folder.

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
