"""Export only the reviewed P0 Zhuang Fangyi action replacements.

Lightning pricing remains one strike unit, as in the fixed base quote. Sword
count, hits, Conductive consumption and combo ownership are not inferred here.
"""


def release_stance(row, store):
    from src.data.native_gameplay import native_record

    if row["key"] != "zhuang_fangyi":
        return None
    if row["profile"]["potential"] != 0 or row["profile"]["skill_rank"] != 12:
        raise ValueError("Changed stance profile requires review")
    prefix = "chr_0030_zhuangfy"
    hashes = {
        prefix + "_ultimate_skill": "5f1b6d1f1770f81aa78c2e51b4c982e8a8a17f4113e3fc7f1610f7859611e3cd",
        "buff_chr_0030_zhuangfy_ult_base": "6dd56f3d4820572c0916a897a480f2e0ec7f2cc9789a2baf9c98f13e3491b9de",
        "buff_chr_0030_zhuangfy_ult_skill_free": "27a73b38baf26c48eeb978ae1793c13b1e1bd4ab826751a3bc023e1303822092",
        prefix + "_normal_skill_ult": "33483a4c247260eb69cdd90ab80a604c113fb8e57a79817d3a39f54dbf971ccf",
        prefix + "_attack1_ult": "71ac383a8b3b87b2cab4b36d6b63a2e20ca4e1d37fb7ec5cfdc2787c09b9e3c7",
        prefix + "_attack2_ult": "f2f8763e14acf3e800e8b1ca54bdb7b5f2f63b657ce7b850daac227ecd311e89",
        prefix + "_attack3_ult": "16869e9f3ca84b6efdfb67f383ffc3f7954ea9c1a7d9869d20c9b7bc38c0246a",
    }
    records = {key: native_record(store, key) for key in hashes}
    if any(records[key]["source"]["sha256"] != digest for key, digest in hashes.items()):
        raise ValueError("Reviewed stance native record changed")
    ultimate = records[prefix + "_ultimate_skill"]["data"]
    block = ultimate["actionGroupData"]["timelineActions"][7]
    if block["_startFrame"] != 78 or "buff_chr_0030_zhuangfy_ult_base" not in str(block):
        raise ValueError("Stance creation frame changed")
    selected = {name: {p["key"]: p["value"] for p in store.ranked_skill(name, 12)["blackboard"]}
                for name in hashes if name.startswith("chr_")}
    normal_ids = [prefix + f"_attack{i}_ult" for i in range(1, 4)]
    profiles = [store.profile(name) for name in normal_ids]
    # Match each real allow-next edge, including attack3 -> attack1. These are
    # authored scheduling hints with the same grace used by the base scheduler.
    durations = []
    for index, profile in enumerate(profiles):
        following = normal_ids[(index + 1) % 3]
        edges = [start + .05 for start, _, allowed in profile.allow_next if following in allowed]
        if not edges:
            raise ValueError("Enhanced normal chain edge missing")
        durations.append(max(profile.handoff, min(edges)))
    basis = row["panel"]["damage_basis"]
    attack = (basis["attack_white"] * (1 + basis["attack_percent"]) + basis["attack_flat"]) * basis["attribute_factor"]
    originals = {s["type"]: s for s in row["skills"]}
    quotes = {}
    multipliers = {"normal": sum(selected[name]["atk_scale"] for name in normal_ids),
                   "battle": selected[prefix + "_normal_skill_ult"]["atk_scale"]}
    for kind, skill_type in (("normal", "普通攻击"), ("battle", "战技")):
        original = originals[skill_type]
        non_crit = attack * multipliers[kind] * (1 + original["bonus_pct"] / 100) * (1 + basis["amplification"]["电磁"])
        quote_basis = dict(original["quote_basis"], multiplier=multipliers[kind],
                           scope="enhanced_three_stage_chain" if kind == "normal" else "enhanced_single_lightning_unit_without_sword_count")
        quotes[kind] = {"non_crit": non_crit, "crit_expect": non_crit * (1 + basis["crit_rate"] * basis["crit_damage"]),
                        "bonus_pct": original["bonus_pct"], "quote_basis": quote_basis}
    battle = store.profile(prefix + "_normal_skill_ult")
    return {"key": "buff_chr_0030_zhuangfy_ult_base", "trigger": "ult", "starts_after": 78 / 30,
            "duration": selected[prefix + "_ultimate_skill"]["duration"], "free_battle_uses": 1,
            "battle_profile": battle.skill_id, "battle_handoff": battle.handoff,
            "battle_actionable": battle.actionable, "normal_duration": sum(durations),
            "normal_profiles": normal_ids, "quotes": quotes,
            "source_evidence": {"native_record_sha256": hashes, "ranked_parameters": selected,
                                "release_frame": 78, "fps": 30,
                                "normal_edge_seconds": durations,
                                "excluded": ["sword_count", "conductive_consumption", "hit_confirmation",
                                             "combo_dispatch", "native_extend_buff_lifetime"]}}
