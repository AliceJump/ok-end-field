from __future__ import annotations

from src.core.BaseEfTask import BaseEfTask
from src.core.RegionConfig import (
    DEFAULT_REGION_CONFIG,
    REGION_CONFIG_DESCRIPTION,
    REGION_CONFIG_MODE_KEY,
    REGION_CONFIG_TYPE,
    REGION_SCOPE_AREAS_KEY,
    REGION_SCOPE_MODE_KEY,
    get_region_config,
    migrate_legacy_region_scope,
    resolve_region_areas,
)
from src.data.world_map import areas_list


class RegionScopeMixin(BaseEfTask):
    """Shared global/independent region-scope configuration for tasks."""

    migrate_legacy_region_bools = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._register_region_scope_config()

    def _register_region_scope_config(self) -> None:
        if not hasattr(self, "default_config") or self.default_config is None:
            self.default_config = {}
        if not hasattr(self, "config_description") or self.config_description is None:
            self.config_description = {}
        if not hasattr(self, "config_type") or self.config_type is None:
            self.config_type = {}

        self.default_config.update(
            {
                REGION_CONFIG_MODE_KEY: False,
                **DEFAULT_REGION_CONFIG,
            }
        )
        self.config_description.update(REGION_CONFIG_DESCRIPTION)
        self.config_description[REGION_CONFIG_MODE_KEY] = "勾选后使用当前任务的独立地区范围，否则使用全局地区配置。"
        self.config_type.update(REGION_CONFIG_TYPE)
        self.config_type[REGION_CONFIG_MODE_KEY] = {
            "sub_configs": {
                True: [REGION_SCOPE_MODE_KEY, REGION_SCOPE_AREAS_KEY],
            },
        }

    @staticmethod
    def _parse_use_independent(value) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if value == "使用独立配置" or normalized in {"true", "1", "yes", "on"}:
                return True
            if value == "使用全局配置" or normalized in {"false", "0", "no", "off"}:
                return False
        return False

    def get_region_areas(self, available_areas=None) -> list[str]:
        """Resolve the effective ordered region list for the current task/account."""
        available = list(areas_list if available_areas is None else available_areas)
        if self._parse_use_independent(self.config.get(REGION_CONFIG_MODE_KEY, False)):
            mode = self.config.get(REGION_SCOPE_MODE_KEY, DEFAULT_REGION_CONFIG[REGION_SCOPE_MODE_KEY])
            selected = self.config.get(REGION_SCOPE_AREAS_KEY, DEFAULT_REGION_CONFIG[REGION_SCOPE_AREAS_KEY])
        else:
            global_config = get_region_config()
            mode = global_config.get(REGION_SCOPE_MODE_KEY, DEFAULT_REGION_CONFIG[REGION_SCOPE_MODE_KEY])
            selected = global_config.get(REGION_SCOPE_AREAS_KEY, DEFAULT_REGION_CONFIG[REGION_SCOPE_AREAS_KEY])
        return resolve_region_areas(mode, selected, available)

    def load_config(self):
        if self.migrate_legacy_region_bools:
            try:
                migrate_legacy_region_scope(self.__class__.__name__, areas_list)
            except Exception as exc:
                # 兼容迁移失败不应阻断任务配置加载；保留原文件交给框架按现有逻辑处理。
                self.log_warning(f"旧地区配置迁移失败，保留原配置继续加载: {exc}")
        return super().load_config()
