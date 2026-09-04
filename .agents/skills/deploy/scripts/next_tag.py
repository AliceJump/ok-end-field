#!/usr/bin/env python3
"""Calculate the next stable, beta, or alpha version tag."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass

STABLE_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
PRERELEASE_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)-(alpha|beta)\.(\d+)$")


@dataclass(frozen=True, order=True)
class Version:
    major: int
    minor: int
    patch: int

    def next_patch(self) -> Version:
        return Version(self.major, self.minor, self.patch + 1)

    def tag(self) -> str:
        return f"v{self.major}.{self.minor}.{self.patch}"


@dataclass(frozen=True, order=True)
class Prerelease:
    version: Version
    number: int


def repository_tags() -> list[str]:
    result = run_git("tag", "--list", "v*")
    return result.stdout.splitlines()


def remote_tags(remote: str) -> list[str]:
    result = run_git("ls-remote", "--refs", "--tags", remote, "refs/tags/v*")
    return [ref.removeprefix("refs/tags/") for _, ref in (line.split("\t", 1) for line in result.stdout.splitlines())]


def run_git(*args: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown git error"
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
    return result


def ensure_repository() -> None:
    result = run_git("rev-parse", "--is-inside-work-tree")
    if result.stdout.strip() != "true":
        raise RuntimeError("Current directory is not inside a git worktree.")


def ensure_remote(remote: str) -> None:
    run_git("remote", "get-url", remote)


def parse_tags(tags: list[str]) -> tuple[list[Version], dict[str, list[Prerelease]]]:
    stable: list[Version] = []
    prereleases: dict[str, list[Prerelease]] = {"alpha": [], "beta": []}
    for tag in tags:
        if match := STABLE_RE.fullmatch(tag.strip()):
            stable.append(Version(*(int(value) for value in match.groups())))
            continue
        if match := PRERELEASE_RE.fullmatch(tag.strip()):
            major, minor, patch, channel, number = match.groups()
            prereleases[channel].append(Prerelease(Version(int(major), int(minor), int(patch)), int(number)))
    return stable, prereleases


def next_tag(channel: str, tags: list[str]) -> str:
    known_tags = {tag.strip() for tag in tags if tag.strip()}
    stable, prereleases = parse_tags(tags)
    if not stable:
        raise ValueError("Cannot calculate a version tag without an existing stable vMAJOR.MINOR.PATCH tag.")

    latest_stable = max(stable)
    if channel == "release":
        candidate = latest_stable.next_patch().tag()
        if candidate in known_tags:
            raise ValueError(f"Calculated tag already exists: {candidate}")
        return candidate

    channel_tags = prereleases[channel]
    if channel_tags:
        latest_prerelease = max(channel_tags)
        if latest_prerelease.version > latest_stable:
            candidate = f"{latest_prerelease.version.tag()}-{channel}.{latest_prerelease.number + 1}"
            if candidate in known_tags:
                raise ValueError(f"Calculated tag already exists: {candidate}")
            return candidate

    candidate = f"{latest_stable.next_patch().tag()}-{channel}.1"
    if candidate in known_tags:
        raise ValueError(f"Calculated tag already exists: {candidate}")
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("channel", choices=("release", "beta", "alpha"))
    parser.add_argument(
        "--tag",
        action="append",
        dest="tags",
        help="Use an explicit existing tag instead of reading tags from the current git repository. Repeat as needed.",
    )
    parser.add_argument(
        "--remote",
        help="Include tag names published on this git remote, for example origin.",
    )
    args = parser.parse_args()

    try:
        if args.tags is None:
            ensure_repository()
            tags = repository_tags()
        else:
            tags = list(args.tags)
        if args.remote:
            ensure_remote(args.remote)
            tags.extend(remote_tags(args.remote))
        print(next_tag(args.channel, sorted(set(tags))))
    except (RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
