"""Bundled Latin type for labels and figures; Chinese keeps the system UI face."""

from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase  # type: ignore

_FONT_ID: int | None = None


def load_fonts() -> None:
    global _FONT_ID
    if _FONT_ID is None:
        font_id = QFontDatabase.addApplicationFont(str(Path(__file__).with_name("assets") / "Saira.ttf"))
        if font_id == -1:
            raise RuntimeError("Unable to load bundled Saira font")
        _FONT_ID = font_id


def ui_font(size: int = 14, weight: QFont.Weight = QFont.Normal) -> QFont:
    font = QFont()
    font.setFamilies(["Segoe UI", "Microsoft YaHei UI"])
    font.setPixelSize(size)
    font.setWeight(weight)
    return font


def field_font(size: int = 12, *, label: bool = False) -> QFont:
    load_fonts()
    font = QFont("Saira")
    font.setPixelSize(size)
    font.setWeight(QFont.DemiBold if label else QFont.Medium)
    font.setVariableAxis(QFont.Tag("wdth"), 125.0 if label else 112.0)
    font.setVariableAxis(QFont.Tag("wght"), 600.0 if label else 500.0)
    font.setFeature(QFont.Tag("tnum"), 1)
    font.setLetterSpacing(QFont.AbsoluteSpacing, 1.0 if label else 0.2)
    return font
