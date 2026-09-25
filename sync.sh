#!/usr/bin/env bash
#
# sync.sh — copy the live Codex skills, personal plugin skills and Omarchy
# shell plugins from this machine into this repo, then show what changed.
#
# Run it after editing a skill or plugin, then commit and push:
#   ./sync.sh && git commit -am "sync" && git push
#
# Deliberate exclusions (see README): the markdown-ppt engine binary is a
# 22 MB build artifact, and the design-metrics .venv is regenerated from
# engine/requirements.txt.
set -euo pipefail

repo="$(cd "$(dirname "$0")" && pwd)"
codex_home="${CODEX_HOME:-$HOME/.codex}"
plugin_cache="$codex_home/plugins/cache/personal"
omarchy_plugins="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins"

# ~/.codex/skills — .system ships with Codex, and omarchy/diagnose-crash are
# symlinks into the Omarchy package, so both stay out of the backup.
for skill in data-clean data-workflow markdown-ppt omarchy-proxyctl smartboard-analysis; do
  [ -d "$codex_home/skills/$skill" ] || continue
  rsync -a --delete --exclude 'bin/markdown-ppt' \
    "$codex_home/skills/$skill/" "$repo/codex-skills/$skill/"
done

# Personal plugin payload: manifest + engine + skills, without the .venv.
for version_dir in "$plugin_cache"/design-metrics-skills/*/; do
  [ -d "$version_dir" ] || continue
  rsync -a --delete --exclude '.venv' "$version_dir" "$repo/codex-plugins/design-metrics-skills/"
done

for plugin in io.github.alexwoo79.proxy; do
  [ -d "$omarchy_plugins/$plugin" ] || continue
  rsync -a --delete "$omarchy_plugins/$plugin/" "$repo/omarchy-plugins/$plugin/"
done

echo "--- git status"
git -C "$repo" status --short
