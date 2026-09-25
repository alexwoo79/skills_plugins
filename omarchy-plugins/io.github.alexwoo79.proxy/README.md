# Proxy (proxyctl) — Omarchy bar widget with a details panel

System proxy state in the Omarchy bar, driven by [`proxyctl`](https://github.com/alexwoo79/go_coding/tree/main/proxyctl).

```
󰖟 off            direct — click to enable 10.10.10.111:7890
󰖟 on             proxy is on, no health probe yet
󰖟 on 87ms        proxy is on and a request through it took 87 ms
󰖟 on !           proxy is on but the last probe failed (urgent colour)
```

## Interactions

Clicking the bar widget only opens the panel. Nothing on the bar changes
system state, so a stray click can never cut the proxy out from under a
browser.

The panel is a rectangle under the bar:

```
󰖟 Proxy                                    On · working
──────────────────────────────────────────────────────
Endpoint   [ 10.10.10.111:7890                  ]  ⏎
Health        2613ms (checked 21:16:23)
Exit IP       155.117.84.157
──────────────────────────────────────────────────────
System proxy  Enabled — browsers, terminals, git …   ( ●──)
TUN mode      Stopped — verify the endpoint before … ( ○──)
──────────────────────────────────────────────────────
Layers
  Desktop proxy  10.10.10.111:7890
  Session env    proxy.conf present
  Browser flags  10.10.10.111:7890
  Git            10.10.10.111:7890
  Dev tools      6/6 configured
  TUN            stopped
──────────────────────────────────────────────────────
  [ Check ]                      [ Copy ]
```

With TUN running the picture changes — the probes go through the tunnel
(`via TUN · 608ms`) and the layer list collapses, because TUN owns the network
layer and the per-app rows would just be noise:

```
Health        via TUN · 608ms (checked 21:41:21)
Exit IP       155.117.84.156
System proxy  Not needed while TUN is on — every app is already routed
TUN mode      Running — every app routed at the network layer
Layers
  TUN            running (Meta)
  Per-app layers not used while TUN is on
```

| Control | Action |
|---|---|
| **Endpoint** field | Type `host:port`; Enter applies it (and re-points a running proxy) and saves it |
| **System proxy** switch | `proxyctl on --address <configured>` or `proxyctl off` |
| **TUN mode** switch | `proxyctl tun on --address <endpoint>` / `proxyctl tun off` |
| Check | Re-run the health and exit-IP probes |
| Copy | Copy the status block to the clipboard |

The **Layers** rows come from `proxyctl status` (and `proxyctl tools list` for
the dev-tool count), so a half-applied state — for example a browser that never
got its `--proxy-server` flag — is visible without reading the report by hand.
While TUN is running only the TUN row remains: the other layers are not in the
path any more.

`Endpoint` is saved into this widget's entry in
`~/.config/omarchy/shell.json` through Omarchy's `updateEntryInline`, so the
settings panel sees the same value. An address typed but not applied is
ignored; a malformed one is refused and the field turns urgent.

**TUN mode** is the network-layer option from the
`omarchy-proxyctl` skill: it routes every app through the local mihomo using
the endpoint above, which makes the per-tool configs unnecessary. It needs
`~/.local/bin/mihomo` (proxyctl generates and validates the config), and
pointing it at a dead upstream cuts the machine off — verify the endpoint
first, and `proxyctl tun off` (the switch) is the escape hatch.

It works with or without the per-app system proxy: with that switch off, the
panel's health and exit IP are measured straight through the tunnel, and the
bar label reads `tun` instead of `off`.

Hovering the bar widget shows the endpoint, the measured latency and when it
was last checked, plus `TUN on` while TUN is running. Outside-click, `Esc`
(via the popout grab) or a second click on the widget closes the panel.

The widget never opens a window of its own. Omarchy's presentation terminal
(`omarchy-launch-floating-terminal-with-presentation`) holds itself open until
a key is pressed, so a spawn-on-click action leaves windows behind — the full
`proxyctl status` output is one keystroke away in any terminal instead.

For scripted control the widget also answers IPC:

```sh
omarchy-shell io.github.alexwoo79.proxy panel    # open/close
omarchy-shell io.github.alexwoo79.proxy toggle   # enable/disable
omarchy-shell io.github.alexwoo79.proxy check    # re-probe
omarchy-shell io.github.alexwoo79.proxy apply 10.10.10.111:7890
```

## Where the state comes from

The widget reads the file `proxyctl` writes for the desktop session
(`~/.config/environment.d/proxy.conf`). Present = proxy on, missing = direct,
so `proxyctl on` / `off` / `restore` from a terminal moves the bar
immediately. Nothing is written except by `proxyctl` itself.

The health probe is one bounded `curl` through the proxy
(`https://www.gstatic.com/generate_204`, 8 s cap), which is why the bar can
distinguish "on" from "working" — a proxy that is on but unreachable leaves
Chromium with no internet at all, since browser flags have no fallback.

## Settings

Configured in `~/.config/omarchy/shell.json` under the widget's layout entry,
or from the Omarchy settings panel.

| Key | Default | Meaning |
|---|---|---|
| `address` | `10.10.10.111:7890` | Endpoint enabled when the proxy is off |
| `checkInterval` | `60` | Seconds between health probes (30–3600) |
| `healthCheck` | `true` | Set `false` for a display-and-toggle-only widget |
| `proxyctlPath` | `~/.local/bin/proxyctl` | Binary to run for toggles |

## Dependencies

- `proxyctl` — install it with Go, then make sure it is on `PATH` or at
  `~/.local/bin/proxyctl`:

  ```sh
  go install github.com/alexwoo79/go_coding/proxyctl@latest
  ```

  It toggles the whole system: gsettings, session env, browser flags, git and
  the dev tools.
- `curl` and `omarchy-launch-floating-terminal-with-presentation` (already on
  Omarchy)

## Disable

```sh
omarchy plugin disable io.github.alexwoo79.proxy
```

## Tests

```sh
node test-model.js
```
