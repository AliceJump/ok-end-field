"""Reviewed current-client Si release projection, separate from the old research export."""

import hashlib
import json
import math


def si_release_actor(root):
    contract_path = root / "assets/data/release_burst/si-release-contract.json"
    contract_raw = contract_path.read_bytes()
    contract = json.loads(contract_raw)
    if (contract["schema_version"] != 1 or contract["skill"] != "chr_0036_jsspsi_ultimate_skill"
            or contract["release_frame"] != 62 or contract["fps"] != 30
            or len(contract["recipient_paths"]) != 6
            or contract["buff_policy"] != {
                "id": "buff_chr_0036_jsspsi_atk_buff_2", "life_type": 0, "stacking_type": 2,
                "max_stacks": 1, "event": 5, "subtypes": [4, 5], "child_cleanup": True,
                "auto_finish": False, "recipient": "team", "ends_on_source_action": False}):
        raise ValueError("Si release contract changed; review before exporting")
    for name, digest in contract["source_hashes"].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Stale Si release source: {name}")
    canonical = json.loads((root / "assets/data/character_skills/si.json").read_bytes())
    baseline = next(r for r in json.loads((root / "assets/data/damage_baseline.json").read_bytes()) if r["key"] == "si")
    skill = next(s for s in canonical["skills"] if s["skill_id"] == "si_ultimate")
    selected = next(p for p in contract["parameters"] if p["level"] == 12)
    rate, duration = selected["enhance_final"], selected["enhance_duration"]
    if (not math.isclose(rate, .36, abs_tol=1e-7) or duration != 12
            or {e["effect_id"] for e in skill["effects"]} != {"BUFF_COLD_UP", "BUFF_BURN_UP"}
            or any(e["value"] != 36 or e["target"] != "ally" for e in skill["effects"])):
        raise ValueError("Si canonical and native amplification disagree")
    rules = []
    for effect, element in (("BUFF_COLD_UP", "寒冷"), ("BUFF_BURN_UP", "灼热")):
        rules.append({
            "key": f"si_ultimate:{effect}", "bucket": "amplification", "elements": [element],
            "recipient": "team", "magnitude": {"base": rate, "terms": [], "by_count": [],
                                                 "count_input": None, "accumulation": "linear"},
            "trigger": "ultimate_cast", "damage_tags": [], "permanent": False, "condition_inputs": [],
            "duration": duration, "duration_input": None, "evaluation": "application", "max_stacks": 1,
            "stack_policy": "replace", "unresolved": None,
            "sources": ["si-release-contract.json", f"SkillPatchTable/{contract['skill']}/level12",
                        f"native:{contract['skill']}:child_frame:62"],
            "lifetime_scope": "timed", "kind": "ult", "ends_on_source_action": False,
            "starts_after": 62 / 30, "blocks_source_actions": [],
        })
    quotes = {}
    for row in baseline["skills"]:
        kind = {"普通攻击": "normal", "战技": "battle", "终结技": "ult"}.get(row["type"])
        if kind is None:
            continue
        quotes[kind] = {key: row[key] for key in ("non_crit", "crit_expect", "bonus_pct")}
        quotes[kind]["quote_basis"] = {
            "skill_rank": 12, "element": "寒冷", "damage_tags": [{"normal": "normal", "battle": "skill", "ult": "ultimate"}[kind]],
            "multiplier": row["multiplier_pct"] / 100, "crit_policy": "baseline_expectation",
            "scope": "master_fixed_build_without_conditional_rows",
        }
    # The entity's many hit windows straddle buff onset. Do not price their sum at cast completion.
    quotes["ult"].update(non_crit=0.0, crit_expect=0.0,
                         pricing_scope="support_opener_only; entity_multi_hit_damage_not_forecast")
    actor = {
        "character": "祀", "key": "si", "profile": {
            "character_level": 90, "skill_rank": 12, "potential": 0,
            "potential_basis": "user_six_star_p0", "all_attribute_nodes": False,
            "constant_passive_sources": [], "conditional_talent_state": "untriggered",
            "scope": "master_static_build_projection; native_progression_not_imported"},
        "build": baseline["build"], "panel": baseline["panel"]["damage_basis"],
        "attribute_basis": {"scope": "master_static_build_without_dynamic_four_stats_or_conversions"},
        "quotes": quotes, "rules": rules, "stance": None,
    }
    evidence = {"character": "祀", "skill": contract["skill"], "release_frame": 62, "fps": 30,
                "duration": duration, "amplification": rate, "profile": actor["profile"],
                "input_policy": "rank12_constant_no_dynamic_attribute_input", "native_record_sha256": contract["native_record_sha256"],
                "native_inputs": contract["native_inputs"], "contract_sha256": hashlib.sha256(contract_raw).hexdigest(),
                "source_hashes": contract["source_hashes"]}
    return actor, evidence
