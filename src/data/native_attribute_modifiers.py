"""Evidence-reviewed attribute bonuses on actual native buff instances.

This is not a general formulaItem interpreter. Unreviewed modifiers keep their
diagnostics; only reviewed Akekuri/Alesh P3 entries are bound here.
"""

from src.data.combat_expressions import CombatExpression, MissingCombatInput, combat_input

AKEKURI_TEAM_ATTACK = "buff_chr_0019_karin_potential_3"
ALESH_TEAM_ATTACK = "buff_chr_0024_deepfin_potential_3"


def compile_attack_additions(buff_id, data):
    modifiers = data["attributeModifier"]
    if not modifiers["attributeModifiers"]:
        return (), False
    parameter = {AKEKURI_TEAM_ATTACK: "atk", ALESH_TEAM_ATTACK: "atk_up"}.get(buff_id)
    if parameter is None:
        return (), False
    # Reviewed alongside P3's description, inherited atk BB and team selector.
    # Do not extend formulaItem=6 to arbitrary attributes or converted inputs.
    expected = {"attributeModifiers": [{"attributeType": 2, "formulaItem": 6,
                "modifyAttributeType": 0,
                "param": {"blackboardKey": parameter, "useBlackboardKey": True, "value": 0.0}}],
                "isConvertedAttribute": False}
    if modifiers != expected or data["buffEventAction"] or data["abilityEventAction"]:
        return (), False
    return (CombatExpression("float32", (combat_input("bb." + parameter),)),), True


def attack_addition(world, actor):
    """Sum captured percentages; removal follows the buff's actual UID lifetime."""
    total = 0.0
    for instance in world.native_buff_instances.values():
        if instance.owner != actor or not instance.definition.attack_additions:
            continue
        if instance.attack_addition is None:
            raise MissingCombatInput(f"Native attack modifier input: {instance.key}")
        total += instance.attack_addition
    return total


def reviewed_attack_binding(character, store):
    """Audit the selected native-only rule separately from canonical modifiers."""
    from src.data.native_gameplay import native_record

    reviewed = {
        "chr_0019_karin_potential_3": (AKEKURI_TEAM_ATTACK, "chr_0019_karin_ultimate_skill", "atk"),
        "chr_0024_deepfin_potential_3": (ALESH_TEAM_ATTACK, "chr_0024_deepfin_combo_skill", "atk_up"),
    }
    passive = next((p for p in character.progression.active_potentials if p.effect_id in reviewed), None)
    if passive is None:
        return None
    buff_id, producer_id, parameter = reviewed[passive.effect_id]
    buff = native_record(store, buff_id)
    if not compile_attack_additions(buff_id, buff["data"])[1]:
        raise ValueError("Reviewed P3 attribute binding changed")
    producer = native_record(store, producer_id)
    return {"passive_id": passive.effect_id, "buff_id": buff_id,
            "bucket": "attack", "recipient": "actual_buff_owner_from_confirmed_team_group"
                         if buff_id == ALESH_TEAM_ATTACK else "actual_buff_owner_from_living_team_selector",
            "producer": producer_id, "input": "inherited_bb." + parameter,
            "selected_parameter": passive.parameters[parameter], "evaluation": "instance_creation_snapshot",
            "lifetime": "native_buff_uid; timed_Refresh_or_explicit_finish" if buff_id == ALESH_TEAM_ATTACK
                        else "native_buff_uid; authored_action_end_or_explicit_finish",
            "verified_scope": "buff_instance_ATK_and_Refresh_only; rare_fish_outcome_and_full_combo_not_bound"
                              if buff_id == ALESH_TEAM_ATTACK else
                              "original_P3_producer_block_and_buff; full_ultimate_not_complete",
            "sources": {"passive": passive.source,
                        "producer_sha256": producer["source"]["sha256"],
                        "buff_sha256": buff["source"]["sha256"]}}
