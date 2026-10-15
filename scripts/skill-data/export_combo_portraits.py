"""Package reviewed round portraits for fixed-slot combo recognition.

The source is an already verified local capture, not a runtime dependency.
Raw bundles, extraction scripts and screenshots stay outside this export.
"""

import argparse
import hashlib
import json
import re
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]


def export(source: Path, destination: Path):
    # Import only the product's character/profile mapping, never research code.
    import sys

    sys.path.insert(0, str(ROOT))
    from src.data.skill_timing import load_skill_timings

    characters_raw = (ROOT / "assets/data/characters.json").read_bytes()
    characters = json.loads(characters_raw)
    bindings_raw = (source / "bindings.json").read_bytes()
    bindings = {row["path"]: row for row in json.loads(bindings_raw)}
    owners = {}
    store = load_skill_timings()
    for key, row in characters.items():
        profiles = store.profiles(row["zh"], "ult")
        if not profiles:
            raise ValueError(f"No native character mapping: {key}")
        for profile in profiles:
            native_id = profile.skill_id.split("_ultimate_skill", 1)[0]
            if re.fullmatch(r"chr_\d{4}_[a-z0-9]+", native_id) is None:
                raise ValueError(f"Unreviewed native id: {native_id}")
            owners[native_id] = (key, row["zh"])
    paths = sorted((source / "media/Texture2D").glob("icon_round_chr_*.png"))
    if len(paths) != 35:
        raise ValueError("Review the portrait capture before changing the 35-image boundary")
    atlas = Image.new("RGBA", (154 * 5, 154 * 7))
    records = []
    for index, path in enumerate(paths):
        native_id = path.stem.rsplit("_p", 1)[0].removeprefix("icon_round_")
        asset_path = f"Assets/Beyond/DynamicAssets/Gameplay/UI/Sprites/CharRoundIcon/icon_round_{native_id}.png"
        binding = bindings[asset_path]
        with Image.open(path) as image:
            if image.size != (156, 156):
                raise ValueError(f"Unreviewed portrait dimensions: {native_id}")
            tile = image.convert("RGBA").crop((1, 1, 155, 155))
        x, y = index % 5 * 154, index // 5 * 154
        atlas.paste(tile, (x, y))
        key, name = owners.get(native_id, (None, None))
        records.append({
            "native_id": native_id, "character": name,
            "aliases": [key, name, native_id] if name else [native_id],
            "tile": [x, y, 154, 154], "asset_path": asset_path,
            "source_png_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "source_bundle_sha256": binding["sha256"], "vfs_path_hash": binding["hash"],
        })
    if not set(owners) <= {r["native_id"] for r in records}:
        raise ValueError("Current character portraits are missing")
    destination.mkdir(parents=True, exist_ok=True)
    atlas_path = destination / "portraits.png"
    atlas.save(atlas_path)
    manifest = {
        "schema_version": 1, "atlas": "portraits.png", "tile_size": 154,
        "atlas_sha256": hashlib.sha256(atlas_path.read_bytes()).hexdigest(),
        "capture": "20261015-current-client-vfs5", "character_count": len(characters),
        "characters_sha256": hashlib.sha256(characters_raw).hexdigest(),
        "bindings_sha256": hashlib.sha256(bindings_raw).hexdigest(), "portraits": records,
        "scope": "round portrait identity only; not castability, timer, hit or release confirmation",
    }
    (destination / "index.json").write_bytes((json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode())
    print(f"Exported {len(records)} portraits, mapped to {len(characters)} product characters")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--out", type=Path, default=ROOT / "assets/data/combo_portraits")
    args = parser.parse_args()
    export(args.source, args.out)
