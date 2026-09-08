from ok.gui.tasks.ConfigCard import ConfigCard
from ok.gui.widget.CustomTab import CustomTab
from PySide6.QtCore import QTimer
from qfluentwidgets import FluentIcon, NavigationItemPosition

from src.core.BattleConfig import BATTLE_CONFIG_NAME
from src.core.global_config_store import ZIP_LINE_CONFIG_NAME, get_all_visible_configs

GLOBAL_CONFIG_GROUPS = {
    "战斗配置": [BATTLE_CONFIG_NAME],
    "键位配置": ["Game Hotkey Config"],
    "基础配置": ["Ensure Main Once Action Sleep"],
    "滑索配置": [ZIP_LINE_CONFIG_NAME],
}

# 启动后空闲预热：在用户点进本页之前把配置卡片建好，消除首次切换的卡顿。
# 事件循环空闲后才会触发，不影响启动速度。
PREBUILD_DELAY_MS = 3000


class GlobalConfigTab(CustomTab):
    def __init__(self):
        super().__init__()
        self._pending_cards = []
        self._build_scheduled = False
        QTimer.singleShot(PREBUILD_DELAY_MS, self, self._prewarm_build)

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
        self._schedule_build()

    def _prewarm_build(self):
        self._schedule_build()

    def _schedule_build(self):
        """只排程一次；卡片逐个在事件循环空闲时构建，切换到本页不再被阻塞。"""
        if self._build_scheduled:
            return
        self._build_scheduled = True
        self._pending_cards = self._collect_cards()
        QTimer.singleShot(0, self, self._build_next_card)

    def _collect_cards(self):
        visible_configs = {name: (config, option) for name, config, option in get_all_visible_configs()}
        shown = set()
        cards = []
        for group_name, config_names in GLOBAL_CONFIG_GROUPS.items():
            for config_name in config_names:
                config_and_option = visible_configs.get(config_name)
                if config_and_option is None:
                    continue
                config, option = config_and_option
                shown.add(config_name)
                cards.append((group_name, config, option))

        for config_name, (config, option) in visible_configs.items():
            if config_name in shown:
                continue
            cards.append(("其他配置", config, option))
        return cards

    def _build_next_card(self):
        if not self._pending_cards:
            return
        group_name, config, option = self._pending_cards.pop(0)
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
        # 标题即分组名（如「滑索配置」），ConfigCard 构造时已用 group_name 作为卡片标题
        self.add_widget(card)
        if self._pending_cards:
            # 每次事件循环只构建一张卡片，构建间隙保持界面响应
            QTimer.singleShot(0, self, self._build_next_card)
