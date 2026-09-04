#!/usr/bin/env bash
# Check the oh-my-openagent plugin itself: is the installed build current, and
# does the config still line up with THAT build's schema?
#
# Usage: check-plugin.sh [config-dir]   (default: ~/.config/opencode)
#
# Read-only. Exit 0 = plugin current and config clean; 1 = something to review.

set -uo pipefail

CFG_DIR="${1:-$HOME/.config/opencode}"
META="${XDG_STATE_HOME:-$HOME/.local/state}/opencode/plugin-meta.json"
ISSUES=0

# --- 1. What is actually loaded right now -----------------------------------
# plugin-meta.json is opencode's own record of what it resolved and loaded.
# Trust this over the spec string in opencode.jsonc: "@latest" is a *request*,
# not a fact -- the resolved build can lag far behind npm's latest tag.
if [ ! -f "$META" ]; then
  echo "WARN: no plugin-meta.json at $META -- cannot determine loaded build." >&2
  exit 1
fi

read -r TARGET INSTALLED SPEC < <(python3 - "$META" <<'PY'
import json, sys
meta = json.load(open(sys.argv[1]))
for entry in meta.values():
    if "oh-my-openagent" in str(entry.get("spec", "")):
        print(entry.get("target", ""), entry.get("version", ""), entry.get("spec", ""))
        break
PY
)

if [ -z "${INSTALLED:-}" ]; then
  echo "oh-my-openagent not found in plugin-meta.json (plugin not installed?)."
  exit 1
fi

echo "Requested spec : $SPEC"
echo "Loaded build   : $INSTALLED"
echo "Path           : $TARGET"

# --- 2. Compare against the published tags ----------------------------------
TAGS=$(npm view oh-my-openagent dist-tags --json 2>/dev/null)
if [ -n "$TAGS" ]; then
  LATEST=$(echo "$TAGS" | python3 -c 'import json,sys; d=json.load(sys.stdin); d=d[0] if isinstance(d,list) else d; print(d.get("latest",""))')
  echo "npm latest     : $LATEST"
  if [ -n "$LATEST" ] && [ "$LATEST" != "$INSTALLED" ]; then
    echo
    echo "  ⚠️  Loaded build ($INSTALLED) differs from npm latest ($LATEST)."
    echo "      A floating '@latest' spec does NOT guarantee a current build --"
    echo "      the resolved package is cached and can go stale indefinitely."
    ISSUES=1
  fi
else
  echo "npm latest     : (npm unavailable -- skipped)"
fi

# --- 3. Audit the config against the INSTALLED build's schema ---------------
# Use the schema inside the installed package. Do NOT fetch it from the repo's
# dev branch: that describes a different (often pre-release) version.
#
# Two schemas ship, and they disagree. oh-my-opencode.schema.json is the broad
# editor-facing one; the key set actually ENFORCED is the zod OmoConfigSchema in
# the bundle, which is .strict() and much narrower. Auditing against the JSON
# file yields false positives (it omits the "[opencode]" host scope, which is
# valid) and false negatives (it declares keys the runtime rejects). So prefer
# the bundle's zod key set, and fall back to the JSON file only if unavailable.
SCHEMA="$TARGET/dist/oh-my-opencode.schema.json"
BUNDLE="$TARGET/dist/index.js"
echo

ALLOWED=$(python3 - "$BUNDLE" <<'PY'
import re, sys
try:
    s = open(sys.argv[1], encoding="utf8", errors="replace").read()
except OSError:
    sys.exit(1)
m = re.search(r"OmoConfigSchema = z\d*\.object\(\{", s)
if not m:
    sys.exit(1)
i = m.end(); depth = 1; j = i
while depth:
    if s[j] == "{": depth += 1
    elif s[j] == "}": depth -= 1
    j += 1
body = s[i:j - 1]
keys = re.findall(r'(?:^|\n)\s*(?:"([^"]+)"|([A-Za-z_$][\w$]*))\s*:', body)
print("\n".join(a or b for a, b in keys))
PY
) && SOURCE="runtime zod OmoConfigSchema (enforced, strict)"

if [ -z "$ALLOWED" ]; then
  [ -f "$SCHEMA" ] || { echo "WARN: no schema found -- skipping config audit." >&2; exit $ISSUES; }
  ALLOWED=$(python3 -c 'import json,sys; print("\n".join(json.load(open(sys.argv[1])).get("properties",{})))' "$SCHEMA")
  SOURCE="oh-my-opencode.schema.json (editor-facing, broader than enforced)"
fi

python3 - "$CFG_DIR" "$SOURCE" "$ALLOWED" <<'PY' || ISSUES=1
import glob, json, os, sys

cfg_dir, source, allowed_raw = sys.argv[1], sys.argv[2], sys.argv[3]
allowed = set(allowed_raw.split())
print(f"Authoritative key set: {source} -- {len(allowed)} keys.")

# Both the current (omo.jsonc) and legacy (oh-my-openagent.json) names, in both
# the config dir and ~/.omo -- a migration may have relocated the live config.
patterns = [os.path.join(cfg_dir, "omo.jsonc*"),
            os.path.join(cfg_dir, "oh-my-openagent.json*"),
            os.path.join(os.path.expanduser("~/.omo"), "omo.jsonc*")]
paths = sorted({p for pat in patterns for p in glob.glob(pat)})
if not paths:
    print(f"\n  No OMO config found under {cfg_dir} or ~/.omo")

bad = False
for path in paths:
    if os.path.islink(path) or ".bak" in path:
        continue
    try:
        raw = open(path, encoding="utf8", errors="replace").read()
        # Strip whole-line // comments only: matching // anywhere eats the
        # scheme separator in the $schema URL and fakes a parse error.
        cfg = json.loads("\n".join(
            l for l in raw.splitlines() if not l.lstrip().startswith("//")))
    except Exception as exc:
        print(f"\n  BAD {os.path.basename(path)}: {exc}")
        bad = True
        continue

    used = set(cfg) - {"$schema"}
    unknown = sorted(used - allowed)
    print(f"\n  {os.path.basename(path)}: {len(used)} keys used")
    if unknown:
        print(f"    UNKNOWN to this build: {unknown}")
        print("      -> either a typo, or a key from a newer version than the")
        print("         one loaded. Such keys are silently ignored, not errors.")
        bad = True

    print(f"    Unused optional keys available: {len(allowed - used)}")

sys.exit(1 if bad else 0)
PY

echo
if [ "$ISSUES" -eq 0 ]; then
  echo "== Plugin current, config consistent with loaded build. =="
else
  echo "== Review needed (see above). =="
fi
exit $ISSUES
