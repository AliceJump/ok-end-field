"""Export-only review boundary for two fixed-build support ultimates.

This projection does not supply native attribute queries to the research world.
The live layer has no four-stat/conversion producers: it prices the selected
fixed build, as its other quotes do. Do not generalise this to changed builds.
"""

from dataclasses import asdict


def support_release(row, store):
    from src.data.damage_modifiers import DamageBucket, DamageModifierSpec, ModifierMagnitude
    from src.data.native_gameplay import native_record

    from src.data.character_skills import get_character

    key = row["key"]
    if key not in {"xaihi", "liino"}:
        return [], None
    profile = row["profile"]
    expected = {"xaihi": (5, "骑士精神"), "liino": (0, "曜夜的首演")}[key]
    selected_builds = {
        "xaihi": ("长息轻护甲·壹型", "涉渊护手", "长息加固板", "长息加固板"),
        "liino": ("长息轻护甲", "长息手套", "长息辅助臂", "生物辅助护板"),
    }
    if (profile["potential"], row["build"]["weapon"]) != expected or profile["skill_rank"] != 12:
        raise ValueError("Support fixed-build review must be repeated for a changed profile")
    if (tuple(row["build"]["pieces"]) != selected_builds[key] or profile["character_level"] != 90
            or row["data_flow"]["runtime_modifiers"] != "not_applied"):
        raise ValueError("Changed support gear/level/runtime modifiers require a new attribute review")
    char = get_character(key, potential=profile["potential"], skill_rank=profile["skill_rank"])
    native = char.progression.native_id + "_ultimate_skill"
    record = native_record(store, native)
    index, stat, attribute = (17, "智识", 41) if key == "xaihi" else (22, "意志", 42)
    block = record["data"]["actionGroupData"]["timelineActions"][index]
    expected_frame = 58 if key == "xaihi" else 77
    if block["_startFrame"] != expected_frame:
        raise ValueError("Support release marker changed")
    basis = row["attribute_basis"]
    if (basis["domain"] != "final_panel" or basis["unverified_attack_dependencies"]
            or profile["conditional_talent_state"] != "untriggered"):
        raise ValueError("Support projection requires the reviewed constant build")
    for passive in (*char.progression.talents, *char.progression.active_potentials):
        for mod in passive.modifiers:
            attr = mod.get("attrModifier")
            if attr and attr["attrType"] == attribute and (mod["activeCondition"] or attr["modifyAttributeType"] != 0):
                raise ValueError("Converted/conditional support attribute cannot use this projection")
    value = basis["totals"][stat]
    ids = (["buff_chr_0011_seraph_atk_buff", "buff_chr_0011_seraph_ultimate_effect",
            "buff_chr_0011_seraph_ultimate_effect_2"] if key == "xaihi" else
           ["buff_chr_0035_liino_ultskill_music_tag", "buff_chr_0035_liino_normalskill_buff_atkup",
            "buff_chr_0035_liino_atkup", "buff_chr_0035_liino_atkup_owner",
            "buff_chr_0035_liino_ultskill_buff_atkup", "buff_chr_0035_liino_spellenhance"])
    records = {name: native_record(store, name) for name in ids}
    reviewed_hashes = {
        "chr_0011_seraph_ultimate_skill": "aeb22f042668624c2de97fab95b2d8900eb6cb4017ef0eee1d0c786886f0c0da",
        "buff_chr_0011_seraph_atk_buff": "1cc3a54f53c85fee01e7514fd14fc64e00ec9ecd00363b11d5d300f1339ad715",
        "chr_0035_liino_ultimate_skill": "2a844f1d6bf2db133fad1888fc15bb8d6c805bc1b456a3123cd47575b2459457",
        "buff_chr_0035_liino_ultskill_music_tag": "3b3553f24e0c3887898d4db511f4d88f3f4353a50e423e8375d0dd2e6ee1571c",
    }
    for name, native_row in {native: record, **records}.items():
        if name in reviewed_hashes and native_row["source"]["sha256"] != reviewed_hashes[name]:
            raise ValueError("Reviewed support native block changed")
    owner = records[ids[0]]["data"]
    query = (owner["buffEventAction"][0]["actions"][0]["actionData"][0]["$value"]
             if key == "xaihi" else block["_sequenceActionData"]["actionData"][0]["$value"])
    if (query["attributeType"] != attribute or query["storeAttributeType"] != 1
            or query["useFloor"] or query["targetSettings"]["targetSource"] != 1):
        raise ValueError("Support scaling attribute domain changed")
    if key == "liino":
        aura = owner["buffEventAction"][0]["actions"][0]["actionData"][0]["$value"]
        if (aura["auraType"] != 1 or aura["shapeData"]["_shape"] != 0
                or aura["targetObjectType"] != 8 or not aura["targetFilter"]["checkAlive"]):
            raise ValueError("Support team aura selector changed")
    skill = next(s for s in char.skills if s.skill_id == key + "_ultimate")
    specs = [effect.damage_modifier for effect in skill.effects if effect.damage_modifier is not None]
    if len(specs) != (2 if key == "xaihi" else 3):
        raise ValueError("Support bonus selection changed")
    selected = {p["key"]: p["value"] for p in store.ranked_skill(native, profile["skill_rank"])["blackboard"]}
    # Xaihi's potential modifies all three amplitude parameters, independently.
    scale = next(p.parameters["atk_up"] for p in char.progression.active_potentials
                 if p.effect_id.endswith("potential_5")) if key == "xaihi" else 1.0
    rate = min(value * selected["wisd_up" if key == "xaihi" else "will_up"] * scale,
               selected["wisd_max" if key == "xaihi" else "will_max"] * scale)
    if key == "xaihi":
        rate += selected["atk_up"] * scale
    duration = selected["duration" if key == "xaihi" else "ultmusic_duration"]
    evidence = {"character": row["character"], "skill": native,
                "release_frame": expected_frame, "fps": store.index["frames_per_second"],
                "input_domain": f"source.native.final_nonconverted.{attribute}",
                "input_policy": "fixed_build_projection_without_dynamic_four_stats_or_conversions",
                "input_value": value, "attribute_basis": basis, "profile": profile, "build": row["build"],
                "native_query": query, "ranked_parameters": selected, "potential_scale": scale,
                "native_record_sha256": {native: record["source"]["sha256"],
                                         **{k: r["source"]["sha256"] for k, r in records.items()}},
                "duration": duration, "amplification": rate,
                "source_hashes": row["data_flow"]["sources"]}
    result = []
    for spec in specs:
        magnitude = selected["atk_up"] if spec.bucket == DamageBucket.ATTACK else rate
        constant = DamageModifierSpec(spec.key, spec.bucket, spec.elements, "team",
                                      ModifierMagnitude(magnitude), "ultimate_cast", duration=duration,
                                      sources=(*spec.sources, f"native:{native}:frame:{expected_frame}"))
        data = asdict(constant)
        data.update(kind="ult", starts_after=expected_frame / evidence["fps"],
                    ends_on_source_action=key == "liino", blocks_source_actions=["battle", "normal"] if key == "liino" else [])
        result.append(data)
    return result, evidence
