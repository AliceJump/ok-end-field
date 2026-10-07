"""Decode the selected Unity AnimationCurve wire layout without guessing Evaluate.

This is MemoryPack.AnimationCurveFormatter's 28-byte Unity Keyframe layout,
not Beyond.FAnimationCurve's 32-byte FKeyframe layout. Null curves, null key
arrays and empty key arrays remain distinct. Infinity tangents are preserved.
"""

import struct
from dataclasses import dataclass

from src.data.immutable_combat_value import ImmutableCombatValue

_KEYFRAME = struct.Struct("<ffffiff")
_HEADER = struct.Struct("<iii")


@dataclass(frozen=True)
class NativeCurveKeyframe(ImmutableCombatValue):
    time: float
    value: float
    in_tangent: float
    out_tangent: float
    weighted_mode: int
    in_weight: float
    out_weight: float


@dataclass(frozen=True)
class NativeAnimationCurve(ImmutableCombatValue):
    pre_wrap_mode: int
    post_wrap_mode: int
    keys: tuple[NativeCurveKeyframe, ...] | None


def decode_animation_curve(raw: bytes) -> NativeAnimationCurve | None:
    if raw == b"\xff":
        return None
    if len(raw) < 13 or raw[0] != 3:
        raise ValueError("Invalid native AnimationCurve header")
    pre, post, count = _HEADER.unpack_from(raw, 1)
    if count < -1 or len(raw) != 13 + _KEYFRAME.size * max(0, count):
        raise ValueError("Invalid native AnimationCurve key array extent")
    keys = None if count == -1 else tuple(
        NativeCurveKeyframe(*_KEYFRAME.unpack_from(raw, 13 + index * _KEYFRAME.size)) for index in range(count)
    )
    return NativeAnimationCurve(pre, post, keys)


def encode_animation_curve(curve: NativeAnimationCurve | None) -> bytes:
    if curve is None:
        return b"\xff"
    count = -1 if curve.keys is None else len(curve.keys)
    result = b"\x03" + _HEADER.pack(curve.pre_wrap_mode, curve.post_wrap_mode, count)
    return result + b"".join(_KEYFRAME.pack(key.time, key.value, key.in_tangent, key.out_tangent,
                                          key.weighted_mode, key.in_weight, key.out_weight)
                             for key in curve.keys or ())


def decode_curve_profile(profile) -> NativeAnimationCurve | None:
    if profile["profile"] != "curve_profile":
        raise ValueError("Unknown native curve profile")
    raw = bytes.fromhex(profile["rawHex"])
    if len(raw) != profile["bytes"]:
        raise ValueError("Native curve profile byte count mismatch")
    curve = decode_animation_curve(raw)
    if encode_animation_curve(curve) != raw:
        raise ValueError("Native curve profile does not round-trip exactly")
    return curve
