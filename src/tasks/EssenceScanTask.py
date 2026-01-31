from pathlib import Path

import pywintypes
from qfluentwidgets import FluentIcon

from src.tasks.BaseEfTask import BaseEfTask
from src.essence.weapon_data import load_weapon_data, match_weapon_requirements


_GOLD_COLOR = {
    "r": (160, 255),
    "g": (130, 255),
    "b": (0, 140),
}

_PURPLE_COLOR = {
    # 紫色品质条可能受 HDR/亮度影响变得更“灰”，适当放宽范围提升检出率
    "r": (60, 255),
    "g": (0, 180),
    "b": (60, 255),
}

_DARK_COLOR = {
    "r": (0, 90),
    "g": (0, 90),
    "b": (0, 90),
}

_WHITE_COLOR = {
    "r": (220, 255),
    "g": (220, 255),
    "b": (220, 255),
}

# 锁按钮状态判断：使用“暗像素左右差异”
# - diff 越大越像“开锁”（未锁）
# - diff 越小越像“闭锁”（已锁）
_LOCK_ASYM_LOCKED_THRESHOLD = 0.22
_LOCK_ASYM_UNLOCKED_THRESHOLD = 0.30


class EssenceScanTask(BaseEfTask):
    """
    一次性遍历武器基质列表，识别右侧信息面板，匹配毕业基质并自动上锁。
    参考：../Endfield_essence 的网格遍历/滑动/锁定思路。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "基质毕业识别与上锁"
        self.description = "遍历武器基质列表，匹配 weapon_data.csv 并自动上锁毕业基质"
        self.icon = FluentIcon.SEARCH
        self.show_info_panel = True
        self.default_config.update(
            {
                "上锁毕业基质": True,
                # 以下为内部参数，前面加 "_" 以在 GUI 配置页隐藏
                "_武器数据CSV": str(Path("assets") / "weapon_data.csv"),
                # ok 的 ModifyListItem 假设 list 元素可 len()（即字符串）。这里用字符串避免 GUI 崩溃。
                "_参考分辨率": ["2560", "1440"],
                "_网格起点": ["190", "256"],
                "_网格步长": ["204", "208"],
                "_每行数量": 9,
                "_每屏行数": 5,
                "_图标采样尺寸": ["238", "236"],
                "_锁按钮坐标": ["2444", "372"],
                "_点击等待秒": 0.35,
                "_滑动距离像素": 140,
                "_滑动后等待秒": 1.5,
                "_最大翻页": 200,
                "_毕业列表保留行数": 30,
            }
        )
        self.config_description.update(
            {
                "上锁毕业基质": "命中毕业词条后自动点击右侧小锁上锁",
            }
        )

    def post_init(self) -> None:
        super().post_init()
        # 迁移旧配置：
        # 1) 老 key -> 新 key（内部参数加 "_" 并隐藏）
        migrate_keys = {
            "武器数据CSV": "_武器数据CSV",
            "参考分辨率": "_参考分辨率",
            "网格起点": "_网格起点",
            "网格步长": "_网格步长",
            "每行数量": "_每行数量",
            "每屏行数": "_每屏行数",
            "图标采样尺寸": "_图标采样尺寸",
            "锁按钮坐标": "_锁按钮坐标",
            "点击等待秒": "_点击等待秒",
            "滑动距离像素": "_滑动距离像素",
            "滑动后等待秒": "_滑动后等待秒",
            "最大翻页": "_最大翻页",
            "毕业列表保留行数": "_毕业列表保留行数",
        }
        for old_key, new_key in migrate_keys.items():
            if old_key in self.config and new_key not in self.config:
                self.config[new_key] = self.config.get(old_key)
                self.config.pop(old_key, None)

        # 2) list[int] -> list[str]（避免 ok 的 ModifyListItem 对 int 调 len() 崩溃）
        for key in ("_参考分辨率", "_网格起点", "_网格步长", "_图标采样尺寸", "_锁按钮坐标"):
            value = self.config.get(key)
            if isinstance(value, list) and any(not isinstance(v, str) for v in value):
                self.config[key] = [str(v) for v in value]

    def _parse_xy(self, value, default) -> tuple[int, int]:
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            try:
                return int(value[0]), int(value[1])
            except Exception:
                return int(default[0]), int(default[1])
        if isinstance(value, str):
            text = value.strip().lower().replace("x", ",")
            parts = [p.strip() for p in text.split(",") if p.strip()]
            if len(parts) >= 2:
                try:
                    return int(parts[0]), int(parts[1])
                except Exception:
                    return int(default[0]), int(default[1])
        return int(default[0]), int(default[1])

    def _ref_w_h(self) -> tuple[int, int]:
        return self._parse_xy(self.config.get("_参考分辨率"), (2560, 1440))

    def _ref_xy(self, xy, default) -> tuple[int, int]:
        return self._parse_xy(xy, default)

    def _ref_box(self, x1: float, y1: float, x2: float, y2: float, *, name: str):
        ref_w, ref_h = self._ref_w_h()
        return self.box_of_screen(
            x1 / ref_w,
            y1 / ref_h,
            x2 / ref_w,
            y2 / ref_h,
            name=name,
        )

    def _click_ref(self, x: float, y: float, *, after_sleep: float = 0.0):
        ref_w, ref_h = self._ref_w_h()
        self.click(x / ref_w, y / ref_h, hcenter=True, vcenter=True, after_sleep=after_sleep)

    def _is_locked(self, lock_x: int, lock_y: int) -> bool:
        return self._lock_asymmetry(lock_x, lock_y) <= _LOCK_ASYM_LOCKED_THRESHOLD

    def _lock_asymmetry(self, lock_x: int, lock_y: int) -> float:
        """
        基于“开锁/闭锁”图标的不对称性计算一个指标（越大越像开锁）。
        使用灰度下的“暗像素”左右占比差异，比纯白占比对 HDR/抗锯齿更稳。
        """
        box = self._ref_box(lock_x - 20, lock_y - 20, lock_x + 20, lock_y + 20, name="lock_icon")
        frame = box.crop_frame(self.frame)
        if frame is None or getattr(frame, "size", 0) == 0:
            return 0.0

        # frame 为 BGR/RGB 均可，灰度用均值近似即可
        gray = frame.mean(axis=2)
        dark = gray < 120
        h, w = dark.shape[:2]
        if w < 2:
            return 0.0
        left = dark[:, : w // 2].mean()
        right = dark[:, w // 2 :].mean()
        return float(abs(left - right))

    def _try_lock(self, lock_x: int, lock_y: int) -> tuple[bool, bool]:
        """
        锁按钮是“切换”按钮：已锁再点会解锁。

        返回 (locked_ok, did_lock)
        - locked_ok: 最终是否处于“锁定”状态（或认为已锁定）
        - did_lock: 本次是否执行过“上锁动作”（仅用于计数）
        """
        self.next_frame()
        diff0 = self._lock_asymmetry(lock_x, lock_y)
        # diff0 越小越像“闭锁”（已锁）。边界区域宁愿当作已锁，避免误点导致解锁。
        if diff0 <= _LOCK_ASYM_UNLOCKED_THRESHOLD:
            return True, False

        # 明确未锁：点一次尝试上锁，并做一次确认
        self._click_ref(lock_x, lock_y, after_sleep=0.45)
        self.next_frame()
        diff1 = self._lock_asymmetry(lock_x, lock_y)
        # 点一次后，只要不再“明确开锁”，就认为上锁成功（避免二次点击导致反向切换）
        if diff1 <= _LOCK_ASYM_UNLOCKED_THRESHOLD:
            return True, True

        # 若仍然明确未锁（可能点击未生效），再点一次；否则不再尝试，避免误触发“解锁”
        if diff1 >= _LOCK_ASYM_UNLOCKED_THRESHOLD:
            self._click_ref(lock_x, lock_y, after_sleep=0.45)
            self.next_frame()
            diff2 = self._lock_asymmetry(lock_x, lock_y)
            return diff2 <= _LOCK_ASYM_UNLOCKED_THRESHOLD, diff2 <= _LOCK_ASYM_UNLOCKED_THRESHOLD

        return False, False

    def _is_gold_cell(self, cell_box) -> bool:
        # 采样底部颜色条（品质提示区域）
        # 使用更宽的采样带，避免不同亮度/HDR/抗锯齿导致底条像素不足
        # Box.copy 的 height_offset 是“增量”，要截取底部 15%：height_offset = -0.85h
        strip_box = cell_box.copy(y_offset=cell_box.height * 0.85, height_offset=-cell_box.height * 0.85)
        # 去掉两侧黑边/边框干扰，提高色占比的稳定性
        strip_box = strip_box.copy(x_offset=strip_box.width * 0.12, width_offset=-strip_box.width * 0.24)
        gold_percent = float(self.calculate_color_percentage(_GOLD_COLOR, strip_box))
        purple_percent = float(self.calculate_color_percentage(_PURPLE_COLOR, strip_box))
        if purple_percent > 0.08 and purple_percent > gold_percent:
            return False
        return gold_percent > 0.03 and gold_percent > purple_percent

    def _is_purple_cell(self, cell_box) -> bool:
        strip_box = cell_box.copy(y_offset=cell_box.height * 0.85, height_offset=-cell_box.height * 0.85)
        strip_box = strip_box.copy(x_offset=strip_box.width * 0.12, width_offset=-strip_box.width * 0.24)
        gold_percent = float(self.calculate_color_percentage(_GOLD_COLOR, strip_box))
        purple_percent = float(self.calculate_color_percentage(_PURPLE_COLOR, strip_box))
        # 紫色条：purple 占比明显，且 gold 占比很低（避免把金色边框误判为紫）
        return purple_percent > 0.08 and purple_percent > gold_percent and gold_percent < 0.03

    def _scroll_next_page(self, grid_x: int, grid_y: int, dx: int, dy: int, cols: int, rows: int, move_pixel: int):
        start_x = grid_x + (cols // 2) * dx
        start_y = grid_y + (rows - 1) * dy
        end_y = start_y - move_pixel

        ref_w, ref_h = self._ref_w_h()
        frame_h, frame_w = self.frame.shape[:2]
        from_x = int(round(start_x / ref_w * frame_w))
        from_y = int(round(start_y / ref_h * frame_h))
        to_x = int(round(start_x / ref_w * frame_w))
        to_y = int(round(end_y / ref_h * frame_h))
        self.swipe(from_x, from_y, to_x, to_y, duration=0.5)

    def run(self):
        self.info_set("状态", "加载武器数据")

        csv_path = Path(str(self.config.get("_武器数据CSV", ""))).expanduser()
        requirements = load_weapon_data(csv_path)
        if not requirements:
            self.log_error(f"未加载到武器数据: {csv_path}")
            self.info_set("状态", "失败：武器数据为空")
            return
        self.info_set("武器数据", f"{len(requirements)} 条")

        grid_x, grid_y = self._ref_xy(self.config.get("_网格起点"), [190, 256])
        dx, dy = self._ref_xy(self.config.get("_网格步长"), [204, 208])
        cols = int(self.config.get("_每行数量", 9))
        rows = int(self.config.get("_每屏行数", 5))
        icon_w, icon_h = self._ref_xy(self.config.get("_图标采样尺寸"), [238, 236])
        lock_x, lock_y = self._ref_xy(self.config.get("_锁按钮坐标"), [2444, 372])

        click_wait = float(self.config.get("_点击等待秒", 0.35))
        move_pixel = int(self.config.get("_滑动距离像素", 140))
        scroll_wait = float(self.config.get("_滑动后等待秒", 1.5))
        max_pages = int(self.config.get("_最大翻页", 200))
        keep_lines = int(self.config.get("_毕业列表保留行数", 30))

        lock_enabled = bool(self.config.get("上锁毕业基质", True))

        self.info_set("状态", "开始扫描")
        self.next_frame()

        scanned = 0
        graduated = 0
        locked = 0
        lock_skipped = 0
        logs: list[str] = []
        graduated_weapons: set[str] = set()
        graduated_weapon_logs: list[str] = []
        self.info_set("已识别", "0")
        self.info_set("已毕业基质", "0")
        self.info_set("已上锁", "0")
        self.info_set("已锁定跳过", "0")
        self.info_set("已毕业武器", "0")
        # 翻页滑动距离：如果配置过小，会导致同一屏反复扫描。
        move_pixel = max(move_pixel, int(dy * (rows - 1)))

        last_first_cell_mean: float | None = None
        gold_seen_any = False
        non_gold_consecutive = 0

        for page in range(max_pages):
            if not self.enabled:
                break

            self.info_set("翻页", str(page))
            gold_on_page = 0
            stop_all = False

            # 若已进入金色段，翻页后首格非金色，说明金色已结束：无需再扫描整屏
            if page > 0 and gold_seen_any:
                self.next_frame()
                first_cell_box = self._ref_box(
                    grid_x - icon_w / 2,
                    grid_y - icon_h / 2,
                    grid_x + icon_w / 2,
                    grid_y + icon_h / 2,
                    name="first_cell_check",
                )
                if self._is_purple_cell(first_cell_box):
                    self.info_set("状态", "完成：未检测到金色，停止")
                    break

            row_start = 0 if page == 0 else 1  # 翻页会有 1 行重叠，避免重复扫描
            for row_in_view in range(row_start, rows):
                if not self.enabled:
                    break
                for col in range(cols):
                    if not self.enabled:
                        break

                    self.next_frame()
                    cx = grid_x + col * dx
                    cy = grid_y + row_in_view * dy

                    cell_box = self._ref_box(
                        cx - icon_w / 2,
                        cy - icon_h / 2,
                        cx + icon_w / 2,
                        cy + icon_h / 2,
                        name="matrix_cell",
                    )
                    if self._is_purple_cell(cell_box):
                        self.info_set("状态", "完成：检测到紫色，停止")
                        stop_all = True
                        break
                    if gold_seen_any and not self._is_gold_cell(cell_box):
                        self.info_set("状态", "完成：金色已结束，停止")
                        stop_all = True
                        break

                    global_row = page * (rows - 1) + row_in_view + 1
                    self.info_set("点击格子", f"{global_row}-{col + 1}")

                    try:
                        self._click_ref(cx, cy, after_sleep=click_wait)
                    except pywintypes.error as e:
                        if getattr(e, "winerror", None) == 5:
                            self.info_set("状态", "失败：无法点击（权限不足）")
                            self.info_set(
                                "错误",
                                "PostMessage 拒绝访问。请用「管理员身份」启动本程序后重试：uv run python main.py -t 2",
                            )
                            return
                        raise
                    self.next_frame()

                    info = self.read_essence_info()
                    if not info:
                        continue

                    if info.is_gold:
                        gold_seen_any = True
                        non_gold_consecutive = 0
                        gold_on_page += 1
                    else:
                        non_gold_consecutive += 1
                        if non_gold_consecutive >= 2:
                            self.info_set("状态", "完成：遇到非金色，停止")
                            stop_all = True
                            break
                        continue

                    if len(info.entries) != 3:
                        continue

                    scanned += 1
                    self.info_set("已识别", str(scanned))

                    matches = match_weapon_requirements(requirements, info.entry_names)
                    entry_text = " ".join(
                        f"{e.name}+{e.level}" if e.level is not None else e.name for e in info.entries
                    )
                    self.info_set("当前基质", f"{info.name} {info.source or ''}")
                    self.info_set("当前词条", entry_text)

                    if not matches:
                        continue

                    graduated += 1
                    # 状态栏展示用：毕业基质数量
                    self.info_set("已毕业基质", str(graduated))
                    # 兼容旧字段（已有人在看日志）
                    self.info_set("毕业", str(graduated))

                    if lock_enabled:
                        locked_ok, did_lock = self._try_lock(lock_x, lock_y)
                        if not locked_ok:
                            self.info_set("上锁失败", info.name)
                        elif did_lock:
                            locked += 1
                            self.info_set("已上锁", str(locked))
                        else:
                            lock_skipped += 1
                            self.info_set("已锁定跳过", str(lock_skipped))

                    matched_weapons = "、".join(m.weapon for m in matches[:5])
                    if len(matches) > 5:
                        matched_weapons += "…"

                    pos = f"[{global_row}-{col + 1}]"
                    logs.append(f"{pos} {info.name} {entry_text} -> {matched_weapons}")
                    if keep_lines > 0:
                        logs = logs[-keep_lines:]
                    self.info_set("毕业列表", "\n".join(logs))

                    for m in matches:
                        if m.weapon in graduated_weapons:
                            continue
                        graduated_weapons.add(m.weapon)
                        graduated_weapon_logs.append(f"{m.weapon}({m.star}) -> {pos} {info.name} {entry_text}")
                        if keep_lines > 0:
                            graduated_weapon_logs = graduated_weapon_logs[-keep_lines:]
                        self.info_set("已毕业武器", str(len(graduated_weapons)))
                        self.info_set("已毕业武器列表", "\n".join(graduated_weapon_logs))
                if stop_all:
                    break

            if stop_all:
                break

            # 当前页没有任何金色：一般说明已进入紫色/其他区域，停止即可（避免多轮）
            if page > 0 and gold_on_page == 0:
                self.info_set("状态", "完成：未检测到金色，停止")
                break

            # 简单的“是否真正翻页”校验：如果首格均值几乎不变，说明已到列表底部或滑动无效
            self.next_frame()
            first_cell = self._ref_box(
                grid_x - icon_w / 2,
                grid_y - icon_h / 2,
                grid_x + icon_w / 2,
                grid_y + icon_h / 2,
                name="first_cell",
            ).crop_frame(self.frame)
            first_mean = float(first_cell.mean()) if first_cell.size else 0.0
            if last_first_cell_mean is not None and abs(first_mean - last_first_cell_mean) < 0.2:
                self.info_set("状态", "完成：已到列表底部")
                break
            last_first_cell_mean = first_mean

            self._scroll_next_page(grid_x, grid_y, dx, dy, cols, rows, move_pixel)
            self.sleep(scroll_wait)
            self.next_frame()
        else:
            self.info_set("状态", f"停止：达到最大翻页 {max_pages}")

        self.info_set("状态", "已停止")
