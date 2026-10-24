"""Immutable accepted/predicted held-normal windows for the burst search."""

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class NormalProjection:
    owner: str
    cast_at: float
    input_at: float
    layers: int = 0
    layers_until: float | None = None


@dataclass(frozen=True)
class HeldNormalState:
    main: str | None = None
    windows: tuple[NormalProjection, ...] = ()
    enabled: bool = False

    def window(self, actor):
        return next((w for w in self.windows if w.owner == actor), None)

    def keep(self, actors):
        return HeldNormalState(self.main if self.main in actors else None,
                               tuple(w for w in self.windows if w.owner in actors), self.enabled)

    def expire(self, models, now):
        return replace(self, windows=tuple(w for w in self.windows if now <= w.cast_at + models[w.owner].activation
                                           + models[w.owner].duration + models[w.owner].final_delay))

    def relative(self, now):
        return (self.main, self.enabled, tuple((w.owner, w.cast_at-now, w.input_at-now, w.layers,
                                  None if w.layers_until is None else w.layers_until-now) for w in self.windows))

    def command(self, models, actor, kind, started, resumed):
        windows = []
        for projection in self.windows:
            model = models[projection.owner]
            past = [a for a in model.attacks(projection.cast_at, input_at=projection.input_at,
                    initial_layers=projection.layers, initial_layers_until=projection.layers_until)
                    if a.kind == "shot" and a.projected_at < started]
            count = past[-1].layers if past else projection.layers
            expires = past[-1].layer_expires_at if past else projection.layers_until
            if expires is not None and resumed >= expires:
                count, expires = 0, None
            windows.append(replace(projection, input_at=max(resumed, projection.input_at),
                                   layers=count, layers_until=expires))
        main = self.main
        if kind == "ult" and actor in models:
            model = models[actor]
            windows = [w for w in windows if w.owner != actor]
            windows.append(NormalProjection(actor, started, max(resumed, started+model.first_input)))
            main = actor  # This reviewed ultimate explicitly switches to its owner.
        return HeldNormalState(main, tuple(windows), self.enabled)


def model_price(planner, model, actor, projection, started, end, bonuses, mechanism_state, *, frozen=None):
    panel = planner.rows[actor]["panel"]
    bonus = (panel["damage_bonus"].get("all", 0) + panel["damage_bonus"].get("all_skill", 0)
             + panel["damage_bonus"].get("寒冷", 0) + panel["damage_bonus"].get("normal", 0))
    attack = (panel["attack_white"]*(1+panel["attack_percent"])+panel["attack_flat"])*panel["attribute_factor"]
    amplification = panel["amplification"].get("寒冷", 0)
    cuts = sorted({value for b in bonuses for value in (b.starts_at, b.expires_at)})

    def resolve(event, frozen):
        rate = min(1, max(0, panel["crit_rate"]+event.layers*model.crit_per_layer
                         + panel.get("crit_rate_bonus", {}).get("normal", 0)))
        critical = panel["crit_damage"] + (model.full_crit_damage if event.layers == model.maximum else 0)
        if frozen:
            critical += model.frozen_talent_crit_damage
        value = attack*event.multiplier*(1+bonus)*(1+amplification)*(1+rate*critical)
        return planner._price_value(actor, event.projected_at, "寒冷", {"normal"}, bonus*100, value, bonuses)

    return model.price(projection.cast_at, resolve, cuts=cuts, from_time=started, until=end,
                       input_at=projection.input_at, initial_layers=projection.layers,
                       initial_layers_until=projection.layers_until, frozen=frozen)
