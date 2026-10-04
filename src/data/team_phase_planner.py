"""Team-level phase planning for finite burst windows.

This module deliberately does not extend the steady-state DPS search. It models
shared SP, multiple participating slots, private mechanic transitions and a
finite action sequence so the runtime can distinguish:

    PREP -> CHARGE -> BURST_READY -> BURST -> RECOVER

A BurstPlan may contain actions from several operators. min_start_sp is derived
from the complete action sequence (including refunds) rather than being
hard-coded to 300 SP.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from src.data.character_mechanics import CharacterMechanic
from src.data.hidden_state_expectation import (
    ExpectedDamage,
    HiddenStateExpectation,
    load_damage_envelopes,
)


class CombatPhase(str, Enum):
    NORMAL = "normal"
    PREP = "prep"
    CHARGE = "charge"
    BURST_READY = "burst_ready"
    BURST = "burst"
    RECOVER = "recover"


@dataclass(frozen=True)
class BurstAction:
    slot: str
    actor: str
    kind: str
    label: str
    sp_gate: float = 0.0
    sp_cost: float = 0.0
    sp_refund: float = 0.0
    duration: float = 0.0
    expected_damage: float | None = None
    damage_low: float | None = None
    damage_high: float | None = None
    full_probability: float | None = None
    expected_fraction: float | None = None
    damage_basis: str | None = None
    repeats: int = 1
    requires: tuple[str, ...] = ()
    consumes: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()

    @property
    def net_sp_cost(self) -> float:
        return self.sp_cost - self.sp_refund


@dataclass(frozen=True)
class BurstPlan:
    key: str
    owner_slot: str | None
    actions: tuple[BurstAction, ...]
    min_start_sp: float
    reserve_floor: float
    expected_end_sp: float
    expected_damage: float | None
    expected_duration: float
    runtime_executable: bool = False
    evidence: tuple[str, ...] = ()

    @property
    def participants(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(action.slot for action in self.actions))

    @property
    def is_team_plan(self) -> bool:
        return len(self.participants) > 1


@dataclass
class TeamCombatState:
    sp: float = -1.0
    phase: CombatPhase = CombatPhase.NORMAL
    burst_key: str | None = None
    action_index: int = 0
    disabled_slots: set[str] = field(default_factory=set)
    private_resources: dict[str, dict[str, float]] = field(default_factory=dict)
    statuses: set[str] = field(default_factory=set)


def derive_sp_budget(actions: tuple[BurstAction, ...]) -> tuple[float, float]:
    """Return (minimum starting SP, ending SP at that minimum).

    Natural recovery is intentionally excluded here. The burst reserve is a
    conservative guarantee: if the sequence fits without passive recovery, any
    in-window natural recovery is slack rather than a hidden requirement.
    """
    required = 0.0
    offset = 0.0
    for action in actions:
        gate = max(0.0, float(action.sp_gate))
        cost = max(0.0, float(action.sp_cost))
        refund = max(0.0, float(action.sp_refund))
        for _ in range(max(1, int(action.repeats))):
            required = max(required, gate - offset)
            offset += refund - cost
    required = max(0.0, min(300.0, required))
    return required, max(0.0, min(300.0, required + offset))


def make_burst_plan(
    key: str,
    actions: tuple[BurstAction, ...],
    *,
    owner_slot: str | None = None,
    reserve_floor: float | None = None,
    runtime_executable: bool = False,
    evidence: tuple[str, ...] = (),
) -> BurstPlan:
    if not actions:
        raise ValueError("burst plan requires at least one action")
    minimum, ending = derive_sp_budget(actions)
    known_damage = [action.expected_damage for action in actions]
    damage = sum(known_damage) if all(value is not None for value in known_damage) else None
    duration = sum(max(0.0, action.duration) * max(1, int(action.repeats)) for action in actions)
    reserve = minimum if reserve_floor is None else max(0.0, min(300.0, reserve_floor))
    return BurstPlan(
        key=key,
        owner_slot=owner_slot,
        actions=actions,
        min_start_sp=minimum,
        reserve_floor=reserve,
        expected_end_sp=ending,
        expected_damage=damage,
        expected_duration=duration,
        runtime_executable=runtime_executable,
        evidence=evidence,
    )


def _transition_action(
    slot: str,
    actor: str,
    transition,
    duration: float = 0.0,
    expected: ExpectedDamage | None = None,
) -> BurstAction:
    return BurstAction(
        slot=slot,
        actor=actor,
        kind=transition.action,
        label=transition.phase,
        sp_gate=float(transition.sp_gate or 0),
        sp_cost=float(transition.sp_cost or 0),
        sp_refund=float(transition.sp_refund or 0),
        duration=max(0.0, duration),
        expected_damage=None if expected is None else expected.expected,
        damage_low=None if expected is None else expected.low,
        damage_high=None if expected is None else expected.high,
        full_probability=None if expected is None else expected.full_probability,
        expected_fraction=None if expected is None else expected.expected_fraction,
        damage_basis=None if expected is None else expected.basis,
        repeats=max(1, int(transition.repeats)),
        requires=tuple(transition.requires),
        consumes=tuple(transition.consumes),
        produces=tuple(transition.produces),
    )


def build_team_burst_plans(
    team: list[str],
    mechanics: dict[str, CharacterMechanic],
    store,
) -> tuple[BurstPlan, ...]:
    """Build finite-window plans with expected, not assumed-full, hidden damage."""
    plans = []
    envelopes = load_damage_envelopes()
    kind_name = {"battle": "战技", "link": "连携技", "ult": "终结技"}

    def build_actions(slot, actor, transitions, profiles=()):
        belief = HiddenStateExpectation(team, mechanics)
        actions = []
        for index, transition in enumerate(transitions):
            envelope = envelopes.get((actor, kind_name.get(transition.action, "")))
            estimate = belief.estimate_damage(
                actor,
                kind_name.get(transition.action, ""),
                envelope,
                transition,
            )
            duration = profiles[index].handoff if index < len(profiles) else 0.0
            action = _transition_action(slot, actor, transition, duration, estimate)
            actions.append(action)
            for _ in range(max(1, action.repeats)):
                belief.apply_action(action)
        return tuple(actions)

    for index, actor in enumerate(team, 1):
        if actor == "?":
            continue
        slot = str(index)
        mechanic = mechanics.get(actor)
        if mechanic is None:
            continue

        if mechanic.archetype == "multi_stage_battle":
            transitions = tuple(t for t in mechanic.transitions if t.action == "battle")
            profiles = store.battle_phase_profiles(actor)
            if transitions and len(transitions) == len(profiles):
                actions = build_actions(slot, actor, transitions, profiles)
                plans.append(
                    make_burst_plan(
                        f"{slot}:{mechanic.key}:battle-chain",
                        actions,
                        owner_slot=slot,
                        runtime_executable=True,
                        evidence=mechanic.evidence,
                    )
                )
            continue

        if mechanic.archetype == "main_control_attack_channel":
            transitions = tuple(
                transition
                for transition in mechanic.transitions
                if transition.action in {"battle", "normal", "link", "ult"}
            )
            actions = build_actions(slot, actor, transitions)
            if actions:
                plans.append(
                    make_burst_plan(
                        f"{slot}:{mechanic.key}:hover-chain",
                        actions,
                        owner_slot=slot,
                        runtime_executable=False,
                        evidence=mechanic.evidence,
                    )
                )
            continue

        if mechanic.archetype == "consume_status_build_stack_burst":
            ult = next((t for t in mechanic.transitions if t.action == "ult"), None)
            free = next((t for t in mechanic.transitions if t.phase.startswith("天理合真首次")), None)
            normal = next(
                (t for t in mechanic.transitions if t.action == "battle" and t is not free),
                None,
            )
            transitions = tuple(t for t in (ult, free, normal) if t is not None)
            actions = build_actions(slot, actor, transitions)
            if actions:
                plans.append(
                    make_burst_plan(
                        f"{slot}:{mechanic.key}:ult-window",
                        actions,
                        owner_slot=slot,
                        runtime_executable=False,
                        evidence=mechanic.evidence,
                    )
                )
            continue

        if mechanic.archetype == "consume_attachment_freeze_control":
            transitions = tuple(
                transition
                for kind in ("battle", "link", "ult")
                for transition in mechanic.transitions
                if transition.action == kind
            )
            actions = build_actions(slot, actor, transitions)
            if actions:
                plans.append(
                    make_burst_plan(
                        f"{slot}:{mechanic.key}:freeze-window",
                        actions,
                        owner_slot=slot,
                        runtime_executable=False,
                        evidence=mechanic.evidence,
                    )
                )

    return tuple(plans)


class TeamPhasePlanner:
    """Small runtime state machine around one selected finite burst plan."""

    def __init__(self):
        self.state = TeamCombatState()
        self.plans: tuple[BurstPlan, ...] = ()
        self.active_plan: BurstPlan | None = None

    def configure(
        self,
        plans: tuple[BurstPlan, ...],
        *,
        preferred_slots: tuple[str, ...] = (),
    ) -> BurstPlan | None:
        self.plans = plans
        self.active_plan = None
        self.state = TeamCombatState()

        executable = [plan for plan in plans if plan.runtime_executable]
        if preferred_slots:
            # The existing damage rotation is only a provisional core-role
            # signal. Do not let a support character with a known mechanic
            # hijack the whole team's shared SP reserve merely because it has
            # an executable state machine.
            primary = preferred_slots[0]
            executable = [
                plan
                for plan in executable
                if plan.owner_slot == primary or (plan.owner_slot is None and primary in plan.participants)
            ]
            executable.sort(key=lambda plan: (-plan.min_start_sp, plan.key))
        else:
            executable.sort(key=lambda plan: (-plan.min_start_sp, plan.key))

        if executable:
            self.active_plan = executable[0]
            self.state.burst_key = self.active_plan.key
            self.state.phase = CombatPhase.CHARGE
        return self.active_plan

    @property
    def next_action(self) -> BurstAction | None:
        if self.active_plan is None:
            return None
        if self.state.action_index >= len(self.active_plan.actions):
            return None
        return self.active_plan.actions[self.state.action_index]

    @property
    def remaining_actions(self) -> tuple[BurstAction, ...]:
        if self.active_plan is None:
            return ()
        return self.active_plan.actions[self.state.action_index :]

    @property
    def target_sp(self) -> float:
        actions = self.remaining_actions
        return derive_sp_budget(actions)[0] if actions else 0.0

    @property
    def reserve_floor(self) -> float:
        if self.active_plan is None:
            return 0.0
        if self.state.phase in {CombatPhase.PREP, CombatPhase.CHARGE, CombatPhase.BURST_READY}:
            return self.target_sp
        return 0.0

    def observe_sp(self, sp: float) -> tuple[CombatPhase, CombatPhase]:
        old = self.state.phase
        self.state.sp = sp
        if self.active_plan is None or sp < 0:
            return old, self.state.phase

        if self.state.phase in {CombatPhase.NORMAL, CombatPhase.PREP, CombatPhase.CHARGE}:
            self.state.phase = CombatPhase.BURST_READY if sp >= self.target_sp else CombatPhase.CHARGE
        elif self.state.phase == CombatPhase.RECOVER:
            self.state.action_index = 0
            self.state.phase = CombatPhase.BURST_READY if sp >= self.target_sp else CombatPhase.CHARGE
        return old, self.state.phase

    def can_spend(
        self,
        slot: str,
        kind: str,
        current_sp: float,
        net_sp_cost: float,
    ) -> bool:
        if self.active_plan is None or current_sp < 0:
            return True
        next_action = self.next_action
        is_next = next_action is not None and next_action.slot == slot and next_action.kind == kind

        if self.state.phase == CombatPhase.CHARGE:
            if net_sp_cost <= 0:
                return True
            if is_next:
                return False
            return current_sp - net_sp_cost >= self.reserve_floor

        if self.state.phase == CombatPhase.BURST_READY:
            return is_next or net_sp_cost <= 0

        if self.state.phase == CombatPhase.BURST:
            return is_next or net_sp_cost <= 0

        return True

    def start_if_ready(self, slot: str, kind: str) -> bool:
        action = self.next_action
        if (
            self.active_plan is None
            or self.state.phase != CombatPhase.BURST_READY
            or action is None
            or action.slot != slot
            or action.kind != kind
        ):
            return False
        self.state.phase = CombatPhase.BURST
        return True

    def observe_action(self, slot: str, kind: str) -> tuple[CombatPhase, CombatPhase]:
        old = self.state.phase
        action = self.next_action
        if (
            self.active_plan is None
            or action is None
            or action.slot != slot
            or action.kind != kind
            or self.state.phase not in {CombatPhase.BURST_READY, CombatPhase.BURST}
        ):
            return old, self.state.phase

        self.state.phase = CombatPhase.BURST
        self.state.action_index += 1
        if self.state.action_index >= len(self.active_plan.actions):
            self.state.phase = CombatPhase.RECOVER
        return old, self.state.phase

    def reset_cycle(self) -> None:
        if self.active_plan is None:
            self.state.phase = CombatPhase.NORMAL
            self.state.action_index = 0
            return
        self.state.action_index = 0
        self.state.phase = (
            CombatPhase.BURST_READY if self.state.sp >= 0 and self.state.sp >= self.target_sp else CombatPhase.CHARGE
        )

    def seek_action(self, slot: str, kind: str, label: str | None = None) -> bool:
        """Synchronize a native replacement/shortcut to a later burst action."""
        if self.active_plan is None:
            return False
        for index, action in enumerate(self.active_plan.actions):
            if action.slot != slot or action.kind != kind:
                continue
            if label is not None and action.label != label:
                continue
            self.state.action_index = index
            if self.state.sp >= 0:
                self.state.phase = CombatPhase.BURST_READY if self.state.sp >= self.target_sp else CombatPhase.CHARGE
            return True
        return False

    def disable_slots(self, slots: set[str]) -> bool:
        self.state.disabled_slots.update(slots)
        if self.active_plan is None:
            return False
        if any(slot in self.state.disabled_slots for slot in self.active_plan.participants):
            self.active_plan = None
            self.state.phase = CombatPhase.NORMAL
            self.state.burst_key = None
            self.state.action_index = 0
            return True
        return False
