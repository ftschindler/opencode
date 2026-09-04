#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["json5>=0.9.25"]
# ///
"""Select which provider this machine uses.

The choice is a property of the machine, not of a session: it lives in
``~/.config/environment.d/opencode.conf`` and changes rarely.

    ./skills/update-opencode-config/set-provider.py                 # show the current selection
    ./skills/update-opencode-config/set-provider.py ovhcloud        # select a provider
    ./skills/update-opencode-config/set-provider.py --check         # report readiness, change nothing

Providers are kept isolated for privacy and data-compliance reasons, so a
session sees exactly one. Selecting nothing is safe: the base config enables no
provider, so opencode stops rather than reaching for whatever is authenticated.
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

import json5

def find_repo_root(start: Path) -> Path:
    """Walk up to the directory holding opencode.jsonc.

    Not a fixed number of parents: this file has moved once already, and a
    hardcoded parent count fails silently -- the wrong directory is still a
    valid Path, so checks run against nothing and report success.
    """
    for candidate in [start, *start.parents]:
        if (candidate / "opencode.jsonc").is_file():
            return candidate
    raise SystemExit(
        f"cannot locate the opencode config repo above {start}: no opencode.jsonc found"
    )


REPO = find_repo_root(Path(__file__).resolve().parent)
PROFILES_DIR = REPO / "profiles"
ENV_FILE = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "environment.d" / "opencode.conf"
VARIABLE = "OPENCODE_CONFIG_DIR"

# Shells do not read environment.d. This is what bridges the gap for SSH.
SHELL_SNIPPET = """\
# Import the systemd user environment (~/.config/environment.d) into shells that
# bypass the systemd user session -- notably SSH. Uses systemd's own generator
# rather than sourcing the files, which are KEY=VALUE and would have quotes and
# $(...) in values evaluated by a shell. Already-set variables are left alone,
# so a forwarded SSH_AUTH_SOCK survives.
import_environment_d() {
    local generator=/usr/lib/systemd/user-environment-generators/30-systemd-environment-d-generator
    [ -x "$generator" ] || return 0
    local line name
    while IFS= read -r line; do
        case "$line" in
            [A-Za-z_]*=*) ;;
            *) continue ;;
        esac
        name=${line%%=*}
        [ -n "${!name+x}" ] && continue
        export "$line"
    done < <("$generator" 2>/dev/null)
}
import_environment_d
"""

SHELL_FILES = [Path.home() / ".bashrc", Path.home() / ".zshrc", Path.home() / ".profile"]

OK = "  ok     "
WARN = "  warn   "
TODO = "  todo   "


def available_profiles() -> list[str]:
    if not PROFILES_DIR.is_dir():
        return []
    return sorted(
        entry.name
        for entry in PROFILES_DIR.iterdir()
        if entry.is_dir() and (entry / "opencode.json").exists()
    )


def configured_profile() -> str | None:
    """The provider named in environment.d, if any."""
    if not ENV_FILE.exists():
        return None
    for line in ENV_FILE.read_text().splitlines():
        match = re.match(rf"\s*{VARIABLE}=(.+)$", line)
        if match:
            return match.group(1).strip().rstrip("/").rsplit("/", 1)[-1]
    return None


def write_selection(name: str) -> None:
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    ENV_FILE.write_text(
        "# opencode: this machine's provider profile.\n"
        "#\n"
        "# One provider per session, always -- providers are kept separate for\n"
        "# privacy and data-compliance reasons, so a session must never see both.\n"
        "#\n"
        "# Written by skills/update-opencode-config/set-provider.py. Changing this needs a fresh login:\n"
        "# environment.d is read when the systemd user session starts.\n"
        "#\n"
        "# If this variable is unset, opencode falls back to its base config, which\n"
        "# enables no providers at all. That is deliberate: a missed variable fails\n"
        "# loudly instead of silently enabling every provider.\n"
        f"{VARIABLE}=${{HOME}}/.config/opencode/profiles/{name}\n"
    )


def shell_imports_environment_d() -> Path | None:
    for path in SHELL_FILES:
        if path.exists() and "environment-d-generator" in path.read_text():
            return path
    return None


AUTH_FILE = (
    Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    / "opencode"
    / "auth.json"
)


def authenticated_via_connect(name: str) -> bool:
    """Whether opencode holds a credential for this provider from /connect."""
    if not AUTH_FILE.exists():
        return False
    try:
        return name in json5.loads(AUTH_FILE.read_text())
    except Exception:
        return False


def credential_state(name: str) -> tuple[str, str]:
    """Report whether this profile's credential is in place.

    Providers differ in how they can be authenticated. Some accept a key you can
    paste into the profile's .env; others only issue a token through an
    interactive sign-in, which opencode stores centrally in auth.json.
    """
    config = json5.loads((PROFILES_DIR / name / "opencode.json").read_text())
    options = config.get("provider", {}).get(name, {}).get("options", {})
    if "apiKey" not in options:
        if authenticated_via_connect(name):
            return OK, f"signed in via /connect (credential in {AUTH_FILE})"
        readme = PROFILES_DIR / name / "README.md"
        hint = f" -- see profiles/{name}/README.md" if readme.exists() else ""
        return TODO, (
            f"no credential for '{name}'. This profile has no .env, so it needs an "
            f"interactive sign-in: start opencode with this profile active and run "
            f"/connect{hint}"
        )

    env = PROFILES_DIR / name / ".env"
    if not env.exists():
        readme = PROFILES_DIR / name / "README.md"
        hint = f"see profiles/{name}/README.md" if readme.exists() else "copy .env.sample to .env"
        return TODO, f"profiles/{name}/.env is missing -- {hint}"
    if not env.read_text().strip():
        return TODO, f"profiles/{name}/.env is empty"
    if env.stat().st_mode & 0o077:
        return WARN, f"profiles/{name}/.env is group- or world-readable; chmod 600 it"
    return OK, f"credential present in profiles/{name}/.env (not verified -- see below)"


def report(selected: str | None) -> None:
    profiles = available_profiles()
    print(f"available providers: {', '.join(profiles) if profiles else '(none)'}")
    print(f"selected for this machine: {selected or '(none -- opencode will not start)'}")
    print()

    if selected is None:
        print(TODO + f"no provider selected. Run: ./skills/update-opencode-config/set-provider.py <{'|'.join(profiles)}>")
        return
    if selected not in profiles:
        print(WARN + f"{ENV_FILE} names '{selected}', which is not a profile directory")
        return

    status, detail = credential_state(selected)
    print(status + detail)

    shell_file = shell_imports_environment_d()
    if shell_file:
        print(OK + f"{shell_file.name} imports environment.d, so SSH sessions get it too")
    else:
        print(TODO + "no shell file imports environment.d.")
        print("           Graphical sessions read it, but shells -- notably SSH -- do not.")
        print(f"           Add this to ~/.bashrc (or the rc file your shell reads):\n")
        for line in SHELL_SNIPPET.splitlines():
            print(f"             {line}")
        print()

    model = json5.loads((PROFILES_DIR / selected / "opencode.json").read_text()).get("model", "")
    print()
    print("  A credential is only proven by an actual request. Listing models is not")
    print("  enough: the catalogue and the inference endpoint authenticate separately,")
    print("  so a rejected credential can still return a full model list. Verify with:")
    print()
    print(f"    OPENCODE_CONFIG_DIR={PROFILES_DIR / selected} \\")
    print(f"      opencode run --model {model} 'Reply with exactly: OK'")
    print()

    live = os.environ.get(VARIABLE, "")
    live_name = live.rstrip("/").rsplit("/", 1)[-1] if live else None
    if live_name == selected:
        print(OK + f"this shell already uses '{selected}'")
    elif live_name:
        print(WARN + f"this shell still uses '{live_name}'; log out and back in")
    else:
        print(TODO + f"{VARIABLE} is not set in this shell; log out and back in")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("provider", nargs="?", help="profile directory name, e.g. ovhcloud")
    parser.add_argument("--check", action="store_true", help="report readiness without changing anything")
    args = parser.parse_args()

    profiles = available_profiles()
    if not profiles:
        print("no profiles found under profiles/")
        return 2

    if args.provider and not args.check:
        if args.provider not in profiles:
            print(f"unknown provider '{args.provider}'. Available: {', '.join(profiles)}")
            return 2
        write_selection(args.provider)
        print(f"selected '{args.provider}' in {ENV_FILE}\n")
        report(args.provider)
        return 0

    report(configured_profile())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
