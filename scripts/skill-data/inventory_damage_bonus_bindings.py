"""Inventory existing bonus contracts; never promote a formula to a producer."""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUDIT = "docs/dev/damage-data-flow-audit.json"
TARGET_PASSIVES = {
    "chr_0022_bounda_talent_1_2:damage_bonus", "chr_0004_pelica_talent_1_2:damage_bonus",
    "chr_0017_yvonne_talent_2_2:crit_damage:cold", "chr_0017_yvonne_talent_2_2:crit_damage:frozen",
}


def inventory(*, root=ROOT):
    raw = (root / AUDIT).read_bytes()
    audit = json.loads(raw)
    records, evidence, inactive = [], [], []
    totals = Counter()

    def add(character, unit, origin, rule, category):
        records.append({"character_key": character["key"], "character": character["character"],
                        "unit": unit, "origin": origin, "category": category, "rule": rule})

    def release_category(rule):
        if rule["unresolved"]:
            return "deferred_producer"
        if rule["trigger"] == "combo_cast":
            return "deferred_identified_combo"
        if rule["required_inputs"]:
            return "deferred_attribute_input"
        if rule["trigger"] in {"battle_cast", "ultimate_cast"}:
            return "first_release_candidate"
        return "deferred_producer"

    def evidence_record(character, field, candidate, pointer):
        # Keep a precise source pointer instead of duplicating raw action trees.
        keys = {"passive_id", "set", "activated_by_selected_build", "attribute_buff", "parent_buff",
                "weapon_id", "producer_skill", "selected_parameters", "selected_rank", "execution_status",
                "unexecuted_components", "source", "selected_piece_ids"}
        return {"character": character["character"], "kind": field, "source_pointer": pointer,
                "candidate": {key: value for key, value in candidate.items() if key in keys}}

    for char_index, character in enumerate(audit["characters"]):
        released = {rule["key"]: rule for skill in character["skills"]
                    for rule in skill["model_release_bonus_rules"]}
        for skill in character["skills"]:
            unit = skill["type"] + " / " + skill["name"]
            for rule in skill["damage_rules"]:
                totals["skill_damage_rules"] += 1
                reviewed = released.get(rule["key"])
                add(character, unit, "skill", reviewed or rule,
                    release_category(reviewed) if reviewed else "deferred_producer")
            for branch in skill["branches"]:
                for rule in branch["damage_rules"]:
                    totals["skill_damage_rules"] += 1
                    add(character, unit + " / " + branch["name"], "skill_branch", rule, "deferred_producer")
            for rule in skill["model_release_bonus_rules"]:
                totals["model_release_bonus_bindings"] += 1
                if rule["key"].startswith(("weapon:", "set:")):
                    add(character, unit, "selected_weapon_or_set", rule, release_category(rule))
                elif not any(rule["key"] == spec["key"] for spec in skill["damage_rules"]):
                    raise ValueError("Released skill rule has no canonical source")
        for passive in character["passives"]:
            for rule in passive["damage_rules"]:
                totals["selected_passive_damage_rules"] += 1
                target = rule["key"] in TARGET_PASSIVES
                if target and not (rule["permanent"] and rule["evaluation"] == "hit"):
                    raise ValueError("Reviewed target-condition contract changed")
                add(character, passive["name"], "selected_passive", rule,
                    "conditional_current_target" if target else "deferred_producer")
            for rule in passive["native_buff_attribute_rules"]:
                totals["native_buff_attribute_damage_bindings"] += 1
                add(character, passive["name"], "native_attribute_instance", rule, "confirmed_native_fragment_only")
        for rule in character["target_state_bonus_rules"]:
            totals["target_state_bonus_bindings"] += 1
            add(character, rule["sources"][0].split("/")[1], "selected_weapon_target", rule, "conditional_current_target")
        for field in ("native_enhanced_attack_candidates", "native_next_skill_set_candidates",
                      "native_marker_release_candidates"):
            for index, candidate in enumerate(character[field]):
                item = evidence_record(character, field, candidate, f"/characters/{char_index}/{field}/{index}")
                (inactive if candidate.get("activated_by_selected_build") is False else evidence).append(item)
        for index, candidate in enumerate(character["dynamic_attribute_flow"]["native_weapon_attribute_candidates"]):
            field = "native_weapon_attribute_candidates"
            evidence.append(evidence_record(character, field, candidate,
                                            f"/characters/{char_index}/dynamic_attribute_flow/{field}/{index}"))
    for key, count in totals.items():
        if audit["summary"][key] != count:
            raise ValueError("Inventory count differs from audit: " + key)
    return {"schema_version": 1, "scope": "inventory_only; no_master_integration_or_observation_implementation",
            "source": {"audit": AUDIT, "sha256": hashlib.sha256(raw).hexdigest()},
            "count_unit": "per character / source / trigger or branch; aliases of a released skill are counted once",
            "summary": {"fixed_panels": len(audit["characters"]), "fixed_quotes": audit["summary"]["replay_verified_quotes"],
                        "registered_rule_or_instance_rows": len(records), "categories": dict(Counter(r["category"] for r in records)),
                        "additional_native_evidence_candidates": len(evidence), "inactive_selected_set_rows": len(inactive)},
            "categories": {
                "first_release_candidate": "Use an identified successful battle/ultimate release; exact native frame is outside this model contract.",
                "conditional_current_target": "Calculate only with explicit current hit target predicates; missing input stays unknown.",
                "confirmed_native_fragment_only": "Confirmed fragment/outcome and recipient/lifetime required; no automatic cast mapping.",
                "deferred_identified_combo": "Keep master combo handling; anonymous E does not identify owner or skill.",
                "deferred_attribute_input": "Verified formula needs actual native attribute-domain input, not final panel substitution.",
                "deferred_producer": "Existing formula does not prove hit/outcome/field/count or cleanup production."},
            "records": records, "additional_native_evidence": evidence, "inactive_selected_sets": inactive}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = inventory()
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    print(json.dumps(result["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
