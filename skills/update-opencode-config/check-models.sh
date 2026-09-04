#!/usr/bin/env bash
# Cross-check every model referenced in the opencode config against the models
# your connected providers actually serve -- for ALL providers, not one vendor.
#
# Usage: check-models.sh [config-dir]   (default: ~/.config/opencode)
#        PROVIDERS=github-copilot,ovhcloud check-models.sh   # restrict
#
# Exit 0 = all references resolve. 1 = at least one broken. 2 = environment.
#
# Scope note: this validates model IDs *written in* the config. It cannot see a
# slot that has no pin -- an empty config passes trivially while agents silently
# fall through upstream's routing. Pair it with check-resolution.sh, which is
# the check for that.

set -uo pipefail

CFG_DIR="${1:-$HOME/.config/opencode}"
[ -d "$CFG_DIR" ] || { echo "no such config dir: $CFG_DIR" >&2; exit 2; }

AVAIL=$(mktemp) REFS=$(mktemp)
trap 'rm -f "$AVAIL" "$REFS"' EXIT

# What every connected provider currently serves. Must run from the config dir
# so opencode picks up this setup's providers and auth.
(cd "$CFG_DIR" && opencode models 2>/dev/null) | sort -u > "$AVAIL"

if [ ! -s "$AVAIL" ]; then
  echo "ERROR: 'opencode models' returned nothing." >&2
  echo "  That means opencode is not on PATH, or no provider is authenticated --" >&2
  echo "  NOT that every model is broken. Fix the environment before changing" >&2
  echo "  any config." >&2
  exit 2
fi

# Restrict to specific providers if asked; otherwise audit every provider that
# appears in the catalogue.
if [ -n "${PROVIDERS:-}" ]; then
  PATTERN="^($(echo "$PROVIDERS" | tr ',' '|'))/"
  grep -E "$PATTERN" "$AVAIL" > "$AVAIL.f" && mv "$AVAIL.f" "$AVAIL"
  [ -s "$AVAIL" ] || { echo "ERROR: no models for PROVIDERS=$PROVIDERS" >&2; exit 2; }
fi

PROVIDER_LIST=$(cut -d/ -f1 "$AVAIL" | sort -u | tr '\n' ' ')

# Config files to scan. Every variant, not just the active symlink target: an
# inactive variant rots unnoticed and breaks the day the user switches to it.
# Both the current (~/.omo, omo.jsonc) and legacy (oh-my-openagent.json)
# locations, since a config may sit in either after a migration.
mapfile -t FILES < <(
  ls -1 "$CFG_DIR"/opencode.jsonc \
        "$CFG_DIR"/omo.jsonc* \
        "$CFG_DIR"/oh-my-openagent.json* \
        "$HOME"/.omo/omo.jsonc* 2>/dev/null \
  | grep -v '\.bak' | sort -u
)
[ "${#FILES[@]}" -gt 0 ] || { echo "ERROR: no config files found under $CFG_DIR" >&2; exit 2; }

# Extract provider-prefixed model ids textually. Plain grep, not jq: it works
# across .json and .jsonc alike, and only ids whose provider is actually in the
# catalogue are considered, so prose and URLs cannot produce false positives.
# Trailing (variant) is stripped -- "model(xhigh)" references model.
PROV_RE=$(cut -d/ -f1 "$AVAIL" | sort -u | paste -sd'|')
grep -rhoE "($PROV_RE)/[A-Za-z0-9._-]+" "${FILES[@]}" 2>/dev/null \
  | sort -u > "$REFS"

echo "Providers audited:    $PROVIDER_LIST"
echo "Models available:     $(wc -l < "$AVAIL")"
echo "Referenced in config: $(wc -l < "$REFS")"
echo "Files scanned:        ${#FILES[@]}"
echo

echo "== BROKEN (referenced but NOT provided) =="
BROKEN=$(comm -23 "$REFS" "$AVAIL")
if [ -z "$BROKEN" ]; then
  echo "  (none)"
else
  echo "$BROKEN" | sed 's/^/  /'
  echo
  echo "  Locations:"
  while read -r m; do
    [ -n "$m" ] || continue
    grep -rn --color=never -- "$m" "${FILES[@]}" 2>/dev/null | sed 's/^/    /'
  done <<< "$BROKEN"
fi

echo
echo "== UNUSED (provided but not referenced) =="
comm -13 "$REFS" "$AVAIL" | sed 's/^/  /'
echo
echo "  Note: 'unused' is not a defect. Slots with no pin are served by the"
echo "  plugin's built-in routing; see check-resolution.sh."

[ -z "$BROKEN" ]
