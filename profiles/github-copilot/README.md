# GitHub Copilot profile

Unlike a provider with an API key, Copilot cannot be authenticated from a file.
It needs a one-time interactive sign-in per machine, and opencode stores the
resulting token centrally in `~/.local/share/opencode/auth.json` rather than in
this directory. That is why this profile has no `.env`.

## Signing in

**Run `/connect` with this profile active.** The profile enables
`github-copilot`, so it is offered; the base config enables nothing, so it is
the wrong place to sign in from.

```bash
OPENCODE_CONFIG_DIR=~/.config/opencode/profiles/github-copilot opencode
```

Then `/connect`, select GitHub Copilot, and approve the device code in the
browser. If this machine's profile is already `github-copilot`, plain `opencode`
does the same thing.

## Verify with a real request

`opencode models` is not a verification. It authenticates against a different
endpoint and returns a full catalogue even for a credential that cannot make a
single inference call.

```bash
OPENCODE_CONFIG_DIR=~/.config/opencode/profiles/github-copilot \
  opencode run --model github-copilot/gpt-5.4-mini "Reply with exactly: OK"
```

A reply means it works. `AI_APICallError: Bad Request` means the credential was
rejected, whatever `opencode models` claims.

## Why not a personal access token

A fine-grained PAT with the `Copilot Requests` permission is tempting: it can be
created by hand, per machine, without a browser. It does not work here, and the
failure looks like success at first — `opencode models` lists the full
catalogue, then the first real request fails with `AI_APICallError: Bad
Request`, which is GitHub's `Personal Access Tokens are not supported for this
endpoint`.

opencode reaches Copilot by exchanging the token at `/copilot_internal/v2/token`
for a short-lived bearer, and that exchange rejects PATs. GitHub's own Copilot
CLI skips the exchange, which is why the same token works there but not here.

Classic PATs (`ghp_`) do not work either; the Copilot permission does not exist
for that token type.

## The stored credential

`auth.json` is machine-local and outside this repository, so it is never
committed and never shared between machines. It holds credentials for every
provider signed in this way, not just Copilot — so do not delete it to "clean
up" one provider. To remove just this one:

```bash
opencode auth logout github-copilot
```
