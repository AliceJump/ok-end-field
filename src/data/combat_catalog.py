"""Bind a fixed build and ranked native programs into a persistent battle world."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from src.data.character_skills import get_character
from src.data.combat_simulation import CombatWorldState, EffectRequirement, UnresolvedMechanic
from src.data.damage_resolution import FixedDamagePanel
from src.data.native_action_program import compile_native_action
from src.data.native_character_events import bind_character_events
from src.data.native_gameplay import native_asset
from src.data.native_reactions import reaction_parameters
from src.data.skill_types import SkillType

BASELINE = Path(__file__).resolve().parents[2] / "assets/data/fixed_damage_baseline.json"


@dataclass
class CombatCatalog:
    world: CombatWorldState
    programs: dict[tuple[str, str], tuple]
    diagnostics: tuple[str, ...]

    def candidates(self, actor=None, kind=None):
        return tuple(p for (a, k), group in self.programs.items() for p in group
                     if (actor is None or a == actor) and (kind is None or k == kind))

    def available(self, actor, kind):
        # Replacements are selected through state requirements, not phase counters.
        return tuple(p for p in self.candidates(actor, kind)
                     if self.world.satisfies(actor, p.enemy, p.requires, p.any_requires)
                     and not any(self.world.satisfies(actor, p.enemy, (r,)) for r in p.forbids))


def _groups(groups):
    all_of, any_of = [], []
    for group in groups:
        values = tuple(EffectRequirement(r.effect, r.min_count) for r in group.requirements)
        if group.operator == "all":
            all_of.extend(values)
        else:
            any_of.append(values)
    return tuple(all_of), tuple(any_of)


def build_combat_catalog(team, store, *, baseline=BASELINE):
    rows = {row["character"]: row for row in json.loads(Path(baseline).read_text(encoding="utf-8"))}
    actors = tuple(str(i + 1) for i in range(len(team)))
    world = CombatWorldState(actors)
    settings = native_asset("SkillSetting")
    world.regen = settings["atbRecover"]
    world.reaction_inputs = reaction_parameters()
    world.default_energy_per_sp = {
        "self": settings["atbConsumedDefaultUspGainSelf"],
        "team": settings["atbConsumedDefaultUspGainOther"],
    }
    programs, diagnostics = {}, []
    for actor, name in zip(actors, team, strict=True):
        row = rows.get(name)
        if row is None:
            diagnostics.append(f"Missing fixed build: {name}")
            continue
        profile = row["profile"]
        character = get_character(row["key"], skill_rank=profile["skill_rank"], potential=profile["potential"])
        state = world.characters[actor]
        state.panel = FixedDamagePanel(**row["panel"]["damage_basis"])
        state.attributes = {key: value for key, value in row["panel"].items() if isinstance(value, (float, int))}
        state.attributes["energy_gain"] = row["panel"].get("ult_charge", 0) / 100
        state.attributes["arts_strength"] = row["panel"]["源石技艺强度"]
        state.attributes["level"] = profile["character_level"]
        world.register_damage_passives(actor, character.progression.damage_modifiers)
        for kind, skill_type in (("battle", SkillType.SKILL), ("link", SkillType.LINK_SKILL), ("ult", SkillType.ULTIMATE)):
            skill = next((s for s in character.skills if s.skill_type == skill_type), None)
            if skill is None:
                continue
            profiles = store.battle_phase_profiles(name) if kind == "battle" else ()
            profiles = profiles or store.profiles(name, kind)
            # Male/female Administrator entries are aliases for one character,
            # not an action replacement chain. Select the baseline's native ID.
            native_id = character.progression.native_id
            matching = tuple(p for p in profiles if p.skill_id.startswith(native_id + "_"))
            profiles = matching or profiles
            quote = next((s for s in row["skills"] if s["skill_id"] == skill.skill_id), None)
            replacements = [e for e in skill.enhancements if e.replaces_base_action]
            group = []
            for index, timing in enumerate(profiles):
                try:
                    program = compile_native_action(store, character, timing, actor, kind,
                                                    damage_bonus=(quote or {}).get("bonus_pct", 0) / 100,
                                                    attributes=state.attributes, panel=state.panel)
                    if kind == "ult":
                        state.energy_cap = program.energy_cost
                    if kind == "battle" and len(profiles) > 1 and replacements:
                        if len(replacements) != len(profiles) - 1:
                            raise UnresolvedMechanic(f"Native phase/canonical replacement mismatch: {name}")
                        if index == 0:
                            forbidden = tuple(r for e in replacements for g in e.trigger_effect_groups
                                              for r in _groups((g,))[0])
                            program = replace(program, forbids=forbidden)
                        else:
                            variant = replacements[index - 1]
                            required, any_of = _groups(variant.trigger_effect_groups)
                            program = replace(program, requires=required, any_requires=any_of, replacement=variant.name)
                    group.append(program)
                except (KeyError, ValueError) as error:
                    diagnostics.append(f"{name}/{kind}: {error}")
            programs[actor, kind] = tuple(group)
            if kind == "link" and group:
                try:
                    diagnostics.extend(bind_character_events(world, store, character, profiles[0], actor))
                except (KeyError, ValueError, StopIteration) as error:
                    diagnostics.append(f"{name}/CharacterData: {error}")
    return CombatCatalog(world, programs, tuple(diagnostics))
