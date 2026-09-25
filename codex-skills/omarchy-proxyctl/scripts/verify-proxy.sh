#!/usr/bin/env bash
#
# verify-proxy.sh — decide whether a proxy endpoint is actually usable before
# proxyctl points the whole desktop at it.
#
# Usage:
#   verify-proxy.sh HOST:PORT [TIMEOUT_SECONDS] [ATTEMPTS]
#
# Why: once `proxyctl on` runs, Chromium/Chrome start with --proxy-server and
# have no fallback, so a dead endpoint means "no internet" in the browser even
# though curl still works directly.
#
# Checks TCP reachability, HTTP CONNECT (the path browsers and git take),
# SOCKS5 on the same port (Clash/mihomo mixed ports speak both), the exit IP
# that traffic leaves through, and how many of ATTEMPTS requests succeed —
# a proxy on a lossy link passes single tests and still fails half the time.
#
# Exit status: 0 usable, 1 not usable, 2 bad usage, 3 unreliable (flaky).

set -uo pipefail

ADDR="${1:-}"
TIMEOUT="${2:-10}"
ATTEMPTS="${3:-5}"

if [[ -z "$ADDR" || "$ADDR" != *:* ]]; then
  echo "usage: ${0##*/} HOST:PORT [TIMEOUT_SECONDS] [ATTEMPTS]" >&2
  exit 2
fi

if [[ ! "$ATTEMPTS" =~ ^[0-9]+$ ]] || ((ATTEMPTS < 1)); then
  echo "error: invalid attempt count '$ATTEMPTS'" >&2
  exit 2
fi

# Accept [::1]:7890 as well as host:7890.
if [[ "$ADDR" == \[*\]:* ]]; then
  HOST="${ADDR%%]:*}"
  HOST="${HOST#[}"
  PORT="${ADDR##*:}"
else
  HOST="${ADDR%:*}"
  PORT="${ADDR##*:}"
fi

if [[ ! "$PORT" =~ ^[0-9]+$ ]] || ((PORT < 1 || PORT > 65535)); then
  echo "error: invalid port '$PORT'" >&2
  exit 2
fi

if [[ ! "$HOST" =~ ^[A-Za-z0-9._:-]+$ ]]; then
  echo "error: invalid host '$HOST'" >&2
  exit 2
fi

command -v curl >/dev/null 2>&1 || {
  echo "error: curl is required" >&2
  exit 2
}

PROBE_URL="https://www.gstatic.com/generate_204"
EXITIP_URL="https://api.ipify.org"

fail=0
flaky=0
ok() { printf '  [ ok ]  %s\n' "$1"; }
warn() {
  printf '  [WARN]  %s\n' "$1"
  flaky=1
}
bad() {
  printf '  [FAIL]  %s\n' "$1"
  fail=1
}

echo "verifying proxy $ADDR (timeout ${TIMEOUT}s, $ATTEMPTS attempts)"
echo

# 1. TCP reachability, with retries so packet loss is not read as "host down".
tcp_ok=0
for ((i = 1; i <= 3; i++)); do
  if timeout "$TIMEOUT" bash -c "exec 3<>/dev/tcp/$HOST/$PORT" 2>/dev/null; then
    tcp_ok=1
    break
  fi
done
if ((tcp_ok)); then
  ok "tcp $HOST:$PORT open"
else
  bad "tcp $HOST:$PORT refused or unreachable"
fi

# 2. HTTP CONNECT, repeated: the same path browsers and git take.
http_ok=0
timings=""
for ((i = 1; i <= ATTEMPTS; i++)); do
  if out=$(curl -sS -m "$TIMEOUT" -x "http://$ADDR" -o /dev/null -w '%{http_code} %{time_total}' "$PROBE_URL" 2>&1) &&
    [[ "${out%% *}" == "204" ]]; then
    http_ok=$((http_ok + 1))
    timings+="${out#* }s "
  fi
done
if ((http_ok == ATTEMPTS)); then
  ok "http connect $http_ok/$ATTEMPTS ok ($timings)"
elif ((http_ok > 0)); then
  warn "http connect only $http_ok/$ATTEMPTS ok ($timings)"
else
  bad "http connect 0/$ATTEMPTS ok"
fi

# 3. SOCKS5 on the same port.
socks_ok=0
if out=$(curl -sS -m "$TIMEOUT" --socks5-hostname "$ADDR" -o /dev/null -w '%{http_code} %{time_total}' "$PROBE_URL" 2>&1) &&
  [[ "${out%% *}" == "204" ]]; then
  socks_ok=1
  ok "socks5 -> 204 (${out#* }s)"
else
  bad "socks5 failed"
fi

# 4. Exit IP: proves traffic leaves through the proxy instead of falling back.
exit_ip=""
if exit_ip=$(curl -sS -m "$TIMEOUT" -x "http://$ADDR" "$EXITIP_URL" 2>/dev/null) && [[ -n "$exit_ip" ]]; then
  ok "exit ip $exit_ip"
else
  bad "could not read the exit ip through the proxy"
fi

echo
if ((fail == 0 && flaky == 0)); then
  echo "usable -> proxyctl on --address $ADDR"
  exit 0
fi

if ((fail == 0)); then
  echo "unreliable: the endpoint proxies traffic but not every request succeeds."
  echo "  Check the link to the proxy host (ping -c 5 $HOST, packet loss, Wi-Fi),"
  echo "  try another endpoint, and re-run this script before applying."
  exit 3
fi

if ((http_ok > 0)) || ((socks_ok == 1)) || [[ -n "$exit_ip" ]]; then
  echo "PARTIAL: some checks passed but the endpoint is not dependable; not recommended."
else
  echo "NOT usable; do not point the system at this endpoint."
  echo "  port refused           the proxy program is not running on that host"
  echo "  host unreachable       wrong address, or the machine is off"
  echo "  port open, checks fail that service is not a forward proxy (or wants auth)"
fi
exit 1
