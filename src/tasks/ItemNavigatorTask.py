# -*- coding: utf-8 -*-
import json
import math
import threading
import time
from pathlib import Path
from typing import Dict, Tuple

from qfluentwidgets import FluentIcon

from ok import Logger, TriggerTask
from src.tasks.BaseEfTask import BaseEfTask
from src.tasks.mixin.ws_position_mixin import WsPositionMixin
from src.data import item_map_query

logger = Logger.get_logger(__name__)


class ItemNavigatorTask(WsPositionMixin,BaseEfTask, TriggerTask):
    """实时从本地 WebSocket 拿玩家位置，指向已选物品的最近点，并支持按键标记已获取。

    设计原则：
    - `default_config` 只放面向用户的配置（见初始化），不把内部服务端口等放在 default_config
    - 轮询使用固定内部 WS 端点（可在部署时改代码），物品选择从任务配置读取
    """

    # minimal, user-facing defaults only (用户可见/配置)
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "物品导航"
        self.description = "监听本地 WebSocket 位置数据，指向已选物品的最近点并支持按键标记"
        self.icon = FluentIcon.SEARCH
        # 只把面向用户的选项放在 default_config
        self.default_config.update({
            # 由用户在 UI 中配置要导航的物品名列表（可空）
            '选择物品': [],
            # 标记按键（UI 映射），例如 'f'，当玩家按下且目标在阈值内时标记为已获取
            '标记按键': 'f',
            # 接近提示的水平阈值（世界坐标单位）
            '接近阈值': 20.0,
        })

        # config type: use button_list for multi-select UI
        try:
            self.config_type['选择物品'] = {
                'type': 'button_list',
                'options': item_map_query.get_supported_item_names()
            }
        except Exception:
            # fallback to empty options if data unavailable
            self.config_type['选择物品'] = {'type': 'button_list', 'options': []}

        # internal constants (not user-facing)
        self._init_ws_position_mixin()
        self._marked_store = Path('assets') / 'items' / 'map' / 'marked_points.json'
        self._marked_lock = threading.Lock()
        self._marked: Dict[str, set] = {}  # mapId -> set of point hashes

        # 箭头渲染可调参数（便于快速微调视觉）
        self._arrow_center_rel = (162/1920, 166/1080)  # 相对于窗口的箭头中心位置（比例），默认在左上角稍微偏右下   
        self._arrow_max_len_ratio = 0.08
        self._arrow_min_len_px = 20.0
        self._arrow_scale = 3.0
        # 箭头样式参数（可调）
        self._arrow_color = (0, 255, 0)  # RGB
        self._arrow_alpha = 160  # 透明度 0-255，160 为半透明
        self._arrow_shaft_width_norm = 0.005  # 箭身宽度（细）

        self._load_marked()
        # dirty-save 控制：标记后延迟合并写盘
        self._dirty = False
        self._last_save_time = 0.0
        # 上一帧按键状态（用于边沿触发）
        self._prev_mark_key_state = False

    # --- persistence for marked points ---
    def _load_marked(self):
        try:
            if self._marked_store.exists():
                data = json.loads(self._marked_store.read_text(encoding='utf-8'))
                for k, v in (data or {}).items():
                    self._marked[k] = set(v or [])
        except Exception as e:
            self.log_error(f"加载 marked_points 失败: {e}")

    def _save_marked(self):
        try:
            with self._marked_lock:
                data = {k: list(v) for k, v in self._marked.items()}
                self._marked_store.parent.mkdir(parents=True, exist_ok=True)
                # 原子写入：先写到临时文件再替换
                tmp = self._marked_store.with_suffix('.tmp')
                tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
                tmp.replace(self._marked_store)
                # 更新最后保存时间
                self._last_save_time = time.time()
                self._dirty = False
        except Exception as e:
            self.log_error(f"保存 marked_points 失败: {e}")

    @staticmethod
    def _point_hash(pt: Dict[str, float], item_name: str | None = None) -> str:
        # 包含物品名以避免不同物品同坐标冲突
        name = item_name or ''
        return f"{name}|{round(pt.get('x',0),3)}|{round(pt.get('y',0),3)}|{round(pt.get('z',0),3)}"

    # --- core helpers ---
    @staticmethod
    def _xy_dist(a: Tuple[float, float], b: Tuple[float, float]) -> float:
        return math.hypot(a[0] - b[0], a[1] - b[1])

    def _get_candidates_for_map(self, map_id: str, selected_items: list[str]) -> Dict[str, list]:
        # Use item_map_query to get items; then restrict to given map_id
        if not selected_items:
            return {}
        summary = item_map_query.get_item_map(selected_items)
        return summary.get(map_id, {})

    def _draw_nav_arrow(self, dx: float, dz: float, tooltip: str):
        try:

            width = getattr(self, 'width', None)
            height = getattr(self, 'height', None)
            if not width or not height:
                return

            center_x = width * self._arrow_center_rel[0]
            center_y = height * self._arrow_center_rel[1]
            max_length = min(width, height) * self._arrow_max_len_ratio
            draw_length = max(self._arrow_min_len_px, math.hypot(dx, dz) * self._arrow_scale)
            # 纵向方向按当前导航坐标系翻转，避免上下显示反向。
            angle_deg = math.degrees(math.atan2(dx, dz))

            self.draw_window_arrow_from_center(
                center_x=center_x,
                center_y=center_y,
                max_length=max_length,
                draw_length=draw_length,
                angle_deg=angle_deg,
                color=self._arrow_color,
                alpha=self._arrow_alpha,
                shaft_width_norm=self._arrow_shaft_width_norm,
            )

            if tooltip:
                self.info_set('导航箭头', tooltip)
        except Exception:
            pass

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
        self.log_info("ItemNavigatorTask 启动")

        try:
            self._start_ws_position_server(host='127.0.0.1', port=3001)

            # read current selected items from task config (this is user-facing)
            selected_items = list(self.config.get('选择物品') or [])

            # fetch player position from local websocket service
            try:
                payload = self._recv_ws_position_payload(timeout=0.5)
            except Exception:
                self.info_set('导航', '无法读取WS位置')
                self.sleep(0.5)
                return

            # parse payload (兼容扁平结构 / data 包裹)
            pos, map_id, px, py, pz = self._extract_position_payload(payload)
            if not pos or not map_id:
                self.info_set('导航', 'WS位置数据不完整')
                self.sleep(0.5)
                return

            # build candidates for this map and selected items
            candidates = self._get_candidates_for_map(map_id, selected_items)
            if not candidates:
                self.info_set('导航', '无候选物品')
                self.sleep(0.5)
                return

            best = None
            best_meta = None
            best_dxz = float('inf')

            for item_name, pts in candidates.items():
                for pt in pts:
                    h = self._point_hash(pt, item_name)
                    if h in self._marked.get(map_id, set()):
                        return
                    dxz = self._xy_dist((px, pz), (pt.get('x', 0), pt.get('z', 0)))
                    if dxz < best_dxz:
                        best_dxz = dxz
                        best = pt
                        best_meta = item_name

            if best is None:
                self.info_set('导航', '无未标记候选')
                self.sleep(0.5)
                return

            # y 是高度，方位与水平距离都在 xz 平面
            dy_height = best.get('y', 0) - py
            near_xz = best_dxz <= float(self.config.get('接近阈值', 5.0))

            # direction angle in degrees for XZ vector (player->target) relative to +X
            dx = best.get('x', 0) - px
            dz = best.get('z', 0) - pz
            angle = math.degrees(math.atan2(dz, dx))

            status = f"目标={best_meta} 距离XZ={best_dxz:.1f} 角度={angle:.0f}°"
            if near_xz:
                updown = '上方' if dy_height > 0 else '下方' if dy_height < 0 else '同高'
                status += f" 接近: 高差Y={dy_height:.2f} ({updown})"

            # publish minimal UI info (任务显示栏)
            self.info_set('导航', status)

            # overlay: 左上角矢量箭头（固定中心 + 最大长度 + 自由箭头结尾）
            self._draw_nav_arrow(dx, dz, tooltip=f"{best_meta} | XZ:{best_dxz:.1f} | Y:{dy_height:.1f}")

            # handle marking: use edge-trigger (上一帧无按下，本帧按下) 来避免按住重复触发
            mark_key = str(self.config.get('标记按键') or '').strip() or 'f'
            cur_key = self._is_key_pressed(mark_key)
            if cur_key and (not self._prev_mark_key_state) and best_dxz <= float(self.config.get('接近阈值', 5.0)):
                h = self._point_hash(best, best_meta)
                with self._marked_lock:
                    self._marked.setdefault(map_id, set()).add(h)
                    # 标记为脏，延迟写盘
                    self._dirty = True
                self.info_set('导航', f'已标记: {best_meta} ({h})')
            # 更新上一帧按键状态
            self._prev_mark_key_state = cur_key

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

