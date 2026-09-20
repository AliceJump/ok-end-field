# -*- coding: utf-8 -*-
"""全局「导航配置」（src/core/NavConfig.py）的选档逻辑与注册。

比例尺随画面宽变化（``比例尺 = 常数 / 画面宽``），所以这里最要紧的是：
档位与常数两条路径必须**连续**，以及 NavConfig 里拼出来的键名必须真的存在于默认配置里
——键名拼错会静默退化成常数路径，不报错但行为悄悄变了。
"""

import unittest

from src.core.NavConfig import (
    DEFAULT_NAV_CONFIG,
    DEFAULT_SCALE_CONSTANT,
    NAV_CONFIG_DESCRIPTION,
    NAV_CONFIG_NAME,
    NAV_CONTENT_KEY,
    NAV_MATRIX_SUFFIX,
    NAV_RESOLUTION_TIERS,
    NAV_SCALE_CONSTANT_KEY,
    NAV_SCALE_SUFFIX,
    NAV_WS_ACCOUNT_KEY,
    nav_profile_for_width,
    tier_for_width,
)


def _tier_keys() -> list[str]:
    return [
        f"{name}{suffix}"
        for name, _ in NAV_RESOLUTION_TIERS
        for suffix in (NAV_SCALE_SUFFIX, NAV_MATRIX_SUFFIX)
    ]


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
        profile = nav_profile_for_width(
            2560, self._values(**{f"2K{NAV_SCALE_SUFFIX}": 0.7001}))
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
        self.assertAlmostEqual(a11, -a22, places=6)      # 世界_z 与地图_y 符号相反
        self.assertGreater(a11, 0.0)

    def test_broken_tier_entry_falls_back_to_constant(self):
        """档位值坏掉（空/0/非数字）时不能返回一个坏 profile，要退到常数。"""
        for bad in ("", "0", "abc", None):
            profile = nav_profile_for_width(
                1920, self._values(**{f"1K{NAV_SCALE_SUFFIX}": bad}))
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
        self.assertIsNone(nav_profile_for_width(
            1600, {NAV_SCALE_CONSTANT_KEY: 0}))
        self.assertIsNone(nav_profile_for_width(
            1600, {NAV_SCALE_CONSTANT_KEY: "abc"}))

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

    def test_registered_as_global_config(self):
        from src.core.global_config_store import GLOBAL_CONFIG_OPTIONS, get_all_visible_configs
        names = [option.name for option in GLOBAL_CONFIG_OPTIONS]
        self.assertIn(NAV_CONFIG_NAME, names)
        self.assertIn(NAV_CONFIG_NAME, [name for name, _c, _o in get_all_visible_configs()])

    def test_global_config_exposes_nav_config_keys(self):
        """注册到全局配置的那份必须覆盖 NavConfig 定义的全部键。

        只比键与结构性默认值：``真值content`` / ``真值地图账号`` 是用户可改的，
        真实配置文件里本来就可能不是默认值。
        """
        from src.core.global_config_store import get_global_config
        config = get_global_config(NAV_CONFIG_NAME)
        for key, default in DEFAULT_NAV_CONFIG.items():
            self.assertIsNotNone(config.get(key), f"{key} 未注册到全局配置")
            if key in (NAV_CONTENT_KEY, NAV_WS_ACCOUNT_KEY):
                continue
            self.assertEqual(config.get(key), default, key)

    def test_gui_group_lists_nav_config(self):
        from src.gui.GlobalConfigTab import GLOBAL_CONFIG_GROUPS
        self.assertIn(NAV_CONFIG_NAME, GLOBAL_CONFIG_GROUPS.get("导航配置", []))


class TestMixinUsesGlobalNavConfig(unittest.TestCase):
    """mixin 真的从全局配置取比例尺，并按当前画面宽度选档。"""

    def _task_at(self, width):
        from src.core.NavConfig import DEFAULT_NAV_CONFIG as _defaults
        from src.tasks.mixin.minimap_position_mixin import MinimapPositionMixin

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
            self.assertAlmostEqual(profile.scale,
                                   DEFAULT_NAV_CONFIG[f"{expect_tier}{NAV_SCALE_SUFFIX}"], places=6)

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


if __name__ == "__main__":
    unittest.main()
