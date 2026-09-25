#!/usr/bin/env bash
#
# find-lan-proxy.sh — locate a machine on the local network that serves an
# HTTP/SOCKS proxy (Clash / mihomo / similar mixed port).
#
# Usage:
#   find-lan-proxy.sh [--ports 7890,7891,7892,1080] [--subnet 10.0.0.0/24]
#
# Read-only discovery on the local subnet; nothing is configured or changed.
# Follow up with scripts/verify-proxy.sh HOST:PORT on any candidate found,
# because an open port is not proof that the endpoint proxies traffic.

set -uo pipefail

PORTS="7890,7891,7892,7893,1080,1081,1087,1088,8118,8080,8888,10809,20171"
SUBNET=""

while (($#)); do
  case "$1" in
  --ports)
    PORTS="${2:?--ports needs a value}"
    shift 2
    ;;
  --subnet)
    SUBNET="${2:?--subnet needs a value}"
    shift 2
    ;;
  -h | --help)
    sed -n '2,14p' "$0"
    exit 0
    ;;
  *)
    echo "unknown option: $1" >&2
    exit 2
    ;;
  esac
done

if [[ -z "$SUBNET" ]]; then
  cidr=$(ip -o -4 addr show scope global 2>/dev/null | awk '{print $4}' | head -1)
  if [[ -z "$cidr" ]]; then
    echo "error: cannot detect the local subnet; pass --subnet CIDR" >&2
    exit 1
  fi
  SUBNET="${cidr%.*}.0/24"
fi

echo "scanning $SUBNET for: $PORTS"
echo

if command -v nmap >/dev/null 2>&1; then
  nmap -Pn -n -T4 --min-rate 2000 --max-retries 1 --host-timeout 20s \
    -p "$PORTS" "$SUBNET" 2>/dev/null |
    awk '
      /^Nmap scan report for/  { host = $NF }
      /^[0-9]+\/tcp +open/     { printf "  %s  %s\n", host, $1 }
    '
else
  echo "(nmap not found; falling back to a bash /dev/tcp sweep)"
  IFS=, read -r -a port_list <<<"$PORTS"
  prefix="${SUBNET%.0/24}"
  seq 1 254 |
    xargs -P 64 -I{} bash -c '
      host="$1.$2"
      shift 2
      for p in "$@"; do
        timeout 2 bash -c "exec 3<>/dev/tcp/$host/$p" 2>/dev/null &&
          printf "  %s  %s/tcp open\n" "$host" "$p"
      done
    ' _ "$prefix" {} "${port_list[@]}"
fi

echo
echo "next: scripts/verify-proxy.sh HOST:PORT"
