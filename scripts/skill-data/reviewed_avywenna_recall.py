"""Separate the nominal whirlwind from unobserved recalled-spear hits.

The pinned native program iterates actual owned entities and launches distinct
return projectiles. A parameter-table row is not an additional guaranteed hit.
"""

import hashlib
import json

RECORDS = {
    "chr_0012_avywen_normal_skill": "dfadd90a92539712513bfcfe93e59add3e134abc455b6056fdaa200948970e3c",
    "chr_0012_avywen_combo_skill_lance_back": "b38a4f21a384df878c40abf41f729b4b7c17338aab6f194ca132c1dfb23b894c",
    "chr_0012_avywen_ultimate_skill_lance_back": "13c4f5e28d684f4b932a8cb5df190ad274c1aafe0f8dcb7aefe2a668629853d1",
}


def recall_quote(row, store):
    for skill_id, digest in RECORDS.items():
        if store.record(skill_id)["source"]["sha256"] != digest:
            raise ValueError("Unreviewed Avywenna recall program")
    rank = row["profile"]["skill_rank"]
    if rank != 12 or row["profile"]["potential"] != 5:
        raise ValueError("Unreviewed Avywenna recall build")
    skill_id = "chr_0012_avywen_normal_skill"
    panel = row["panel"]["damage_basis"]
    fixed = panel["damage_bonus"]
    bonus = fixed["all"] + fixed["all_skill"] + fixed["电磁"] + fixed["skill"]
    attack = (panel["attack_white"] * (1 + panel["attack_percent"]) + panel["attack_flat"])
    unit = attack * panel["attribute_factor"] * (1 + bonus) * (1 + panel["amplification"]["电磁"])
    critical = 1 + panel["crit_rate"] * panel["crit_damage"]

    def component(parameter):
        multiplier = store.ranked_parameter(skill_id, parameter, rank)
        return {"parameter_key": parameter, "multiplier": multiplier,
                "non_crit": unit * multiplier, "crit_expect": unit * multiplier * critical}

    base = component("atk_scale")
    deferred = []
    for parameter, kind, source in (
        ("atk_scale_lance", "normal_spear", "chr_0012_avywen_combo_skill_lance_back"),
        ("atk_scale_lance_ult", "strong_spear", "chr_0012_avywen_ultimate_skill_lance_back"),
    ):
        deferred.append({**component(parameter), "kind": kind, "source_skill_id": source,
                         "actual_count": None, "included_in_quote": False,
                         "requires": ["actual_owned_entity_count", "return_projectile_path_hit"]})
    native_row = store.ranked_skill(skill_id, rank)
    return {"non_crit": base["non_crit"], "crit_expect": base["crit_expect"], "bonus_pct": bonus * 100,
            "quote_basis": {"skill_rank": rank, "element": "电磁", "damage_tags": ["skill"],
                            "multiplier": base["multiplier"], "crit_policy": "baseline_expectation",
                            "scope": "whirlwind_only_without_recalled_spear_hits"},
            "pricing_scope": "unconditional_cast_only; recall_entities_and_path_hits_unobserved",
            "deferred_components": deferred,
            "source_evidence": {"native_record_sha256": RECORDS,
                                "ranked_row_sha256": hashlib.sha256(json.dumps(
                                    native_row, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                                "whirlwind_action_index": 30,
                                "normal_return_launch_index": 67,
                                "strong_return_launch_index": 75}}
