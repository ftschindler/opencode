#!/usr/bin/env bash
# Replay oh-my-openagent's model routing for every agent and category, against
# the models your connected providers actually serve, and report any slot that
# resolves to nothing.
#
# Usage: check-resolution.sh [config-dir]   (default: ~/.config/opencode)
#
# Why this exists: neither `omo doctor` nor check-models.sh can catch this.
# The doctor lints config *shape*; check-models.sh validates model IDs *written
# in* your config -- so a config with no pins passes both trivially while some
# agents silently have no model. That happens because OMO's built-in fallback
# chains are curated across ~26 providers, and an entry naming your provider may
# name a model your provider does not serve. When a chain has no reachable entry
# the slot falls through to opencode.jsonc's default model with no error and no
# warning: wrong tier, wrong cost, no signal.
#
# Read-only. Exit 0 = every slot resolves. 1 = at least one falls through.
# 2 = environment problem (caches or plugin missing).

set -uo pipefail

CFG_DIR="${1:-$HOME/.config/opencode}"
META="${XDG_STATE_HOME:-$HOME/.local/state}/opencode/plugin-meta.json"
OMO_CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/oh-my-opencode"

[ -f "$META" ] || { echo "no plugin-meta.json at $META" >&2; exit 2; }

TARGET=$(python3 - "$META" <<'PY'
import json, sys
for entry in json.load(open(sys.argv[1])).values():
    if "oh-my-openagent" in str(entry.get("spec", "")):
        print(entry.get("target", "")); break
PY
)
[ -n "$TARGET" ] && [ -d "$TARGET" ] || { echo "oh-my-openagent not resolvable from plugin-meta.json" >&2; exit 2; }

BUNDLE="$TARGET/dist/index.js"
[ -f "$BUNDLE" ] || { echo "no bundle at $BUNDLE" >&2; exit 2; }

for f in connected-providers.json provider-models.json; do
  [ -f "$OMO_CACHE/$f" ] || {
    echo "ERROR: missing $OMO_CACHE/$f" >&2
    echo "  OMO writes these once opencode has started with the plugin loaded." >&2
    echo "  Start opencode at least once, then re-run. Do NOT read an absent" >&2
    echo "  cache as 'no models available'." >&2
    exit 2
  }
done

# Extract the routing tables from the loaded build. They are plain object
# literals in the bundle; brace-match them and let node eval them to JSON, so we
# read the tables the running plugin actually uses -- never a copy from the repo,
# which may describe a different version.
WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT

python3 - "$BUNDLE" "$WORK" <<'PY' || { echo "could not extract routing tables" >&2; exit 2; }
import re, sys
bundle, work = sys.argv[1], sys.argv[2]
s = open(bundle, encoding="utf8", errors="replace").read()
for sym in ("AGENT_MODEL_REQUIREMENTS", "CATEGORY_MODEL_REQUIREMENTS"):
    m = re.search(sym + r"\s*=\s*", s)
    if not m:
        sys.exit(1)
    i = m.end(); depth = 0; j = i
    while True:
        if s[j] == "{": depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0: break
        j += 1
    open(f"{work}/{sym}.js", "w").write(s[i:j + 1])
PY

node -e '
const fs = require("fs");
const work = process.argv[1];
for (const sym of ["AGENT_MODEL_REQUIREMENTS", "CATEGORY_MODEL_REQUIREMENTS"]) {
  const obj = eval("(" + fs.readFileSync(work + "/" + sym + ".js", "utf8") + ")");
  fs.writeFileSync(work + "/" + sym + ".json", JSON.stringify(obj));
}' "$WORK" || { echo "could not parse routing tables" >&2; exit 2; }

python3 - "$WORK" "$OMO_CACHE" "$CFG_DIR" <<'PY'
import json, os, re, sys

work, cache, cfg_dir = sys.argv[1], sys.argv[2], sys.argv[3]

import datetime
_pm = json.load(open(f"{cache}/provider-models.json"))
_ts = _pm.get("updatedAt")
if _ts:
    try:
        _age = (datetime.datetime.now(datetime.timezone.utc)
                - datetime.datetime.fromisoformat(_ts.replace("Z", "+00:00")))
        if _age.total_seconds() > 86400:
            print(f"WARNING: OMO provider cache is {_age.days}d old ({_ts}).")
            print("  It refreshes only when opencode runs with the plugin loaded, so a")
            print("  retired model may still look available. Start opencode, then re-run.")
            print()
    except ValueError:
        pass

connected = set(json.load(open(f"{cache}/connected-providers.json"))["connected"])
models = json.load(open(f"{cache}/provider-models.json"))["models"]

# opencode gates providers itself, via disabled_providers / enabled_providers in
# opencode.jsonc. OMO's cache does NOT respect that gating -- it has been
# observed listing a provider opencode had disabled -- so a chain could resolve
# to a model the harness cannot actually serve. Apply the harness gate here.
core_disabled, core_enabled = set(), None
_core = os.path.join(cfg_dir, "opencode.jsonc")
if not os.path.isfile(_core):
    _core = os.path.join(cfg_dir, "opencode.json")
if os.path.isfile(_core):
    try:
        _raw = open(_core, encoding="utf8", errors="replace").read()
        _cfg = json.loads("\n".join(
            l for l in _raw.splitlines() if not l.lstrip().startswith("//")))
        core_disabled = {p.lower() for p in (_cfg.get("disabled_providers") or [])}
        if _cfg.get("enabled_providers"):
            core_enabled = {p.lower() for p in _cfg["enabled_providers"]}
    except Exception:
        pass

def _gated(provider):
    p = provider.lower()
    if core_enabled is not None and p not in core_enabled:
        return True
    return p in core_disabled

gated = {p for p in connected if _gated(p)}
connected -= gated
models = {p: l for p, l in models.items() if not _gated(p)}
available = {f"{p}/{m['id']}" for p, lst in models.items() for m in lst}

# Mirrors of the plugin's own resolution helpers. Kept deliberately literal so
# they can be diffed against the bundle after an upstream change.
def normalize_model_name(name):
    n = name.lower()
    n = re.sub(r"claude-(opus|sonnet|haiku)-(\d+)[.-](\d+)", r"claude-\1-\2.\3", n)
    n = re.sub(r"kimi-k2[.-](\d+)", r"kimi-k2.\1", n)
    n = re.sub(r"\b(glm|gpt)-(\d+)[.-](\d+)", r"\1-\2.\3", n)
    return n

def transform(provider, model):
    if provider == "github-copilot":
        m = re.sub(r"claude-(\w+)-(\d+)-(\d+)", r"claude-\1-\2.\3", model)
        m = re.sub(r"gemini-3\.1-pro(?!-)", "gemini-3.1-pro-preview", m)
        return re.sub(r"(?<!antigravity-)gemini-3-flash(?!-)", "gemini-3-flash-preview", m)
    if provider == "google":
        m = re.sub(r"gemini-3\.1-pro(?!-)", "gemini-3.1-pro-preview", model)
        return re.sub(r"(?<!antigravity-)gemini-3-flash(?!-)", "gemini-3-flash-preview", m)
    if provider in ("kimi-coding", "kimi-for-coding"):
        return {"kimi-k3": "k3", "kimi-k3-256k": "k3-256k"}.get(model, model)
    return model

def fuzzy(target, provider):
    tn = normalize_model_name(target)
    cands = [m for m in available if m.split("/")[0] == provider]
    matches = [m for m in cands if tn in normalize_model_name(m)]
    if not matches:
        return None
    for m in matches:
        if normalize_model_name(m) == tn:
            return m
    exact = [m for m in matches
             if normalize_model_name("/".join(m.split("/")[1:])) == tn]
    return min(exact or matches, key=len)

def resolve(chain):
    for entry in chain:
        for provider in entry["providers"]:
            if provider not in connected:
                continue
            transformed = transform(provider, entry["model"])
            candidates = [entry["model"]]
            if transformed != entry["model"]:
                candidates.append(transformed)
            for mid in candidates:
                hit = fuzzy(f"{provider}/{mid}", provider)
                if hit:
                    return hit, entry.get("variant"), entry["model"]
    return None, None, None

# A slot pinned in config never consults the chain -- an override wins outright
# in resolveModelPipeline, so an unreachable chain behind it is harmless.
#
# Audit each variant SEPARATELY. Only the symlink target is active at any time,
# so merging pins across variants lets one variant's pin mask another's gap --
# and the masked gap surfaces the day the user switches.
def collect(cfg):
    # Schema allows agents/categories BOTH top-level and inside a host scope
    # ("[opencode]", "[codex]", "[senpi]"). Scan both, or a pinned slot in a
    # host-scoped config is misreported as unpinned.
    scopes = [("", cfg)]
    scopes += [(k, v) for k, v in cfg.items()
               if k.startswith("[") and isinstance(v, dict)]
    pins = {}
    disabled = set()
    for scope, block in scopes:
        for prov in (block.get("disabled_providers") or []):
            disabled.add(prov.lower())
    for scope, block in scopes:
        for kind in ("agents", "categories"):
            for slot, spec in (block.get(kind) or {}).items():
                if not isinstance(spec, dict):
                    continue
                model = spec.get("model") or (spec.get("models") or [None])[0]
                if model:
                    pins[(kind, slot)] = (model, scope)
    return pins, disabled

def read_variants(path):
    """One entry per independently-activatable config: the file itself, plus one
    per `profiles.<name>` (OMO layers base -> [harness] -> profile ->
    profile.[harness], and only one profile is active at a time)."""
    raw = open(path, encoding="utf8", errors="replace").read()
    # Strip only whole-line // comments: a naive regex eats the // in $schema URLs.
    stripped = "\n".join(l for l in raw.splitlines() if not l.lstrip().startswith("//"))
    try:
        cfg = json.loads(stripped)
    except Exception as exc:
        return None, exc
    profiles = cfg.get("profiles") or {}
    if not profiles:
        return [("", collect(cfg))], None
    out = [(" (no profile selected)", collect(cfg))]
    for pname, pcfg in profiles.items():
        base_pins, base_dis = collect(cfg)
        prof_pins, prof_dis = collect(pcfg if isinstance(pcfg, dict) else {})
        merged = dict(base_pins); merged.update(prof_pins)
        out.append((f" profile={pname}", (merged, base_dis | prof_dis)))
    return out, None

variants = []
for name in sorted(os.listdir(cfg_dir)):
    if not (name.startswith("omo.json") or name.startswith("oh-my-openagent.json")):
        continue
    full = os.path.join(cfg_dir, name)
    if os.path.islink(full) or not os.path.isfile(full) or ".bak" in name:
        continue
    variants.append((name, full))

active = None
for link in ("omo.jsonc", "oh-my-openagent.json"):
    for base in (os.path.expanduser("~/.omo"), cfg_dir):
        cand = os.path.join(base, link)
        if os.path.islink(cand):
            active = os.path.realpath(cand)
            break
    if active:
        break

print(f"Connected providers: {', '.join(sorted(connected))}")
if gated:
    print(f"Gated by opencode.jsonc:  {', '.join(sorted(gated))} "
          f"(present in OMO's cache, not served by opencode)")
print(f"Models available:    {len(available)}")
print(f"Active variant:      {os.path.basename(active) if active else '(no symlink found)'}")
if active and not os.path.exists(active):
    print()
    print(f"ERROR: the active config symlink is dangling -- {active} does not exist.")
    print("  The plugin treats a missing config as an empty one and the doctor still")
    print("  reports 'System OK', so every pin is silently dropped. Repoint the")
    print("  symlink at an existing file before deleting the old target.")
    sys.exit(1)
print()

if not variants:
    print("No config variants found -- auditing bare upstream routing only.")
    variants = [("(no config)", None)]

failures = []
strays = []
for name, full in variants:
    entries, err = ([("", ({}, set()))], None) if full is None else read_variants(full)
    tag = "  <-- ACTIVE" if full and active and os.path.samefile(full, active) else ""
    if err:
        print(f"#### {name}{tag}")
        print(f"  UNPARSEABLE: {err}")
        failures.append((name, "-", "-", "unparseable"))
        print()
        continue
    for suffix_label, (pins, disabled) in entries:
        name_shown = f"{name}{suffix_label}"
        print(f"#### {name_shown}{tag}")

        # A variant exists to select a provider. The routing table cannot express
        # that -- it is a global preference order -- so when several providers are
        # authenticated, an UNPINNED slot resolves to whichever the table prefers,
        # not to the one this variant is for. Infer the intent from the variant's
        # own pins and flag anything resolving elsewhere.
        pin_provs = [m.split("/")[0] for m, _ in pins.values() if "/" in m]
        intended_provider = max(set(pin_provs), key=pin_provs.count) if pin_provs else None
        if disabled:
            print(f"  (isolation: disabled_providers = {', '.join(sorted(disabled))})")

        for sym, kind, label in (("AGENT_MODEL_REQUIREMENTS", "agents", "AGENT"),
                                 ("CATEGORY_MODEL_REQUIREMENTS", "categories", "CATEGORY")):
            table = json.load(open(f"{work}/{sym}.json"))
            print(f"  == {label} ==")
            for slot, req in table.items():
                pin = pins.get((kind, slot))
                if pin:
                    model, scope = pin
                    prov = model.split("/")[0].lower() if "/" in model else ""
                    if prov in disabled:
                        print(f"    {slot:<20} LEAK    {model}{scope}   (pinned to a disabled provider)")
                        strays.append((name_shown, kind, slot, prov, "an allowed provider"))
                    else:
                        print(f"    {slot:<20} PINNED  {model}{scope}")
                    continue
                chain = req.get("fallbackChain") or []
                hit, variant, intended = resolve(chain)
                top = chain[0]["model"] if chain else None
                if hit:
                    got_provider = hit.split("/")[0]
                    off_provider = (got_provider.lower() in disabled) if disabled else (
                        intended_provider is not None and got_provider != intended_provider)
                    mark = "STRAY" if off_provider else ("ok  " if intended == top else "down")
                    if off_provider:
                        why = ("provider is in disabled_providers" if disabled
                               else f"not {intended_provider}, this variant's provider")
                        suffix = f"   ({why})"
                        strays.append((name_shown, kind, slot, got_provider, intended_provider))
                    else:
                        suffix = "" if intended == top else f"   (degraded from {top})"
                    shown = f"{hit}({variant})" if variant else hit
                    print(f"    {slot:<20} {mark}    {shown}{suffix}")
                else:
                    print(f"    {slot:<20} NONE    -> falls through to systemDefaultModel")
                    failures.append((name_shown, kind, slot, top or "?"))
        print()

if strays:
    print("== Slots resolving to a different provider than the variant intends ==")
    for name, kind, slot, got, want in strays:
        print(f"  [{name}] {kind}.{slot}  -> {got}, expected {want}")
    print()
    print("Upstream routing encodes a global preference order, not 'use my")
    print("provider'. With several providers authenticated these slots ignore the")
    print("variant entirely. Pin them explicitly, or the variant silently does")
    print("nothing for that slot.")
    print()

if not failures and not strays:
    print("== Every slot resolves, on the intended provider, in every variant. ==")
    sys.exit(0)

if not failures:
    sys.exit(1)

print("== Slots with no reachable model ==")
for name, kind, slot, intended in failures:
    print(f"  [{name}] {kind}.{slot}  (upstream intended: {intended})")
print()
print("These fall through to opencode.jsonc's top-level \"model\" -- silently, and")
print("usually at the wrong tier and cost. Pin each one explicitly. Choose by the")
print("slot's required capability, and prefer a substitution upstream itself makes")
print("elsewhere in its own chains over inventing one.")
print()
print("Express effort as model(variant) in a plain string, e.g.")
print("  \"agents\": { \"prometheus\": { \"model\": \"<provider>/<model>(xhigh)\" } }")
print("parseVariantFromModelID accepts that form, so no fallback_models is needed")
print("and the doctor stays clean.")
sys.exit(1)
PY
