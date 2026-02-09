from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum
from typing import Final

import pywintypes
from qfluentwidgets import FluentIcon

from src.tasks.BaseEfTask import BaseEfTask
from src.essence.weapon_data import load_weapon_data, match_weapon_requirements


class LockState(str, Enum):
    LOCKED = "locked"
    UNLOCKED = "unlocked"
    UNKNOWN = "unknown"


_INFO_STATUS: Final = "状态"
_INFO_SCANNED: Final = "已识别"
_INFO_LOCK_SUCCESS: Final = "上锁成功"
_INFO_LOCK_SKIPPED: Final = "已锁定跳过"
_INFO_GRADUATED_ESSENCE: Final = "已毕业基质"
_INFO_GRADUATED_WEAPONS: Final = "已毕业武器"


_FEATURE_ESSENCE_UI_MARKER: Final = "essence_ui_marker"
_FEATURE_ESSENCE_QUALITY_GOLD: Final[tuple[str, ...]] = (
    # 兼容旧单标签
    "essence_quality_gold",
    # 多模板支持：选中/未选中等不同样式请用不同 tag（每个 tag 在 COCO 中只能有 1 个框）
    "essence_quality_gold_1",
    "essence_quality_gold_2",
    "essence_quality_gold_3",
)
_FEATURE_ESSENCE_LOCKED: Final = "essence_locked"
_FEATURE_ESSENCE_UNLOCKED: Final = "essence_unlocked"

_ESSENCE_UI_THRESHOLD: Final = 0.75
_ESSENCE_QUALITY_THRESHOLD: Final = 0.75
_ESSENCE_LOCK_THRESHOLD: Final = 0.75

_LOCK_CLICK_WAIT_SEC: Final = 0.45
_LOCK_ICON_HALF_SIZE: Final = 36
_DEFAULT_REF_RESOLUTION: Final[tuple[int, int]] = (2560, 1440)
_DEFAULT_GRID_ORIGIN: Final[tuple[int, int]] = (190, 256)
_DEFAULT_GRID_STEP: Final[tuple[int, int]] = (204, 208)
_DEFAULT_ICON_SIZE: Final[tuple[int, int]] = (238, 236)
_DEFAULT_LOCK_BUTTON: Final[tuple[int, int]] = (2444, 372)
_DEFAULT_GRID_COLS: Final = 9
_DEFAULT_GRID_ROWS: Final = 5
_DEFAULT_CLICK_WAIT_SEC: Final = 0.35
_DEFAULT_SCROLL_PIXELS: Final = 140
_DEFAULT_SCROLL_WAIT_SEC: Final = 1.5
_DEFAULT_MAX_PAGES: Final = 200


def _parse_xy(value, default: tuple[int, int]) -> tuple[int, int]:
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


@dataclass(frozen=True)
class EssenceScanSettings:
    weapon_csv: Path
    ref_resolution: tuple[int, int]
    grid_origin: tuple[int, int]
    grid_step: tuple[int, int]
    grid_cols: int
    grid_rows: int
    icon_size: tuple[int, int]
    lock_button: tuple[int, int]
    click_wait_sec: float
    scroll_pixels: int
    scroll_wait_sec: float
    max_pages: int

    @classmethod
    def from_task(cls, task: "EssenceScanTask") -> "EssenceScanSettings":
        config = getattr(task, "config", {}) or {}
        weapon_csv = Path(str(config.get("_武器数据CSV", str(Path("assets") / "weapon_data.csv")))).expanduser()
        ref_resolution = _parse_xy(config.get("_参考分辨率"), _DEFAULT_REF_RESOLUTION)
        grid_origin = _parse_xy(config.get("_网格起点"), _DEFAULT_GRID_ORIGIN)
        grid_step = _parse_xy(config.get("_网格步长"), _DEFAULT_GRID_STEP)
        icon_size = _parse_xy(config.get("_图标采样尺寸"), _DEFAULT_ICON_SIZE)
        lock_button = _parse_xy(config.get("_锁按钮坐标"), _DEFAULT_LOCK_BUTTON)

        try:
            grid_cols = int(config.get("_每行数量", _DEFAULT_GRID_COLS))
        except Exception:
            grid_cols = _DEFAULT_GRID_COLS
        try:
            grid_rows = int(config.get("_每屏行数", _DEFAULT_GRID_ROWS))
        except Exception:
            grid_rows = _DEFAULT_GRID_ROWS

        try:
            click_wait_sec = float(config.get("_点击等待秒", _DEFAULT_CLICK_WAIT_SEC))
        except Exception:
            click_wait_sec = _DEFAULT_CLICK_WAIT_SEC
        try:
            scroll_pixels = int(config.get("_滑动距离像素", _DEFAULT_SCROLL_PIXELS))
        except Exception:
            scroll_pixels = _DEFAULT_SCROLL_PIXELS
        try:
            scroll_wait_sec = float(config.get("_滑动后等待秒", _DEFAULT_SCROLL_WAIT_SEC))
        except Exception:
            scroll_wait_sec = _DEFAULT_SCROLL_WAIT_SEC
        try:
            max_pages = int(config.get("_最大翻页", _DEFAULT_MAX_PAGES))
        except Exception:
            max_pages = _DEFAULT_MAX_PAGES

        # 翻页滑动距离：如果过小会导致同一屏反复扫描。至少要跨过 rows-1 行。
        scroll_pixels = max(scroll_pixels, int(grid_step[1] * (grid_rows - 1)))

        return cls(
            weapon_csv=weapon_csv,
            ref_resolution=ref_resolution,
            grid_origin=grid_origin,
            grid_step=grid_step,
            grid_cols=grid_cols,
            grid_rows=grid_rows,
            icon_size=icon_size,
            lock_button=lock_button,
            click_wait_sec=click_wait_sec,
            scroll_pixels=scroll_pixels,
            scroll_wait_sec=scroll_wait_sec,
            max_pages=max_pages,
        )


@dataclass
class EssenceScanStats:
    scanned: int = 0
    graduated: int = 0
    lock_success: int = 0
    lock_skipped: int = 0
    matched_weapons: set[str] = field(default_factory=set)

    def update_info(self, task: "EssenceScanTask") -> None:
        task.info_set(_INFO_SCANNED, str(self.scanned))
        task.info_set(_INFO_GRADUATED_ESSENCE, str(self.graduated))
        task.info_set(_INFO_LOCK_SUCCESS, str(self.lock_success))
        task.info_set(_INFO_LOCK_SKIPPED, str(self.lock_skipped))
        task.info_set(_INFO_GRADUATED_WEAPONS, str(len(self.matched_weapons)))


class EssenceScanTask(BaseEfTask):
    """
    一次性遍历武器基质列表，识别右侧信息面板，匹配毕业基质并自动上锁。
    参考：../Endfield_essence 的网格遍历/滑动/锁定思路。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "毕业基质识别"
        self.description = "遍历武器基质列表，匹配 weapon_data.csv 并处理毕业基质上锁"
        self.icon = FluentIcon.SEARCH
        # 该 output 文本框默认会显示在“开始”按钮同一行，观感较差；此任务用实时日志 + 状态栏即可。
        self.show_info_panel = False
        self.default_config.update(
            {
                "上锁毕业基质": True,
                "非毕业基质取消上锁": False,
                # 以下为内部参数，前面加 "_" 以在 GUI 配置页隐藏
                "_武器数据CSV": str(Path("assets") / "weapon_data.csv"),
                "_参考分辨率": [str(_DEFAULT_REF_RESOLUTION[0]), str(_DEFAULT_REF_RESOLUTION[1])],
                "_网格起点": [str(_DEFAULT_GRID_ORIGIN[0]), str(_DEFAULT_GRID_ORIGIN[1])],
                "_网格步长": [str(_DEFAULT_GRID_STEP[0]), str(_DEFAULT_GRID_STEP[1])],
                "_每行数量": _DEFAULT_GRID_COLS,
                "_每屏行数": _DEFAULT_GRID_ROWS,
                "_图标采样尺寸": [str(_DEFAULT_ICON_SIZE[0]), str(_DEFAULT_ICON_SIZE[1])],
                "_锁按钮坐标": [str(_DEFAULT_LOCK_BUTTON[0]), str(_DEFAULT_LOCK_BUTTON[1])],
                "_点击等待秒": _DEFAULT_CLICK_WAIT_SEC,
                "_滑动距离像素": _DEFAULT_SCROLL_PIXELS,
                "_滑动后等待秒": _DEFAULT_SCROLL_WAIT_SEC,
                "_最大翻页": _DEFAULT_MAX_PAGES,
            }
        )
        self.config_description.update(
            {
                "上锁毕业基质": "命中毕业词条后自动点击右侧小锁上锁",
                "非毕业基质取消上锁": "非毕业基质会尝试取消上锁（已上锁的会解锁）",
            }
        )

    def _ref_box(self, settings: EssenceScanSettings, x1: float, y1: float, x2: float, y2: float, *, name: str):
        ref_w, ref_h = settings.ref_resolution
        return self.box_of_screen(
            x1 / ref_w,
            y1 / ref_h,
            x2 / ref_w,
            y2 / ref_h,
            name=name,
        )

    def _click_ref(self, settings: EssenceScanSettings, x: float, y: float, *, after_sleep: float = 0.0):
        ref_w, ref_h = settings.ref_resolution
        self.click(x / ref_w, y / ref_h, hcenter=True, vcenter=True, after_sleep=after_sleep)

    def _has_feature(self, feature_name: str, *, box=None, threshold: float = 0.0) -> bool:
        try:
            return bool(self.find_one(feature_name, box=box, threshold=threshold))
        except Exception:
            return False

    def _in_essence_ui(self) -> bool:
        self.next_frame()
        return self._has_feature(_FEATURE_ESSENCE_UI_MARKER, threshold=_ESSENCE_UI_THRESHOLD)

    def _lock_icon_box(self, settings: EssenceScanSettings, lock_x: int, lock_y: int):
        hs = _LOCK_ICON_HALF_SIZE
        return self._ref_box(settings, lock_x - hs, lock_y - hs, lock_x + hs, lock_y + hs, name="essence_lock_icon")

    def _lock_state(self, settings: EssenceScanSettings, lock_x: int, lock_y: int) -> LockState:
        """
        锁按钮是“切换”按钮：已锁再点会解锁。
        用模板匹配（X-AnyLabeling/COCO）判断当前锁状态：locked / unlocked / unknown。
        """
        roi = self._lock_icon_box(settings, lock_x, lock_y)
        locked = self._has_feature(_FEATURE_ESSENCE_LOCKED, box=roi, threshold=_ESSENCE_LOCK_THRESHOLD)
        unlocked = self._has_feature(_FEATURE_ESSENCE_UNLOCKED, box=roi, threshold=_ESSENCE_LOCK_THRESHOLD)
        if locked and not unlocked:
            return LockState.LOCKED
        if unlocked and not locked:
            return LockState.UNLOCKED
        if locked and unlocked:
            # 宁愿当作已锁，避免误点导致解锁
            return LockState.LOCKED
        return LockState.UNKNOWN

    def _try_lock(self, settings: EssenceScanSettings, lock_x: int, lock_y: int) -> tuple[bool, bool]:
        """
        锁按钮是“切换”按钮：已锁再点会解锁。

        返回 (locked_ok, did_lock)
        - locked_ok: 最终是否处于“锁定”状态（或认为已锁定）
        - did_lock: 本次是否执行过“上锁点击”（仅用于计数）
        """
        self.next_frame()
        state0 = self._lock_state(settings, lock_x, lock_y)
        if state0 == LockState.LOCKED:
            return True, False
        if state0 == LockState.UNKNOWN:
            # 判不清时不点击，避免“已锁 -> 解锁”的灾难性误触
            return True, False

        # 明确未锁：点一次尝试上锁，并做一次确认（避免双击导致“上锁->解锁”）
        self._click_ref(settings, lock_x, lock_y, after_sleep=_LOCK_CLICK_WAIT_SEC)
        self.next_frame()
        state1 = self._lock_state(settings, lock_x, lock_y)
        if state1 == LockState.LOCKED:
            return True, True

        # 若仍明确未锁（可能点击没生效），再尝试一次
        if state1 == LockState.UNLOCKED:
            self._click_ref(settings, lock_x, lock_y, after_sleep=_LOCK_CLICK_WAIT_SEC)
            self.next_frame()
            state2 = self._lock_state(settings, lock_x, lock_y)
            return state2 == LockState.LOCKED, True

        return False, True

    def _try_unlock(self, settings: EssenceScanSettings, lock_x: int, lock_y: int) -> tuple[bool, bool]:
        """
        取消上锁：已解锁则跳过，未知状态则不操作。
        返回 (unlocked_ok, did_unlock)
        """
        self.next_frame()
        state0 = self._lock_state(settings, lock_x, lock_y)
        if state0 == LockState.UNLOCKED:
            return True, False
        if state0 == LockState.UNKNOWN:
            return True, False

        self._click_ref(settings, lock_x, lock_y, after_sleep=_LOCK_CLICK_WAIT_SEC)
        self.next_frame()
        state1 = self._lock_state(settings, lock_x, lock_y)
        return state1 == LockState.UNLOCKED, True

    def _is_gold_cell(self, cell_box) -> bool:
        for feature_name in _FEATURE_ESSENCE_QUALITY_GOLD:
            if self._has_feature(feature_name, box=cell_box, threshold=_ESSENCE_QUALITY_THRESHOLD):
                return True
        return False

    def _scroll_next_page(self, settings: EssenceScanSettings):
        grid_x, grid_y = settings.grid_origin
        dx, dy = settings.grid_step
        cols = settings.grid_cols
        rows = settings.grid_rows
        move_pixel = settings.scroll_pixels

        start_x = grid_x + (cols // 2) * dx
        start_y = grid_y + (rows - 1) * dy
        end_y = start_y - move_pixel

        ref_w, ref_h = settings.ref_resolution
        frame_h, frame_w = self.frame.shape[:2]
        from_x = int(round(start_x / ref_w * frame_w))
        from_y = int(round(start_y / ref_h * frame_h))
        to_x = int(round(start_x / ref_w * frame_w))
        to_y = int(round(end_y / ref_h * frame_h))
        self.swipe(from_x, from_y, to_x, to_y, duration=0.5)

    def run(self):
        # 只保留该任务需要展示的 info keys，避免旧版本遗留字段出现在状态栏
        info = getattr(self, "info", None)
        if isinstance(info, dict):
            info.clear()

        self.info_set(_INFO_STATUS, "运行中")

        settings = EssenceScanSettings.from_task(self)
        requirements = load_weapon_data(settings.weapon_csv)
        if not requirements:
            self.log_error(f"未加载到武器数据: {settings.weapon_csv}")
            self.info_set(_INFO_STATUS, "错误")
            return

        lock_enabled = bool(self.config.get("上锁毕业基质", True))
        unlock_non_graduate = bool(self.config.get("非毕业基质取消上锁", False))
        stats = EssenceScanStats()
        stats.update_info(self)

        self.next_frame()
        if not self._in_essence_ui():
            self.info_set(_INFO_STATUS, "错误")
            self.log_error("[essence] please open [武器基质] page first")
            return

        self.log_info(
            "[essence] settings "
            f"ref={settings.ref_resolution} origin={settings.grid_origin} step={settings.grid_step} "
            f"cols={settings.grid_cols} rows={settings.grid_rows} icon={settings.icon_size} "
            f"scroll={settings.scroll_pixels}px max_pages={settings.max_pages} lock={lock_enabled}"
        )

        grid_x, grid_y = settings.grid_origin
        dx, dy = settings.grid_step
        cols = settings.grid_cols
        rows = settings.grid_rows
        icon_w, icon_h = settings.icon_size
        lock_x, lock_y = settings.lock_button

        last_first_cell_mean: float | None = None
        gold_seen_any = False

        stopped_by_user = False
        for page in range(settings.max_pages):
            if not self.enabled:
                stopped_by_user = True
                break

            self.log_info(f"[essence] page {page}")
            gold_on_page = 0
            stop_all = False

            row_start = 0 if page == 0 else 1  # 翻页会有 1 行重叠，避免重复扫描
            for row_in_view in range(row_start, rows):
                if not self.enabled:
                    stopped_by_user = True
                    stop_all = True
                    break
                for col in range(cols):
                    if not self.enabled:
                        stopped_by_user = True
                        stop_all = True
                        break

                    self.next_frame()
                    cx = grid_x + col * dx
                    cy = grid_y + row_in_view * dy

                    cell_box = self._ref_box(
                        settings,
                        cx - icon_w / 2,
                        cy - icon_h / 2,
                        cx + icon_w / 2,
                        cy + icon_h / 2,
                        name="essence_cell",
                    )

                    is_gold_candidate = self._is_gold_cell(cell_box)
                    force_click = col == 0 and not gold_seen_any
                    if not is_gold_candidate and not (gold_seen_any or force_click):
                        continue

                    global_row = page * (rows - 1) + row_in_view + 1
                    pos = f"{global_row}-{col + 1}"
                    self.log_info(f"[essence] click {pos}")

                    try:
                        self._click_ref(settings, cx, cy, after_sleep=settings.click_wait_sec)
                    except pywintypes.error as e:
                        if getattr(e, "winerror", None) == 5:
                            self.info_set(_INFO_STATUS, "错误")
                            self.log_error(
                                "[essence] PostMessage access denied. Start as Administrator then retry: "
                                "uv run python main.py -t 2"
                            )
                            return
                        raise
                    self.next_frame()

                    info = self.read_essence_info()
                    if not info:
                        self.log_debug(f"[essence] {pos} ocr empty")
                        continue

                    entry_text = " ".join(
                        f"{e.name}+{e.level}" if e.level is not None else e.name for e in info.entries
                    )
                    self.log_info(f"[essence] {pos} {info.name} {info.source or ''} | {entry_text}")

                    if not info.is_gold:
                        self.log_info(f"[essence] {pos} skip: non-gold {info.name}")
                        if gold_seen_any:
                            self.log_info("[essence] stop: non-gold encountered")
                            stop_all = True
                            break
                        continue

                    gold_seen_any = True
                    gold_on_page += 1

                    if len(info.entries) != 3:
                        self.log_debug(f"[essence] {pos} skip: entries={len(info.entries)}")
                        continue

                    stats.scanned += 1
                    self.info_set(_INFO_SCANNED, str(stats.scanned))

                    matches = match_weapon_requirements(requirements, info.entry_names)
                    if not matches:
                        if unlock_non_graduate:
                            unlocked_ok, did_unlock = self._try_unlock(settings, lock_x, lock_y)
                            self.log_info(
                                f"[essence] {pos} unlock did_click={did_unlock} ok={unlocked_ok}"
                            )
                        continue

                    stats.graduated += 1
                    self.info_set(_INFO_GRADUATED_ESSENCE, str(stats.graduated))

                    weapons_text = "、".join(f"{m.weapon}({m.star})" for m in matches)
                    self.log_info(f"[essence] {pos} graduated -> {weapons_text}")

                    if lock_enabled:
                        state_before = self._lock_state(settings, lock_x, lock_y)
                        locked_ok, did_lock = self._try_lock(settings, lock_x, lock_y)
                        state_after = self._lock_state(settings, lock_x, lock_y)
                        self.log_info(
                            f"[essence] {pos} lock {state_before.value}->{state_after.value} "
                            f"did_click={did_lock} ok={locked_ok}"
                        )

                        if not locked_ok:
                            self.log_error(f"[essence] {pos} lock failed {info.name}")
                        elif did_lock:
                            stats.lock_success += 1
                            self.info_set(_INFO_LOCK_SUCCESS, str(stats.lock_success))
                        else:
                            stats.lock_skipped += 1
                            self.info_set(_INFO_LOCK_SKIPPED, str(stats.lock_skipped))

                    stats.matched_weapons.update(m.weapon for m in matches)
                    self.info_set(_INFO_GRADUATED_WEAPONS, str(len(stats.matched_weapons)))
                if stop_all:
                    break

            if stop_all:
                break

            # 当前页没有任何金色：一般说明已进入紫色/其他区域，停止即可（避免多轮）
            if page > 0 and gold_on_page == 0:
                self.log_info("[essence] stop: no gold found on page")
                break

            # 简单的“是否真正翻页”校验：如果首格均值几乎不变，说明已到列表底部或滑动无效
            self.next_frame()
            first_cell = self._ref_box(
                settings,
                grid_x - icon_w / 2,
                grid_y - icon_h / 2,
                grid_x + icon_w / 2,
                grid_y + icon_h / 2,
                name="essence_first_cell_mean",
            ).crop_frame(self.frame)
            first_mean = float(first_cell.mean()) if first_cell.size else 0.0
            if (
                last_first_cell_mean is not None
                and abs(first_mean - last_first_cell_mean) < 0.2
            ):
                self.log_info("[essence] stop: reached bottom (first cell unchanged)")
                break
            last_first_cell_mean = first_mean

            self._scroll_next_page(settings)
            self.sleep(settings.scroll_wait_sec)
            self.next_frame()
        else:
            # 达到最大翻页也认为本次扫描结束（细节写入日志）
            self.log_info(f"[essence] stop: reached max_pages={settings.max_pages}")

        if stopped_by_user:
            self.info_set(_INFO_STATUS, "已停止")
        else:
            self.info_set(_INFO_STATUS, "已完成")
