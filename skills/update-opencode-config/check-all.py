#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["json5>=0.9.25"]
# ///
"""Audit every provider profile in one pass.

Each profile isolates one provider, so a check run under one profile says
nothing about the others. This runs the per-profile checks across all of them
and prints one table, so a gap in a profile you are not currently using cannot
hide until the day you switch to it.

    ./check-all.py                 # audit every profile
    ./check-all.py --profile ovh   # just one
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

import json5

MODEL_ID = re.compile(r"^([A-Za-z0-9_.-]+)/[A-Za-z0-9_.:-]+$")

SKILL_DIR = Path(__file__).resolve().parent
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "opencode"
PROFILES_DIR = CONFIG_DIR / "profiles"
AUTH_FILE = (
    Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    / "opencode"
    / "auth.json"
)


def run(cmd: list[str], env_profile: Path | None, timeout: int = 180) -> tuple[int, str]:
    env = dict(os.environ)
    env["OPENCODE_CONFIG_DIR"] = str(env_profile) if env_profile else ""
    try:
        done = subprocess.run(
            cmd, capture_output=True, text=True, env=env, timeout=timeout, check=False
        )
        return done.returncode, done.stdout + done.stderr
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    except FileNotFoundError:
        return 127, "not found"


def profiles() -> list[str]:
    if not PROFILES_DIR.is_dir():
        return []
    return sorted(
        p.name for p in PROFILES_DIR.iterdir() if (p / "opencode.json").is_file()
    )


def credential_kind(name: str) -> str:
    """How this profile authenticates, and whether it currently can."""
    config = json5.loads((PROFILES_DIR / name / "opencode.json").read_text())
    options = config.get("provider", {}).get(name, {}).get("options", {})
    if "apiKey" in options:
        env = PROFILES_DIR / name / ".env"
        if not env.exists() or not env.read_text().strip():
            return "key: MISSING"
        return "key: present"
    if AUTH_FILE.exists():
        try:
            if name in json5.loads(AUTH_FILE.read_text()):
                return "sign-in: present"
        except Exception:
            pass
    return "sign-in: MISSING"


def audit(name: str) -> dict[str, str]:
    directory = PROFILES_DIR / name
    row: dict[str, str] = {"profile": name}

    row["credential"] = credential_kind(name)

    code, out = run(["opencode", "models"], directory)
    # run() merges stderr, where the plugin writes warnings containing absolute
    # paths -- a bare '/' test reads those as model ids and cries isolation breach.
    served = sorted(
        {m.group(1) for line in out.splitlines() if (m := MODEL_ID.match(line.strip()))}
    )
    row["providers"] = ",".join(served) if served else "(none)"
    row["isolated"] = "yes" if served == [name] else f"NO -> {row['providers']}"

    code, _ = run(["npx", "--yes", "oh-my-openagent", "doctor"], directory)
    row["doctor"] = "ok" if code == 0 else f"exit {code}"

    code, out = run([str(SKILL_DIR / "check-models.sh")], directory)
    broken = [l for l in out.splitlines() if l.strip() and l.startswith("  ") and "none" not in l]
    row["dead pins"] = "none" if code == 0 else f"{len(broken)} broken"

    code, out = run([str(SKILL_DIR / "check-resolution.sh")], directory)
    row["resolution"] = "ok" if code == 0 else "gaps"

    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", help="audit only this profile")
    args = parser.parse_args()

    names = profiles()
    if not names:
        print(f"no profiles under {PROFILES_DIR}", file=sys.stderr)
        return 2
    if args.profile:
        if args.profile not in names:
            print(f"unknown profile '{args.profile}'. Available: {', '.join(names)}")
            return 2
        names = [args.profile]

    print(f"auditing {len(names)} profile(s) under {PROFILES_DIR}\n")
    rows = [audit(name) for name in names]

    columns = ["profile", "credential", "isolated", "doctor", "dead pins", "resolution"]
    widths = {c: max(len(c), *(len(r[c]) for r in rows)) for c in columns}
    print("  ".join(c.ljust(widths[c]) for c in columns))
    print("  ".join("-" * widths[c] for c in columns))
    for row in rows:
        print("  ".join(row[c].ljust(widths[c]) for c in columns))

    problems = [
        r["profile"]
        for r in rows
        if r["doctor"] != "ok"
        or r["resolution"] != "ok"
        or r["dead pins"] != "none"
        or not r["isolated"].startswith("yes")
        or "MISSING" in r["credential"]
    ]
    print()
    if problems:
        print(f"needs attention: {', '.join(problems)}")
        print("Re-run the individual scripts under that profile for detail:")
        print(f"  OPENCODE_CONFIG_DIR={PROFILES_DIR}/<name> {SKILL_DIR}/check-resolution.sh")
        return 1

    print("all profiles clean.")
    print(
        "Note: none of this proves a credential works. Only a real request does --\n"
        "  OPENCODE_CONFIG_DIR=<profile> opencode run --model <id> 'Reply with exactly: OK'"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
