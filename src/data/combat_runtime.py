"""Commit predicted events only after runtime evidence accepts the attempted cast."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from src.data.combat_simulation import (
    ActionProgram,
    CombatSearchLimit,
    CombatWorldState,
    MechanismPlan,
    plan_action_sequence,
    walk_combat_events,
)


@dataclass
class PendingCombatAction:
    program: ActionProgram
    action_id: str
    started: float
    predicted: CombatWorldState
    resource_version: int


@dataclass(frozen=True)
class BattleRecommendation:
    program: ActionProgram | None = None
    plan: MechanismPlan | None = None
    reason: str = ""


class CombatRuntime:
    def __init__(self, catalog, *, epoch):
        self.catalog = catalog
        self.epoch = epoch
        self.pending: PendingCombatAction | None = None
        self.last_resource_sample: tuple[float, float] | None = None
        self._attempt = 0
        self._resource_version = 0
        self.observation_gaps: set[str] = set()

    def note_observation_gap(self, reason):
        self.observation_gaps.add(reason)

    def recommend_battle(self, now, *, max_sample_age=.5):
        """Select an immediately legal first battle action from a complete local model.

        Links and ultimates retain their HUD-driven dispatch. This comparison only
        covers battle actions against the existing single-target scene assumption.
        Observation gaps remain diagnostic evidence; they no longer globally disable
        the local battle search.
        """
        self.advance(now)
        if self.pending is not None:
            return BattleRecommendation(reason="cast_pending")
        if self.catalog.diagnostics or self.world.unresolved:
            return BattleRecommendation(reason="unresolved_model")
        sample = self.last_resource_sample
        if sample is None or not 0 <= now - sample[0] <= max_sample_age:
            return BattleRecommendation(reason="stale_sp_observation")
        actors = tuple(a for a, s in self.world.characters.items() if s.alive)
        if any(self.world.characters[a].panel is None or not self.catalog.candidates(a, "battle") for a in actors):
            return BattleRecommendation(reason="incomplete_team")
        programs = tuple(p for p in self.catalog.candidates(kind="battle") if p.actor in actors)
        if not programs or len(programs) > 16 or len({p.key for p in programs}) != len(programs):
            return BattleRecommendation(reason="unsupported_candidate_set")
        if any(event.unresolved for p in programs for event in walk_combat_events(p.events)):
            return BattleRecommendation(reason="unresolved_action")
        try:
            plan = plan_action_sequence(self.world, programs, depth=2, horizon=6, beam_width=8,
                                        max_expansions=144, timeout=.025)
        except CombatSearchLimit:
            return BattleRecommendation(reason="search_budget")
        if plan is None or not math.isfinite(plan.damage) or plan.damage <= 0:
            return BattleRecommendation(reason="no_priced_sequence")
        program = next(p for p in programs if p.key == plan.actions[0])
        if (program not in self.catalog.available(program.actor, "battle")
                or self.world.ready_at(program) > self.world.time
                or self.world.sp < max(program.sp_cost, program.gate or 0)
                or self.world.characters[program.actor].energy < program.energy_cost
                or plan.outcomes[0].before.time > self.world.time):
            return BattleRecommendation(reason="first_action_requires_wait")
        return BattleRecommendation(program, plan)

    @property
    def world(self):
        return self.catalog.world

    def advance(self, now):
        self.world.advance(max(self.world.time, now - self.epoch))

    def extend_catalog(self, catalog, actors, now):
        """Complete previously unidentified slots without restarting the battle."""
        actors = tuple(actors)
        world = self.world
        if set(world.characters) != set(catalog.world.characters) or any(
            actor not in world.characters or world.characters[actor].panel is not None for actor in actors
        ):
            raise ValueError("Catalog completion must identify existing unknown slots")
        self.advance(now)
        worlds = [world]
        if self.pending is not None:
            predicted = self.pending.predicted
            predicted.advance(max(predicted.time, now - self.epoch))
            worlds.append(predicted)
        from src.data.native_passive_runtime import activate_passive

        fresh = catalog.world
        for state in worlds:
            state.native_buff_tags.update(fresh.native_buff_tags)
            for actor in actors:
                character = state.characters[actor]
                template = fresh.characters[actor]
                character.panel = template.panel
                character.attributes.update(template.attributes)
                character.energy_cap = template.energy_cap
                state.register_damage_passives(actor, fresh.passive_modifiers.get(actor, ()))
                for (owner, _), program in fresh.native_programs.items():
                    if owner == actor:
                        state.register_native_program(program)
                for (owner, slot), key in fresh.native_skill_slots.items():
                    if owner == actor:
                        state.native_skill_slots.setdefault((actor, slot), key)
                for trigger, program in fresh.native_character_hooks:
                    if program.actor == actor:
                        state.register_character_hook(trigger, program)
                for passive in fresh.native_passives.values():
                    if passive.program.actor == actor:
                        activate_passive(state, passive)
                # Actions before portrait identification cannot be reconstructed
                # from the new catalog. Keep that uncertainty visible to pricing.
                if state.time > 0:
                    state.unresolved.add(f"Unobserved native history before slot identification: {actor}")
        self.catalog = replace(catalog, world=world)

    def observe_sp(self, sp, now):
        self.advance(now)
        if 0 <= sp <= 300:
            self._resource_version += 1
            self.last_resource_sample = (now, sp)
            self.world.observe_resources(sp=sp)

    def stage(self, program, now, *, energy_ready=False):
        self.pending = None
        self.advance(now)
        predicted = self.world.fork()
        if energy_ready:
            # A positive HUD-ready observation establishes this cast's budget.
            # It must not grant another character energy or survive a rejected cast.
            predicted.characters[program.actor].energy = max(
                predicted.characters[program.actor].energy, program.energy_cost)
        self._attempt += 1
        action_id = f"runtime:{self._attempt}:{program.actor}:{program.key}"
        if not predicted.start(program, action_id=action_id):
            return False
        self.pending = PendingCombatAction(program, action_id, now, predicted, self._resource_version)
        return True

    def observe_main_control(self, actor, now):
        self.advance(now)
        if actor not in self.world.characters or not self.world.characters[actor].alive:
            return False
        self.world.main_control = actor
        if self.pending is not None:
            self.pending.predicted.main_control = actor
        return True

    def confirm(self, actor, kind, now):
        pending = self.pending
        if pending is None or (pending.program.actor, pending.program.kind) != (actor, kind):
            return False
        predicted = pending.predicted
        sample = self.last_resource_sample
        if sample is not None and self._resource_version > pending.resource_version and sample[0] >= pending.started:
            # The sample already includes cast spend and refunds. Replace it;
            # replaying the payout on top would double credit resources.
            predicted.advance(max(predicted.time, sample[0] - self.epoch))
            predicted.observe_resources(sp=sample[1])
        predicted.advance(max(predicted.time, now - self.epoch))
        # Preserve the shared world's object identity for phase/state observers.
        self.world.__dict__.clear()
        self.world.__dict__.update(predicted.__dict__)
        self.pending = None
        return True

    def cancel(self, actor=None, kind=None):
        pending = self.pending
        if pending is not None and (actor is None or actor == pending.program.actor) and (
            kind is None or kind == pending.program.kind
        ):
            self.pending = None

    def disable_actor(self, actor, now):
        self.advance(now)
        self.cancel(actor)
        self.world.disable_actor(actor)
        if self.pending is not None:
            self.pending.predicted.disable_actor(actor)
