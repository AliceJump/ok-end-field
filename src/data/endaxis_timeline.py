"""Decode EndAxis share codes and bridge them to OKEF rotation actions.

The importer intentionally keeps EndAxis parsing separate from combat execution.
Current legacy rotation compatibility maps only the action kinds MaaEnd also
consumes today: ``skill`` / ``battleSkill`` and ``ultimate``. Other EndAxis
action kinds are ignored by this bridge rather than guessed into the wrong OKEF
semantics; the structured action model can be extended by a richer executor.
"""

from __future__ import annotations

import base64
import gzip
import io
import json
import math
import re
from dataclasses import dataclass

TIMELINE_FRAME_BASE = 300
TIMELINE_FPS = 60.0
_MAX_PLAIN_SIZE = 5 << 20
_ENDAXIS_SPEC_RE = re.compile(r"^endaxis(?:\[([^\]]+)\])?:(.*)$", re.IGNORECASE | re.DOTALL)


class EndAxisTimelineError(ValueError):
    """Raised when an EndAxis share code or scenario is invalid."""


@dataclass(frozen=True)
class EndAxisAction:
    kind: str
    slot: int
    start_frame: int
    duration_frames: int
    name: str = ""
    action_id: str = ""

    @property
    def start_seconds(self) -> float:
        return max(0.0, (self.start_frame - TIMELINE_FRAME_BASE) / TIMELINE_FPS)

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.duration_frames / TIMELINE_FPS)


@dataclass(frozen=True)
class EndAxisScenario:
    scenario_id: str
    name: str
    actions: tuple[EndAxisAction, ...]


def parse_endaxis_rotation_spec(raw: str) -> tuple[str | None, str] | None:
    """Parse ``endaxis:<code>`` or ``endaxis[scenario]:<code>``.

    The optional selector matches either the EndAxis scenario id or its exact
    display name. Returning ``None`` means the value is an ordinary OKEF
    rotation string and must be handled by the existing parser unchanged.
    """
    if not isinstance(raw, str):
        return None
    match = _ENDAXIS_SPEC_RE.match(raw.strip())
    if not match:
        return None
    selector = (match.group(1) or "").strip() or None
    code = match.group(2).strip()
    if not code:
        raise EndAxisTimelineError("EndAxis 数据码为空")
    return selector, code


def decode_endaxis_share_code(code: str) -> dict:
    """Decode EndAxis ``base64url(gzip(JSON))`` share data with a size cap."""
    trimmed = str(code or "").strip().rstrip("=")
    if not trimmed:
        raise EndAxisTimelineError("EndAxis 数据码为空")

    try:
        padding = "=" * ((4 - len(trimmed) % 4) % 4)
        compressed = base64.urlsafe_b64decode((trimmed + padding).encode("ascii"))
    except (UnicodeEncodeError, ValueError) as exc:
        raise EndAxisTimelineError(f"EndAxis base64url 解码失败: {exc}") from exc

    try:
        with gzip.GzipFile(fileobj=io.BytesIO(compressed), mode="rb") as stream:
            plain = stream.read(_MAX_PLAIN_SIZE + 1)
    except (OSError, EOFError) as exc:
        raise EndAxisTimelineError(f"EndAxis gzip 解压失败: {exc}") from exc

    if len(plain) > _MAX_PLAIN_SIZE:
        raise EndAxisTimelineError(f"EndAxis 数据超过 {_MAX_PLAIN_SIZE} 字节限制")

    try:
        payload = json.loads(plain.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EndAxisTimelineError(f"EndAxis JSON 解析失败: {exc}") from exc
    if not isinstance(payload, dict):
        raise EndAxisTimelineError("EndAxis 根节点必须是对象")
    return payload


def _normalize_action(raw: dict, slot: int) -> EndAxisAction | None:
    action_type = str(raw.get("type") or "")
    if action_type in {"skill", "battleSkill"}:
        kind = "skill"
    elif action_type == "ultimate":
        kind = "ultimate"
    else:
        return None

    try:
        raw_start = raw.get("startTime", TIMELINE_FRAME_BASE)
        raw_duration = raw.get("duration", 0)
        if not math.isfinite(float(raw_start)) or not math.isfinite(float(raw_duration)):
            raise ValueError("non-finite timing value")
        start_frame = int(raw_start)
        duration_frames = max(0, int(raw_duration))
    except (TypeError, ValueError, OverflowError) as exc:
        raise EndAxisTimelineError(f"EndAxis 动作时间字段非法: {raw!r}") from exc

    return EndAxisAction(
        kind=kind,
        slot=slot,
        start_frame=start_frame,
        duration_frames=duration_frames,
        name=str(raw.get("name") or ""),
        action_id=str(raw.get("id") or ""),
    )


def parse_endaxis_scenarios(code: str) -> tuple[EndAxisScenario, ...]:
    """Decode all scenarios while retaining only currently executable actions."""
    payload = decode_endaxis_share_code(code)
    scenario_list = payload.get("scenarioList")
    if not isinstance(scenario_list, list):
        raise EndAxisTimelineError("EndAxis 数据缺少 scenarioList")

    scenarios: list[EndAxisScenario] = []
    for raw_scenario in scenario_list:
        if not isinstance(raw_scenario, dict):
            continue
        data = raw_scenario.get("data")
        tracks = data.get("tracks") if isinstance(data, dict) else None
        if not isinstance(tracks, list):
            tracks = []

        indexed_actions: list[tuple[int, EndAxisAction]] = []
        order = 0
        for track_index, track in enumerate(tracks[:4]):
            if not isinstance(track, dict):
                continue
            raw_actions = track.get("actions")
            if not isinstance(raw_actions, list):
                continue
            for raw_action in raw_actions:
                if not isinstance(raw_action, dict):
                    continue
                action = _normalize_action(raw_action, track_index + 1)
                if action is not None:
                    indexed_actions.append((order, action))
                order += 1

        indexed_actions.sort(key=lambda item: (item[1].start_frame, item[0]))
        scenarios.append(
            EndAxisScenario(
                scenario_id=str(raw_scenario.get("id") or ""),
                name=str(raw_scenario.get("name") or ""),
                actions=tuple(action for _, action in indexed_actions),
            )
        )
    return tuple(scenarios)


def select_endaxis_scenario(
    scenarios: tuple[EndAxisScenario, ...],
    selector: str | None = None,
) -> EndAxisScenario:
    """Select by exact id/name, otherwise choose the first executable scenario."""
    if selector:
        for scenario in scenarios:
            if selector in {scenario.scenario_id, scenario.name}:
                if not scenario.actions:
                    raise EndAxisTimelineError(f"EndAxis 场景 {selector!r} 没有可执行战技/终结技")
                return scenario
        raise EndAxisTimelineError(f"找不到 EndAxis 场景 {selector!r}")

    for scenario in scenarios:
        if scenario.actions:
            return scenario
    raise EndAxisTimelineError("EndAxis 数据中没有可执行战技/终结技场景")


def _format_seconds(seconds: float) -> str:
    value = round(max(0.0, float(seconds)), 6)
    if value.is_integer():
        return str(int(value))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def endaxis_scenario_to_rotation_tokens(scenario: EndAxisScenario) -> list[str]:
    """Approximate one EndAxis scenario with the existing sequential token DSL.

    EndAxis timestamps are absolute timeline positions, while the legacy OKEF
    DSL is sequential. This bridge therefore preserves start-time *gaps* as
    ``sleep_N`` tokens. It is format compatibility, not a claim of frame-exact
    execution; the richer timed scheduler can later consume ``EndAxisAction``
    directly without changing the decoder.
    """
    tokens: list[str] = []
    previous_start = 0.0
    for action in scenario.actions:
        start = action.start_seconds
        gap = max(0.0, start - previous_start)
        if gap > 1e-6:
            tokens.append(f"sleep_{_format_seconds(gap)}")
        if action.kind == "ultimate":
            tokens.append(f"ult_{action.slot}")
        else:
            tokens.append(str(action.slot))
        previous_start = start
    return tokens


def endaxis_spec_to_rotation_tokens(raw: str) -> tuple[EndAxisScenario, list[str]] | None:
    """Convert an ``endaxis...`` rotation spec, or return ``None`` for legacy input."""
    parsed = parse_endaxis_rotation_spec(raw)
    if parsed is None:
        return None
    selector, code = parsed
    scenario = select_endaxis_scenario(parse_endaxis_scenarios(code), selector)
    return scenario, endaxis_scenario_to_rotation_tokens(scenario)
