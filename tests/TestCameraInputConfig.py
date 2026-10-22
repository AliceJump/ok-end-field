"""Verify default camera gains, orbit geometry and saved-setting fallbacks."""

import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.core import camera_input_config as camera


class TestCameraInputConfig(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = json.loads(camera._PROFILE_PATH.read_text(encoding="utf-8"))

    def yaw(self, width=1920, speed=0.0, reverse=False, **kwargs):
        settings = {"speed_x": speed, "reverse_x": reverse}
        with (
            patch.object(camera, "_load_profile", return_value=self.profile),
            patch.object(camera, "read_saved_camera_settings", return_value=(settings, ())),
        ):
            return camera.get_default_camera_yaw_ratio(width, **kwargs)

    def vertical(self, height=1080, speed=0.0, reverse=False):
        settings = {"speed_y": speed, "reverse_y": reverse}
        with (
            patch.object(camera, "_load_profile", return_value=self.profile),
            patch.object(camera, "read_saved_camera_settings", return_value=(settings, ())),
        ):
            return camera.get_default_camera_vertical_track(height)

    def test_yaw_width_scaling(self):
        for width, expected in ((1920, 0.084375), (2560, 0.06328125), (3840, 0.0421875)):
            with self.subTest(width=width):
                self.assertAlmostEqual(self.yaw(width), expected, places=12)

    def test_sensitivity_endpoints_and_interior(self):
        for speed, factor in ((-5, 0.5), (0, 1), (5, 1.5), (2, 1.2)):
            with self.subTest(speed=speed):
                self.assertAlmostEqual(self.yaw(speed=speed), 0.084375 * factor, places=9)

    def test_reverse_and_minimap_signs(self):
        self.assertAlmostEqual(self.yaw(reverse=True), -0.084375)
        self.assertAlmostEqual(self.yaw(minimap=True), -0.084375)
        self.assertAlmostEqual(self.yaw(reverse=True, minimap=True), 0.084375)

    def test_vertical_track_units_and_height_scaling(self):
        first = self.vertical()
        self.assertAlmostEqual(first["linear_track_units_per_received_y"], 0.0002916666666666667, places=14)
        self.assertAlmostEqual(
            self.vertical(2160)["linear_track_units_per_received_y"],
            first["linear_track_units_per_received_y"] / 2,
            places=14,
        )
        self.assertEqual((first["min_vertical_value"], first["max_vertical_value"]), (0, 1))
        self.assertEqual(first["vertical_tween_speed"], 16)
        self.assertAlmostEqual(first["acceleration_threshold_received_counts_per_second"], 3428571.4285714286)

    def test_vertical_geometry_and_inverse_endpoint(self):
        track = self.vertical()
        samples = track["geometry_samples"]
        self.assertAlmostEqual(samples[0]["arm_height"], -2)
        self.assertAlmostEqual(samples[0]["distance"], 0.2)
        self.assertAlmostEqual(samples[0]["nominal_pitch_degrees"], -84.28940686, places=6)
        self.assertAlmostEqual(samples[-1]["nominal_pitch_degrees"], 55.713123, places=5)
        self.assertAlmostEqual(track["pitch_to_track_range"]["max_track"], 0.99)

    def test_vertical_sensitivity_and_reverse(self):
        ratio = self.vertical()["linear_track_units_per_received_y"]
        self.assertAlmostEqual(self.vertical(speed=5)["linear_track_units_per_received_y"], ratio * 1.5)
        self.assertAlmostEqual(self.vertical(reverse=True)["linear_track_units_per_received_y"], -ratio)

    def test_invalid_screen_size_or_speed_rejected(self):
        for value in (0, -1, True, 1920.0):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.yaw(value)
                with self.assertRaises(ValueError):
                    self.vertical(value)
        for speed in (-5.1, 5.1, float("nan"), float("inf")):
            with self.subTest(speed=speed), self.assertRaises(ValueError):
                self.yaw(speed=speed)

    def test_registry_float_bits_and_fresh_reads(self):
        import winreg

        bits = struct.unpack("<I", struct.pack("<f", 2.5))[0]
        with (
            patch.object(winreg, "OpenKey", return_value=MagicMock()),
            patch.object(
                winreg,
                "QueryValueEx",
                side_effect=[
                    (bits, winreg.REG_DWORD),
                    (1, winreg.REG_DWORD),
                    (0, winreg.REG_DWORD),
                    (0, winreg.REG_DWORD),
                    (0, winreg.REG_DWORD),
                    (0, winreg.REG_DWORD),
                    (0, winreg.REG_DWORD),
                    (0, winreg.REG_DWORD),
                ],
            ),
        ):
            first, fallback = camera.read_saved_camera_settings()
            self.assertEqual((first["speed_x"], first["reverse_x"]), (2.5, True))
            self.assertEqual(fallback, ())
            second, fallback = camera.read_saved_camera_settings()
            self.assertEqual((second["speed_x"], second["reverse_x"]), (0.0, False))
            self.assertEqual(fallback, ())

    def test_reads_both_axes_registry_keys(self):
        import winreg

        with (
            patch.object(winreg, "OpenKey", return_value=MagicMock()),
            patch.object(winreg, "QueryValueEx", return_value=(0, winreg.REG_DWORD)) as query,
        ):
            values, fallback = camera.read_saved_camera_settings()
            self.assertEqual((values["speed_y"], values["reverse_y"]), (0, False))
            self.assertEqual(fallback, ())
            self.assertEqual(
                [c.args[1] for c in query.call_args_list],
                [
                    "controller_camera_speed_x_h2607638912",
                    "controller_camera_reverse_x_h1335373511",
                    "controller_camera_speed_y_h2607638913",
                    "controller_camera_reverse_y_h1335373510",
                ],
            )

    def test_invalid_saved_field_preserves_other_settings(self):
        import winreg

        for speed in ((0, winreg.REG_SZ), (0x7FC00000, winreg.REG_DWORD), (0x7F800000, winreg.REG_DWORD)):
            with (
                patch.object(winreg, "OpenKey", return_value=MagicMock()),
                patch.object(
                    winreg,
                    "QueryValueEx",
                    side_effect=[speed, (1, winreg.REG_DWORD), (0, winreg.REG_DWORD), (0, winreg.REG_DWORD)],
                ),
            ):
                values, fallback = camera.read_saved_camera_settings()
                self.assertEqual(values["speed_x"], 0.0)
                self.assertTrue(values["reverse_x"])
                self.assertEqual(fallback, ("speed_x",))
                self.assertAlmostEqual(camera.get_default_camera_yaw_ratio(1920, settings=values), -0.084375)

    def test_missing_settings_use_baseline_for_both_axes(self):
        import winreg

        with patch.object(winreg, "OpenKey", side_effect=FileNotFoundError):
            self.assertAlmostEqual(camera.get_default_camera_yaw_ratio(1920), 0.084375)
            self.assertAlmostEqual(
                camera.get_default_camera_vertical_track(1080)["linear_track_units_per_received_y"],
                0.0002916666666666667,
            )
            _, fallback = camera.read_saved_camera_settings()
            self.assertEqual(set(fallback), {"speed_x", "speed_y", "reverse_x", "reverse_y"})

    def test_runtime_defaults_ignore_old_installation_audit_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            profile_path = root / "profile.json"
            profile = dict(
                self.profile,
                installed_files={str(root / "GameAssembly.dll"): "0" * 64},
                bundle_slices=[{"path": str(root / "missing.chk"), "sha256": "0" * 64}],
            )
            profile_path.write_text(json.dumps(profile), encoding="utf-8")
            with patch.object(camera, "_PROFILE_PATH", profile_path):
                for client in (None, b"old client", b"updated client"):
                    if client is not None:
                        (root / "GameAssembly.dll").write_bytes(client)
                    self.assertAlmostEqual(
                        camera.get_default_camera_yaw_ratio(1920, settings={"speed_x": 0.0, "reverse_x": False}),
                        0.084375,
                    )
                    self.assertAlmostEqual(
                        camera.get_default_camera_vertical_track(1080, settings={"speed_y": 0.0, "reverse_y": False})[
                            "linear_track_units_per_received_y"
                        ],
                        0.0002916666666666667,
                    )

    def test_bundled_profile_schema_still_validated(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            profile_path = root / "profile.json"
            profile_path.write_text(json.dumps(dict(self.profile, schema=999)), encoding="utf-8")
            with (
                patch.object(camera, "_PROFILE_PATH", profile_path),
                self.assertRaisesRegex(ValueError, "schema"),
            ):
                camera.get_default_camera_yaw_ratio(1920, settings={"speed_x": 0.0, "reverse_x": False})

    def test_weighted_curve_rejected(self):
        with self.assertRaisesRegex(ValueError, "nonweighted"):
            camera._evaluate_curve([{"weightedMode": 1}, {"weightedMode": 0}], 0)


if __name__ == "__main__":
    unittest.main()
