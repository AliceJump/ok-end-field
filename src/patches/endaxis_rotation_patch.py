"""Allow fixed rotation config to consume EndAxis share codes.

Syntax:
    endaxis:<share-code>
    endaxis[scenario-id-or-name]:<share-code>

The patch is intentionally narrow: it only adapts the existing fixed-rotation
parser. Runtime execution, skill readiness, combat exit, and all ordinary OKEF
rotation strings continue through the original BattleMixin implementation.
"""

from __future__ import annotations

from src.data.endaxis_timeline import EndAxisTimelineError, endaxis_spec_to_rotation_tokens

_PATCH_INSTALLED = False
_HELP_TEXT = (
    "\n也可直接粘贴 EndAxis 数据码：endaxis:<数据码>；"
    "多场景时可用 endaxis[场景ID或名称]:<数据码>。"
)


def install_endaxis_rotation_patch() -> None:
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from src.core.BattleConfig import BATTLE_CONFIG_DESCRIPTION, KEY_ROTATION_SEQUENCE
    from src.tasks.mixin.battle_mixin import BattleMixin

    description = BATTLE_CONFIG_DESCRIPTION.get(KEY_ROTATION_SEQUENCE, "")
    if _HELP_TEXT not in description:
        BATTLE_CONFIG_DESCRIPTION[KEY_ROTATION_SEQUENCE] = description + _HELP_TEXT

    original = BattleMixin._parse_skill_sequence
    if getattr(original, "_endaxis_rotation_wrapped", False):
        _PATCH_INSTALLED = True
        return

    def parse_skill_sequence_with_endaxis(self, raw_config):
        if not isinstance(raw_config, str) or not raw_config.lstrip().lower().startswith("endaxis"):
            return original(self, raw_config)

        try:
            converted = endaxis_spec_to_rotation_tokens(raw_config)
        except EndAxisTimelineError as exc:
            self.log_info(f"EndAxis 排轴配置无效: {exc}；本轮固定排轴禁用")
            return []

        if converted is None:
            self.log_info("EndAxis 排轴配置格式无效；本轮固定排轴禁用")
            return []

        scenario, tokens = converted
        label = scenario.name or scenario.scenario_id or "未命名场景"
        self.log_info(
            f"EndAxis 排轴导入: 场景 {label}，{len(scenario.actions)} 个动作 -> {len(tokens)} 个 OKEF token"
        )
        return original(self, tokens)

    parse_skill_sequence_with_endaxis._endaxis_rotation_wrapped = True
    BattleMixin._parse_skill_sequence = parse_skill_sequence_with_endaxis
    _PATCH_INSTALLED = True
