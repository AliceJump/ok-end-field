"""Audit every character/skill's damage data, without scheduling or recognition."""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.data.character_skills import get_character
from src.data.combat_input_requirements import modifier_input_keys
from src.data.damage_quote_data import read_fixed_quote, verify_sources
from src.data.damage_release_rules import KINDS, release_rules
from src.data.damage_state_rules import fixed_weapon_bonuses, state_bonus_rules
from src.data.native_attribute_modifiers import reviewed_attack_binding
from src.data.skill_timing import SkillTimingStore


def rule_record(spec, origin):
    return {
        "origin": origin, "key": spec.key, "bucket": spec.bucket.value,
        "elements": list(spec.elements), "damage_tags": list(spec.damage_tags),
        "recipient": spec.recipient, "trigger": spec.trigger,
        "duration": spec.duration, "duration_input": spec.duration_input,
        "permanent": spec.permanent, "lifetime_scope": spec.lifetime_scope,
        "evaluation": spec.evaluation, "max_stacks": spec.max_stacks,
        "stack_policy": spec.stack_policy, "condition_inputs": list(spec.condition_inputs),
        "magnitude": asdict(spec.magnitude), "sources": list(spec.sources),
        "required_inputs": sorted(modifier_input_keys(spec)), "unresolved": spec.unresolved,
        "producer_status": "not_verified_by_this_audit",
    }


def effect_rules(effects, origin):
    rules, gaps = [], []
    for effect in effects:
        spec = effect.damage_modifier
        is_damage = effect.effect_id.value.startswith(("VULN_", "BUFF_")) and (
            effect.effect_id.value.startswith("VULN_") or effect.effect_id.value in {
                "BUFF_ATTACK_UP", "BUFF_DAMAGE_UP", "BUFF_CRIT_RATE_UP", "BUFF_CRIT_DMG_UP",
                "BUFF_COLD_UP", "BUFF_BURN_UP", "BUFF_NATURAL_UP", "BUFF_ELECTROMAGNETIC_UP", "BUFF_SPELL_UP",
            })
        if spec is None:
            if is_damage:
                gaps.append(f"Missing damage binding: {origin}/{effect.effect_id.value}")
            continue
        rules.append(rule_record(spec, origin))
        if not spec.sources:
            gaps.append(f"Missing magnitude provenance: {origin}/{spec.key}")
        if spec.unresolved:
            gaps.append(f"Unresolved rule: {origin}/{spec.key}: {spec.unresolved}")
        if spec.duration is None and spec.duration_input is None and not spec.permanent and spec.lifetime_scope != "field":
            gaps.append(f"Missing lifetime: {origin}/{spec.key}")
    return rules, gaps


def audit(rows=None):
    if rows is None:
        rows = json.loads((ROOT / "assets/data/fixed_damage_baseline.json").read_text(encoding="utf-8"))
    expected_keys = {path.stem for path in (ROOT / "assets/data/character_skills").glob("*.json")}
    keys = [row["key"] for row in rows]
    if len(set(keys)) != len(keys):
        raise ValueError("Duplicate character in fixed baseline")
    report = {"schema_version": 1,
              "scope": "damage data contract per canonical character and skill; not full mechanics execution",
              "missing_characters": sorted(expected_keys - set(keys)), "characters": []}
    verified = total = rules_total = 0
    reviewed_rows = json.loads((ROOT / "assets/data/skill_damage_row_semantics.json").read_text(encoding="utf-8"))["skills"]
    store = SkillTimingStore()
    for row in rows:
        key, profile = row["key"], row["profile"]
        character = get_character(key, skill_rank=profile["skill_rank"], potential=profile["potential"])
        raw_skills = {skill["skill_id"]: skill for skill in json.loads(
            (ROOT / f"assets/data/character_skills/{key}.json").read_text(encoding="utf-8"))["skills"]}
        source_gaps = []
        try:
            verify_sources(row)
        except (KeyError, ValueError) as error:
            source_gaps.append(str(error))
        selected = {passive.effect_id for passive in (*character.progression.talents, *character.progression.active_potentials)}
        fixed = set(profile["constant_passive_sources"])
        if not fixed <= selected:
            source_gaps.append("Fixed panel contains an unselected passive")
        entry = {"key": key, "character": character.name, "profile": profile,
                 "source_gaps": source_gaps, "skills": [], "passives": []}
        releases = release_rules(character, row)
        native_attack = reviewed_attack_binding(character, store)
        entry["target_state_bonus_rules"] = [
            {**rule_record(spec, "reviewed_fixed_weapon_target_predicate"),
             "producer_status": "current_actual_hit_target; no_proc_or_timed_trigger"}
            for spec in state_bonus_rules(character, row)]
        entry["fixed_weapon_bonus_filters"] = fixed_weapon_bonuses(character, row)
        entry["dynamic_attribute_flow"] = {
            "basis": row.get("attribute_basis"),
            "consumer_status": "confirmed_final_deltas_supported; native formula domains not inferred",
            "native_attribute_change_producers": "not_verified",
        }
        quotes = {}
        for quote in row["skills"]:
            quotes.setdefault(quote["skill_id"], []).append(quote)
        canonical_ids = {skill.skill_id for skill in character.skills}
        entry["extra_quote_ids"] = sorted(set(quotes) - canonical_ids)
        for skill in character.skills:
            total += 1
            matches = quotes.get(skill.skill_id, [])
            gaps = list(source_gaps)
            quote_verified = False
            if len(matches) != 1:
                gaps.append(f"Expected one quote, found {len(matches)}")
            else:
                try:
                    typed = read_fixed_quote(row, matches[0])
                    if typed.element != skill.element.value or typed.skill_type != skill.skill_type.value:
                        raise ValueError("Quote/canonical skill identity or element mismatch")
                    reviewed = reviewed_rows.get(skill.skill_id)
                    if reviewed:
                        membership = dict(matches[0]["row_semantics"])
                        components = [{name: value for name, value in component.items() if name != "rank_values"}
                                      for component in membership["components"]]
                        membership["components"] = components
                        if membership != reviewed:
                            raise ValueError("Reviewed quote membership differs from source")
                        ranked = raw_skills[skill.skill_id]["rank_stats"]["rows"]
                        values = {}
                        for label in (*reviewed["base_rows"], *(label for component in reviewed["components"] for label in component["rows"])):
                            found = [r for r in ranked if r["label"] == label]
                            if len(found) != 1:
                                raise ValueError(f"Missing/ambiguous reviewed row: {label}")
                            values[label] = found[0]["values"][profile["skill_rank"] - 1]
                        multiplier = sum(float(values[label].removesuffix("%")) / 100 for label in reviewed["base_rows"])
                        if not math.isclose(multiplier, typed.multiplier, abs_tol=1e-12):
                            raise ValueError("Reviewed quote multiplier differs from selected rank rows")
                        for component in matches[0]["row_semantics"]["components"]:
                            if component["rank_values"] != {label: values[label] for label in component["rows"]}:
                                raise ValueError("Reviewed component values differ from selected rank rows")
                    quote_verified = not source_gaps
                except (KeyError, ValueError) as error:
                    gaps.append(str(error))
            verified += quote_verified
            rules, rule_gaps = effect_rules(skill.effects, "base")
            gaps.extend(rule_gaps)
            branches = []
            for branch in skill.enhancements:
                branch_rules, branch_gaps = effect_rules(branch.effects, branch.name)
                branches.append({"name": branch.name, "trigger_condition": branch.trigger_condition,
                                 "trigger_effect_groups": [
                                     {"operator": group.operator, "requirements": [
                                         {"effect_id": req.effect.value, "min_count": req.min_count}
                                         for req in group.requirements]} for group in branch.trigger_effect_groups],
                                 "evaluation_point": branch.evaluation_point,
                                 "replaces_base_action": branch.replaces_base_action,
                                 "damage_rules": branch_rules})
                gaps.extend(branch_gaps)
                rules_total += len(branch_rules)
            rules_total += len(rules)
            pending = ["Rank-row aggregation still needs native hit/count and conditional-attack verification"]
            if rules or any(branch["damage_rules"] for branch in branches):
                pending.append("Damage-rule producers, input supply and outcome timing not verified by this audit")
            if branches:
                pending.append("Conditional/replacement branches require individual semantic review")
            entry["skills"].append({
                "skill_id": skill.skill_id, "name": skill.name, "type": skill.skill_type.value,
                "element": skill.element.value,
                "quote_status": "replay_verified" if quote_verified else "incomplete",
                "quote_basis": matches[0].get("quote_basis") if len(matches) == 1 else None,
                "row_semantics": matches[0].get("row_semantics") if len(matches) == 1 else None,
                "conditional_rows_not_in_quote": matches[0].get("conditional_rows", []) if len(matches) == 1 else None,
                "damage_rules": rules, "branches": branches, "data_gaps": gaps,
                "model_release_bonus_rules": [{**rule_record(spec, "reviewed_release"),
                    "producer_status": "successful_model_release; native_frame_not_verified"}
                    for spec in releases.get(KINDS.get(skill.skill_type.value), ())],
                "pending_semantic_checks": pending,
                "full_skill_execution": "not_assessed; see mechanism coverage audit",
            })
        for passive in (*character.progression.talents, *character.progression.active_potentials):
            # Ownership comes from the selected passive's bindings. Parameter
            # provenance may point to another passive (e.g. Endmin P2 reads T1).
            owned_keys = {binding["key"] for binding in passive.damage_modifier_bindings}
            parameter_prefix = passive.source + "/parameters/"
            bound = [rule_record(spec, passive.effect_id) for spec in character.progression.damage_modifiers
                     if spec.key in owned_keys]
            dependencies = [spec.key for spec in character.progression.damage_modifiers
                            if spec.key not in owned_keys and
                            any(source.startswith(parameter_prefix) for source in spec.sources)]
            adjustments = []
            for unit in entry["skills"]:
                candidates = [*unit["damage_rules"], *(rule for branch in unit["branches"] for rule in branch["damage_rules"])]
                adjustments.extend({"skill_id": unit["skill_id"], "origin": rule["origin"], "key": rule["key"]}
                                   for rule in candidates if any(source.startswith(parameter_prefix) for source in rule["sources"]))
            entry["passives"].append({"effect_id": passive.effect_id, "name": passive.name,
                                      "rank": passive.level, "source": passive.source,
                                      "placement": "fixed_panel" if passive.effect_id in fixed else
                                      "native_buff_attribute_rule" if native_attack and native_attack["passive_id"] == passive.effect_id else
                                      "runtime_rules" if bound else "skill_rule_parameters" if adjustments else
                                      "not_classified_as_damage_by_this_audit",
                                      "native_modifier_count": len(passive.modifiers), "damage_rules": bound,
                                      "skill_rule_references": adjustments,
                                      "runtime_rule_parameter_references": dependencies,
                                      "native_buff_attribute_rules": [native_attack] if native_attack and native_attack["passive_id"] == passive.effect_id else [],
                                      "complete_semantic_review": False})
        report["characters"].append(entry)
    report["summary"] = {"characters": len(rows), "skills": total, "replay_verified_quotes": verified,
                         "skill_damage_rules": rules_total,
                         "selected_passive_damage_rules": sum(len(passive["damage_rules"]) for row in report["characters"] for passive in row["passives"]),
                         "model_release_bonus_bindings": sum(len(skill["model_release_bonus_rules"]) for row in report["characters"] for skill in row["skills"]),
                         "native_buff_attribute_damage_bindings": sum(len(passive["native_buff_attribute_rules"]) for row in report["characters"] for passive in row["passives"]),
                         "target_state_bonus_bindings": sum(len(row["target_state_bonus_rules"]) for row in report["characters"]),
                         "skills_with_reviewed_row_membership": sum(bool(skill["row_semantics"]) for row in report["characters"] for skill in row["skills"]),
                         "skills_with_structural_data_gaps": sum(bool(skill["data_gaps"]) for row in report["characters"] for skill in row["skills"]),
                         "skills_pending_semantic_review": sum(bool(skill["pending_semantic_checks"]) for row in report["characters"] for skill in row["skills"]),
                         "complete_characters": None,
                         "note": "Numerical replay is not complete per-skill semantic verification; producers/passives remain separate."}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "tmp/damage_data_flow_audit.json")
    args = parser.parse_args()
    report = audit()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
