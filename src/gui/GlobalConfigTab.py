from qfluentwidgets import FluentIcon, NavigationItemPosition

from ok.gui.tasks.ConfigCard import ConfigCard, og
from ok.gui.widget.CustomTab import CustomTab

from src.core.BattleConfig import BATTLE_CONFIG_NAME
from src.core.global_config_store import get_all_visible_configs
from src.gui.ConditionalRotationPanel import ConditionalRotationPanel


GLOBAL_CONFIG_GROUPS = {
    "战斗配置": [BATTLE_CONFIG_NAME],
    "键位配置": ["Game Hotkey Config"],
    "基础配置": ["Ensure Main Once Action Sleep"],
}


class GlobalConfigTab(CustomTab):
    @property
    def name(self):
        return og.app.tr("全局配置")

    @property
    def position(self):
        return NavigationItemPosition.TOP

    @property
    def add_after_default_tabs(self):
        return False

    @property
    def icon(self):
        return FluentIcon.SETTING

    def showEvent(self, event):
        super().showEvent(event)
        if self.vBoxLayout.count() == 0:
            self._build_cards()

    def _build_cards(self):
        visible_configs = {
            name: (config, option)
            for name, config, option in get_all_visible_configs()
        }
        shown = set()
        for group_name, config_names in GLOBAL_CONFIG_GROUPS.items():
            for config_name in config_names:
                config_and_option = visible_configs.get(config_name)
                if config_and_option is None:
                    continue
                config, option = config_and_option
                shown.add(config_name)
                card = ConfigCard(
                    None,
                    group_name,
                    config,
                    option.description,
                    option.default_config,
                    option.config_description,
                    option.config_type,
                    option.icon,
                )
                card.card.setTitle(f"{og.app.tr(group_name)} / {og.app.tr(option.name)}")
                self.add_widget(card)
                # 「实时条件」面板：内嵌于战斗配置卡片内，排在「排轴序列」之后
                if config_name == BATTLE_CONFIG_NAME:
                    panel = ConditionalRotationPanel(card)
                    ref = card.config_widget_by_key.get("排轴序列")
                    if ref is not None:
                        idx = card.viewLayout.indexOf(ref)
                        card.viewLayout.insertWidget(idx + 1, panel)
                    else:
                        card.viewLayout.addWidget(panel)
                    # 重新计算卡片展开高度，确保面板不被裁剪
                    card._adjust_config_content_size()

        for config_name, (config, option) in visible_configs.items():
            if config_name in shown:
                continue
            group_name = "其他配置"
            card = ConfigCard(
                None,
                group_name,
                config,
                option.description,
                option.default_config,
                option.config_description,
                option.config_type,
                option.icon,
            )
            card.card.setTitle(f"{og.app.tr(group_name)} / {og.app.tr(option.name)}")
            self.add_widget(card)
