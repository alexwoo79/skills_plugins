#!/usr/bin/env bash
#
# install.sh — copy this plugin into the running Omarchy shell and enable it.
#
#   ./install.sh                  # into the right section of the bar
#   ./install.sh --section center # or left / center / right
#
# Safe to re-run: it refreshes the installed copy from this folder and only
# touches this plugin's own entry in shell.json.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
command -v jq >/dev/null 2>&1 || {
  echo "install.sh: jq is required (it ships with Omarchy)" >&2
  exit 1
}
id="$(jq -r .id "$here/manifest.json")"
[[ -n "$id" && "$id" != "null" ]] || {
  echo "install.sh: cannot read the plugin id from $here/manifest.json" >&2
  exit 1
}

section="right"
while (($#)); do
  case "$1" in
  --section)
    section="${2:?--section needs a value}"
    shift 2
    ;;
  -h | --help)
    sed -n '3,8p' "$0"
    exit 0
    ;;
  *)
    echo "install.sh: unknown option: $1" >&2
    exit 2
    ;;
  esac
done

case "$section" in
left | center | right) ;;
*)
  echo "install.sh: section must be left, center or right" >&2
  exit 2
  ;;
esac

plugins_dir="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins"
dest="$plugins_dir/$id"

if [[ "$here" != "$dest" ]]; then
  mkdir -p "$plugins_dir"
  cp -aT "$here" "$dest"
  echo "copied $(basename "$here") -> $dest"
fi

# The shell only enables plugins it has already discovered. A folder that was
# copied a moment ago is not in its registry yet, and `omarchy plugin enable`
# would answer "plugin '<id>' is not known" — the rescan is what makes a fresh
# copy installable without restarting the shell.
omarchy-shell shell rescanPlugins >/dev/null 2>&1 || true

if ! omarchy plugin enable "$id" --section "$section"; then
  echo "install.sh: enable failed. If it said the plugin is not known, run" >&2
  echo "  omarchy-shell shell rescanPlugins" >&2
  echo "and try again (or 'omarchy restart shell' if the shell is stuck)." >&2
  exit 1
fi

omarchy plugin list | grep -F "$id" || true
