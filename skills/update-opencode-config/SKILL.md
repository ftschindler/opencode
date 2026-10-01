---
name: update-opencode-config
description: Audit and update this opencode setup - the oh-my-openagent plugin build, config conformance, provider credentials, and the model pins for every provider profile. Use when asked to check or update opencode models, check for broken or stale model references, verify agents actually resolve to a model, update the plugin, add or check a provider, or after any provider adds or retires models.
---

# Update this opencode setup

## Overview

This setup keeps **one provider per session**, chosen per machine. Each provider
gets a profile directory; a profile enables exactly that provider and nothing
else. The repository at `~/.config/opencode` documents the arrangement in its
own README — **read it first**, since the user may have changed it since this
skill was written.

**This skill lives inside the config it maintains**, at
`~/.config/opencode/skills/update-opencode-config/`, and is versioned with it.
That is deliberate: its scripts are the same ones the repository's prek hook
runs, so the checks cannot drift from the config they check.

It is **not** designed to be installed elsewhere — not via `npx skill add`, not
by copying into `~/.agents/skills/`. It assumes this repository's layout, reads
its `opencode.jsonc` to locate the repo root, and shares executables with its
hooks. A copy in another location would be a second, diverging source of truth
and would shadow this one by name. Maintain it here.

Four things drift underneath it, independently and silently:

- **Each provider's catalogue** — models are added and retired.
- **The plugin build** — new config keys, retuned routing, changed defaults.
- **The gap between requested and loaded plugin build** — a floating `@latest`
  spec does not mean the running build is current.
- **Credentials** — tokens are revoked or expire.

None of these announce themselves. A retired model surfaces only when the agent
using it is next invoked, possibly weeks later.

**Audit every profile, not just the active one.** A profile you are not
currently using rots unnoticed and breaks the day you switch to it. Audit them
**separately**: only one is active at a time, so merging their findings lets one
profile's pin mask another's gap.

**This skill is read-only until the user approves.** It ends in a summary and a
proposal. Do not edit config, update the plugin, or change a credential before
the user has seen that proposal and said yes.

## The central rule: route or pin?

The plugin ships a **provider-aware routing table** —
`AGENT_MODEL_REQUIREMENTS` and `CATEGORY_MODEL_REQUIREMENTS` in
`<plugin-target>/dist/index.js`. Every agent and category has an ordered
`fallbackChain`; each entry names a model plus the providers that can serve it:

```js
sisyphus: { fallbackChain: [
  { providers: ["anthropic","github-copilot","opencode","vercel"],
    model: "claude-opus-5", variant: "max" },
  { providers: ["opencode-go","kimi-for-coding", ...], model: "kimi-k3" },
  ...
]}
```

A pin is an **override**: it wins outright and suppresses the chain. Two
questions decide whether a slot needs one:

1. **Reachability** — does the chain resolve this slot at all? A slot is
   unreachable if no entry names a provider this profile enables *with a model
   that provider actually serves*.
2. **Selection** — does it resolve to the provider this profile intends? The
   table encodes a global preference order, not "use my provider".

> Pin only what the routing cannot reach, or cannot reach on the intended
> provider. Everything else stays unpinned: a pin can only freeze a staler pick
> that you then have to maintain.

Because each profile enables exactly one provider, question 2 mostly answers
itself here — but only while isolation holds. Verify rather than assume it.

A provider **absent from the routing table** (niche, regional, self-hosted) gets
no entries at all, so every slot falls through to the default model — one model
for all work, no tiering. Such a provider needs explicit pins on every slot.
That is a structural fact about the table, not a preference. A well-represented
provider may still have isolated gaps. Neither case is guessable — measure it
with `check-resolution.sh`.

Note what does *not* differ between the two cases: **how you choose the model**.
Whether filling two gaps or nineteen, read upstream's intent for that slot from
the chain, then map it to the nearest equivalent the provider actually serves.

### How resolution actually works

Two paths, and they disagree — worth knowing, because it explains failures that
look impossible:

- **Startup** (`resolveModelPipeline`): with a populated provider-models cache,
  each candidate is confirmed against the real catalogue. With an *empty* cache
  it degrades to checking provider connectivity only, and can return a model the
  provider does not serve.
- **Runtime retry** (`createReachabilityChecker`): only ever checks provider
  connectivity, so it can hand off to a model that cannot exist. Upstream
  behaviour, not fixable from config.

When no entry resolves, the slot falls through to `systemDefaultModel` — the
top-level `"model"` in the active profile's `opencode.json`. **No error, no
warning, no doctor finding.** The symptom is not breakage but wrong tier and
wrong cost: a cheap high-volume slot quietly running on the most expensive
model.

Two normalisations before judging a mismatch: ids are compared after
`normalizeModelName` (case-folded, `claude-x-4-5` ≡ `claude-x-4.5`), and some
providers get rewrites via `transformModelForProvider`. A dash-vs-dot difference
is **not** a real mismatch; a genuinely different name (`gpt-5-nano` vs
`gpt-5-mini`) is.

## The setup

| Path | Role |
|---|---|
| `~/.config/opencode/opencode.jsonc` | Base config. Enables **no** provider — see below. |
| `~/.config/opencode/profiles/<id>/opencode.json` | One provider, its default model, optionally its credential. Directory name is the opencode provider id. |
| `~/.config/opencode/profiles/<id>/.env` | That provider's key, gitignored. Absent for providers that authenticate by sign-in. |
| `~/.omo/omo.jsonc` | Plugin config, symlinked into the repo. One `profiles.<id>` block per profile. |
| `~/.local/share/opencode/auth.json` | Credentials from `/connect`. Machine-local, **shared across providers**. |
| `~/.config/environment.d/opencode.conf` | Which profile this machine uses. |

Verified properties, measured on this setup — do not re-derive by trial:

- **`OPENCODE_CONFIG_DIR` replaces the base opencode config, it does not layer.**
  Each profile dir needs a complete config: `plugin`, `enabled_providers`,
  `model`, and any `provider` block.
- **A relative `{file:.env}` resolves from the profile directory**, so the
  credential can live beside the config. A *missing* file is a hard config
  error, not a silent fallback.
- **The base config sets `enabled_providers: []`** — fail-closed. A run that
  picks up no profile gets no models and stops, rather than quietly reaching
  whatever is authenticated. Do not "fix" this by enabling providers there.
- **`~/.omo/` is hard-coded to `$HOME/.omo`** and does not follow
  `OPENCODE_CONFIG_DIR`. One plugin file serves all profiles; the profile key
  selects within it. Layering is base → `[harness]` → profile →
  `profile.[harness]`, so shared settings belong at the base — and a base pin
  naming one provider must be overridden in every other profile.
- **A mistyped profile fails loudly** (`Activated omo profile "X" does not
  exist`) — but opencode **silently creates** a missing config directory, so a
  typo in `OPENCODE_CONFIG_DIR` yields a provider-less directory rather than an
  error. Stray `profiles/*/` without `opencode.json` are this.

### Two ways a provider authenticates

This decides what a fresh machine needs, and it is not a stylistic choice:

- **API key** → lives in the profile's `.env`, referenced as `{file:.env}`.
  Fully declarative; a clone plus the key is enough.
- **Interactive sign-in** → `/connect` only. The token goes to the shared
  `auth.json`, *not* the profile. GitHub Copilot is this kind.

**Sign in with the target profile active.** `/connect` can only offer providers
the running session enables, and the base enables none — so signing in with no
profile offers nothing.

`auth.json` holds every provider signed in this way. Never delete it to clean up
one provider; use `opencode auth logout <id>`.

### Two layers, two `disabled_providers` — do not conflate them

`opencode.json` and the plugin config are read by **different programs**, and
both define a `disabled_providers` key. They do not interact:

| | profile `opencode.json` | plugin config (`omo.jsonc`) |
|---|---|---|
| Read by | opencode core | the plugin only |
| Meaning | provider **not loaded at all** — absent from `opencode models` | rewrites or errors on the plugin's own pins |
| Also offers | `enabled_providers` — an allowlist | — |

opencode never parses the plugin config, so its `"[opencode]"` block is **not**
an opencode directive — it is the plugin's own harness selector, and the same
file read under another harness would use `[codex]` or `[senpi]`.

For isolation the opencode layer is the stronger one: `enabled_providers`
prevents the provider existing at all. The plugin's key only guards pins it can
see. Use it as a loud assertion on top, never as the enforcement.

**Verified mismatch:** the plugin's provider cache does not respect opencode's
gating, so a chain entry naming a gated provider can look reachable to it.
`check-resolution.sh` therefore intersects the cache with the profile's
`enabled_providers`/`disabled_providers` and prints what it gated. Cross-check
rather than trusting either: `opencode models` is harness truth, the cache is
what the plugin's routing believes.

### ⚠️ Known upstream traps

All verified against a real installation.

**1. Two schemas validate the same file, and they disagree.** Scope decides
which one bites: a `"[opencode]"` block is `record(string, unknown)` to the
strict schema, so it faces only the plugin's; top-level blocks face both.

| Shape | top-level `agents` | `[opencode].agents` | `[opencode].categories` |
|---|---|---|---|
| `"model": "prov/id"` | ✅ | ✅ | ✅ |
| `"model": "prov/id(variant)"` | ✅ | ✅ | ✅ |
| `"models": [...]` | 🛑 unknown key, **breaks startup** | 🛑 unknown key, **breaks startup** | ✅ clean |
| `"fallback_models": [...]` | 🛑 **invalid, breaks startup** | ⚠️ deprecation warning, works | ⚠️ deprecation warning, works |

- For **agents there is no non-deprecated way to express a fallback chain.** The
  doctor's lint says replace `fallback_models` with `models`, but
  `agents.models` breaks startup. The lint is a blanket key-name map applied
  without checking the container. Those agent warnings are the doctor's bug —
  `severity: "warning"`, and the check returns `warn`, not fail. Prefer the
  warning over the breakage.
- **A single `model` string is the only shape valid everywhere**, and with
  `model(variant)` it also carries reasoning effort. Prefer it for agents.
- Moving an agents block between `[opencode]` and top level can turn working
  config into a startup error.

The same lint deprecates `reasoningEffort`, `variant` and `thinking` in favour
of **`reasoning`**.

**2. `omo config migrate` can produce a broken config.** It is the doctor's own
suggested remedy and has generated exactly the state above. Do not run it as a
fix. A **plugin update can also run it automatically on first start** — so after
any update, re-run the doctor and check whether config appeared somewhere new.

**3. `fallback_models` is inert by default.** The hook consuming it is gated on
`model_fallback ?? false`, and `runtime_fallback` defaults false too. Unless one
is explicitly enabled, a chain has no runtime effect. Check before treating a
chain as meaningful or recommending one.

**4. Listing models does not verify a credential.** The catalogue and the
inference endpoint authenticate separately. A rejected credential can still
return a full model list — a fine-grained GitHub PAT does exactly this, listing
every model, then failing the first real request with `AI_APICallError: Bad
Request`. **Only a real request verifies a credential.**

**5. A migration cannot write through the `~/.omo/omo.jsonc` symlink.** The
writer accepts only a *resolved* path named `omo.json` or `omo.jsonc` inside a
`.omo` directory. Here the link points at `omo.jsonc.PROFILES` in this repo, so
reads work and every migration write is refused:

```text
Migration target is not an omo config path: ~/.config/opencode/omo.jsonc.PROFILES
```

The attempt is not abandoned. `~/.omo/.migration-journal.json` keeps
`targetWritten: false` and retries on **every start**, so one stuck migration
warns indefinitely. `check-config.py` reports both the unwritable target and a
pending journal.

**Read the journal before clearing it.** Its `targetWrite` payload is a snapshot
taken at the first attempt, so a migration stuck across later config edits will
overwrite them if it ever succeeds — a repair that makes the write work is the
more dangerous of the two available repairs.

Recognise the state; do not reach for a remedy on your own. Writing a marker
into `_migrations` by hand asserts that the migration's content is already
present, which no script here can check — only a person reading the diff can.
Report what the journal names and what the config does or does not contain, and
let them decide.

## Steps

### 0. Pre-flight: make the config recoverable

Mandatory before **any** step that installs, updates, migrates or reinstalls.

The filesystem copy is the safety net and does not depend on version control.
Do not assume the config directory is a git repo.

```bash
B=/tmp/opencode-config-backup-$(date +%Y%m%d-%H%M%S); mkdir -p "$B"
cp -a ~/.config/opencode/opencode.jsonc ~/.config/opencode/omo.jsonc* \
      ~/.config/opencode/profiles ~/.omo/omo.jsonc "$B"/ 2>/dev/null
echo "backup: $B"; ls -la "$B"
```

`cp -a` preserves symlinks rather than dereferencing them. Report the path, and
stop if the copy failed. If the directory *is* a repo, offer to commit or stash
as a second net — but never as a reason to skip the copy, since the interesting
edits are usually the uncommitted ones.

### 1. Audit every profile

```bash
~/.config/opencode/skills/update-opencode-config/check-all.py
```

One table over all profiles: credential kind and presence, provider isolation,
doctor, dead pins, resolution gaps. Exit 1 if any profile needs attention.

Then drill into whatever it flags, with that profile active:

```bash
export OPENCODE_CONFIG_DIR=~/.config/opencode/profiles/<id>
~/.config/opencode/skills/update-opencode-config/check-plugin.sh      # build vs npm
~/.config/opencode/skills/update-opencode-config/check-models.sh      # dead pins
~/.config/opencode/skills/update-opencode-config/check-resolution.sh  # unresolved slots
npx -y oh-my-openagent doctor                                     # conformance
```

`check-resolution.sh` is the one nothing else performs. It replays the loaded
build's chains against the live cache and reports each slot as `PINNED`, `ok`,
`down` (resolved, past upstream's first choice — usually fine), or:

- **`NONE`** — resolves to nothing, silently falling through to the default at
  whatever tier and cost that is.
- **`STRAY`** — resolves to a *different provider* than the profile intends.
- **`LEAK`** — pinned to a provider the profile disables.

If a script reports no models or a missing cache, that is an environment problem
— opencode missing from `PATH`, no provider authenticated, or the plugin never
loaded. Never read it as "everything is broken"; fix the environment first.

### 2. Establish the plugin build

`check-plugin.sh` reports requested spec, **actually loaded build**, npm
`latest`, and audits config keys against the loaded build's own schema.

- **A floating spec is not a guarantee.** opencode resolves `@latest` once and
  caches a hard pin; the cached build can sit versions behind indefinitely. Read
  the loaded version from `~/.local/state/opencode/plugin-meta.json`, never from
  the spec string, and never from `~/.config/opencode/package.json` (that pins
  `@opencode-ai/plugin`, the SDK — a different thing).
- **Unknown config keys are silently ignored** — no error, the setting just does
  nothing.
- `auto_update` defaults to **enabled** and does not prevent this, because
  opencode owns the package pin. Never conclude the build is current from it.

To update, once approved: delete the cached package directory so opencode
re-resolves, then restart and re-run the script.

```bash
rm -rf ~/.cache/opencode/packages/oh-my-openagent@latest   # print the path first
```

This is the plugin's own invalidation — its `postinstall.mjs` calls
`invalidateOpenCodePluginCache()`, which removes exactly these directories.

After the restart, check for `~/.omo/.migration-journal.json`. An update can run
a migration on first start, and here that write is refused (trap 5), leaving a
journal that retries silently on every start afterwards.

**Two paths that do not work, both verified:**

- `opencode plugin oh-my-openagent@latest --global --force` does **not** move
  the pin despite `--force`; it only rewrites config. Never report success from
  it — confirm with `check-plugin.sh`.
- `npm install` inside the cached package directory is **self-destructive**: the
  postinstall deletes the directory npm is installing into.

Re-running the official installer repopulates the cache but **rewrites config by
standard basename**, which has destroyed a live config before. Only with a
backup in hand.

Treat a major-version jump as its own decision: read release notes first, and do
not move onto `next`/`beta` uninvited.

### 3. Read upstream's recommendations for the build in use

Upstream retunes as models land. Fetch fresh; do not rely on memory or on this
skill. **Pin the fetch to the loaded build's tag** — the default branch
describes unreleased work and may reference keys the build does not support:

```bash
VER=<loaded-version-from-step-2>
curl -sL "https://raw.githubusercontent.com/code-yeongyu/oh-my-openagent/v${VER}/README.md" -o /tmp/omo.README.md
grep -nE 'Sisyphus|Hephaestus|Prometheus|routes to|recommended default' /tmp/omo.README.md
```

The recommendation spans providers, so some models it names may not exist on
yours. Treat it as evidence about *what tier a slot wants*, then find the
nearest equivalent that provider serves.

### 4. Judge each slot on required capability

Assign by what the slot must be able to do and by **call volume** — never by
string similarity to the old pin, and never by "newer is better".

| Slot | Required capability |
|---|---|
| `sisyphus` | Main orchestrator: plans, delegates, drives to completion. Long-horizon instruction-following and tool use. Also the default agent. |
| `prometheus` | Strategic planner. Interview-mode questioning over raw code output. |
| `hephaestus` | Autonomous deep worker, given a goal not a recipe. Sustained autonomy. |
| `oracle`, `momus` | Read-only consultation and adversarial critique. Pure reasoning; low volume, so cost matters little. |
| `metis` | Pre-planning analysis, ambiguity detection. Mid reasoning. |
| `atlas`, `sisyphus-junior` | Focused execution without delegation. |
| `explore`, `librarian` | **Highest volume**, fired many at once. Cost and speed dominate; deep reasoning not required. |
| `multimodal-looker` | Must genuinely handle images/PDFs. A hard capability gate. |
| `ultrabrain`, `unspecified-high` | Hard logic, architecture. Top reasoning tier. |
| `deep` | Autonomous research and execution, agentic/codex-style. |
| `visual-engineering` | Frontend and design; benefits from multimodal. |
| `artistry`, `writing` | Creative and prose quality over raw logic. |
| `quick`, `unspecified-low` | **High volume**, trivial work. Cheapest adequate model. |

Recurring traps:

- **A new generation may not span every tier.** If the newest has no cheap
  member, cheap slots correctly stay on an older one. Promoting
  `explore`/`librarian`/`quick` because a model is "newer" multiplies cost on
  the highest-volume slots.
- **Cheap-and-fast ≠ stronger.** A newer flash/mini model is cheaper and faster
  than an older pro model, not better. Fallback material, not a pro-tier
  primary.
- **Names are not a version ordering.** Same-generation variants carry no
  reliable ranking and the CLI exposes no spec sheets. Check upstream; if still
  unclear, **say so and ask**. Do not guess silently.
- **Prefer a substitution upstream itself makes.** If another slot's chain lists
  `X → Y` as its degradation, `Y` is upstream's own opinion about replacing `X`.
  Use the `model(variant)` form to preserve the intended effort level.

### 5. Check each profile's default model

Each `profiles/<id>/opencode.json` has its own `"model"`, and it is the
fallback for every unresolved slot in that profile. It should be served by that
profile's provider, and normally track what `sisyphus` resolves to.

### 6. Summarise and propose — then stop

Lead with the plugin build, then one table **per profile** — never merged, since
only one profile is active at a time:

> Plugin: loaded `<version>`, npm latest `<version>` — ✅ current / ⚠️ N behind.
> Config keys: ✅ all recognised / ⚠️ `<key>` unknown to this build.

| Slot | Kind | Current | Proposed | Status | Rationale |
|---|---|---|---|---|---|
| … | agent/category | … | … | 🛑 broken / ⚠️ stale / ✅ keep | … |

🛑 broken — dead reference or unresolved slot, must change. ⚠️ stale — valid but
a better fit exists. ✅ keep.

Then, explicitly and separately:

- **Deliberately unchanged**, with reasons.
- **Judgement calls without hard evidence** — surface them for correction, never
  bury them.
- **Credential problems**, naming which kind each profile needs.
- **Optional schema improvements** as a clearly separate suggestion.

Sequence it: **plugin update first, model changes second** — a newer build may
retune routing and change what the right proposal is. Say so, and re-run steps
1–4 against the new build before applying model edits.

**Stop here.** Apply nothing yet.

### 7. Apply, only after approval

```bash
# repo invariants (base fail-closed, plugin agreement, no committed secrets, ...)
~/.config/opencode/skills/update-opencode-config/check-config.py

# every profile again
~/.config/opencode/skills/update-opencode-config/check-all.py

# and the only real credential test, per profile
OPENCODE_CONFIG_DIR=~/.config/opencode/profiles/<id> \
  opencode run --model <id>/<cheap-model> "Reply with exactly: OK"
```

Do **not** validate `opencode.jsonc` by stripping `//` comments with a naive
regex — it eats the `https://` in `$schema` and reports a bogus parse error. Use
a real JSONC parser, or let opencode load the file.

After any plugin operation, confirm the config survived — installers write to
`~/.config/opencode`:

```bash
diff -r "$B" ~/.config/opencode 2>&1 | grep -i 'only in\|differ'   # $B from step 0
ls -la ~/.omo/.migration-journal.json 2>/dev/null                  # a half-run migration
```

Restore from the backup, never from memory and never from version control alone,
which silently discards uncommitted work.

Finally: tell the user to **restart opencode**. Neither a changed model nor a
refreshed plugin applies to the running session, and `check-plugin.sh` keeps
reporting the old build until opencode re-resolves it.
