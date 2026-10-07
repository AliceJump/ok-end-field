"""Evidence-reviewed attribute bonuses on actual native buff instances.

This is not a general formulaItem interpreter. Unreviewed modifiers keep their
diagnostics; this binding only covers Akekuri P3's inherited ATK percentage.
"""

from src.data.combat_expressions import CombatExpression, MissingCombatInput, combat_input

AKEKURI_TEAM_ATTACK = "buff_chr_0019_karin_potential_3"


def compile_attack_additions(buff_id, data):
    modifiers = data["attributeModifier"]
    if not modifiers["attributeModifiers"]:
        return (), False
    if buff_id != AKEKURI_TEAM_ATTACK:
        return (), False
    # Reviewed alongside P3's description, inherited atk BB and team selector.
    # Do not extend formulaItem=6 to arbitrary attributes or converted inputs.
    expected = {"attributeModifiers": [{"attributeType": 2, "formulaItem": 6,
                "modifyAttributeType": 0,
                "param": {"blackboardKey": "atk", "useBlackboardKey": True, "value": 0.0}}],
                "isConvertedAttribute": False}
    if modifiers != expected or data["buffEventAction"] or data["abilityEventAction"]:
        return (), False
    return (CombatExpression("float32", (combat_input("bb.atk"),)),), True


def attack_addition(world, actor):
    """Sum captured percentages; removal follows the buff's actual UID lifetime."""
    total = 0.0
    for instance in world.native_buff_instances.values():
        if instance.owner != actor or not instance.definition.attack_additions:
            continue
        if instance.attack_addition is None:
            raise MissingCombatInput(f"Native attack modifier input: {instance.key}/bb.atk")
        total += instance.attack_addition
    return total


def reviewed_attack_binding(character, store):
    """Audit the selected native-only rule separately from canonical modifiers."""
    from src.data.native_gameplay import native_record

    passive = next((p for p in character.progression.active_potentials
                    if p.effect_id == "chr_0019_karin_potential_3"), None)
    if passive is None:
        return None
    buff = native_record(store, AKEKURI_TEAM_ATTACK)
    if not compile_attack_additions(AKEKURI_TEAM_ATTACK, buff["data"])[1]:
        raise ValueError("Reviewed Akekuri P3 attribute binding changed")
    producer = native_record(store, "chr_0019_karin_ultimate_skill")
    return {"passive_id": passive.effect_id, "buff_id": AKEKURI_TEAM_ATTACK,
            "bucket": "attack", "recipient": "actual_buff_owner_from_living_team_selector",
            "producer": "chr_0019_karin_ultimate_skill", "input": "inherited_bb.atk",
            "selected_parameter": passive.parameters["atk"], "evaluation": "instance_creation_snapshot",
            "lifetime": "native_buff_uid; authored_action_end_or_explicit_finish",
            "verified_scope": "original_P3_producer_block_and_buff; full_ultimate_not_complete",
            "sources": {"passive": passive.source,
                        "producer_sha256": producer["source"]["sha256"],
                        "buff_sha256": buff["source"]["sha256"]}}
