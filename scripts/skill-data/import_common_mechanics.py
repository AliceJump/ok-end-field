"""Import an externally decoded, byte-verified common gameplay snapshot."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import zipfile
from pathlib import Path

from import_native_progression import verify_supplement

ROOT = Path(__file__).resolve().parents[2]
TIMINGS = ROOT / "assets/data/skill_timings/20261002"


def import_assets(source: Path):
    """Check the externally verified Unity export against its object/bundle proofs.

    Unity type-tree serialization is performed by the external export runtime.
    Both original and re-encoded object bytes must accompany its certificate;
    the imported JSON tree is bound to that exact certificate by its own hash.
    """
    records = json.loads((source / "native_assets.json").read_text(encoding="utf-8-sig"))
    proof = json.loads((source / "asset_verification.json").read_text(encoding="utf-8-sig"))
    bindings = json.loads((source / "combat_bundle_bindings.json").read_text(encoding="utf-8-sig"))
    checks = {p["name"]: p for p in proof}
    if len(checks) != len(proof) or checks.keys() != records.keys():
        raise ValueError("Duplicate/missing asset verification")
    by_bundle = {Path(b["bundle_name"]).name: b for b in bindings}
    selected, sources = {}, {}
    with zipfile.ZipFile(source / "asset_objects.zip") as archive:
        for name, tree in records.items():
            if name not in {"SkillSetting", "GameplayTagConfig", "DamageScaleProcessorConfig"} and not name.startswith((
                "data_projectile_chr_", "data_abilityentity_chr_", "data_chr_", "data_tag_",
            )):
                continue
            check = checks[name]
            original = archive.read(name + ".original")
            encoded = archive.read(name + ".encoded")
            tree_raw = json.dumps(tree, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            binding = by_bundle[check["bundle"]]
            bundle = (source / "bundles" / check["bundle"]).read_bytes()
            if not check["roundtrip_equal"] or original != encoded:
                raise ValueError(f"Asset object roundtrip mismatch: {name}")
            for raw, digest, label in ((original, check["raw_sha256"], "object"),
                                       (tree_raw, check["tree_sha256"], "tree"),
                                       (bundle, binding["bundle_sha256"], "bundle")):
                if hashlib.sha256(raw).hexdigest() != digest:
                    raise ValueError(f"Asset {label} hash mismatch: {name}")
            if tree["m_Name"] != name:
                raise ValueError(f"Asset identity mismatch: {name}")
            selected[name] = tree
            sources[name] = {
                "asset_path": binding["path"], "bundle_sha256": binding["bundle_sha256"],
                "object_sha256": check["raw_sha256"], "tree_sha256": check["tree_sha256"],
                "object_id": check["object"], "byte_identical": True,
            }
    return selected, sources


def build(source: Path, out: Path, *, assets_source: Path | None = None):
    records = json.loads((source / "records.json").read_text(encoding="utf-8-sig"))
    proof = json.loads((source / "verification.json").read_text(encoding="utf-8-sig"))
    if records.keys() != proof.keys() or not all(p["byte_identical"] for p in proof.values()):
        raise ValueError("Unverified common mechanics export")
    plans = json.loads(gzip.decompress((TIMINGS / "native_read_plans.json.gz").read_bytes()))
    verify_supplement(records, proof, source / "raw.zip", plans)
    enums = json.loads((source / "enums.json").read_text(encoding="utf-8-sig"))
    timing_index = json.loads((TIMINGS / "index.json").read_text(encoding="utf-8"))
    if enums["metadata_sha256"] != timing_index["native_inputs"]["global-metadata.dat"]:
        raise ValueError("Enum metadata does not match the gameplay snapshot")
    assets, asset_sources = import_assets(assets_source) if assets_source is not None else (None, None)
    # Validate everything before replacing any existing snapshot output.
    out.mkdir(parents=True, exist_ok=True)
    previous = json.loads((out / "index.json").read_text(encoding="utf-8")) if (out / "index.json").exists() else {}
    raw = gzip.compress(json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(), mtime=0)
    (out / "records.json.gz").write_bytes(raw)
    enum_raw = (json.dumps(enums, sort_keys=True, indent=2) + "\n").encode()
    (out / "enums.json").write_bytes(enum_raw)
    manifest = {
        "schema_version": 1,
        "records": len(records),
        "records_sha256": hashlib.sha256(raw).hexdigest(),
        "enums_sha256": hashlib.sha256(enum_raw).hexdigest(),
        "native_plans_sha256": hashlib.sha256((TIMINGS / "native_read_plans.json.gz").read_bytes()).hexdigest(),
        "verification": {"byte_identical": len(proof), "failures": []},
        "sources": proof,
        "scope": "Common gameplay BuffData and their referenced SkillData/BuffData; native defaults are not runtime overrides",
    }
    if assets is not None:
        asset_raw = gzip.compress(json.dumps(assets, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(), mtime=0)
        (out / "assets.json.gz").write_bytes(asset_raw)
        manifest.update(assets_count=len(assets), assets_sha256=hashlib.sha256(asset_raw).hexdigest(), asset_sources=asset_sources)
    else:
        # Re-importing BuffData must not erase an independently verified asset
        # snapshot or its provenance from the same manifest.
        for key in ("assets_count", "assets_sha256", "asset_sources"):
            if key in previous:
                manifest[key] = previous[key]
    if "formula_evidence" in previous:
        manifest["formula_evidence"] = previous["formula_evidence"]
    (out / "index.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--out", type=Path, default=ROOT / "assets/data/common_mechanics/20261003")
    parser.add_argument("--assets-source", type=Path, help="Externally verified Unity asset export with object bytes and bundle proofs")
    args = parser.parse_args()
    result = build(args.source, args.out, assets_source=args.assets_source)
    print(f"Imported {result['records']} byte-identical common mechanics records")
