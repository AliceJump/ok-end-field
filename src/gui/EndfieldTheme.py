"""Paper, ink and one signal colour, taken from the endfield-ui skill tokens.

accent is the trace colour (focus, selection bars, checked boxes, qfluent themeColor).
signal fills only what is current or about to act; it is never a line or text on paper.
go is the main action fill.
"""

# 页面顶边三色条：品红 / 信号黄 / 青绿，只作装饰
TRI_STRIP = ("#FF00F0", "#FFFA00", "#00FFA2")

LIGHT = {
    "canvas": "#F4F4F2",
    "navigation": "#F4F4F2",
    "panel": "#FFFFFF",
    "hover": "#E4E4E0",
    "chip": "#E4E4E0",
    "row_hover": "#F4F4F2",
    "border": "#D9D9D6",
    "border_hover": "#8A8A84",
    "divider": "#D9D9D6",
    "text": "#161616",
    "muted": "#5C5C5C",
    "accent": "#161616",
    "on_accent": "#FFFFFF",
    "signal": "#FFFA00",
    "on_signal": "#161616",
    "go": "#161616",
    "on_go": "#FFFFFF",
    "primary_hover": "#FFFA00",
    "primary_pressed": "#E0DC00",
    "disabled_bg": "#ECECE8",
    "disabled_text": "#74746C",
    "overlay": "#161616",
    "input": "#FFFFFF",
    "tag_bg": "#161616",
    "tag_fg": "#FFFFFF",
    "danger": "#B3261E",
}
# 深色是浅色的镜像：调暗的中性灰，面越靠前越亮（画布 L*≈18、面板 L*≈23）；
# 墨色换成浅墨，勾选框、编号牌、主按钮与焦点都用它，信号黄同浅色一样只标当前项；边线比面板亮约 14 L*，与浅色对称
DARK = {
    "canvas": "#2D2D2B",
    "navigation": "#2D2D2B",
    "panel": "#373735",
    "hover": "#444442",
    "chip": "#444442",
    "row_hover": "#3D3D3B",
    "border": "#575755",
    "border_hover": "#91918F",
    "divider": "#575755",
    "text": "#ECECEB",
    "muted": "#B6B6B5",
    "accent": "#ECECEB",
    "on_accent": "#222220",
    "signal": "#FFFA00",
    "on_signal": "#161616",
    "go": "#ECECEB",
    "on_go": "#2D2D2B",
    "primary_hover": "#FFFA00",
    "primary_pressed": "#D4D000",
    "disabled_bg": "#3A3A38",
    "disabled_text": "#949492",
    "overlay": "#141412",
    "input": "#31312F",
    "tag_bg": "#ECECEB",
    "tag_fg": "#222220",
    "danger": "#FF8A7A",
}


def palette(dark: bool) -> dict[str, str]:
    return DARK if dark else LIGHT
