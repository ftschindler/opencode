# opencode config

This is my [opencode](https://github.com/anomalyco/opencode) config,
meant to be provider-agnostic and cross-machine sharable.

Shared across machines:

- [tools](tools/)
- [AGENTS.md](AGENTS.md)
- plugin selection and configuration
- provider configuration: which providers exist, and which models each uses

Not shared: provider credentials. Each lives in its own profile as a gitignored
`.env`, so a session has no access to another provider's key.

Both provider and plugin configuration are organised as [profiles](profiles/),
one directory per provider.

It runs the [oh-my-openagent](https://www.npmjs.com/package/oh-my-openagent)
plugin, which routes each agent and each category of work to a model. Two
providers are wired up: GitHub Copilot and OVHcloud.

**A session uses one provider only.** Providers are kept separate for privacy
and data-compliance reasons, so a session must never see both. Which provider is
active is a property of the machine, not of the session: one box runs GitHub
Copilot, another runs OVHcloud, and the choice changes rarely.

Isolation fails closed. The base config enables no providers at all, so a run
that picks up no profile gets no models and stops, rather than quietly gaining
access to both.

## Getting started on a fresh machine

```bash
git clone <this-repo> ~/.config/opencode
cd ~/.config/opencode

ln -sfn ~/.config/opencode/omo.jsonc.PROFILES ~/.omo/omo.jsonc   # step 2
./skills/update-opencode-config/set-provider.py                                        # step 4, reports what is missing
prek install                                                     # optional
```

1. **Clone to `~/.config/opencode`.** opencode reads this path by default.
2. **Link the plugin's model config into place.** The plugin reads its config
   from `~/.omo/`, outside this repository; the symlink keeps the real file
   version-controlled here.
3. **Select this machine's provider** (see step 4), **then give it its
   credential.** Providers differ in how they are authenticated, and the order
   matters — see "Authenticating a provider" below.
4. **Select this machine's provider.**

   ```bash
   ./skills/update-opencode-config/set-provider.py ovhcloud
   ```

   Run it with no arguments to see what is selected and what is still missing.
   It writes `~/.config/environment.d/opencode.conf`, reports whether the
   credential is in place — including which kind that provider needs — and tells
   you whether your shell will pick the setting up.
5. **Log out and back in**, then start opencode. `environment.d` is read when the
   systemd user session starts, so a new terminal is not enough.

### Authenticating a provider

Providers come in two kinds, and the difference decides what you do on a fresh
machine.

**A provider with an API key** keeps it in its own profile: copy that profile's
`.env.sample` to `.env` and paste the key in. Nothing interactive, and the
credential never leaves the profile directory. OVHcloud works this way.

**A provider that only issues tokens through a sign-in** cannot be set up from a
file. It needs one interactive `/connect` per machine, and opencode stores the
result centrally in `~/.local/share/opencode/auth.json` — not in the profile.
GitHub Copilot works this way; see
[profiles/github-copilot](profiles/github-copilot/README.md).

**Select the profile before signing in.** `/connect` can only offer providers
the running session has enabled, and this repository's base config deliberately
enables none. Signing in with no profile active therefore offers nothing. Start
opencode with the target provider's own profile, where that provider is enabled:

```bash
OPENCODE_CONFIG_DIR=~/.config/opencode/profiles/<id> opencode   # then /connect
```

`auth.json` is machine-local and outside this repository, so it is never
committed. It holds every provider signed in this way, so it is not something to
delete when cleaning up a single one — use `opencode auth logout <id>`.

### Why the provider lives in `environment.d`

It is a systemd specification rather than a desktop-specific hook: any session
started by the systemd user manager reads it, Plasma and GNOME alike. The
KDE-only `~/.config/plasma-workspace/env/` would not survive a move to another
desktop.

Shells do not read `environment.d` themselves, so an import block in `~/.bashrc`
covers sessions that bypass the systemd user session — notably SSH. It uses
systemd's own generator rather than sourcing the files, which are `KEY=VALUE` and
would have quotes and `$(…)` in values evaluated by a shell. Variables that are
already set are left alone, so a forwarded `SSH_AUTH_SOCK` survives.
`set-provider.py` prints the block if it is not already installed.

One context remains uncovered by design: a non-interactive remote command such as
`ssh host 'opencode …'` runs no interactive shell and reads neither file. It gets
the base config, finds no providers, and stops. Pass the variable explicitly
there if you need it.

## Setting this machine's provider

```bash
./skills/update-opencode-config/set-provider.py ovhcloud   # then log out and back in
```

The script rewrites `~/.config/environment.d/opencode.conf` and reports whether
the credential is in place and whether your shell will see the change.

This is the only per-machine difference in the whole setup. Everything else —
`tools/`, `plugins/`, `AGENTS.md`, shared model pins — is identical on every box
and tracked here.

A profile directory is named after the opencode provider it enables, and the
plugin derives its profile name from that same directory name.

| Profile | Provider | Default model |
|---|---|---|
| `github-copilot` | GitHub Copilot | `claude-opus-5` |
| `ovhcloud` | OVHcloud | `qwen3.5-397b-a17b` |
| *(none)* | none — fails closed | — |

A profile loads exactly one provider. The other is not enabled, not listed, and
unreachable by any agent, so a session cannot mix models from both.

One variable switches both halves of the setup. opencode reads
`profiles/<name>/opencode.json` for its own settings, and the plugin derives the
matching entry under `profiles` in `omo.jsonc.PROFILES` from the directory name.

There is deliberately no "all providers" mode. The base config enables none, so
an unset variable produces an error rather than a session with both.

## Adding a provider

Name everything after the opencode provider id, e.g. `anthropic`. The directory
name is load-bearing: opencode reads `profiles/<id>/opencode.json`, and the
plugin derives its own profile name from that same directory.

1. **Decide how it authenticates.** If it takes an API key, create
   `profiles/<id>/.env.sample` describing the credential and `.env` holding the
   real one; `.env` is gitignored, `.env.sample` is committed. If it only issues
   tokens through a sign-in, skip this — the credential goes to `auth.json` via
   `/connect` instead.
2. **Create `profiles/<id>/opencode.json`.** Include the `provider` block only
   if the provider authenticates from a key; omit it for one that needs
   `/connect`, which stores its credential centrally instead.

   ```json
   {
     "$schema": "https://opencode.ai/config.json",
     "plugin": ["oh-my-openagent@latest"],
     "enabled_providers": ["<id>"],
     "model": "<id>/<a-model-it-serves>",
     "provider": { "<id>": { "options": { "apiKey": "{file:.env}" } } }
   }
   ```

   Relative `{file:}` paths resolve from the profile directory, and a missing
   file is a hard config error rather than a silent fallback. Omit the
   `provider` block entirely if the provider needs no key.
3. **Add a `profiles.<id>` block to `omo.jsonc.PROFILES`,** listing every *other*
   provider in `disabled_providers`.
4. **Decide whether it needs model pins.** The plugin ships routing for around
   two dozen hosted providers and picks sensible models for those itself. A
   provider outside that set has no routing at all, so every agent and category
   needs an explicit pin — this is why the `ovhcloud` block is long and the
   `github-copilot` one is nearly empty. Check with:

   ```bash
   OPENCODE_CONFIG_DIR=~/.config/opencode/profiles/<id> \
     skills/update-opencode-config/check-resolution.sh
   ```
5. **Override any base pin that names another provider.** Pins in the top-level
   `[opencode]` block are inherited by every profile, so one naming
   `github-copilot` leaves that agent with no reachable model under `<id>`.
6. **Write `profiles/<id>/README.md`** if obtaining the credential is not
   obvious — see [github-copilot](profiles/github-copilot/README.md).
7. **Verify the invariants, then the credential:**

   ```bash
   ./skills/update-opencode-config/check-config.py
   OPENCODE_CONFIG_DIR=~/.config/opencode/profiles/<id> \
     opencode run --model <id>/<cheap-model> "Reply with exactly: OK"
   ```

   The second command matters: listing models does not prove a credential
   works, because the catalogue and the inference endpoint authenticate
   separately.

A profile layers over the base config rather than replacing it, and holds only
what differs; `tools/`, `plugins/` and `AGENTS.md` are inherited. Credentials are
deliberately *not* shared: each provider's key lives inside its own profile, so a
session running one provider has no access to another's credentials, not even a
path to them. The `plugin` entry is repeated in every profile because the doctor
looks for it in the active profile's own `opencode.json`.

## Checking that the config is healthy

```bash
./skills/update-opencode-config/check-config.py                                   # this repo's own invariants
skills/update-opencode-config/check-all.py   # every profile, one table
```

`check-all.py` audits all profiles at once — credential, provider isolation,
doctor, dead pins, unresolved slots — because a check under one profile says
nothing about the others. To drill into one, set the profile and run the
individual checks:

```bash
export OPENCODE_CONFIG_DIR=~/.config/opencode/profiles/<id>
npx oh-my-openagent doctor                                        # config conformance
skills/update-opencode-config/check-plugin.sh      # plugin build vs npm
skills/update-opencode-config/check-models.sh      # pins naming dead models
skills/update-opencode-config/check-resolution.sh  # agents with no model
```

`check-config.py` runs as a git hook, so the invariants below are enforced on
every commit once `prek install` has been run. Hooks are managed with
[prek](https://github.com/j178/prek), a drop-in replacement for `pre-commit`
that reads the same `.pre-commit-config.yaml`. It checks that the
base config stays fail-closed, that every config agreeing to load the plugin
names the same version, that each profile enables exactly its own provider,
that no credential is committed, that the plugin's profile blocks match the
profile directories, and that no base model pin is left unoverridden in a
profile belonging to a different provider. Each rule exists because breaking it
fails silently at runtime rather than loudly at startup.

None of these prove a credential works. The model catalogue and the inference
endpoint authenticate separately, so a rejected credential can still return a
full model list — a fine-grained GitHub PAT does exactly that, listing every
model and then failing on the first real request. Verify a credential by making
one:

```bash
OPENCODE_CONFIG_DIR=~/.config/opencode/profiles/<id> \
  opencode run --model <id>/<cheap-model> "Reply with exactly: OK"
```

**Run these after any provider changes its catalogue, and after any plugin
update.** Model names are literal strings here, so a retired model becomes a
runtime failure weeks later, when some agent happens to be invoked.

The last script is the least obvious and the most useful. It reports any agent
whose model resolves to nothing, or to a provider the active profile is meant to
exclude. Neither condition produces an error on its own.

The `update-opencode-config` skill drives all of this and proposes replacements.
It lives in this repository, at `skills/update-opencode-config/`, and opencode
picks it up from there — including when a profile is active, since skills are
searched in both the profile directory and its parent.

The skill and the prek hook deliberately share the same executables, so the
checks cannot drift from the config they check. The skill is not meant to be
installed anywhere else: it assumes this repository's layout, and a copy under
`~/.agents/skills/` would shadow it by name.

## Optional: git-ai integration

`plugins/git-ai.ts` is gitignored because it hardcodes an absolute path to the
local `git-ai` binary, which differs per machine. Install `git-ai` and let it
regenerate the plugin to track AI authorship of edits:

```bash
git-ai install-hooks
```

Without this step the config still works, and the plugin is a no-op if absent.

## Layout

| Path | Purpose |
|---|---|
| `opencode.jsonc` | Base config: plugin only, and no provider enabled |
| `omo.jsonc.PROFILES` | Plugin model config, one block per profile |
| `profiles/<provider>/opencode.json` | Per-profile config: one provider, its credentials, its default model |
| `profiles/<provider>/.env` | That provider's credential, gitignored; `.env.sample` is tracked |
| `skills/update-opencode-config/` | The maintenance skill, plus the scripts its prek hook shares |
| `tools/`, `plugins/` | Local tool and plugin definitions, shared by all profiles |
| `AGENTS.md` | Writing style guidance for agents working in this repo |

## Links

- [opencode](https://github.com/anomalyco/opencode)
- [oh-my-openagent](https://www.npmjs.com/package/oh-my-openagent)
- [git-ai integration with opencode](https://ftschindler.github.io/running-linux/done/20260624-git-ai-integration-with-opencode/)
