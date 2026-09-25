---
name: omarchy-proxyctl
description: Manage the system-wide proxy on this Omarchy/Hyprland machine with proxyctl — inspect state, verify an endpoint, enable/disable/restore the proxy, and diagnose why browsers, terminals, git, or dev tools still bypass it. Use when a request involves turning the system proxy on or off, switching the proxy host/port, or proving that apps and browsers really connect through it.
---

# System proxy with proxyctl

`proxyctl` owns proxy state on this machine: one endpoint, applied to every layer at once.

| Layer | proxyctl writes | Read by |
| --- | --- | --- |
| Desktop proxy | `gsettings org.gnome.system.proxy` | GTK/GLib and libproxy consumers |
| Session environment | `~/.config/environment.d/proxy.conf` + `systemctl --user` | apps started via the systemd user session / D-Bus / uwsm |
| Browser flags | `~/.config/chromium-flags.conf`, `~/.config/chrome-flags.conf` | Chromium / Chrome, which ignore the GNOME proxy settings under Omarchy |
| Git | global `http.proxy`, `https.proxy` | git, gh |
| Dev tools | npm / pnpm / pip / cargo / docker / brew configs | those CLIs |

Read [references/omarchy-layers.md](references/omarchy-layers.md) for per-layer detail, the app/browser verification commands, and the troubleshooting playbook.

## Ground rules

1. **Verify the endpoint before enabling it.** A browser started with `--proxy-server` has no fallback: if the endpoint is dead the browser has no internet even though `curl` works directly. Run `scripts/verify-proxy.sh HOST:PORT` first — it separates a dead endpoint (exit 1) from one that works only some of the time (exit 3) — and enable only a verified one.
2. **Prefer proxyctl commands over hand edits.** Editing managed files by hand diverges from the snapshot in `~/.config/proxyctl/state.json` and breaks `proxyctl off` / `restore`.
3. **Expect the endpoint address to drift.** DHCP reassigns it, so addresses in older notes, in this repo's README default (`10.10.10.113:7892`), and in `~/.config/systemd/user/mihomo-tun.service` can all be stale. When a configured address is dead, find the live one with `scripts/find-lan-proxy.sh` instead of guessing.
4. **Private ranges are not bypassed by default.** `no_proxy` covers only `localhost,127.0.0.1,::1`, and a LAN proxy typically answers `502` for private targets, so a browser without a bypass list cannot open router or LAN web UIs. Keep the `--proxy-bypass-list=` line in both browser flag files — proxyctl rewrites only the `--proxy-server` line, so it survives `proxyctl on`.
5. **Never leave the system pointed at an unreachable proxy.** If the endpoint goes away, run `proxyctl off`; direct internet still works for most sites.
6. **Direct access is not a substitute for the proxy here.** DNS and most sites work, but Google, YouTube, `api.ipify.org`, and `git ls-remote` against GitHub fail or stall for minutes.

## Commands

| Command | Effect |
| --- | --- |
| `proxyctl status` | State of every layer, including the Omarchy-specific files |
| `proxyctl doctor` | Full diagnostic: system, proxy, git, ports, connectivity, environment |
| `proxyctl test` | Connectivity test: public IP, HTTP, ping, git |
| `proxyctl on --address HOST:PORT` | Apply the endpoint everywhere (saves a snapshot first) |
| `proxyctl off` | Direct mode: proxy off, git cleared, managed files removed |
| `proxyctl restore` | Revert to the state captured by the last `on` |
| `proxyctl env` / `proxyctl env install` | Proxy variables for the current shell / a hook for new shells |
| `proxyctl profile save\|use\|list` | Remember and switch endpoints; built-in `direct` |
| `proxyctl tun on --address HOST:PORT` | Network-layer TUN via mihomo (no per-tool config needed) |
| `proxyctl port N` | Which local process listens on port N |

Install the binary with Go: `go install github.com/alexwoo79/go_coding/proxyctl@latest` (it lands in `$(go env GOPATH)/bin`, usually `~/go/bin`). It is expected at `~/.local/bin/proxyctl` or anywhere on `PATH`; source lives at `~/Documents/ChatGPT/omarchy/go_coding/proxyctl`, so rebuild with `go build -o proxyctl .` if the binary is missing or older than the source.

## Enable or switch the proxy

```sh
scripts/verify-proxy.sh 10.10.10.111:7890   # current LAN endpoint; must pass first
proxyctl on --address 10.10.10.111:7890     # re-verify if the address may have drifted
proxyctl status                             # confirm every layer flipped
```

Then state what still needs a restart: browsers read their flags only at startup, and an app that was already running keeps the environment it was started with. Omarchy launches terminals and browsers through `systemd-run --user … uwsm-app`, so apps started that way pick the proxy up immediately; Hyprland's own session environment stays stale until the next login.

## Turn it off

```sh
proxyctl off        # direct mode, managed files removed
proxyctl restore    # or: back to the state captured before the last `on`
```

## Troubleshoot "apps/browsers still go direct"

Work through the layers in [references/omarchy-layers.md](references/omarchy-layers.md). Two traps to rule out first:

- `proxyctl doctor` prints `端口 7890 未监听` whenever the proxy is a remote/LAN host — it only inspects *local* listeners, so this is a false positive. Do not "fix" it by starting a local proxy.
- An app that was already running before `proxyctl on` still has its old, proxy-less environment. Relaunch it before concluding the change failed.

## Find an endpoint when the address is unknown

```sh
scripts/find-lan-proxy.sh           # sweep the local subnet for Clash/mihomo-style ports
scripts/verify-proxy.sh HOST:PORT   # confirm a candidate really proxies traffic
```

## TUN mode (optional, network-layer)

```sh
proxyctl tun on --address HOST:PORT
proxyctl tun status
proxyctl tun off
```

TUN routes every app through the local mihomo (needs `~/.local/bin/mihomo`; proxyctl generates the config), which makes the per-tool configs unnecessary. Verify the upstream first — TUN pointed at a dead upstream cuts the machine off, and `proxyctl tun off` is the escape hatch.
