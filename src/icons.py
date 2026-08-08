"""自定义图标模块"""
from pathlib import Path

from qfluentwidgets import FluentIconBase, Theme, isDarkTheme

_ICONS_DIR = Path("assets") / "ui" / "material_icons"


class ThemeIcon(FluentIconBase):
    """根据主题自动切换的图标，dark/light 指图标文件本身的主题色"""

    def __init__(self, light_icon: str, dark_icon: str, *, suffix: str, base_path: Path = _ICONS_DIR):
        """
        Args:
            light_icon: 浅色主题图标文件名
            dark_icon: 深色主题图标文件名
            suffix: 图标文件后缀名，允许使用 ".png"、".svg"
            base_path: 图标文件所在目录，默认为常量 _ICONS_DIR
        """
        assert suffix in (".png", ".svg"), "suffix must be .png or .svg"
        self._light_path = str(base_path / f"{light_icon}{suffix}")
        self._dark_path = str(base_path / f"{dark_icon}{suffix}")

    def path(self, theme: str = Theme.AUTO):
        if theme == Theme.AUTO:
            is_dark = isDarkTheme()
        else:
            is_dark = theme == Theme.DARK
        return self._dark_path if is_dark else self._light_path


class Icons:
    """自定义图标类"""

    Battle = ThemeIcon("swords_black", "swords_white", suffix=".svg")
    Keyboard = ThemeIcon("keyboard_alt_black", "keyboard_alt_white", suffix=".svg")
    Zipline = ThemeIcon("diagonal_line_black", "diagonal_line_white", suffix=".svg")
    Interact = ThemeIcon("touch_app_black", "touch_app_white", suffix=".svg")
    Collect = ThemeIcon("approval_delegation_black", "approval_delegation_white", suffix=".svg")
    Navigation = ThemeIcon("explore_black", "explore_white", suffix=".svg")
    Fetch = ThemeIcon("box_add_black", "box_add_white", suffix=".svg")
    Deliver = ThemeIcon("delivery_truck_speed_black", "delivery_truck_speed_white", suffix=".svg")
    ItemTransfer = ThemeIcon("folder_match_black", "folder_match_white", suffix=".svg")
    SwordChallenge = ThemeIcon("playing_cards_black", "playing_cards_white", suffix=".svg")
    YingTuo = ThemeIcon("crown_black", "crown_white", suffix=".svg")
