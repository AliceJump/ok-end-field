from qfluentwidgets import FluentIcon, NavigationItemPosition

from ok.gui.tasks.ConfigCard import ConfigCard, og
from ok.gui.widget.CustomTab import CustomTab

from src.core.BattleConfig import BATTLE_CONFIG_NAME
from src.core.global_config_store import ZIP_LINE_CONFIG_NAME, get_all_visible_configs


GLOBAL_CONFIG_GROUPS = {
    "战斗配置": [BATTLE_CONFIG_NAME],
    "键位配置": ["Game Hotkey Config"],
    "基础配置": ["Ensure Main Once Action Sleep"],
    "滑索配置": [ZIP_LINE_CONFIG_NAME],
}


class GlobalConfigTab(CustomTab):
    @property
    def name(self):
        # MainWindow 会对 tab 的 name 统一调用 self.app.tr(name)，
        # 这里必须返回源 key（"全局配置"）而非已翻译文本，
        # 否则会对翻译结果二次 tr()，把繁体 key 当作待翻译字符串收集进 ok.po。
        return "全局配置"

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
