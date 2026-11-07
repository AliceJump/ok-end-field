"""Read a reproducible locator baseline from a validated local Git commit."""

import argparse
import re
import subprocess


def baseline_commit(value):
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", value):
        raise argparse.ArgumentTypeError("baseline must be a 7-40 digit hexadecimal Git commit ID")
    return value


def read_baseline_source(revision, root):
    revision = baseline_commit(revision)
    commit = (
        subprocess.check_output(
            ["git", "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"], cwd=root
        )
        .decode("ascii")
        .strip()
    )
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Git did not return a full commit ID")
    return subprocess.check_output(
        ["git", "show", "--no-ext-diff", "--no-textconv", f"{commit}:src/localization/minimap_map_locator.py", "--"],
        cwd=root,
    ).decode("utf-8")
