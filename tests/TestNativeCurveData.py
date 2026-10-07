"""Pin structural curve fields and exact bytes; do not imply runtime interpolation."""

import copy
import math
import struct
import unittest

from src.data.native_action_program import _nodes
from src.data.native_curve_data import (
    NativeAnimationCurve,
    NativeCurveKeyframe,
    decode_animation_curve,
    decode_curve_profile,
    encode_animation_curve,
)
from src.data.native_gameplay import native_record
from src.data.skill_timing import SkillTimingStore


class TestNativeCurveData(unittest.TestCase):
    def test_null_curve_null_keys_and_empty_keys_are_distinct(self):
        for raw, expected in ((b"\xff", None), (b"\x03" + struct.pack("<iii", 8, 4, -1), NativeAnimationCurve(8, 4, None)),
                              (b"\x03" + struct.pack("<iii", 4, 8, 0), NativeAnimationCurve(4, 8, ()))):
            with self.subTest(raw=raw):
                self.assertEqual(decode_animation_curve(raw), expected)
                self.assertEqual(encode_animation_curve(expected), raw)

    def test_native_offsets_preserve_wrap_order_weights_tangents_and_signed_zero(self):
        raw = b"\x03" + struct.pack("<iii", 2, 4, 1) + struct.pack("<ffffiff", -0.0, 7, -2, 3, 3, .25, .75)
        curve = decode_animation_curve(raw)
        self.assertEqual((curve.pre_wrap_mode, curve.post_wrap_mode), (2, 4))
        self.assertEqual(curve.keys, (NativeCurveKeyframe(-0.0, 7, -2, 3, 3, .25, .75),))
        self.assertEqual(math.copysign(1, curve.keys[0].time), -1)
        self.assertEqual(encode_animation_curve(curve), raw)
        self.assertIs(copy.deepcopy(curve), curve)

    def test_infinity_tangents_are_preserved_without_treating_them_as_zero(self):
        raw = b"\x03" + struct.pack("<iii", 8, 8, 1) + struct.pack("<ffffiff", 0, 1, math.inf, -math.inf, 0, 0, 0)
        curve = decode_animation_curve(raw)
        self.assertEqual((curve.keys[0].in_tangent, curve.keys[0].out_tangent), (math.inf, -math.inf))
        self.assertEqual(encode_animation_curve(curve), raw)

    def test_unknown_wrap_modes_and_unsorted_keys_are_preserved_as_data(self):
        raw = b"\x03" + struct.pack("<iii", 123, 456, 2) + b"".join(
            struct.pack("<ffffiff", time, 1, 0, 0, 9, 0, 0) for time in (3, 1))
        curve = decode_animation_curve(raw)
        self.assertEqual([key.time for key in curve.keys], [3, 1])
        self.assertEqual((curve.pre_wrap_mode, curve.post_wrap_mode, curve.keys[0].weighted_mode), (123, 456, 9))
        self.assertEqual(encode_animation_curve(curve), raw)

    def test_truncation_invalid_counts_and_fkeyframe_layout_are_rejected(self):
        cases = (b"", b"\xff\x00", b"\x02" + bytes(12), b"\x03" + struct.pack("<iii", 8, 8, -2),
                 b"\x03" + struct.pack("<iii", 8, 8, 1000000000),
                 b"\x03" + struct.pack("<iii", 8, 8, 1) + bytes(27),
                 b"\x03" + struct.pack("<iii", 8, 8, 1) + bytes(32))
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                decode_animation_curve(raw)

    def test_profile_requires_exact_kind_byte_count_and_round_trip(self):
        profile = {"profile": "curve_profile", "bytes": 1, "rawHex": "ff"}
        self.assertIsNone(decode_curve_profile(profile))
        for changed in ({**profile, "profile": "fAnimationCurve"}, {**profile, "bytes": 2},
                        {**profile, "rawHex": "xyz"}):
            with self.subTest(profile=changed), self.assertRaises(ValueError):
                decode_curve_profile(changed)

    def test_real_endministrator_custom_curve_is_not_the_linear_template(self):
        record = native_record(SkillTimingStore(), "chr_0002_endminm_combo_skill")
        bodies = [node["$value"] for node in _nodes(record)
                  if node["$type"].endswith("CurveEvaluateFloat+Data")]
        body = next(value for value in bodies if value["customCurve"]["bytes"] == 153)
        self.assertTrue(body["useCustomCurve"])
        self.assertEqual(body["curveTemplate"], "Linear")
        curve = decode_curve_profile(body["customCurve"])
        self.assertEqual([key.time for key in curve.keys], [0, 4, 6, 8, 50])
        self.assertTrue(any(math.isinf(key.out_tangent) for key in curve.keys))
        self.assertEqual(encode_animation_curve(curve).hex(), body["customCurve"]["rawHex"])

