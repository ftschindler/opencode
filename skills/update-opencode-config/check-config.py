#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["json5>=0.9.25"]
# ///
"""Check the invariants this config relies on.

Each rule here exists because breaking it fails silently at runtime rather than
loudly at startup: a provider quietly unreachable, a model pin inherited by the
wrong provider, a credential committed. Runs as a prek hook, or by hand:

    ./skills/update-opencode-config/check-config.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
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

BASE_CONFIG = REPO / "opencode.jsonc"
OMO_CONFIG = REPO / "omo.jsonc.PROFILES"
PROFILES_DIR = REPO / "profiles"

# The apiKey value a profile uses to read its own credential file.
CREDENTIAL_REF = "{file:.env}"


@dataclass
class Report:
    """Collected findings. Errors fail the run; warnings do not."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def error(self, rule: str, detail: str) -> None:
        self.errors.append(f"{rule}: {detail}")

    def warn(self, rule: str, detail: str) -> None:
        self.warnings.append(f"{rule}: {detail}")


def load(path: Path) -> dict:
    """Parse JSON or JSONC. json5 tolerates comments and trailing commas."""
    return json5.loads(path.read_text())


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(REPO), *args], capture_output=True, text=True, check=False
    )
    return result.stdout


def tracked_files() -> set[str]:
    return {line for line in git("ls-files").splitlines() if line}


def profile_names() -> list[str]:
    if not PROFILES_DIR.is_dir():
        return []
    return sorted(
        entry.name
        for entry in PROFILES_DIR.iterdir()
        if entry.is_dir() and not entry.name.startswith(".")
    )


def check_base_is_fail_closed(base: dict, report: Report) -> None:
    """The base config must enable no provider.

    Isolation is enforced by each profile naming its own provider. If the base
    enabled anything, a run that picked up no profile would silently gain access
    to it instead of stopping.
    """
    enabled = base.get("enabled_providers")
    if enabled is None:
        report.error("base-fail-closed", "opencode.jsonc has no 'enabled_providers' key")
    elif enabled != []:
        report.error(
            "base-fail-closed",
            f"opencode.jsonc enables {enabled}; it must be [] so an unset "
            "OPENCODE_CONFIG_DIR fails instead of leaking a provider",
        )

    if "provider" in base:
        report.error(
            "base-fail-closed",
            "opencode.jsonc declares a 'provider' block; credentials belong in "
            "the profile that uses them, so no session can reach another's key",
        )


def check_plugins_agree(base: dict, profiles: dict[str, dict], report: Report) -> None:
    """Every config that declares plugins must declare exactly the same ones.

    The plugin entry is repeated in each profile because the doctor looks for it
    in the active profile's own config. Repetition means it can drift, and a
    profile pinning a different version would silently run a different build.
    """
    declared: dict[str, list[str]] = {}
    if "plugin" in base:
        declared["opencode.jsonc"] = base["plugin"]
    for name, config in profiles.items():
        if "plugin" in config:
            declared[f"profiles/{name}/opencode.json"] = config["plugin"]

    if not declared:
        report.error("plugin-agreement", "no config declares a 'plugin' list")
        return

    missing = [f"profiles/{name}/opencode.json" for name, c in profiles.items() if "plugin" not in c]
    for where in missing:
        report.error(
            "plugin-agreement",
            f"{where} declares no 'plugin'; the doctor reads it from the active "
            "profile and does not account for layering",
        )

    reference_where, reference = next(iter(declared.items()))
    for where, plugins in declared.items():
        if plugins != reference:
            report.error(
                "plugin-agreement",
                f"{where} lists {plugins}, but {reference_where} lists {reference}",
            )


def check_profile_shape(name: str, config: dict, report: Report) -> None:
    """A profile enables exactly its own provider, and defaults to its model."""
    enabled = config.get("enabled_providers")
    if enabled != [name]:
        report.error(
            "profile-provider",
            f"profiles/{name}/opencode.json enables {enabled}; it must enable "
            f"exactly ['{name}'], matching the directory name",
        )

    model = config.get("model")
    if not model:
        report.error("profile-model", f"profiles/{name}/opencode.json has no default 'model'")
    elif not model.startswith(f"{name}/"):
        report.error(
            "profile-model",
            f"profiles/{name}/opencode.json defaults to '{model}', which is not "
            f"served by '{name}'",
        )


def check_profile_credential(name: str, config: dict, tracked: set[str], report: Report) -> None:
    """A profile that needs a credential keeps it local, gitignored, and sampled."""
    options = config.get("provider", {}).get(name, {}).get("options", {})
    api_key = options.get("apiKey")
    if api_key is None:
        return

    if api_key != CREDENTIAL_REF:
        report.warn(
            "profile-credential",
            f"profiles/{name}/opencode.json reads its key from '{api_key}' rather "
            f"than '{CREDENTIAL_REF}'; that is allowed but inconsistent",
        )
        return

    env = PROFILES_DIR / name / ".env"
    sample = PROFILES_DIR / name / ".env.sample"

    if f"profiles/{name}/.env" in tracked:
        report.error(
            "secret-not-committed",
            f"profiles/{name}/.env is tracked by git; it holds a live credential",
        )
    if env.exists() and (env.stat().st_mode & 0o077):
        report.warn(
            "secret-permissions",
            f"profiles/{name}/.env is group- or world-readable; chmod 600 it",
        )
    if not sample.exists():
        report.error(
            "profile-credential",
            f"profiles/{name}/opencode.json reads {CREDENTIAL_REF} but there is no "
            ".env.sample documenting what belongs there",
        )
    elif f"profiles/{name}/.env.sample" not in tracked:
        report.warn(
            "profile-credential",
            f"profiles/{name}/.env.sample is untracked; a fresh clone will not "
            "show what credential this profile needs",
        )


def check_omo_profiles_match(omo: dict, names: list[str], report: Report) -> None:
    """The plugin's profile blocks must line up with the profile directories.

    The plugin derives its profile name from the config directory name. A block
    with no matching directory is dead, and a directory with no block silently
    falls back to the base layer.
    """
    blocks = omo.get("profiles", {})
    for name in names:
        if name not in blocks:
            report.error(
                "omo-profile-match",
                f"profiles/{name}/ has no 'profiles.{name}' block in "
                f"{OMO_CONFIG.name}; it would silently use only the base layer",
            )
    for name in blocks:
        if name not in names:
            report.error(
                "omo-profile-match",
                f"{OMO_CONFIG.name} defines 'profiles.{name}' with no matching "
                f"profiles/{name}/ directory; the block is unreachable",
            )


def collect_model_pins(block: dict) -> list[tuple[str, str]]:
    """Return (where, model-id) for every model pinned in an omo config block."""
    pins: list[tuple[str, str]] = []
    for section in ("agents", "categories"):
        for slot, settings in (block.get(section) or {}).items():
            if not isinstance(settings, dict):
                continue
            if isinstance(settings.get("model"), str):
                pins.append((f"{section}.{slot}.model", settings["model"]))
            for entry in settings.get("models") or []:
                if isinstance(entry, str):
                    pins.append((f"{section}.{slot}.models", entry))
                elif isinstance(entry, dict) and isinstance(entry.get("model"), str):
                    pins.append((f"{section}.{slot}.models", entry["model"]))
    return pins


def provider_of(model: str) -> str:
    return model.split("/", 1)[0] if "/" in model else ""


def check_pins_match_provider(omo: dict, names: list[str], report: Report) -> None:
    """Every pin inside a profile block names that profile's own provider."""
    for name, block in (omo.get("profiles") or {}).items():
        harness = block.get("[opencode]", {})
        for where, model in collect_model_pins(harness):
            if provider_of(model) != name:
                report.error(
                    "pin-provider",
                    f"profiles.{name}.[opencode].{where} pins '{model}', which is "
                    f"not served by '{name}'",
                )


def check_base_pins_are_overridden(omo: dict, names: list[str], report: Report) -> None:
    """A base pin naming one provider must be overridden by every other profile.

    Base pins are inherited by all profiles. One naming github-copilot would
    otherwise leak into the ovhcloud session, where that provider is disabled and
    the agent silently loses its model.
    """
    base_block = omo.get("[opencode]", {})
    base_agents = base_block.get("agents") or {}

    for slot, settings in base_agents.items():
        if not isinstance(settings, dict):
            continue
        model = settings.get("model")
        if not isinstance(model, str):
            continue
        owner = provider_of(model)
        if owner not in names:
            report.error(
                "base-pin-override",
                f"[opencode].agents.{slot} pins '{model}', whose provider has no profile",
            )
            continue
        for name in names:
            if name == owner:
                continue
            overrides = (
                (omo.get("profiles", {}).get(name, {}).get("[opencode]", {}).get("agents") or {})
            )
            if slot not in overrides:
                report.error(
                    "base-pin-override",
                    f"[opencode].agents.{slot} pins '{model}', but profile "
                    f"'{name}' does not override it; that agent would have no "
                    f"reachable model under '{name}'",
                )


def check_mutual_exclusion(omo: dict, names: list[str], report: Report) -> None:
    """Each profile block disables every provider except its own.

    Redundant with enabled_providers, and kept as a tripwire: it turns a
    mis-pinned model into a config error rather than a silent provider switch.
    """
    for name in names:
        block = omo.get("profiles", {}).get(name, {}).get("[opencode]", {})
        disabled = set(block.get("disabled_providers") or [])
        for other in names:
            if other != name and other not in disabled:
                report.warn(
                    "mutual-exclusion",
                    f"profiles.{name} does not list '{other}' in disabled_providers",
                )


# Credential-shaped strings, sized so documentation placeholders do not match.
# A real token has a long random tail; "gho_PLACEHOLDER" in prose does not.
SECRET_PATTERNS = [
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}"),
    re.compile(r"\bgh[opsu]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{30,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\."),  # JWT
]


def check_credentials_untracked(names: list[str], tracked: set[str], report: Report) -> None:
    """No credential may be tracked, whatever it is called.

    A profile's real credential is .env, but near misses happen -- .env.PAT,
    .env.old, a key pasted into a scratch file. Matching only the exact name
    ".env" once let a live token reach the index, so this checks every candidate
    by name *and* scans tracked content for credential-shaped strings.
    """
    for name in names:
        directory = PROFILES_DIR / name
        if not directory.is_dir():
            continue
        for candidate in directory.glob(".env*"):
            if candidate.name == ".env.sample":
                continue
            relative = f"profiles/{name}/{candidate.name}"
            ignored = subprocess.run(
                ["git", "-C", str(REPO), "check-ignore", "-q", relative], check=False
            )
            if ignored.returncode != 0:
                report.error(
                    "secret-not-committed",
                    f"{relative} is not gitignored; only .env.sample may be tracked",
                )

    for relative in sorted(tracked):
        path = REPO / relative
        if not path.is_file() or path.stat().st_size > 1_000_000:
            continue
        try:
            content = path.read_text(errors="ignore")
        except OSError:
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(content):
                report.error(
                    "secret-not-committed",
                    f"{relative} contains something shaped like a live credential",
                )
                break


def check_migrations_can_write(report: Report) -> None:
    """The plugin's migration writer rejects a target it resolved through a symlink.

    It accepts only a resolved path named omo.json or omo.jsonc, inside a .omo
    directory. Here ~/.omo/omo.jsonc is a symlink to omo.jsonc.PROFILES in this
    repo, so reads work and every migration write is refused. The layout is
    deliberate and this is a warning, not a fault to repair.
    """
    link = Path.home() / ".omo" / "omo.jsonc"
    if not link.exists():
        report.warn("migration-target", f"{link} does not exist; the plugin has no config to read")
        return

    resolved = link.resolve()
    in_omo_dir = resolved.parent.name == ".omo"
    named_right = resolved.name in ("omo.json", "omo.jsonc")
    if in_omo_dir and named_right:
        return

    report.warn(
        "migration-target",
        f"~/.omo/omo.jsonc resolves to {resolved}, which the plugin's migration "
        "writer rejects: reads work, but every migration fails on start until its "
        "marker is added to _migrations by hand",
    )


def check_no_pending_migration_journal(report: Report) -> None:
    """A journal left with targetWritten false retries on every start.

    Its payload is a snapshot from the first attempt, so a migration that has sat
    here through later edits will overwrite them if it ever succeeds. Read the
    payload before clearing it.
    """
    journal_path = Path.home() / ".omo" / ".migration-journal.json"
    if not journal_path.exists():
        return

    try:
        journal = load(journal_path)
    except Exception as exc:  # noqa: BLE001 - any parse failure is the same finding
        report.error("migration-journal", f"{journal_path} exists and cannot be parsed: {exc}")
        return

    if journal.get("targetWritten") is not False:
        return

    migration_id = journal.get("migrationId", "<unnamed>")
    applied = migration_id in (load(OMO_CONFIG).get("_migrations") or [])
    already = (
        "its marker is already in _migrations, so this journal is stale"
        if applied
        else "its marker is absent from _migrations, so the migration has not been applied"
    )
    report.error(
        "migration-journal",
        f"a pending migration '{migration_id}' retries on every start; {already}. "
        f"Read {journal_path} before clearing it: its payload is a snapshot from "
        "the first attempt and can overwrite later edits",
    )


def check_hook_entries_exist(report: Report) -> None:
    """Every local prek hook must point at an executable that exists.

    These scripts have moved once. A stale `entry:` does not fail loudly: prek
    reports the hook as passing work it never ran, so the invariants silently
    stop being enforced.
    """
    config = REPO / ".pre-commit-config.yaml"
    if not config.exists():
        report.warn("hook-entry", "no .pre-commit-config.yaml; invariants are not enforced on commit")
        return

    for line in config.read_text().splitlines():
        stripped = line.strip()
        if not stripped.startswith("entry:"):
            continue
        entry = stripped.split(":", 1)[1].strip()
        target = REPO / entry.split()[0]
        if not target.exists():
            report.error("hook-entry", f".pre-commit-config.yaml points at '{entry}', which does not exist")
        elif not os.access(target, os.X_OK):
            report.error("hook-entry", f"{entry} is not executable, so the hook cannot run it")


def main() -> int:
    report = Report()

    for required in (BASE_CONFIG, OMO_CONFIG):
        if not required.exists():
            print(f"missing required file: {required}", file=sys.stderr)
            return 2

    base = load(BASE_CONFIG)
    omo = load(OMO_CONFIG)
    names = profile_names()
    tracked = tracked_files()

    if not names:
        report.error("profiles", "no profile directories under profiles/")

    profiles: dict[str, dict] = {}
    for name in names:
        config = PROFILES_DIR / name / "opencode.json"
        if not config.exists():
            report.error(
                "profile-config",
                f"profiles/{name}/ has no opencode.json; opencode creates the "
                "directory on a typo, so this is usually a stale mistake",
            )
            continue
        profiles[name] = load(config)

    check_base_is_fail_closed(base, report)
    check_plugins_agree(base, profiles, report)
    for name, config in profiles.items():
        check_profile_shape(name, config, report)
        check_profile_credential(name, config, tracked, report)
    check_omo_profiles_match(omo, names, report)
    check_pins_match_provider(omo, names, report)
    check_base_pins_are_overridden(omo, names, report)
    check_mutual_exclusion(omo, names, report)
    check_credentials_untracked(names, tracked, report)
    check_migrations_can_write(report)
    check_no_pending_migration_journal(report)
    check_hook_entries_exist(report)

    for warning in report.warnings:
        print(f"warning  {warning}")
    for error in report.errors:
        print(f"ERROR    {error}")

    if report.errors:
        print(f"\n{len(report.errors)} invariant(s) violated.")
        return 1

    summary = f"config invariants hold across {len(names)} profile(s): {', '.join(names)}"
    print(f"{summary}." if not report.warnings else f"{summary}, with {len(report.warnings)} warning(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
