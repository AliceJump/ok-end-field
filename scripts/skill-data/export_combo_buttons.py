"""Package native combo-button ownership from a verified local capture.

Only normalized bindings enter the product. Raw resources remain in the main
checkout's ignored tmp directory, alongside the decoder and round-trip proof.
"""

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def contained(directory, relative):
    path = (directory / relative).resolve()
    if not path.is_relative_to(directory):
        raise ValueError("Combo source path escapes capture")
    return path


def export(source, destination):
    common = Path(subprocess.check_output(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=ROOT, text=True).strip())
    scratch = common.parent.resolve() / "tmp"
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_relative_to(scratch):
        raise ValueError("Combo capture must stay in the main checkout's ignored tmp directory")
    allowed = (ROOT / "assets/data/combo_buttons", ROOT / "tmp/combo-buttons-reexport")
    if not any(destination == p.resolve() and destination.is_relative_to(ROOT) for p in allowed):
        raise ValueError("Output must be the combo catalog or its named verification directory")
    lock_raw = contained(source, "source-lock.json").read_bytes()
    lock = json.loads(lock_raw)
    if set(lock["native_inputs"]) != {"GameAssembly.dll", "global-metadata.dat"}:
        raise ValueError("Combo capture requires a complete native pair")
    for name, digest in lock["native_inputs"].items():
        if name not in {"GameAssembly.dll", "global-metadata.dat"}:
            raise ValueError("Unexpected native source")
        path = (source.parent / name).resolve()
        if not path.is_relative_to(scratch) or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("Native combo capture changed")
    proof_raw = contained(source, "pooled-roundtrip-verification.json").read_bytes()
    proof = json.loads(proof_raw)
    if proof["failures"]:
        raise ValueError("Unverified native combo records")
    verified = {r["id"]: r for r in proof["successes"] if r["kind"] == "SkillData"}
    portraits_raw = (ROOT / "assets/data/combo_portraits/index.json").read_bytes()
    owners = {r["native_id"]: r["character"] for r in json.loads(portraits_raw)["portraits"] if r["character"]}
    rows = []
    for path in sorted(contained(source, "decoded-pooled/SkillData").glob("chr_*.json")):
        path = contained(source, path.relative_to(source))
        data = json.loads(path.read_bytes())
        sprite = data.get("comboSkillUISpriteName")
        if data.get("skillSpecification") != 4 or not sprite:
            continue
        owner = "_".join(path.stem.split("_")[:3])
        if owner not in owners:
            continue
        portrait = sprite.removeprefix("icon_round_")
        if not sprite.startswith("icon_round_") or re.fullmatch(r"chr_\d{4}_[a-z0-9]+", portrait) is None:
            raise ValueError("Unreviewed combo portrait name")
        raw = contained(source, f"extracted/775A31D1/Json/SkillData/{path.name}").read_bytes()
        item = verified[path.stem]
        if (len(raw) != item["bytes"] or hashlib.sha256(raw).hexdigest() != item["sha256"]
                or data["skillId"] != path.stem):
            raise ValueError("Combo record disagrees with round-trip proof")
        rows.append({"skill_id": path.stem, "owner_native_id": owner,
                     "character": owners[owner], "portrait_native_id": portrait,
                     "source_sha256": item["sha256"],
                     "decoded_sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    if len(rows) != 43:
        raise ValueError("Review the native button set before changing its 43-record boundary")
    payload = (json.dumps(rows, ensure_ascii=False, indent=2) + "\n").encode("utf8")
    manifest = {"schema_version": 1, "native_inputs": lock["native_inputs"], "buttons": len(rows),
                "buttons_sha256": hashlib.sha256(payload).hexdigest(),
                "source_lock_sha256": hashlib.sha256(lock_raw).hexdigest(),
                "roundtrip_proof_sha256": hashlib.sha256(proof_raw).hexdigest(),
                "portrait_index_sha256": hashlib.sha256(portraits_raw).hexdigest(),
                "exporter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "scope": "button_owner_and_portrait_only; no_variant_inference_or_cast_confirmation"}
    destination.mkdir(parents=True, exist_ok=True)
    contained(destination, "buttons.json").write_bytes(payload)
    contained(destination, "index.json").write_bytes((json.dumps(manifest, indent=2) + "\n").encode("utf8"))
    print(f"Exported {len(rows)} native combo button bindings")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--out", default=ROOT / "assets/data/combo_buttons", type=Path)
    args = parser.parse_args()
    export(args.source, args.out)
