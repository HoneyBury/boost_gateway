#!/usr/bin/env python3
"""Prepare a repository-local Conan home with offline/cache/internal-remote defaults."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONAN_HOME = ROOT / ".conan2-local"
DEFAULT_REMOTES_FILE = ROOT / "conan" / "remotes.example.json"
DEFAULT_LOCAL_REMOTES_FILE = ROOT / "conan" / "remotes.local.json"


def run(cmd: list[str], env: dict[str, str]) -> None:
    completed = subprocess.run(cmd, cwd=ROOT, env=env, check=False)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def conan_env(conan_home: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["CONAN_HOME"] = str(conan_home)
    return env


def load_remotes(remotes_file: Path, local_remotes_file: Path, env: dict[str, str], allow_public: bool, no_remote: bool, disable_example_internal: bool) -> list[dict[str, object]]:
    remotes: list[dict[str, object]] = []
    if remotes_file.exists():
        payload = json.loads(remotes_file.read_text(encoding="utf-8"))
        remotes.extend(payload.get("remotes", []))

    if local_remotes_file.exists():
        payload = json.loads(local_remotes_file.read_text(encoding="utf-8"))
        remotes = payload.get("remotes", remotes)

    env_remote_url = env.get("CONAN_REMOTE_URL", "").strip()
    if env_remote_url:
        remotes.insert(0, {
            "name": env.get("CONAN_REMOTE_NAME", "env-conan"),
            "url": env_remote_url,
            "verify_ssl": env.get("CONAN_REMOTE_VERIFY_SSL", "true").lower() != "false",
            "enabled": True,
        })

    if no_remote:
        return []

    normalized: list[dict[str, object]] = []
    seen: set[str] = set()
    for remote in remotes:
        name = str(remote["name"])
        if name in seen:
            continue
        seen.add(name)
        item = dict(remote)
        if name == "conancenter" and not allow_public:
            item["enabled"] = False
        if disable_example_internal and str(item.get("url", "")).endswith("example.internal"):
            item["enabled"] = False
        normalized.append(item)
    return normalized


def configure_remotes(conan_home: Path, remotes: list[dict[str, object]]) -> None:
    env = conan_env(conan_home)
    run(["conan", "remote", "disable", "*"], env)

    if not remotes:
        return

    for remote in remotes:
        name = str(remote["name"])
        url = str(remote["url"])
        verify_ssl = bool(remote.get("verify_ssl", True))
        enabled = bool(remote.get("enabled", True))
        run(["conan", "remote", "add", name, url, "--force"], env)
        if not verify_ssl:
            run(["conan", "remote", "update", name, "--insecure"], env)
        if not enabled:
            run(["conan", "remote", "disable", name], env)
        else:
            run(["conan", "remote", "enable", name], env)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--conan-home", type=Path, default=DEFAULT_CONAN_HOME)
    parser.add_argument("--remotes-file", type=Path, default=DEFAULT_REMOTES_FILE)
    parser.add_argument("--local-remotes-file", type=Path, default=DEFAULT_LOCAL_REMOTES_FILE)
    parser.add_argument("--allow-public", action="store_true", help="Enable public conancenter if present in remotes file.")
    parser.add_argument("--no-remote", action="store_true", help="Disable all Conan remotes and rely on local cache only.")
    parser.add_argument("--disable-example-internal", action="store_true", help="Disable placeholder remotes ending in example.internal.")
    parser.add_argument("--reset-home", action="store_true", help="Delete the target Conan home before bootstrapping.")
    parser.add_argument("--skip-profile-detect", action="store_true")
    args = parser.parse_args()

    conan_home = args.conan_home if args.conan_home.is_absolute() else ROOT / args.conan_home
    remotes_file = args.remotes_file if args.remotes_file.is_absolute() else ROOT / args.remotes_file
    local_remotes_file = args.local_remotes_file if args.local_remotes_file.is_absolute() else ROOT / args.local_remotes_file
    conan_home.mkdir(parents=True, exist_ok=True)

    if args.reset_home and conan_home.exists():
        shutil.rmtree(conan_home)
        conan_home.mkdir(parents=True, exist_ok=True)

    env = conan_env(conan_home)

    if not args.skip_profile_detect:
        run(["conan", "profile", "detect", "--force"], env)

    remotes = load_remotes(remotes_file, local_remotes_file, env, allow_public=args.allow_public, no_remote=args.no_remote, disable_example_internal=args.disable_example_internal)
    configure_remotes(conan_home, remotes)

    print(f"conan bootstrap complete: CONAN_HOME={conan_home}")
    print(f"remotes file: {remotes_file}")
    print(f"local remotes file: {local_remotes_file}")
    print(f"no remote mode: {args.no_remote}")
    print(f"public remotes enabled: {args.allow_public}")
    print(f"example internal remotes disabled: {args.disable_example_internal}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
