#!/usr/bin/env bash
#
# check-browser-proxy.sh — prove whether Chromium/Chrome is really using the
# configured proxy, and whether a browser restart is still pending.
#
# Usage:
#   check-browser-proxy.sh [--browser chromium|google-chrome-stable] [--lan-host IP]
#
# Runs the browser headless with the same config files Omarchy uses
# (~/.config/chromium-flags.conf, ~/.config/chrome-flags.conf) and reports the
# exit IP it sees, plus the LAN side of the bypass list.

set -uo pipefail

BROWSER=""
LAN_HOST=""
while (($#)); do
  case "$1" in
  --browser)
    BROWSER="${2:?--browser needs a value}"
    shift 2
    ;;
  --lan-host)
    LAN_HOST="${2:?--lan-host needs a value}"
    shift 2
    ;;
  -h | --help)
    sed -n '2,13p' "$0"
    exit 0
    ;;
  *)
    echo "unknown option: $1" >&2
    exit 2
    ;;
  esac
done

if [[ -z "$BROWSER" ]]; then
  for candidate in chromium google-chrome-stable; do
    if command -v "$candidate" >/dev/null 2>&1; then
      BROWSER="$candidate"
      break
    fi
  done
fi
[[ -n "$BROWSER" ]] || {
  echo "error: no chromium/google-chrome-stable on PATH" >&2
  exit 2
}

if [[ -z "$LAN_HOST" ]]; then
  LAN_HOST="$(ip route 2>/dev/null | awk '/^default/ {print $3; exit}')"
fi

flags_file="$HOME/.config/chromium-flags.conf"
[[ "$BROWSER" == *chrome* ]] && flags_file="$HOME/.config/chrome-flags.conf"

echo "browser: $BROWSER"
echo "flags:   $flags_file"
if [[ -f "$flags_file" ]]; then
  sed 's/^/  /' "$flags_file"
else
  echo "  (missing: this browser will go direct)"
fi
echo

if pgrep -x "$BROWSER" >/dev/null 2>&1; then
  echo "note: $BROWSER is running; it keeps the flags it started with,"
  echo "      so restart it to pick up changes made now."
  echo
fi

run_headless() {
  local url="$1" profile
  profile="$(mktemp -d)"
  timeout 60 "$BROWSER" --headless=new --disable-gpu --no-first-run \
    --user-data-dir="$profile" --dump-dom "$url" 2>/dev/null
}

echo "exit IP seen by the browser:"
if dom=$(run_headless https://api.ipify.org) && ip=$(grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}' <<<"$dom" | head -1); then
  echo "  $ip"
else
  echo "  (request failed: the browser could not reach the internet)"
fi

echo
echo "LAN reachability through the browser ($LAN_HOST, bypass-list check):"
if [[ -z "$LAN_HOST" ]]; then
  echo "  (no default gateway found; pass --lan-host IP)"
else
  dom="$(run_headless "http://$LAN_HOST")"
  if [[ -z "$dom" ]]; then
    echo "  no response"
  elif grep -qE 'main-frame-error|ERR_[A-Z_]+|neterror' <<<"$dom"; then
    echo "  BLOCKED: the request went to the proxy and came back as an error."
    echo "  Add --proxy-bypass-list=...,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
    echo "  to the browser flags file."
  else
    title="$(grep -oE '<title>[^<]*' <<<"$dom" | head -1)"
    if [[ -n "${title#<title>}" ]]; then
      echo "  loaded a real page (title: ${title#<title>})"
    else
      echo "  loaded a real page (no title tag)"
    fi
  fi
fi
