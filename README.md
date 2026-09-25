# skills_plugins

Personal Codex skills and Omarchy shell plugins, kept in one place so they can
be restored on a fresh machine.

| Path | What it is |
| --- | --- |
| `codex-skills/omarchy-proxyctl/` | Codex skill: manage the system-wide proxy on Omarchy/Hyprland with `proxyctl` — inspect state, verify an endpoint, enable/disable/restore, find a drifted address, and diagnose why browsers, terminals, git or dev tools still bypass the proxy. |
| `omarchy-plugins/io.github.alexwoo79.proxy/` | Omarchy bar widget: proxy state in one line (glyph + `off` / `on 87ms` / `on !`) with a details panel — editable endpoint, system proxy and TUN switches, live health and exit IP, and the state of every layer `proxyctl` writes. |

Both are driven by [`proxyctl`](https://github.com/alexwoo79/go_coding/tree/main/proxyctl),
which owns the proxy state for the desktop session, the systemd user session,
the browser flags, git and the dev tools.

## Install the skill

```sh
cp -a codex-skills/omarchy-proxyctl ~/.codex/skills/
```

Codex picks it up from `~/.codex/skills/`; invoke it as `$omarchy-proxyctl`.
It ships three helper scripts under `scripts/`:

```sh
verify-proxy.sh HOST:PORT     # usable / unreliable / dead, with exit codes 0/3/1
find-lan-proxy.sh             # sweep the local subnet for a running proxy
check-browser-proxy.sh        # prove Chromium really uses the proxy
```

## Install the plugin

```sh
cp -a omarchy-plugins/io.github.alexwoo79.proxy ~/.config/omarchy/plugins/
omarchy plugin enable io.github.alexwoo79.proxy --section right
```

The Omarchy shell hot-reloads plugins from `~/.config/omarchy/plugins/`, so the
widget appears immediately; `omarchy plugin disable io.github.alexwoo79.proxy`
removes it from the bar again.

`omarchy plugin add <git-url>` expects the repository root to *be* the plugin,
so this repo is installed by copying the folder.

### Behaviour worth knowing

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
