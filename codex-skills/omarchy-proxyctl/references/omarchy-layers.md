# Proxy layers on Omarchy / Hyprland

Detail behind [SKILL.md](../SKILL.md): what each layer looks like when it is correct, how to prove an app really uses it, and the traps that make a working proxy look broken.

## 1. Desktop proxy (gsettings)

```sh
gsettings get org.gnome.system.proxy mode            # 'manual' when on, 'none' when off
gsettings get org.gnome.system.proxy.http host
gsettings get org.gnome.system.proxy.http port
gsettings get org.gnome.system.proxy.ignore-hosts    # proxyctl writes localhost/127.0.0.0/8/::1 only
```

GLib-based apps and libproxy consumers pick this up at runtime. Chromium under Omarchy does not — that is what layer 3 is for.

## 2. Session environment

```sh
cat ~/.config/environment.d/proxy.conf               # managed file, first line is the proxyctl marker
systemctl --user show-environment | grep -i proxy    # what the systemd user manager exports now
```

The file is loaded by the systemd user session at login; `proxyctl on` also imports it into the running session, so units and D-Bus-activated apps see it immediately. `proxyctl off` deletes the whole file.

`no_proxy` is `localhost,127.0.0.1,::1` unless `PROXY_NO` is set when running `on`:

```sh
PROXY_NO="localhost,127.0.0.1,::1,10.0.0.0/8" proxyctl on --address HOST:PORT
```

Only some tools understand CIDR in `no_proxy`, so treat ranges here as best-effort; the browser bypass list below is the reliable place for LAN ranges.

## 3. Chromium / Chrome launch flags

Omarchy launches the browser as `systemd-run --user … uwsm-app -- <browser>`, and the Arch/Omarchy browser wrappers read these files, so these two lines are what actually proxy the browser:

```
--proxy-server=http://HOST:PORT
--proxy-bypass-list=localhost,127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16
```

proxyctl replaces only lines beginning with `--proxy-server`; every other line is preserved across `on` and `off`, so the bypass list only has to be added once.

Flags apply at startup: a browser that was already running keeps its old, direct configuration until it is restarted.

## 4. Git and dev tools

```sh
git config --global --get http.proxy
npm config get proxy
pip config get global.proxy
```

`proxyctl tools` manages npm / pnpm / pip / cargo / docker / brew individually; `proxyctl on --no-tools` applies everything except those.

## The shell you are typing in

`proxyctl` cannot change its parent shell, so an already-open terminal stays direct until you tell it otherwise:

```sh
eval "$(proxyctl env)"                            # this shell only
proxyctl env install                              # new shells follow the proxy state automatically
source ~/Documents/ChatGPT/omarchy/proxy-on.sh    # wrapper: system-wide plus this shell
```

`eval "$(proxyctl env --clear)"` undoes it for the current shell; the wrapper scripts only affect the shell when they are `source`d, not executed.

## Proving an app really goes through the proxy

Environment for newly launched apps:

```sh
systemd-run --user --wait --pipe --collect env | grep -i proxy               # D-Bus / systemd-launched apps
systemd-run --user --wait --pipe --collect uwsm-app -- env | grep -i proxy   # exactly the Omarchy launch path
```

`--wait --pipe` is required; without it the command prints nothing and looks like a failure.

Environment of an already-running process:

```sh
tr '\0' '\n' < /proc/<pid>/environ | grep -i proxy
```

Apps launched by Hyprland itself (`hyprctl dispatch exec`, plain keybindings) inherit Hyprland's startup environment, which does not change until the next login. Omarchy's own launchers avoid this by going through `uwsm-app`.

Browser, end to end (reads the flags files, opens no window):

```sh
chromium --headless=new --disable-gpu --no-first-run --user-data-dir="$(mktemp -d)" \
  --dump-dom https://api.ipify.org
```

It prints the proxy's exit IP. The control is the same URL without the proxy: that request fails on this network, which is what proves the value came through the proxy.

LAN reachability, to confirm the bypass list works:

```sh
chromium --headless=new --disable-gpu --no-first-run --user-data-dir="$(mktemp -d)" \
  --dump-dom http://10.10.10.1 | grep -o '<title>[^<]*'
```

Real page content (for example the router model) means the bypass works; Chromium's own error page, titled with the host, means the request went to the proxy and came back `502`.

Git:

```sh
git ls-remote https://github.com/git/git HEAD   # instant through a working proxy, minutes or never when direct here
```

## Traps

- **False positive in `proxyctl doctor`.** `端口 N 未监听` compares the configured endpoint against *local* listeners, so it always fires for a remote/LAN proxy and means nothing. Do not "fix" it by starting a local proxy.
- **Stale environment in running apps.** The proxy can be fully applied while the browser and terminals you are looking at still go direct. Relaunch them, or log out and back in for a clean session.
- **Half-dead endpoints.** A host that answers ping can still refuse the proxy port, and an endpoint can answer `204` for one site while timing out on another. `scripts/verify-proxy.sh` checks TCP, HTTP CONNECT, SOCKS5 and the exit IP before you commit to it.
- **Lossy link, not a dead proxy.** A proxy on weak Wi-Fi passes single tests and fails a third of real requests. `scripts/verify-proxy.sh` repeats the HTTP CONNECT check and exits `3` for "unreliable"; confirm the link with `ping -c 5 <host>` (packet loss and jitter on a LAN shouldn't exist) and prefer another endpoint or a wired/mesh change over blaming the proxy software.
- **Same exit IP from two hosts.** Two LAN machines running the same subscription report the same exit IP, which does not make them equally reliable. Prefer the one with stable latency on a repeat test.
- **Address drift.** The proxy host's DHCP lease changes; `10.10.10.113:7892` in the repo README and in `mihomo-tun.service` is a historical default. Re-discover with `scripts/find-lan-proxy.sh`.
- **Rotating exit nodes.** A subscription-backed proxy may report a different exit IP between runs (`155.117.84.153` then `155.117.84.156`); that is normal and not a sign the endpoint changed.
