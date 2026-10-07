"""Reviewed constant per-skill potential terms in the selected fixed panel."""

import math


def fixed_skill_crit(character):
    """Perlica P5 is additive ultimate CRIT, not a proc or global panel CRIT."""
    effect_id = "chr_0004_pelica_potential_5"
    selected = [p for p in character.progression.active_potentials if p.effect_id == effect_id]
    if not selected:
        return {}, ()
    passive, = selected
    amount = passive.parameters["crit"]
    expected = {"activeCondition": [], "modifyType": 3, "skillBbModifier": {
        "bbKey": "crit", "floatValue": amount, "modifyType": 1,
        "skillId": "chr_0004_pelica_ultimate_skill", "stringValue": "",
    }}
    if (type(amount) not in (float, int) or not math.isfinite(amount) or amount < 0
            or passive.modifiers != (expected,)
            or passive.description_template != "终结技<@ba.key>协议ε·70.41κ</>的暴击率<@ba.vup>+{crit:0%}</>。"):
        raise ValueError("Changed reviewed Perlica ultimate critical potential")
    return {"ultimate": amount}, (passive,)
