"""Import decoded native growth/effect tables; never infer effects from prose.

Requires the verified local research export, including the progression BuffData
supplement. Output is deterministic; the full client text table stays local.
"""

from __future__ import annotations

import argparse
import ast
import gzip
import hashlib
import json
import re
import struct
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "assets/data/character_progression/20261003"
TIMINGS = ROOT / "assets/data/skill_timings/20261002"
PLACEHOLDER = re.compile(r"\{([^{}:]+)(?::([^{}]+))?\}")
MARKUP = re.compile(r"<[^>]*>")
RESOURCE_ACTIONS = {
    "ObtainCostAction+Data",
    "GainCostAction+Data",
    "CostAction+Data",
    "GainBreakingAttackAtb+Data",
    "ObtainUspInNormalSkill+Data",
    "RefrainObtainUsp+Data",
    "SaveAtbObtainValue+Data",
}


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from strings(child)


def render(template: str | None, parameters: dict) -> str | None:
    if template is None:
        return None

    def replace(match):
        def arithmetic(node):
            if isinstance(node, ast.Constant) and type(node.value) in (int, float):
                return node.value
            if isinstance(node, ast.Name):
                return parameters[node.id]
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
                return -arithmetic(node.operand)
            if isinstance(node, ast.BinOp):
                left, right = arithmetic(node.left), arithmetic(node.right)
                if isinstance(node.op, ast.Add):
                    return left + right
                if isinstance(node.op, ast.Sub):
                    return left - right
            raise ValueError("Unsupported display expression")

        try:
            value = arithmetic(ast.parse(match[1], mode="eval").body)
        except (SyntaxError, KeyError, ValueError, TypeError):
            return match[0]
        if not isinstance(value, (int, float)):
            return match[0]  # Positional/ambiguous values remain explicitly unresolved.
        fmt = match[2] or "0.##"
        digits = len(fmt.split(".", 1)[1].rstrip("%")) if "." in fmt else 0
        if fmt.endswith("%"):
            return f"{value * 100:.{digits}f}%"
        result = f"{value:.{digits}f}"
        return result.rstrip("0").rstrip(".") if match[2] is None else result

    return MARKUP.sub("", PLACEHOLDER.sub(replace, template))


def resource_evidence(value, path="", ancestors=()):
    """Paths point into full trees; surrounding conditions are not discarded.

    An entry is an audit candidate, never an executable guaranteed payout.
    Branch pointers include Switch/IfElse/sequence/event/timeline containers.
    """
    if isinstance(value, dict):
        parents = ancestors
        if "$type" in value:
            name = value["$type"].rsplit(".", 1)[-1]
            if name in RESOURCE_ACTIONS:
                yield {
                    "path": path,
                    "action": value,
                    "ancestor_paths": list(ancestors),
                    "execution_status": "requires_action_interpreter",
                }
            parents = (*ancestors, path)
        elif any(k in value for k in ("eventType", "_startFrame", "onlyExecuteWhenSourceIsMainChar")):
            parents = (*ancestors, path)
        for key, child in value.items():
            yield from resource_evidence(child, path + "/" + key, parents)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from resource_evidence(child, path + "/" + str(index), ancestors)


def write_json(path: Path, value) -> bytes:
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    path.write_bytes(raw)
    return raw


def write_gzip(path: Path, value) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    compressed = gzip.compress(raw, mtime=0)
    path.write_bytes(compressed)
    return hashlib.sha256(compressed).hexdigest()


def compact_modifier(modifier: dict) -> dict:
    """Omit inactive union arms; the complete native row remains in tables.gz."""
    row = {"modifyType": modifier["modifyType"], "activeCondition": modifier["activeCondition"]}
    for arm, key in (
        ("attachSkill", "skillId"),
        ("attachBuff", "buffId"),
        ("skillBbModifier", "bbKey"),
        ("skillParamModifier", "skillId"),
    ):
        if modifier[arm][key]:
            row[arm] = modifier[arm]
    if any(modifier["attrModifier"].values()):
        row["attrModifier"] = modifier["attrModifier"]
    return row


def verify_supplement(records: dict, proof: dict, archive: Path, plans: dict) -> None:
    """Re-encode native-derived plans and compare the complete decrypted bytes."""
    formats = {
        "bool": "?",
        "scalar8": "B",
        "scalar16": "H",
        "scalar32": "i",
        "scalar64": "q",
        "float32": "f",
        "float64": "d",
    }

    def record(definition, value):
        if value is None:
            return b"\xff"
        members = plans["plans"][str(definition)]
        return bytes([len(members)]) + b"".join(member(p, value[p["name"]]) for p in members)

    def member(plan, value):
        kind = plan["kind"]
        if kind == "fixed":
            return (
                bytes.fromhex(value["raw"])
                if isinstance(value, dict)
                else struct.pack("<" + formats[plan["scalar"]], value)
            )
        if kind == "string":
            if value is None:
                return struct.pack("<i", -1)
            raw = bytes.fromhex(value["bytes"]) if isinstance(value, dict) else value.encode("utf-8")
            return struct.pack("<i", len(raw)) + raw
        if kind == "object":
            return record(plan["ref"], value)
        if kind == "nullOnly":
            if value is not None:
                raise ValueError("Non-null value for null-only plan")
            return b"\xff"
        if kind == "list":
            if value is None:
                return struct.pack("<i", -1)
            return struct.pack("<i", len(value)) + b"".join(member(plan["element"], child) for child in value)
        if kind == "union":
            if value is None:
                return b"\xff"
            tag = value["$tag"]
            definition = plans["unionTagMaps"][str(plan["ref"])][str(tag)]
            if value["$type"] != plans["wrappedNames"][str(definition)]:
                raise ValueError("Native union tag/type mismatch")
            prefix = bytes([tag]) if tag < 250 else b"\xfa" + struct.pack("<H", tag)
            return prefix + record(definition, value["$value"])
        if kind == "profile":
            return bytes.fromhex(value["rawHex"])
        if kind == "map":
            if value is None:
                return b"\xff" if plan.get("header") else struct.pack("<i", -1)
            prefix = (b"\x01" if plan.get("header") else b"") + struct.pack("<i", len(value))
            if plan.get("pairSize"):
                return prefix + b"".join(bytes.fromhex(v["key"]) + bytes.fromhex(v["value"]) for v in value)
            return prefix + b"".join(member(plan["key"], v["key"]) + member(plan["value"], v["value"]) for v in value)
        raise ValueError(f"Unsupported native plan kind: {kind}")

    with zipfile.ZipFile(archive) as source:
        expected = {f"{r['kind']}/{name}.json" for name, r in records.items()}
        if set(source.namelist()) != expected:
            raise ValueError("Supplement archive coverage mismatch")
        for name, row in records.items():
            raw = source.read(f"{row['kind']}/{name}.json")
            if hashlib.sha256(raw).hexdigest() != proof[name]["sha256"]:
                raise ValueError(f"Supplement source hash mismatch: {name}")
            definition = plans["namedRoots"]["Beyond.Gameplay.Core." + row["kind"]]
            if record(definition, row["data"]) != raw:
                raise ValueError(f"Native byte roundtrip failed: {name}")


def build(research: Path, texts: Path, out: Path):
    table_root = research / "mechanics-tables"
    names = (
        "CharGrowthTable",
        "CharacterPotentialTable",
        "PotentialTalentEffectTable",
        "CharWpnRecommendTable",
        "WeaponBasicTable",
        "ItemTable",
        "RewardTable",
    )
    tables = {name: read(table_root / (name + ".json")) for name in names}
    cn = read(texts)

    def text(obj):
        value = cn.get(str(obj["id"]), cn.get(f"{obj['id'] & ((1 << 64) - 1):016x}", obj.get("text")))
        return value or None

    index = read(TIMINGS / "index.json")
    records = json.loads(gzip.decompress((TIMINGS / "records.json.gz").read_bytes()))
    supplement = read(research / "progression-supplement/records.json")
    proof = read(research / "progression-supplement/verification.json")
    if set(supplement) != set(proof) or not all(p["byte_identical"] for p in proof.values()):
        raise ValueError("Unverified progression supplement")
    raw_archive = research / "progression-supplement/raw.zip"
    plans = json.loads(gzip.decompress((TIMINGS / "native_read_plans.json.gz").read_bytes()))
    verify_supplement(supplement, proof, raw_archive, plans)
    records.update(supplement)
    effects = tables["PotentialTalentEffectTable"]
    semantic_rules = read(ROOT / "assets/data/character_progression/semantic_rules.json")
    modifier_rules = read(ROOT / "assets/data/character_progression/resource_modifier_rules.json")
    characters = {}

    def passive(effect_id, name, level, source, slot=None):
        effect = effects[effect_id]
        candidates: dict[str, set] = {}
        defaults: dict[str, set] = {}
        for modifier in effect["dataList"]:
            for owner, field in (("attachSkill", "skillId"), ("attachBuff", "buffId")):
                attached = modifier[owner]
                if attached[field] and attached[field] not in records:
                    raise ValueError(f"Missing native producer: {attached[field]}")
                if attached[field]:
                    for bb in records[attached[field]]["data"].get("blackboard") or []:
                        value = bb.get("valueStr") or bb.get("valueDouble", bb.get("value"))
                        if value is not None:
                            defaults.setdefault(bb["key"], set()).add(value)
                for bb in attached["blackboard"]:
                    candidates.setdefault(bb["key"], set()).add(bb["valueStr"] or bb["value"])
            bb = modifier["skillBbModifier"]
            if bb["bbKey"]:
                candidates.setdefault(bb["bbKey"], set()).add(bb["stringValue"] or bb["floatValue"])
        parameters = {key: next(iter(values)) for key, values in candidates.items() if len(values) == 1}
        for key, values in defaults.items():
            if key not in candidates and len(values) == 1:
                parameters[key] = next(iter(values))
        template = text(effect["desc"])
        resource_modifiers = list(modifier_rules.get(effect_id, []))
        cost_factors = {}
        if template and "所需的终结技能量" in template:
            for modifier in effect["dataList"]:
                cost = modifier["skillParamModifier"]
                if cost["skillId"] and cost["paramType"] == 1 and cost["modifyType"] == 2:
                    if modifier["activeCondition"]:
                        raise ValueError(f"Conditional cost modifier needs explicit mapping: {effect_id}")
                    cost_factors[cost["skillId"]] = cost["paramValue"]
            if not cost_factors or len(set(cost_factors.values())) != 1:
                raise ValueError(f"Ambiguous described ultimate cost: {effect_id}")
            factor = next(iter(cost_factors.values()))
            parameters.update(costvalue=factor, costValue=factor)
            resource_modifiers.append(
                {
                    "resource": "ultimate_energy",
                    "target": "self",
                    "scope": "cost",
                    "operation": "multiply",
                    "value": factor,
                    "skill_ids": sorted(cost_factors),
                    "trigger": "对应终结技施放时修改原能量消耗；不产生能量回复",
                }
            )
        for rule in modifier_rules.get(effect_id, []):
            if rule["value"] is None:
                if not rule.get("formula") or any(k not in parameters for k in rule["native_bb_keys"]):
                    raise ValueError(f"Unbound described resource formula: {effect_id}")
                continue
            observed = {
                m["skillBbModifier"]["floatValue"]
                for m in effect["dataList"]
                if m["skillBbModifier"]["bbKey"] in rule["native_bb_keys"]
                and m["skillBbModifier"]["skillId"] in rule["skill_ids"]
            }
            if not observed or any(abs(v - rule["value"]) > 1e-6 for v in observed):
                raise ValueError(f"Description/resource multiplier disagrees with BB: {effect_id}")
        return {
            "effect_id": effect_id,
            "name": text(name),
            "level": level,
            "slot": slot,
            "description_template": template,
            "description": render(template, parameters),
            "parameters": parameters,
            "modifiers": [compact_modifier(m) for m in effect["dataList"]],
            "source": source,
            "resource_changes": semantic_rules.get(effect_id, []),
            "resource_modifiers": resource_modifiers,
        }

    for path in sorted((ROOT / "assets/data/character_skills").glob("*.json")):
        canonical = read(path)
        key = canonical["character_id"]
        native = next(cid for cid, c in index["characters"].items() if c["key"] == key)
        if key == "endministrator":
            native = "chr_9000_endmin"  # Sex-specific growth rows still contain obsolete talents.
        growth = tables["CharGrowthTable"][native]
        ranks, attributes = [], []
        for node_id, node in sorted(growth["talentNodeMap"].items()):
            source = f"CharGrowthTable/{native}/talentNodeMap/{node_id}"
            if node["nodeType"] == 4:
                talent = node["passiveSkillNodeInfo"]
                ranks.append(
                    passive(talent["talentEffectId"], talent["name"], talent["level"], source, talent["index"])
                )
            elif node["nodeType"] == 3:
                attributes.append({"node_id": node_id, "source": source, **node["attributeNodeInfo"]})
        potentials = [
            passive(
                p["potentialEffectId"],
                p["name"],
                p["level"],
                f"CharacterPotentialTable/{native}/potentialUnlockBundle/{i}",
            )
            for i, p in enumerate(tables["CharacterPotentialTable"][native]["potentialUnlockBundle"])
        ]
        baseline = {
            "potential": None if key == "endministrator" else 0 if canonical["star"] == 6 else 5,
            "talent_policy": "all_combat_talents_max_rank",
            "potential_basis": "unverified_unlocks"
            if key == "endministrator"
            else "user_six_star_p0"
            if canonical["star"] == 6
            else "user_four_five_star_p5",
        }
        if key == "ember":
            recommended = tables["CharWpnRecommendTable"][native]["weaponIds1"]
            weapon = next(w for w in recommended if tables["WeaponBasicTable"][w]["rarity"] == 5)
            baseline.update(
                character_level=80,
                skill_rank=9,
                weapon_id=weapon,
                weapon_name=text(tables["ItemTable"][weapon]["name"]),
            )
        characters[key] = {
            "native_id": native,
            "name": canonical["name"],
            "baseline": baseline,
            "talent_ranks": ranks,
            "potentials": potentials,
            "attribute_nodes": attributes,
        }

    audit = {name: list(resource_evidence(record["data"])) for name, record in sorted(records.items())}
    audit = {name: rows for name, rows in audit.items() if rows}
    out.mkdir(parents=True, exist_ok=True)
    (out / "raw.zip").write_bytes(raw_archive.read_bytes())
    used_native = {c["native_id"] for c in characters.values()}
    used_effects = {p["effect_id"] for c in characters.values() for p in c["talent_ranks"] + c["potentials"]}
    used_weapons = {
        w
        for cid in used_native
        for values in tables["CharWpnRecommendTable"][cid].values()
        if isinstance(values, list)
        for w in values
    }
    selected_tables = {
        name: {k: v for k, v in rows.items() if k in used_native}
        for name, rows in tables.items()
        if name in ("CharGrowthTable", "CharacterPotentialTable", "CharWpnRecommendTable")
    }
    selected_tables["PotentialTalentEffectTable"] = {k: effects[k] for k in sorted(used_effects)}
    selected_tables["WeaponBasicTable"] = {k: tables["WeaponBasicTable"][k] for k in sorted(used_weapons)}
    selected_tables["ItemTable"] = {
        k: v for k, v in tables["ItemTable"].items() if k in used_weapons or k == "item_charpotentialup_chr_9000_endmin"
    }
    selected_tables["RewardTable"] = {
        k: v
        for k, v in tables["RewardTable"].items()
        if "item_charpotentialup_chr_9000_endmin" in set(strings(v)) or k == "reward_mission_e2m8_new"
    }
    text_ids = set()

    def collect_text_ids(value):
        if isinstance(value, dict):
            if isinstance(value.get("id"), int) and "text" in value:
                text_ids.add(value["id"])
            for child in value.values():
                collect_text_ids(child)
        elif isinstance(value, list):
            for child in value:
                collect_text_ids(child)

    collect_text_ids(selected_tables)
    selected_tables["I18nTextTable_CN"] = {str(i): text({"id": i, "text": ""}) for i in sorted(text_ids)}
    # Review visible resource clauses, rather than treating every native ATB action
    # as a new unconditional payout. Lower talent ranks are superseded by baseline.
    observers = {"chr_0019_karin_potential_1", "chr_0029_pograni_talent_1_2", "chr_0029_pograni_potential_3"}
    description_review = {}
    for key, character in characters.items():
        highest = {}
        for talent in character["talent_ranks"]:
            if talent["slot"] not in highest or talent["level"] > highest[talent["slot"]]["level"]:
                highest[talent["slot"]] = talent
        for passive_row in [*highest.values(), *character["potentials"]]:
            desc = passive_row["description"] or ""
            if not re.search(r"技力|终结.*能量|能量.*终结", desc):
                continue
            status = (
                "mapped_recovery"
                if passive_row["resource_changes"]
                else "mapped_modifier"
                if passive_row["resource_modifiers"]
                else "resource_observer"
                if passive_row["effect_id"] in observers
                else "unresolved"
            )
            description_review[passive_row["effect_id"]] = {
                "character": key,
                "description": desc,
                "source": passive_row["source"],
                "status": status,
                "resource_changes_count": len(passive_row["resource_changes"]),
                "resource_modifiers_count": len(passive_row["resource_modifiers"]),
            }
    review_raw = write_json(out / "description_resource_review.json", description_review)
    char_raw = write_json(out / "characters.json", characters)
    manifest = {
        "schema_version": 1,
        "snapshot_date": "2026-10-03",
        "native_inputs": index["native_inputs"],
        "timing_snapshot": "../../skill_timings/20261002",
        "characters_sha256": hashlib.sha256(char_raw).hexdigest(),
        "description_resource_review_sha256": hashlib.sha256(review_raw).hexdigest(),
        "resource_modifier_rules_sha256": hashlib.sha256(
            (ROOT / "assets/data/character_progression/resource_modifier_rules.json").read_bytes()
        ).hexdigest(),
        "input_sha256": {
            name: hashlib.sha256((table_root / (name + ".json")).read_bytes()).hexdigest() for name in names
        },
        "texts_sha256": hashlib.sha256(texts.read_bytes()).hexdigest(),
        "semantic_rules_sha256": hashlib.sha256(
            (ROOT / "assets/data/character_progression/semantic_rules.json").read_bytes()
        ).hexdigest(),
        "supplement_verification": proof,
        "raw_sha256": hashlib.sha256(raw_archive.read_bytes()).hexdigest(),
        "supplement_sha256": write_gzip(out / "supplement.json.gz", supplement),
        "resource_audit_sha256": write_gzip(out / "resource_actions.json.gz", audit),
        "tables_sha256": write_gzip(out / "tables.json.gz", selected_tables),
        "counts": {
            "characters": len(characters),
            "talent_ranks": sum(len(c["talent_ranks"]) for c in characters.values()),
            "potentials": sum(len(c["potentials"]) for c in characters.values()),
            "supplement_records": len(supplement),
            "resource_records": len(audit),
            "resource_action_occurrences": sum(map(len, audit.values())),
            "description_resource_clauses": len(description_review),
            "description_unresolved": sum(r["status"] == "unresolved" for r in description_review.values()),
        },
    }
    sources = research / "progression-supplement/sources.json"
    if sources.is_file():
        manifest["vfs_sources"] = read(sources)
    reward_scan = table_root / "missions/endmin_reward_scan.json"
    if reward_scan.is_file():
        scan_raw = write_json(out / "endmin_reward_scan.json", read(reward_scan))
        manifest["endmin_reward_scan_sha256"] = hashlib.sha256(scan_raw).hexdigest()
    write_json(out / "index.json", manifest)
    print(json.dumps(manifest["counts"], ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-dir", required=True, type=Path)
    parser.add_argument("--texts", required=True, type=Path)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    build(args.research_dir, args.texts, args.out)


if __name__ == "__main__":
    main()
