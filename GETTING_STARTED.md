# Getting started on a fresh machine

This page is for the case where opencode is already installed and running on its
default provider, and this configuration has to replace it without losing the
sessions that machine has already accumulated.

Work through it in order: clear the config directory, install the
prerequisites, clone this repository into place, then select a provider.

## 1. What to delete, what to keep

The two directories are separate on purpose, and only one of them holds data.

**Delete `~/.config/opencode/`.** It holds configuration only, and every file in
it is replaced by this repository. A default opencode install leaves an
`opencode.json` here, maybe a `tui.json`, maybe an `AGENTS.md`. None of it is
data. If you would rather not delete it outright, move it aside:

```bash
mv ~/.config/opencode ~/.config/opencode.before-felix
```

**Delete `~/.omo/`** for the same reason. It holds the plugin's model
configuration, which step 4 replaces with a symlink into this repository.

**Keep `~/.local/share/opencode/` untouched.** This is where the data lives:

| Path | What it is |
|---|---|
| `opencode.db` (plus `-shm`, `-wal`) | The session database. Deleting this loses your history |
| `storage/`, `snapshot/`, `repos/` | Session message bodies, file snapshots, per-repo state |
| `auth.json` | Credentials from every interactive `/connect`, GitHub Copilot among them |
| `mcp-auth.json` | OAuth tokens for MCP servers |
| `log/`, `tool-output/` | Disposable, but there is no reason to touch them |

An existing `auth.json` is worth keeping for a second reason: if that machine has
already signed in to GitHub Copilot, step 5 is done before you start.

**`~/.cache/opencode/` is disposable.** Downloaded skills and plugin builds; it
refills on the next run. Clearing it costs you a slow first start, nothing else.

## 2. Install the prerequisites

Four tools, and only the first two are strictly required.

- **`uv`** - runs `set-provider.py` and the check scripts, which declare their
  own dependencies in a script header, and runs the `markitdown` MCP server via
  `uvx`. Install from [astral.sh/uv](https://docs.astral.sh/uv/getting-started/installation/).
- **Node with `npx`** - fetches the `oh-my-openagent` plugin and runs its
  doctor. Any current LTS release works.
- **`prek`** - runs the config invariant checks as a git hook. Optional unless
  you intend to commit changes back. See
  [j178/prek](https://github.com/j178/prek).
- **`git-ai`** - records AI authorship of edits. Entirely optional; the config
  works without it. See
  [the write-up](https://ftschindler.github.io/running-linux/done/20260624-git-ai-integration-with-opencode/).

Check what you have:

```bash
uv --version && npx --version && prek --version
```

## 3. Clone the repository into place

The path is load-bearing: opencode reads `~/.config/opencode` by default, and
nothing here tells it otherwise.

```bash
git clone git@github.com:ftschindler/opencode.git ~/.config/opencode
cd ~/.config/opencode
```

If you have no SSH key on this machine, clone over HTTPS instead:
`https://github.com/ftschindler/opencode.git`.

Then link the plugin's model configuration into place. It has to sit in `~/.omo`,
outside the repository; the symlink keeps the real file version-controlled here.

```bash
mkdir -p ~/.omo
ln -sfn ~/.config/opencode/omo.jsonc.PROFILES ~/.omo/omo.jsonc
```

Two optional steps, if you plan to edit the config rather than just use it:

```bash
prek install    # the invariant checks as a pre-commit hook
npm install     # type definitions for the TypeScript under plugins/ and tools/
```

## 4. Select a provider

Each provider lives in its own profile, and a session sees exactly one. The base
config enables none, so a machine with nothing selected stops with an error
rather than quietly reaching for whatever is authenticated. That error is the
usual reason a fresh clone looks broken.

Run the script with no arguments first - it reports what is selected and what is
missing, and changes nothing:

```bash
./skills/update-opencode-config/set-provider.py
```

Then select one:

```bash
./skills/update-opencode-config/set-provider.py github-copilot
```

| Profile | Credential | Default model |
|---|---|---|
| `github-copilot` | interactive sign-in, see step 5 | `claude-opus-5` |
| `ovhcloud` | API key in `profiles/ovhcloud/.env` | `qwen3.5-397b-a17b` |

The script writes `~/.config/environment.d/opencode.conf` and tells you whether
your shell will pick the setting up. **Log out and back in afterwards**:
`environment.d` is read when the systemd user session starts, so a new terminal
is not enough.

## 5. Give the provider its credential

What you do depends on how the provider authenticates.

**A provider with an API key** keeps it inside its own profile. Copy the sample
and paste the key in:

```bash
cp profiles/ovhcloud/.env.sample profiles/ovhcloud/.env
$EDITOR profiles/ovhcloud/.env
```

`.env` is gitignored; `.env.sample` is tracked and describes what the credential
looks like.

**A provider that only issues tokens through a sign-in** cannot be set up from a
file. It needs one interactive `/connect` per machine, and opencode stores the
result in `~/.local/share/opencode/auth.json` rather than in the profile. GitHub
Copilot works this way; see [profiles/github-copilot](profiles/github-copilot/README.md).

Start opencode with that provider's profile explicitly, because `/connect` can
only offer providers the running session has enabled, and the base config
enables none:

```bash
OPENCODE_CONFIG_DIR=~/.config/opencode/profiles/github-copilot opencode
# then, in the session:  /connect
```

## 6. Verify it works

```bash
skills/update-opencode-config/check-all.py
```

This audits every profile in one table: credential present, provider isolation,
plugin conformance, dead model pins, agents whose model resolves to nothing.

A passing table does not prove the credential works, because the model catalogue
and the inference endpoint authenticate separately - a rejected key can still
list every model. Make one real request:

```bash
OPENCODE_CONFIG_DIR=~/.config/opencode/profiles/<id> \
  opencode run --model <id>/<a-cheap-model> "Reply with exactly: OK"
```

Then start `opencode` normally. It picks the profile up from the environment
variable set in step 4.

## If something is still wrong

| Symptom | Cause |
|---|---|
| "no providers enabled", or no model to pick | Step 4 not done, or you have not logged out and back in since |
| Provider listed but every request fails | Credential missing or rejected - rerun step 5, then the real request in step 6 |
| `opencode.conf` exists but the session disagrees | A non-interactive SSH command reads neither `environment.d` nor your shell rc. Pass `OPENCODE_CONFIG_DIR` explicitly there |
| An agent starts and immediately has no model | A base model pin naming another provider. `check-resolution.sh` reports exactly which |

The [README](README.md) covers the rest: how the base config and profiles layer,
how to add a provider, and what each check script looks at.
