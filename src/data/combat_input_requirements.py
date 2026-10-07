"""Event-local numeric reads; callbacks are evaluated in their own input scope."""

from dataclasses import fields, is_dataclass

from src.data.combat_expressions import CombatExpression
from src.data.combat_value_snapshots import ResourceValue
from src.data.damage_modifiers import DamageModifierSpec


def modifier_input_keys(spec):
    keys = set(spec.condition_inputs)
    keys.update(term.input for term in spec.magnitude.terms)
    if spec.magnitude.count_input:
        keys.add(spec.magnitude.count_input)
    if spec.duration_input:
        keys.add(spec.duration_input)
    return keys


def event_input_keys(value):
    if isinstance(value, CombatExpression):
        return value.referenced_inputs()
    if isinstance(value, DamageModifierSpec):
        return modifier_input_keys(value)
    if isinstance(value, ResourceValue):
        if value.source_effect_id is not None and value.count_basis not in {"consumed", "trigger_event"}:
            return {f"count.{value.source_effect_id.value}"}
        return set()
    if isinstance(value, tuple):
        return set().union(*(event_input_keys(item) for item in value)) if value else set()
    if not is_dataclass(value):
        return set()
    # A buff inherits caller values, but its own callbacks run later in its BB.
    if type(value).__name__ == "NativeBuffProgram":
        return event_input_keys(value.inherited)
    if type(value).__name__ in {"NativeListener", "NativeIteration", "ActionProgram"}:
        return set()
    return set().union(*(event_input_keys(getattr(value, field.name)) for field in fields(value)))


def required_input_keys(world, event, program):
    # Keep eager evaluation for user-supplied definitions with mutable children.
    if not event._share_on_deepcopy:
        return None
    keys = set(event.input_keys)
    if event.hit is not None or event.effects:
        if program.kind == "link":
            keys.update(("count.ATTACH_COLD", "count.STATUS_FROZEN"))
        for modifier in world.damage_state.modifiers:
            keys.update(modifier_input_keys(modifier.spec))
        for specs in world.passive_modifiers.values():
            for spec in specs:
                keys.update(modifier_input_keys(spec))
    return keys
