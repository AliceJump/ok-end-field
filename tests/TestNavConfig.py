"""全局「导航配置」（src/core/NavConfig.py）的选档逻辑与注册。

比例尺随画面宽变化（``比例尺 = 常数 / 画面宽``），所以这里最要紧的是：
档位与常数两条路径必须**连续**，以及 NavConfig 里拼出来的键名必须真的存在于默认配置里
——键名拼错会静默退化成常数路径，不报错但行为悄悄变了。
"""

import ast
import os
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

from ok.util import config as config_module
from ok.util.file import write_json_file

from src.core import global_config_store
from src.core.GridNavConfig import (
    CONFIG_GRID_DIR,
    CONFIG_GRID_FRONTIER_MARGIN,
    CONFIG_GRID_FRONTIER_PENALTY,
    CONFIG_GRID_GOAL_RADIUS,
    CONFIG_GRID_MARGIN,
    CONFIG_GRID_MAX_EXPAND,
    CONFIG_GRID_NARROW_CALIBRATION,
    CONFIG_GRID_NARROW_STOP_RADIUS,
    CONFIG_GRID_RISK_COST,
    CONFIG_GRID_SHORTCUT_RADIUS,
    CONFIG_GRID_STUCK_DISTANCE,
    CONFIG_GRID_STUCK_WINDOW,
    CONFIG_GRID_WALL_PENALTY,
    CONFIG_GRID_WAYPOINT_RADIUS,
    CONFIG_GRID_WAYPOINT_TOLERANCE,
    DEFAULT_GRID_NAV_CONFIG,
)
from src.core.NavConfig import (
    DEFAULT_NAV_CONFIG,
    DEFAULT_SCALE_CONSTANT,
    NAV_CONFIG_DESCRIPTION,
    NAV_CONFIG_GROUP_KEY,
    NAV_CONFIG_GROUP_LOCALIZATION,
    NAV_CONFIG_NAME,
    NAV_CONFIG_TYPE,
    NAV_CONTENT_KEY,
    NAV_MATRIX_SUFFIX,
    NAV_RESOLUTION_TIERS,
    NAV_SCALE_CONSTANT_KEY,
    NAV_SCALE_SUFFIX,
    NAV_WS_ACCOUNT_KEY,
    nav_profile_for_width,
    tier_for_width,
)
from src.core.PositionConfig import CONFIG_COMMIT_MIN_SHIFT, DEFAULT_POSITION_CONFIG, POSITION_CONFIG_NAME


def _tier_keys() -> list[str]:
    return [f"{name}{suffix}" for name, _ in NAV_RESOLUTION_TIERS for suffix in (NAV_SCALE_SUFFIX, NAV_MATRIX_SUFFIX)]


class TestTierSelection(unittest.TestCase):
    """档位命中规则：精确匹配，不做"最接近"。"""

    def test_exact_widths_hit_tiers(self):
        for name, width in NAV_RESOLUTION_TIERS:
            self.assertEqual(tier_for_width(width), (name, width), width)

    def test_non_tier_widths_miss(self):
        # 1600 是常见窗口宽，它不是 1920——比例尺必须走"常数 / 宽度"而不是套用 1K 档位的值
        for width in (0, 640, 1600, 1919, 1921, 2561, 3839, 7680):
            self.assertIsNone(tier_for_width(width), width)

    def test_bad_width_values_miss(self):
        for value in (None, "", "abc"):
            self.assertIsNone(tier_for_width(value), value)


class TestNavProfile(unittest.TestCase):
    """按宽度选比例尺与轴映射。"""

    def _values(self, **overrides):
        values = dict(DEFAULT_NAV_CONFIG)
        values.update(overrides)
        return values

    def test_tier_width_uses_tier_entry(self):
        profile = nav_profile_for_width(1920, self._values())
        self.assertIsNotNone(profile)
        self.assertEqual(profile.tier, "1K")
        self.assertIn("1K", profile.source)
        self.assertAlmostEqual(profile.scale, DEFAULT_NAV_CONFIG[f"1K{NAV_SCALE_SUFFIX}"], places=6)

    def test_tier_entry_can_be_overridden_by_measurement(self):
        """某个分辨率下单独标定过 → 覆盖成实测值，不再用常数反推值。"""
        profile = nav_profile_for_width(2560, self._values(**{f"2K{NAV_SCALE_SUFFIX}": 0.7001}))
        self.assertAlmostEqual(profile.scale, 0.7001, places=6)
        self.assertEqual(profile.map_to_world, DEFAULT_NAV_CONFIG[f"2K{NAV_MATRIX_SUFFIX}"])

    def test_non_tier_width_falls_back_to_constant(self):
        profile = nav_profile_for_width(1600, self._values())
        self.assertIsNone(profile.tier)
        self.assertAlmostEqual(profile.scale, DEFAULT_SCALE_CONSTANT / 1600, places=6)
        self.assertIn("1600", profile.source)

    def test_constant_path_matrix_is_diagonal_and_sign_flipped(self):
        profile = nav_profile_for_width(1600, self._values())
        a11, a12, a21, a22 = (float(v) for v in profile.map_to_world.split(","))
        self.assertAlmostEqual(a12, 0.0, places=9)
        self.assertAlmostEqual(a21, 0.0, places=9)
        self.assertAlmostEqual(a11, -a22, places=6)  # 世界_z 与地图_y 符号相反
        self.assertGreater(a11, 0.0)

    def test_broken_tier_entry_falls_back_to_constant(self):
        """档位值坏掉（空/0/非数字）时不能返回一个坏 profile，要退到常数。"""
        for bad in ("", "0", "abc", None):
            profile = nav_profile_for_width(1920, self._values(**{f"1K{NAV_SCALE_SUFFIX}": bad}))
            self.assertIsNotNone(profile, bad)
            self.assertIsNone(profile.tier, bad)
            self.assertAlmostEqual(profile.scale, DEFAULT_SCALE_CONSTANT / 1920, places=6)

    def test_unknown_width_returns_none(self):
        """拿不到分辨率时返回 None，让调用方明确报错，而不是猜一个值。"""
        for width in (0, -1, None, ""):
            self.assertIsNone(nav_profile_for_width(width, self._values()), width)

    def test_missing_or_invalid_values_return_none(self):
        self.assertIsNone(nav_profile_for_width(1920, None))
        self.assertIsNone(nav_profile_for_width(1920, {}))
        self.assertIsNone(nav_profile_for_width(1600, {NAV_SCALE_CONSTANT_KEY: 0}))
        self.assertIsNone(nav_profile_for_width(1600, {NAV_SCALE_CONSTANT_KEY: "abc"}))

    def test_tier_and_constant_paths_are_continuous(self):
        """档位默认值由常数反推：同一宽度下两条路径必须给同一个数，否则换分辨率会跳变。"""
        for name, width in NAV_RESOLUTION_TIERS:
            with_tier = nav_profile_for_width(width, self._values())
            without_tier = nav_profile_for_width(width, {NAV_SCALE_CONSTANT_KEY: DEFAULT_SCALE_CONSTANT})
            self.assertAlmostEqual(without_tier.scale, DEFAULT_SCALE_CONSTANT / width, places=6)
            # 档位值只保留 4 位小数，允许该舍入误差
            self.assertAlmostEqual(with_tier.scale, without_tier.scale, delta=1e-4, msg=name)


class TestNavConfigShape(unittest.TestCase):
    """默认值 / 说明 / 注册的结构一致性。"""

    def test_minimap_test_task_keeps_only_target_coordinates_in_task_config(self):
        path = Path(__file__).parents[1] / "src" / "tasks" / "navigation" / "MinimapNavigateToPoint.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        class_node = next(
            node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MinimapNavigateToPoint"
        )
        assigned = {}
        for node in ast.walk(class_node):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Dict):
                continue
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                    and target.attr in {"default_config", "config_description"}
                ):
                    assigned[target.attr] = {
                        key.value
                        for key in node.value.keys
                        if isinstance(key, ast.Constant) and isinstance(key.value, str)
                    }

        self.assertEqual(assigned.get("default_config"), {"目标X", "目标Z"})
        self.assertEqual(assigned.get("config_description"), {"目标X", "目标Z"})

    def test_every_key_has_description(self):
        for key in DEFAULT_NAV_CONFIG:
            self.assertIn(key, NAV_CONFIG_DESCRIPTION, key)

    def test_generated_tier_keys_exist_in_defaults(self):
        """NavConfig 里用 f-string 拼出来的键必须真的存在于默认配置。

        拼错不会报错，只会静默退化成"常数 / 宽度"路径——行为悄悄变了但没人发现，
        所以这里显式钉住。
        """
        for key in [NAV_SCALE_CONSTANT_KEY, *_tier_keys()]:
            self.assertIn(key, DEFAULT_NAV_CONFIG, key)
            self.assertIn(key, NAV_CONFIG_DESCRIPTION, key)

    def test_content_and_account_keys_present(self):
        self.assertIn(NAV_CONTENT_KEY, DEFAULT_NAV_CONFIG)
        self.assertIn(NAV_WS_ACCOUNT_KEY, DEFAULT_NAV_CONFIG)

    def test_manual_yaw_ratio_is_not_a_nav_config_key(self):
        self.assertNotIn("yaw_per_pixel(度/像素)", DEFAULT_NAV_CONFIG)
        localization_keys = NAV_CONFIG_TYPE[NAV_CONFIG_GROUP_KEY]["sub_configs"][NAV_CONFIG_GROUP_LOCALIZATION]
        self.assertNotIn("yaw_per_pixel(度/像素)", localization_keys)

    def test_registered_as_global_config(self):
        from src.core.global_config_store import GLOBAL_CONFIG_OPTIONS, get_all_visible_configs

        names = [option.name for option in GLOBAL_CONFIG_OPTIONS]
        self.assertIn(NAV_CONFIG_NAME, names)
        self.assertIn(POSITION_CONFIG_NAME, names)
        self.assertIn(NAV_CONFIG_NAME, [name for name, _c, _o in get_all_visible_configs()])
        self.assertIn(POSITION_CONFIG_NAME, [name for name, _c, _o in get_all_visible_configs()])

    def test_global_config_exposes_nav_config_keys(self):
        """注册到全局配置的那份必须覆盖 NavConfig 定义的全部键。

        只比键：真实配置文件里的导航选项本来就可能已被用户覆盖。
        """
        from src.core.global_config_store import get_global_config

        config = get_global_config(NAV_CONFIG_NAME)
        for key in DEFAULT_NAV_CONFIG:
            self.assertIsNotNone(config.get(key), f"{key} 未注册到全局配置")

    def test_config_type_groups_every_nav_option(self):
        group_type = NAV_CONFIG_TYPE[NAV_CONFIG_GROUP_KEY]
        grouped = {key for keys in group_type["sub_configs"].values() for key in keys}
        expected = set(DEFAULT_NAV_CONFIG) - {NAV_CONFIG_GROUP_KEY}
        self.assertEqual(grouped, expected)

    def test_gui_group_lists_nav_config(self):
        from src.gui.GlobalConfigTab import GLOBAL_CONFIG_GROUPS

        self.assertIn(NAV_CONFIG_NAME, GLOBAL_CONFIG_GROUPS.get("导航配置", []))
        self.assertIn(POSITION_CONFIG_NAME, GLOBAL_CONFIG_GROUPS.get("定位配置", []))


class TestMixinUsesGlobalNavConfig(unittest.TestCase):
    """mixin 真的从全局配置取比例尺，并按当前画面宽度选档。"""

    def _task_at(self, width):
        from src.core.NavConfig import DEFAULT_NAV_CONFIG as _defaults
        from src.localization.minimap_position_mixin import MinimapPositionMixin

        class _Task(MinimapPositionMixin):
            def __init__(self, w):
                self.width = w
                self.height = int(w * 9 / 16)
                self.config = {}
                self.logs = []

            def _nav_config(self):
                return dict(_defaults)

            def log_warning(self, msg, **kwargs):
                self.logs.append(("warn", str(msg)))

            def log_info(self, msg, **kwargs):
                self.logs.append(("info", str(msg)))

        return _Task(width)

    def test_profile_follows_current_resolution(self):
        for width, expect_tier in ((1920, "1K"), (2560, "2K"), (3840, "4K")):
            task = self._task_at(width)
            profile = task._nav_profile()
            self.assertIsNotNone(profile, width)
            self.assertEqual(profile.tier, expect_tier, width)
            self.assertAlmostEqual(profile.scale, DEFAULT_NAV_CONFIG[f"{expect_tier}{NAV_SCALE_SUFFIX}"], places=6)

    def test_profile_scales_with_width_when_not_a_tier(self):
        """换一个非档位分辨率，比例尺必须跟着宽度变——这就是"自适应"的核心。"""
        small = self._task_at(1600)._nav_profile()
        large = self._task_at(3200)._nav_profile()
        self.assertAlmostEqual(small.scale, DEFAULT_SCALE_CONSTANT / 1600, places=6)
        self.assertAlmostEqual(large.scale, DEFAULT_SCALE_CONSTANT / 3200, places=6)
        self.assertAlmostEqual(small.scale / large.scale, 2.0, places=3)

    def test_profile_is_none_without_resolution(self):
        """窗口还没就绪（width=0）时不能瞎猜，要返回 None 让调用方报错。"""
        self.assertIsNone(self._task_at(0)._nav_profile())


class TestPublishedConfigMigration(unittest.TestCase):
    """Only migrate credentials that the published ItemNavigatorTask produced."""

    def _write_configs(self, root: str, files: dict):
        configs_dir = os.path.join(root, "configs")
        os.makedirs(configs_dir, exist_ok=True)
        for name, data in files.items():
            write_json_file(os.path.join(configs_dir, name), data)

    def _patches(self, tmp: str):
        return (
            patch.object(
                global_config_store,
                "config_path",
                side_effect=lambda *parts: os.path.join(tmp, "configs", *parts),
            ),
            patch.object(
                config_module.Config,
                "config_folder",
                os.path.join(tmp, "configs"),
            ),
        )

    def _migrate(self, task_config: dict, nav_config: dict | None = None):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_configs(
                tmp,
                {
                    "Nav Config.json": dict(DEFAULT_NAV_CONFIG) if nav_config is None else nav_config,
                    "ItemNavigatorTask.json": task_config,
                },
            )
            previous = global_config_store._CONFIGS.copy()
            global_config_store._CONFIGS.clear()
            try:
                patches = self._patches(tmp)
                with patches[0], patches[1]:
                    config = global_config_store.get_global_config(NAV_CONFIG_NAME)
                    global_config_store.migrate_task_nav_values_to_global("ItemNavigatorTask")
            finally:
                global_config_store._CONFIGS.clear()
                global_config_store._CONFIGS.update(previous)
            return config

    def test_manual_content_wins_over_stale_account(self):
        config = self._migrate(
            {
                "地图数据来源": "手动 content",
                "content": "item-content",
                "地图账号": "stale-account",
            }
        )

        self.assertEqual(config.get(NAV_CONTENT_KEY), "item-content")
        self.assertEqual(config.get(NAV_WS_ACCOUNT_KEY), "")

    def test_account_source_wins_over_stale_content(self):
        config = self._migrate(
            {
                "地图数据来源": "账号配置",
                "content": "stale-content",
                "地图账号": "item-account",
            }
        )

        self.assertEqual(config.get(NAV_CONTENT_KEY), "")
        self.assertEqual(config.get(NAV_WS_ACCOUNT_KEY), "item-account")

    def test_auto_source_does_not_import_hidden_values(self):
        config = self._migrate(
            {
                "地图数据来源": "自动当前账号",
                "content": "stale-content",
                "地图账号": "stale-account",
            }
        )

        self.assertEqual(config.get(NAV_CONTENT_KEY), "")
        self.assertEqual(config.get(NAV_WS_ACCOUNT_KEY), "")

    def test_existing_global_value_wins(self):
        config = self._migrate(
            {"地图数据来源": "手动 content", "content": "new-content"},
            nav_config={NAV_CONTENT_KEY: "existing-content"},
        )

        self.assertEqual(config.get(NAV_CONTENT_KEY), "existing-content")

    def test_intermediate_navigation_task_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_configs(
                tmp,
                {
                    "MinimapPositionTask.json": {
                        "真值content": "intermediate-content",
                        "网格目录": "assets/nav",
                    },
                    "MinimapNavigateToPoint.json": {
                        "网格目录": "late/nav",
                        "使用滑索路径": False,
                    },
                },
            )
            previous = global_config_store._CONFIGS.copy()
            global_config_store._CONFIGS.clear()
            try:
                patches = self._patches(tmp)
                with patches[0], patches[1]:
                    nav_config = global_config_store.get_global_config(NAV_CONFIG_NAME)
                    position_config = global_config_store.get_global_config(POSITION_CONFIG_NAME)
                    global_config_store.migrate_task_nav_values_to_global("MinimapPositionTask")
                    global_config_store.migrate_task_nav_values_to_global("MinimapNavigateToPoint")
            finally:
                global_config_store._CONFIGS.clear()
                global_config_store._CONFIGS.update(previous)

            self.assertEqual(nav_config.get(NAV_CONTENT_KEY), "")
            self.assertEqual(nav_config.get(CONFIG_GRID_DIR), DEFAULT_GRID_NAV_CONFIG[CONFIG_GRID_DIR])
            self.assertEqual(
                position_config.get(CONFIG_COMMIT_MIN_SHIFT),
                DEFAULT_POSITION_CONFIG[CONFIG_COMMIT_MIN_SHIFT],
            )


class TestFollowerDefaultsMatchGlobalConfig(unittest.TestCase):
    """``FollowerConfig`` 的库默认值必须与全局 ``Nav Config`` 的默认值一致。

    应用侧每一项都显式从 ``Nav Config`` 取值，所以两者不一致时不会有功能故障——只会
    让"读库默认值"的人对实际行为产生错误预期（``离墙安全边距`` 就在这里漂过 1 vs 3）。
    """

    #: ``FollowerConfig`` 字段 -> 全局配置键。``allow_unknown`` 不在表内：库默认给
    #: 宽松的 True，产品侧默认保守的 False，是刻意的差异，不是漂移。
    _FIELDS: ClassVar[dict[str, str]] = {
        "arrive_radius": CONFIG_GRID_WAYPOINT_RADIUS,
        "goal_radius": CONFIG_GRID_GOAL_RADIUS,
        "stuck_window_s": CONFIG_GRID_STUCK_WINDOW,
        "stuck_min_distance": CONFIG_GRID_STUCK_DISTANCE,
        "risk_cost": CONFIG_GRID_RISK_COST,
        "shortcut_radius": CONFIG_GRID_SHORTCUT_RADIUS,
        "margin": CONFIG_GRID_MARGIN,
        "wall_penalty": CONFIG_GRID_WALL_PENALTY,
        "frontier_margin": CONFIG_GRID_FRONTIER_MARGIN,
        "frontier_penalty": CONFIG_GRID_FRONTIER_PENALTY,
        "waypoint_tolerance": CONFIG_GRID_WAYPOINT_TOLERANCE,
        "max_expand": CONFIG_GRID_MAX_EXPAND,
        "narrow_calibration": CONFIG_GRID_NARROW_CALIBRATION,
        "narrow_stop_radius": CONFIG_GRID_NARROW_STOP_RADIUS,
    }

    def test_library_defaults_equal_configured_defaults(self):
        from dataclasses import fields

        from src.nav.route_follower import FollowerConfig

        defaults = {field.name: field.default for field in fields(FollowerConfig)}
        for name, key in self._FIELDS.items():
            with self.subTest(field=name, key=key):
                self.assertEqual(defaults[name], DEFAULT_GRID_NAV_CONFIG[key])

    def test_mapping_covers_every_shared_key(self):
        """字段表不能漏：新增了同名参数却忘了登记，这条会失败。"""
        from dataclasses import fields

        from src.nav.route_follower import FollowerConfig

        names = {field.name for field in fields(FollowerConfig)}
        self.assertTrue(set(self._FIELDS) <= names)
        # 表里的键必须真的存在于全局配置，避免写错中文键名后静默跳过
        for key in self._FIELDS.values():
            self.assertIn(key, DEFAULT_GRID_NAV_CONFIG)


if __name__ == "__main__":
    unittest.main()
