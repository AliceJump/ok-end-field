import json
import math
import os
import re
import subprocess
import tempfile
import threading
import time
import webbrowser
from pathlib import Path

import win32gui
from ok import Logger, TriggerTask
from qfluentwidgets import FluentIcon

from src.config import config
from src.core.BaseEfTask import BaseEfTask
from src.data import item_map_query
from src.icons import Icons
from src.tasks.account.account_scope_store import get_account_map_content, load_overrides, resolve_account_id
from src.tasks.mixin.instructions_mixin import InstructionsMixin, inst_gap, inst_line
from src.tasks.mixin.ws_position_mixin import WsPositionMixin

logger = Logger.get_logger(__name__)
# 特殊物品Y坐标修正
SPECIAL_ITEM_Y_OFFSET = {
    "储藏箱": {
        "value": -0.06055,
        "pattern": re.compile(r"^储藏箱"),
    },
}

# 本地 WS 模式依赖的油猴脚本（相对仓库根目录）
RELAY_USER_SCRIPT = "assets/scripts/endfield-ws-position-relay.user.js"

# 「获取 content」使用说明里展示给用户的地址（用户手动访问 / 在开发者工具里筛选用）
OFFICIAL_MAP_PAGE_URL = "https://game.skland.com/map/endfield"
HG_CHECK_API_URL = "https://web-api.skland.com/account/info/hg/check"

# 浮层方位文案：索引 0 为正北，顺时针每 45° 一档。
# 与方向箭头共用同一套坐标系（+Z 为地图上方/北，+X 为地图右侧/东），
# 因此浮层文字与箭头朝向始终一致；若日后确认坐标系不同，只需调整此表顺序。
COMPASS_LABELS = ("北", "东北", "东", "东南", "南", "西南", "西", "西北")


class ItemNavigatorTask(InstructionsMixin, WsPositionMixin, BaseEfTask, TriggerTask):
    """实时从本地 WebSocket 拿玩家位置，指向已选物品的最近点，并支持按键标记已获取。

    设计原则：
    - `default_config` 只放面向用户的配置（见初始化），不把内部服务端口等放在 default_config
    - 轮询使用固定内部 WS 端点（可在部署时改代码），物品选择从任务配置读取
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.name = "物品导航"
        self.description = "监听本地 WebSocket 位置数据，指向已选物品的最近点并支持按键标记"
        self.icon = Icons.Navigation

        # 只把面向用户的选项放在 default_config
        self.default_config.update(
            {
                # 可选：直接填写 hg/check 的 data.content。为空时按“地图账号”读取账户配置页 content。
                "content": "",
                # 可选：从账号配置页读取对应账号的地图同步 content。
                "地图账号": "",
                # 由用户在 UI 中配置要导航的物品名列表（可空）
                "选择物品": [],
                # 标记按键（UI 映射），例如 'f'，当玩家按下且目标在阈值内时标记为已获取
                "标记按键": "f",
                # 标记时需要按住的最小时长（秒）；小于等于 0 或非法值时回退到该默认值
                "标记按住时长": 2.0,
                # 浮层目标信息（物品名 / 距离 / 方位 / 高度）
                "浮层信息": True,
                # 以下三项仅在「浮层信息」开启时显示（见 config_type 的 sub_configs）
                "浮层文字透明度": 92,
                "浮层背景透明度": 59,
                "浮层字号": 26,
            }
        )

        self.config_type["浮层信息"] = {
            # 关闭浮层信息时，隐藏文字/背景透明度与字号选项
            "sub_configs": {True: ["浮层文字透明度", "浮层背景透明度", "浮层字号"]},
        }
        self.config_type["浮层文字透明度"] = {"min": 0, "max": 100}
        self.config_type["浮层背景透明度"] = {"min": 0, "max": 100}
        self.config_type["浮层字号"] = {"min": 10, "max": 80}

        self.config_type["选择物品"] = {
            "options_available": item_map_query.get_supported_item_names(),
            "allow_duplication": False,
        }
        self.config_type["地图账号"] = {
            "type": "drop_down",
            "options": self._get_map_account_options(),
        }
        self.config_type["油猴脚本帮助"] = {
            "type": "button",
            "text": "浏览器油猴脚本帮助",
            "icon": FluentIcon.LINK,
            "callback": self.open_userscript_help,
        }
        self.config_description.update(
            {
                "content": (
                    "可选。直接填写 web-api.skland.com/account/info/hg/check 返回 JSON 里的 data.content 值。\n"
                    "此项有值时优先使用，不再读取账号配置页。\n"
                    "获取步骤见任务卡「使用说明」的「获取 content」一节。"
                ),
                "地图账号": (
                    "可选。content 为空时，从账号配置页读取该账号保存的地图同步 content。\n"
                    "账号列表来自账号配置页；留空则尝试使用当前任务账号上下文。\n"
                    "在账号配置页填入 content 的步骤见任务卡「使用说明」。"
                ),
                "选择物品": ("选择要参与导航的物品列表。\n只会在当前地图里匹配这些物品。"),
                "标记按键": ("接近目标后用于标记“已获取”的键位。\n默认按键为 f。"),
                "标记按住时长": ("按住标记按键并持续达到这个时长后，\n才会把当前目标标记为已获取。"),
                "浮层信息": ("在游戏窗口浮层上显示当前指向目标的物品名、\n距离、方位与高度。"),
                "浮层文字透明度": ("浮层文字的透明度（0-100）。\n0 表示文字完全透明，100 表示完全不透明。"),
                "浮层背景透明度": (
                    "浮层文字底板（黑色背景）的透明度（0-100）。\n"
                    "0 表示不绘制底板；headless 模式下该值为 0 才不显示底板。"
                ),
                "浮层字号": ("浮层文字字号，按 1080p 窗口高度为基准的像素值，\n会随窗口高度等比缩放。"),
                "油猴脚本帮助": ("打开临时帮助文档。\n同时打开油猴脚本目录。"),
            }
        )
        self.default_config_group.update(
            {
                "网页地图同步": ["content", "地图账号", "油猴脚本帮助"],
                "浮层显示": ["浮层信息", "浮层文字透明度", "浮层背景透明度", "浮层字号"],
            }
        )

        self.needs_frame = False  # 纯 WS 驱动，不识别画面

        # internal constants (not user-facing)
        self._init_ws_position_mixin()
        cfg_folder = Path(config.get("config_folder", "configs"))
        self._marked_store = cfg_folder / "marked_points.json"
        self._marked_lock = threading.Lock()
        self._marked: dict[str, set] = {}  # mapId -> set of point hashes
        # WS 服务启动状态追踪（仅记录首次启动日志）
        self._ws_server_start_logged = False
        self._navigator_window_missing_logged = False

        # 箭头渲染可调参数（便于快速微调视觉）
        self._arrow_center_rel = (162 / 1920, 166 / 1080)  # 相对于窗口的箭头中心位置（比例），默认在左上角稍微偏右下
        self._arrow_max_len_ratio = 0.08
        self._arrow_min_len_px = 20.0
        self._arrow_scale = 1.144
        self._nearby_marker_max_distance = 75.524
        # 附近小箭头：箭尖固定在目标位置，尾巴长度表示高差（同高最短，高差越大越长）
        self._nearby_marker_min_len_px = 12
        self._nearby_marker_max_len_px = 36
        # 箭头样式参数（可调）
        self._arrow_color = (0, 255, 0)  # RGB
        self._arrow_alpha = 160  # 透明度 0-255，160 为半透明
        self._arrow_shaft_width_norm = 0.005  # 箭身宽度（细）
        self._near_xz_threshold = 20.0
        # 高差归一化上限：超过该值小箭头长度不再增加
        self._height_max_abs_dy = 30.0

        # 浮层目标信息文字（物品名 / 距离 / 方位 / 高度）
        # 下面这些内部常量同时充当配置缺失/非法时的回退值（配置见 default_config）
        self._info_text_key = "target_info"
        # 左上角归一化坐标：x 固定；y 只作为下限，实际 y 由 _info_text_y_norm() 动态计算
        self._info_text_pos = (0.015, 0.20)
        # 附近标记云团的原始像素半径（与 _draw_nearby_markers 的偏移量同源）
        self._nearby_marker_radius_px = self._nearby_marker_max_distance * self._arrow_scale
        # 文字块与云团底边之间保留的原始像素间隙
        self._info_text_gap_px = 16.0
        self._info_text_font_size_norm = 0.024  # 字号（相对窗口高度）的回退值
        self._info_text_color = (255, 255, 255)  # RGB
        self._info_text_alpha = 235  # 文字 alpha 回退值（对应「浮层文字透明度」92）
        self._info_text_line_spacing = 1.35
        self._info_text_panel_alpha = 150  # 底板 alpha 回退值（对应「浮层背景透明度」59）

        self._load_marked()
        # dirty-save 控制：标记后延迟合并写盘
        self._dirty = False
        self._last_save_time = 0.0
        # 标记按键状态：记录本次按住期间是否已标记（防止按住期间反复标记）
        self._mark_key_held_in_cycle = False
        # 按键按下计时：用于判断按住持续时间（None 表示当前未按下）
        self._mark_key_hold_start = None
        # 锁定待标记的目标（在接近阈值内）
        # 格式: {'map_id': str, 'hash': str, 'start_time': float | None}
        self._mark_lock_target = None

    @staticmethod
    def _format_seconds(value: float) -> str:
        """把秒数格式化为界面友好文本：20.0 → 20，0.8 → 0.8。"""
        text = f"{float(value):.2f}".rstrip("0").rstrip(".")
        return text or "0"

    def _mark_hold_seconds(self) -> float:
        """读取「标记按住时长」配置（秒）。

        缺失、非法或小于等于 0 时回退到 default_config 中的默认值。
        每轮读取，便于在 UI 中即时调整而无需重启任务。
        """
        fallback = float(self.default_config.get("标记按住时长") or 2.0)
        try:
            seconds = float(self.config.get("标记按住时长", fallback))
        except (TypeError, ValueError):
            return fallback
        return seconds if math.isfinite(seconds) and seconds > 0 else fallback

    def build_instructions(self):
        """物品导航配置使用说明（简要）。

        文本经 self.tr() 走 gettext i18n（msgid 写入 i18n/*/LC_MESSAGES/ok.po）；
        emoji、树形符号与 HTML 样式留在代码里拼接，只有可翻译的纯文本进入目录。
        由 InstructionsMixin 延迟构建，任务卡片上的「使用说明」按钮读取 instructions 时才会执行。
        """
        marked_display = str(self._marked_store).replace("\\", "/")
        # 「获取 content」的分步说明。地址经 .format() 注入，避免把 URL 写进 msgid 收集池。
        content_steps = [
            inst_line(f"└─ {self.tr('1. 打开浏览器，按 F12 打开开发者工具')}", indent=1),
            inst_line(
                "└─ " + self.tr("2. 访问 {map_url} 并登录").format(map_url=OFFICIAL_MAP_PAGE_URL),
                indent=1,
            ),
            inst_line(
                "└─ " + self.tr("3. 切到「网络 / Network」标签，在筛选框输入 {api}").format(api=HG_CHECK_API_URL),
                indent=1,
            ),
            inst_line(
                f"└─ {self.tr('4. 在筛选结果里选中该请求，从「响应 / Response」中取 data.content 的值')}", indent=1
            ),
            inst_line(
                f"└─ {self.tr('5. 把该值填入本任务 content；或填入账号配置页的「地图同步 content」，再用「地图账号」选择该账号')}",
                indent=1,
            ),
        ]
        return "<br>".join(
            [
                inst_line("📍 " + self.tr("物品导航配置说明"), "#FF5555", bold=True),
                inst_line(
                    "⚙️ " + self.tr("位置来源：content 有值时使用官方地图 WebSocket，为空时使用本地 WS"),
                    "#FF5555",
                    bold=True,
                ),
                inst_gap(),
                inst_line("🧭 " + self.tr("关键配置"), "#FE821D", bold=True),
                inst_line(
                    f"└─ {self.tr('选择物品：勾选要导航的物品，只匹配当前地图；为空时不会有任何目标')}", indent=1
                ),
                inst_line(
                    f"└─ {self.tr('地图账号：content 为空时从中读取地图同步 content，选项来自账号配置页')}", indent=1
                ),
                inst_line(f"└─ {self.tr('标记按键：接近目标后用于标记已获取的键位，仅支持单个字符')}", indent=1),
                inst_line(f"└─ {self.tr('标记按住时长：连续按住标记键达到该时长即记为已获取（默认 2 秒）')}", indent=1),
                inst_gap(),
                # 浮层显示的文案分组：浮层功能本身在 feat/window-overlay-text 分支，
                # 但使用说明只存在于本分支，因此该分组随使用说明一起落地。
                inst_line("🎨 " + self.tr("浮层显示"), "#FE821D", bold=True),
                inst_line(
                    f"└─ {self.tr('浮层信息：开启后在浮层上显示物品名、距离、方位与高度（默认开启）')}", indent=1
                ),
                inst_line(f"└─ {self.tr('浮层文字透明度 / 浮层背景透明度：取值 0-100，0 表示完全透明')}", indent=1),
                inst_line(f"└─ {self.tr('浮层字号：以 1080p 窗口高度为基准的像素值，会随窗口高度等比缩放')}", indent=1),
                inst_gap(),
                inst_line("🔑 " + self.tr("获取 content（官方地图同步）"), "#FE821D", bold=True),
                *content_steps,
                inst_gap(),
                inst_line("🖱️ " + self.tr("标记已获取"), "#FE821D", bold=True),
                inst_line(
                    "└─ "
                    + self.tr(
                        "水平距离 {distance} 以内连续按住标记键 {seconds} 秒即记为已获取，之后不再指向该点"
                    ).format(
                        distance=self._format_seconds(self._near_xz_threshold),
                        seconds=self._format_seconds(self._mark_hold_seconds()),
                    ),
                    indent=1,
                ),
                inst_line(f"└─ {self.tr('中途松开或离开范围会取消本次标记')}", indent=1),
                inst_line(
                    f"└─ {self.tr('标记记录保存在 {path}，删除对应条目即可重新导航').format(path=marked_display)}",
                    indent=1,
                ),
                inst_gap(),
                inst_line("📡 " + self.tr("本地 WS 模式准备"), "#FE821D", bold=True),
                inst_line(
                    "└─ "
                    + self.tr("安装 Tampermonkey 并导入 {path}（可用「油猴脚本帮助」按钮打开脚本目录）").format(
                        path=RELAY_USER_SCRIPT
                    ),
                    indent=1,
                ),
                inst_line(f"└─ {self.tr('打开网页地图并保持页面存活，脚本会自动开启位置同步')}", indent=1),
                inst_gap(),
                inst_line("🪟 " + self.tr("显示条件"), "#FE821D", bold=True),
                inst_line(f"└─ {self.tr('箭头仅在游戏窗口处于前台时显示')}", indent=1),
                inst_line(f"└─ {self.tr('浮层同时显示当前指向的物品名、距离、方位（北、东北、东、东南、南、西南、西、西北）与上下高度')}", indent=1),
                inst_line(f"└─ {self.tr('游戏窗口不存在或不可见时任务会暂停并停止位置同步')}", indent=1),
            ]
        )

    @staticmethod
    def _get_map_account_options() -> list[str]:
        data = load_overrides()
        registry = data.get("account_registry") or {}
        account_list_text = str(data.get("account_list_text") or "")
        names: list[str] = [""]

        for raw in account_list_text.splitlines():
            name = raw.strip().split(",", 1)[0].strip()
            if name and name not in names:
                names.append(name)

        for meta in registry.values():
            if isinstance(meta, dict):
                name = str(meta.get("username") or "").strip()
                if name and name not in names:
                    names.append(name)

        return names

    def _is_game_window_alive(self) -> bool:
        hwnd_window = getattr(getattr(self, "executor", None), "device_manager", None)
        hwnd_window = getattr(hwnd_window, "hwnd_window", None)
        hwnd = getattr(hwnd_window, "hwnd", None)
        if not hwnd:
            return False
        try:
            return bool(win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd))
        except Exception:
            return False

    def _cleanup_navigator_runtime(self):
        if self._is_map_ws_client_enabled():
            self._stop_map_ws_client()
        if self._is_ws_position_server_enabled():
            self._stop_ws_position_server()
        self.clear_window_arrows()
        self._ws_server_start_logged = False

    def _get_account_map_content(self) -> str:
        direct_content = str(self.config.get("content") or "").strip()
        if direct_content:
            return direct_content

        selected_account = str(self.config.get("地图账号") or "").strip()
        if selected_account:
            account_id = resolve_account_id(selected_account, create_if_missing=False) or selected_account
            return get_account_map_content(account_id, account_name=selected_account)

        account_id = str(getattr(self, "current_account_id", "") or "").strip()
        account_name = str(getattr(self, "current_user", "") or "").strip()

        executor = getattr(self, "executor", None)
        current_task = getattr(executor, "current_task", None) if executor is not None else None
        if current_task is not None and current_task is not self:
            account_id = account_id or str(getattr(current_task, "current_account_id", "") or "").strip()
            account_name = account_name or str(getattr(current_task, "current_user", "") or "").strip()

        return get_account_map_content(account_id or account_name, account_name=account_name)

    def _point_height_delta(self, pt: dict, item_name: str | None, py: float) -> float:
        """目标相对玩家的高度差（正值表示目标在上方），套用特殊物品的 Y 修正。"""
        target_y = pt.get("y", 0)
        for cfg in SPECIAL_ITEM_Y_OFFSET.values():
            if cfg["pattern"].match(item_name or ""):
                target_y += cfg["value"]
                break
        return target_y - py

    def _nearby_marker_length_px(self, dy: float) -> float:
        """附近小箭头的长度：按高差从最短伸缩到最长（超过上限后不再变长）。"""
        t = min(abs(float(dy)) / max(1e-6, float(self._height_max_abs_dy)), 1.0)
        return self._nearby_marker_min_len_px + (
            self._nearby_marker_max_len_px - self._nearby_marker_min_len_px
        ) * t

    def _draw_nearby_markers(
        self,
        px: float,
        pz: float,
        py: float,
        candidates: dict[str, list],
        map_id: str,
    ):
        try:
            width, height = self._get_window_arrow_size()
            if width <= 0 or height <= 0:
                return

            center_x = width * self._arrow_center_rel[0]
            center_y = height * self._arrow_center_rel[1]

            max_distance = self._nearby_marker_max_distance
            for item_name, pts in candidates.items():
                for pt in pts:
                    h = self._point_hash(pt, item_name)

                    if h in self._marked.get(map_id, set()):
                        continue

                    dx = pt.get("x", 0) - px
                    dz = pt.get("z", 0) - pz

                    dist = math.hypot(dx, dz)

                    if dist > max_distance:
                        continue

                    screen_x = center_x + dx * self._arrow_scale
                    screen_y = center_y - dz * self._arrow_scale

                    # 高差编码：箭尖固定在目标位置，尾巴长度随 |dy| 伸缩，
                    # dy>0（目标在上）时尾巴在下方、箭头朝上；dy<0 时相反。
                    dy = self._point_height_delta(pt, item_name, py)
                    marker_len = self._nearby_marker_length_px(dy)
                    tail_y = screen_y + marker_len if dy > 0 else screen_y - marker_len

                    self.draw_window_arrow(
                        start_x_norm=screen_x / width,
                        start_y_norm=tail_y / height,
                        end_x_norm=screen_x / width,
                        end_y_norm=screen_y / height,
                        shaft_width_norm=self._arrow_shaft_width_norm * 0.5,
                        color=(255, 255, 0),
                        alpha=180,
                        arrow_type=f"nearby_{h}",
                    )

        except Exception as e:
            self.log_error(f"[附近目标] 绘制失败: {e}")

    def open_userscript_help(self, *_):
        """打开浏览器油猴脚本使用帮助，并打开脚本目录。"""
        script_rel = Path("assets") / "scripts" / "endfield-ws-position-relay.user.js"
        script_abs = (Path.cwd() / script_rel).resolve()
        script_dir = script_abs.parent
        help_text = (
            "终末地坐标转发油猴脚本使用帮助\n\n"
            "1. 安装浏览器扩展 Tampermonkey（油猴）。\n"
            "2. 打开脚本目录并导入脚本文件：\n"
            f"   {script_abs}\n"
            "3. 在 Tampermonkey 中启用该脚本。\n"
            "4. 打开网页地图 https://game.skland.com/map/endfield ，确认脚本已运行。\n"
            "5. 启动物品导航任务后，程序会监听 ws://127.0.0.1:3001 的位置数据。\n\n"
            "提示：\n"
            "- 先确保本地未被防火墙拦截 3001 端口。\n"
            "- 如脚本无日志，检查 Tampermonkey 是否允许在目标网址运行。\n"
        )

        try:
            tf = tempfile.NamedTemporaryFile(delete=False, suffix=".txt", mode="w", encoding="utf-8")
            tf.write(help_text)
            tf.flush()
            tf.close()
            help_path = tf.name

            if os.name == "nt":
                os.startfile(help_path)
            else:
                webbrowser.open(f"file://{help_path}")
            self.log_info(f"已打开油猴脚本帮助: {help_path}")
        except Exception as e:
            self.log_error(f"打开油猴脚本帮助失败: {e}")

        try:
            if os.name == "nt":
                if script_abs.exists():
                    subprocess.Popen(["explorer", f"/select,{script_abs}"])
                else:
                    os.startfile(str(script_dir))
            else:
                webbrowser.open(f"file://{script_dir}")
            self.log_info(f"已打开油猴脚本目录: {script_dir}")
        except Exception as e:
            self.log_error(f"打开油猴脚本目录失败: {e}")

    # --- persistence for marked points ---
    def _load_marked(self):
        try:
            # 先尝试加载新路径
            if self._marked_store.exists():
                data = json.loads(self._marked_store.read_text(encoding="utf-8"))
                for k, v in (data or {}).items():
                    self._marked[k] = set(v or [])
                return

            # 如果新路径不存在，检查旧路径并迁移
            old_path = Path("assets") / "items" / "map" / "marked_points.json"
            if old_path.exists():
                # 存储路径是运行时值不过 tr
                self.log_info(
                    self.tr("发现旧路径的 marked_points.json，正在迁移到新路径: {path}").format(path=self._marked_store)
                )
                try:
                    data = json.loads(old_path.read_text(encoding="utf-8"))
                    for k, v in (data or {}).items():
                        self._marked[k] = set(v or [])
                    # 自动保存到新路径
                    self._save_marked()
                    # 可选：删除旧文件
                    try:
                        old_path.unlink()
                        self.log_info("旧文件已删除")
                    except Exception:
                        self.log_info("旧文件保留（删除失败）")
                except Exception as e:
                    self.log_error(f"迁移旧文件失败: {e}")
        except Exception as e:
            self.log_error(f"加载 marked_points 失败: {e}")

    def _save_marked(self):
        try:
            with self._marked_lock:
                data = {k: list(v) for k, v in self._marked.items()}
                self._marked_store.parent.mkdir(parents=True, exist_ok=True)
                # 原子写入：先写到临时文件再替换
                tmp = self._marked_store.with_suffix(".tmp")
                tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                tmp.replace(self._marked_store)
                # 更新最后保存时间
                self._last_save_time = time.time()
                self._dirty = False
        except Exception as e:
            self.log_error(f"保存 marked_points 失败: {e}")

    @staticmethod
    def _point_hash(pt: dict[str, float], item_name: str | None = None) -> str:
        # 包含物品名以避免不同物品同坐标冲突
        name = item_name or ""
        return f"{name}|{round(pt.get('x', 0), 3)}|{round(pt.get('y', 0), 3)}|{round(pt.get('z', 0), 3)}"

    # --- core helpers ---
    @staticmethod
    def _xy_dist(a: tuple[float, float], b: tuple[float, float]) -> float:
        return math.hypot(a[0] - b[0], a[1] - b[1])

    def _get_candidates_for_map(self, map_id: str, selected_items: list[str]) -> dict[str, list]:
        # Use item_map_query to get items; then restrict to given map_id
        if not selected_items:
            return {}
        summary = item_map_query.get_item_map(selected_items)
        return summary.get(map_id, {})

    def _draw_nav_arrow(self, dx: float, dz: float, tooltip: str):
        try:
            # 使用 _get_window_arrow_size() 而不是查找 self.width/self.height
            width, height = self._get_window_arrow_size()
            if width <= 0 or height <= 0:
                return

            center_x = width * self._arrow_center_rel[0]
            center_y = height * self._arrow_center_rel[1]
            max_length = min(width, height) * self._arrow_max_len_ratio
            draw_length = math.hypot(dx, dz) * self._arrow_scale
            # 纵向方向按当前导航坐标系翻转，避免上下显示反向。
            angle_deg = math.degrees(math.atan2(dx, dz))

            success = self.draw_window_arrow_from_center(
                center_x=center_x,
                center_y=center_y,
                max_length=max_length,
                draw_length=draw_length,
                angle_deg=angle_deg,
                color=self._arrow_color,
                alpha=self._arrow_alpha,
                shaft_width_norm=self._arrow_shaft_width_norm,
                arrow_type="default",
            )

            if not success:
                self.log_info("[箭头] 绘制失败")
                return

            if tooltip:
                self.info_set("导航箭头", tooltip)
        except Exception as e:
            self.log_error(f"[箭头] 异常: {e}")

    # --- 浮层目标信息文字（物品名 / 距离 / 方位 / 高度） ---

    # 「浮层字号」以该窗口高度为基准换算成归一化字号（窗口变高时字号等比放大）
    INFO_TEXT_FONT_REFERENCE_HEIGHT = 1080

    @staticmethod
    def _percent_to_alpha(value, fallback: int) -> int:
        """把 0-100 的百分比透明度换算成 0-255 的 alpha。

        缺失或非法（非数字 / NaN）时回退到 fallback；越界值截断到 [0, 100]。
        与「标记按住时长」不同，0 是合法取值（完全透明），因此不做 ≤0 回退。
        """
        try:
            percent = float(value)
        except (TypeError, ValueError):
            return fallback
        if not math.isfinite(percent):
            return fallback
        return round(max(0.0, min(100.0, percent)) * 255.0 / 100.0)

    def _overlay_info_enabled(self) -> bool:
        """是否在浮层上显示目标信息文字。"""
        return bool(self.config.get("浮层信息", True))

    def _overlay_text_alpha(self) -> int:
        """浮层文字 alpha（0-255），来自「浮层文字透明度」（0-100）。"""
        return self._percent_to_alpha(self.config.get("浮层文字透明度"), self._info_text_alpha)

    def _overlay_panel_alpha(self) -> int:
        """浮层文字底板 alpha（0-255），来自「浮层背景透明度」（0-100）。"""
        return self._percent_to_alpha(self.config.get("浮层背景透明度"), self._info_text_panel_alpha)

    def _overlay_font_size_norm(self) -> float:
        """浮层字号（像素，基准 1080p）换算成相对窗口高度的归一化字号。"""
        try:
            pixels = float(self.config.get("浮层字号"))
        except (TypeError, ValueError):
            return self._info_text_font_size_norm
        if not math.isfinite(pixels) or pixels <= 0:
            return self._info_text_font_size_norm
        return pixels / self.INFO_TEXT_FONT_REFERENCE_HEIGHT

    @staticmethod
    def _compass_index(dx: float, dz: float) -> int:
        """把「玩家 -> 目标」水平向量映射到八方位索引（0=正北，顺时针）。"""
        angle = math.degrees(math.atan2(dx, dz)) % 360.0
        return int((angle + 22.5) // 45.0) % 8

    def _height_label(self, dy_height: float) -> str:
        if abs(dy_height) < 0.05:
            return self.tr("同高")
        if dy_height > 0:
            return self.tr("上方")
        if dy_height < 0:
            return self.tr("下方")
        return self.tr("同高")

    def build_target_info_lines(
        self,
        item_name: str | None,
        dx: float,
        dz: float,
        dist_xz: float,
        dy_height: float,
    ) -> list[str]:
        """拼装浮层上显示的目标信息：物品名 / 距离+方位 / 高度。"""
        direction = self.tr(COMPASS_LABELS[self._compass_index(dx, dz)])
        lines = [
            str(item_name or "").strip(),
            self.tr("距离 {distance} · 方位 {direction}").format(distance=f"{float(dist_xz):.1f}", direction=direction),
            self.tr("高度 {direction} {height}").format(
                direction=self._height_label(dy_height), height=f"{abs(float(dy_height)):.1f}"
            ),
        ]
        return [line for line in lines if line]

    def _info_text_y_norm(self, height: int) -> float:
        """计算文字块的归一化 y：跟随「附近标记云团」的原始像素半径下移。

        `_draw_nearby_markers` 的偏移量是原始像素（`距离 × _arrow_scale`，不随分辨率
        缩放），而文字锚点是归一化的。若用固定归一化 y，低分辨率下云团的归一化尺寸
        变大，底边会压到文字上（1280×720 实测重叠约 9.5k px²）。这里同样用原始像素
        换算间隙，保证任意分辨率下文字都稳定位于云团下方。
        """
        safe_height = max(1, int(height))
        # 小箭头长度会随高差伸缩，云团底边要把最长的小箭头一并算进去，
        # 否则高差大时文字会被箭头压住
        cloud_bottom_px = (
            safe_height * self._arrow_center_rel[1]
            + self._nearby_marker_radius_px
            + self._nearby_marker_max_len_px
        )
        y_norm = (cloud_bottom_px + self._info_text_gap_px) / safe_height
        return min(max(y_norm, self._info_text_pos[1]), 0.85)

    def _draw_target_info_text(
        self,
        item_name: str | None,
        dx: float,
        dz: float,
        dist_xz: float,
        dy_height: float,
    ) -> None:
        try:
            if not self._overlay_info_enabled():
                # 关闭时主动清一次，保证运行中切换开关立即生效
                self.clear_window_texts()
                return
            lines = self.build_target_info_lines(item_name, dx, dz, dist_xz, dy_height)
            if not lines:
                return
            width, height = self._get_window_arrow_size()
            if width <= 0 or height <= 0:
                return
            self.draw_window_text(
                lines=lines,
                text_key=self._info_text_key,
                x_norm=self._info_text_pos[0],
                y_norm=self._info_text_y_norm(height),
                color=self._info_text_color,
                alpha=self._overlay_text_alpha(),
                font_size_norm=self._overlay_font_size_norm(),
                line_spacing=self._info_text_line_spacing,
                panel_alpha=self._overlay_panel_alpha(),
            )
        except Exception as e:
            self.log_error(f"[目标信息] 绘制失败: {e}")

    # --- keyboard check (detect player pressing mark key) ---
    def _is_key_pressed(self, key: str) -> bool:
        # simple mapping for letters and function keys like 'f'
        try:
            import ctypes

            vk = ord(key.upper()) if len(key) == 1 else None
            if vk is None:
                return False
            state = ctypes.windll.user32.GetAsyncKeyState(vk)
            return bool(state & 0x8000)
        except Exception:
            return False

    def run(self):
        self.check_resolution()
        try:
            if not self._is_game_window_alive():
                self._cleanup_navigator_runtime()
                if not self._navigator_window_missing_logged:
                    self.log_info("物品导航：游戏窗口不存在或不可见，已暂停地图同步")
                    self._navigator_window_missing_logged = True
                self.info_set("导航", "游戏窗口不存在，等待重新选择/启动游戏")
                return

            self._navigator_window_missing_logged = False
            map_cred = self._get_account_map_content()

            if map_cred:
                if self._is_ws_position_server_enabled():
                    self._stop_ws_position_server()
                if not self._is_map_ws_client_enabled() or self._map_ws_auth_source != map_cred:
                    self.log_info("ItemNavigatorTask 启动 - 正在启动官方地图WS客户端")
                    self._start_map_ws_client(map_cred)
                    self._ws_server_start_logged = False
            else:
                if self._is_map_ws_client_enabled():
                    self._stop_map_ws_client()
                if not self._is_ws_position_server_enabled():
                    self.log_info("ItemNavigatorTask 启动 - 正在启动WS服务")
                    self._start_ws_position_server(host="127.0.0.1", port=3001)
                    self._ws_server_start_logged = False
                elif not self._ws_server_start_logged:
                    # 首次检测到 WS 服务已启动，仅记录一次
                    self.log_info("ItemNavigatorTask: WS服务已启动 ws://127.0.0.1:3001")
                    self._ws_server_start_logged = True

            # read current selected items from task config (this is user-facing)
            selected_items = list(self.config.get("选择物品") or [])

            # fetch player position from local websocket service
            # 优先获取最新数据，如果没有新数据则返回缓存的旧值（避免"数据不完整"错误）
            try:
                payload = self._recv_ws_position_payload_or_cached(timeout=0.1)
            except Exception:
                self.info_set("导航", "无法读取WS位置")
                self.sleep(1.0)
                return

            # parse payload (兼容扁平结构 / data 包裹)
            pos, map_id, px, py, pz = self._extract_position_payload(payload)
            if not pos or not map_id:
                if map_cred:
                    self.info_set("导航", "地图WS位置数据待接收...")
                else:
                    self.info_set("导航", "WS位置数据待接收... (需要客户端连接到 ws://127.0.0.1:3001)")
                self.sleep(1.0)
                return

            # build candidates for this map and selected items
            candidates = self._get_candidates_for_map(map_id, selected_items)
            if not candidates:
                self.info_set("导航", "无候选物品")
                self.sleep(0.5)
                return

            best = None
            best_meta = None
            best_dxz = float("inf")

            for item_name, pts in candidates.items():
                for pt in pts:
                    h = self._point_hash(pt, item_name)
                    if h in self._marked.get(map_id, set()):
                        continue  # 跳过已标记的物品，继续查找下一个
                    dxz = self._xy_dist((px, pz), (pt.get("x", 0), pt.get("z", 0)))
                    if dxz < best_dxz:
                        best_dxz = dxz
                        best = pt
                        best_meta = item_name

            if best is None:
                self.info_set("导航", "无未标记候选")
                self.sleep(0.5)
                return

            # y 是高度，方位与水平距离都在 xz 平面
            dy_height = self._point_height_delta(best, best_meta, py)
            near_xz = best_dxz <= float(self._near_xz_threshold)

            # direction angle in degrees for XZ vector (player->target) relative to +X
            dx = best.get("x", 0) - px
            dz = best.get("z", 0) - pz
            angle = math.degrees(math.atan2(dz, dx))

            status = f"目标={best_meta} 距离XZ={best_dxz:.3f} 角度={angle:.3f}°"
            if near_xz:
                updown = "上方" if dy_height > 0 else "下方" if dy_height < 0 else "同高"
                status += f" 接近: 高差Y={dy_height:.3f} ({updown})"

            # publish minimal UI info (任务显示栏)
            self.info_set("导航", status)

            # overlay: 方向箭头 + 附近目标标记（小箭头长度表示高差）
            self._draw_nearby_markers(
                px=px,
                pz=pz,
                py=py,
                candidates=candidates,
                map_id=map_id,
            )
            self._draw_nav_arrow(dx, dz, tooltip=f"{best_meta} | XZ:{best_dxz:.3f} | Y:{dy_height:.3f}")
            # 浮层文字：直接显示当前指向的物品名、距离方位与上下高度
            self._draw_target_info_text(
                item_name=best_meta,
                dx=dx,
                dz=dz,
                dist_xz=best_dxz,
                dy_height=dy_height,
            )

            # 标记逻辑：锁定目标并要求连续按住配置时长（「标记按住时长」）才能标记
            mark_key = str(self.config.get("标记按键") or "").strip() or "f"
            cur_key = self._is_key_pressed(mark_key)
            mark_hold_seconds = self._mark_hold_seconds()

            if near_xz:
                # 计算目标哈希并确保锁定目标为当前最近目标
                h = self._point_hash(best, best_meta)
                if self._mark_lock_target is None or self._mark_lock_target.get("hash") != h:
                    self._mark_lock_target = {"map_id": map_id, "hash": h, "start_time": None}

                # 如果按键被按下，开始/继续计时；若连续保持足够长则标记
                if cur_key:
                    now = self.active_time()
                    if self._mark_lock_target.get("start_time") is None:
                        self._mark_lock_target["start_time"] = now
                    else:
                        elapsed = now - self._mark_lock_target["start_time"]
                        if elapsed >= mark_hold_seconds:
                            # 最终确认未被提前标记
                            if h not in self._marked.get(map_id, set()):
                                with self._marked_lock:
                                    self._marked.setdefault(map_id, set()).add(h)
                                    self._dirty = True
                                self.info_set("导航", f"已标记: {best_meta} ({h})")
                            # 标记完成或已标记，清除锁定
                            self._mark_lock_target = None
                else:
                    # 在按住计时期间若有一帧松开，则取消本次锁定
                    if self._mark_lock_target and self._mark_lock_target.get("start_time") is not None:
                        self._mark_lock_target = None
            else:
                # 离开接近阈值时清除任何锁定
                self._mark_lock_target = None

        except Exception as e:
            self.log_error(f"ItemNavigatorTask 异常: {e}")

        # 延迟保存：合并多次标记以减少 IO
        try:
            if self._dirty and (time.time() - self._last_save_time) > 3.0:
                # 写盘由 _save_marked 维护 last_save_time 与 _dirty 标志
                self._save_marked()
        except Exception:
            pass

        # lightweight sleep to avoid busy loop; polling interval intentionally not user-configured here
        self.sleep(0.2)
