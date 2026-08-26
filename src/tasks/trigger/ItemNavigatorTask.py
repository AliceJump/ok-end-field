import json
import math
import os
import subprocess
import threading
import tempfile
import time
import webbrowser
import re
from pathlib import Path
from typing import Dict, Tuple

import win32gui
from qfluentwidgets import FluentIcon
from src.icons import Icons
from ok import Logger, TriggerTask
from src.config import config
from src.core.BaseEfTask import BaseEfTask
from src.tasks.account.account_scope_store import get_account_map_content, load_overrides, resolve_account_id
from src.tasks.mixin.ws_position_mixin import WsPositionMixin
from src.data import item_map_query

logger = Logger.get_logger(__name__)
# 特殊物品Y坐标修正
SPECIAL_ITEM_Y_OFFSET = {
    '储藏箱': {
        "value": -0.06055,
        "pattern": re.compile(r'^储藏箱'),
    },
}

class ItemNavigatorTask(WsPositionMixin, BaseEfTask, TriggerTask):
    """实时从本地 WebSocket 拿玩家位置，指向已选物品的最近点，并支持按键标记已获取。

    设计原则：
    - `default_config` 只放面向用户的配置（见初始化），不把内部服务端口等放在 default_config
    - 轮询使用固定内部 WS 端点（可在部署时改代码），物品选择从任务配置读取
    """

    requires_foreground = True  # 物品导航需要移动

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.name = "物品导航"
        self.description = "监听本地 WebSocket 位置数据，指向已选物品的最近点并支持按键标记"
        self.icon = Icons.Navigation

        # 只把面向用户的选项放在 default_config
        self.default_config.update({
            # 可选：直接填写 hg/check 的 data.content。为空时按“地图账号”读取账户配置页 content。
            'content': '',
            # 可选：从账号配置页读取对应账号的地图同步 content。
            '地图账号': '',
            # 由用户在 UI 中配置要导航的物品名列表（可空）
            '选择物品': [],
            # 标记按键（UI 映射），例如 'f'，当玩家按下且目标在阈值内时标记为已获取
            '标记按键': 'f',
            # 标记时需要按住的最小时长（秒）
            '标记按住时长': 0.8,
        })

        self.config_type["选择物品"] = {
            "options_available": item_map_query.get_supported_item_names(),
            "allow_duplication": False,
        }
        self.config_type['地图账号'] = {
            'type': 'drop_down',
            'options': self._get_map_account_options(),
        }
        self.config_type['油猴脚本帮助'] = {
            'type': 'button',
            'text': '浏览器油猴脚本帮助',
            'icon': FluentIcon.LINK,
            'callback': self.open_userscript_help,
        }
        self.config_description.update({
            'content': (
                '可选。直接填写 web-api.skland.com/account/info/hg/check 返回 JSON 里的 data.content 值。\n'
                '此项有值时优先使用，不再读取账号配置页。'
            ),
            '地图账号': (
                '可选。content 为空时，从账号配置页读取该账号保存的地图同步 content。\n'
                '账号列表来自账号配置页；留空则尝试使用当前任务账号上下文。'
            ),
            '选择物品': (
                '选择要参与导航的物品列表。\n'
                '只会在当前地图里匹配这些物品。'
            ),
            '标记按键': (
                '接近目标后用于标记“已获取”的键位。\n'
                '默认按键为 f。'
            ),
            '标记按住时长': (
                '按住标记按键并持续达到这个时长后，\n'
                '才会把当前目标标记为已获取。'
            ),
            '油猴脚本帮助': (
                '打开临时帮助文档。\n'
                '同时打开油猴脚本目录。'
            ),
        })
        self.default_config_group.update({
            '网页地图同步': ['content', '地图账号', '油猴脚本帮助'],
        })

        # internal constants (not user-facing)
        self._init_ws_position_mixin()
        cfg_folder = Path(config.get('config_folder', 'configs'))
        self._marked_store = cfg_folder / 'marked_points.json'
        self._marked_lock = threading.Lock()
        self._marked: Dict[str, set] = {}  # mapId -> set of point hashes
        # WS 服务启动状态追踪（仅记录首次启动日志）
        self._ws_server_start_logged = False
        self._navigator_window_missing_logged = False

        # 箭头渲染可调参数（便于快速微调视觉）
        self._arrow_center_rel = (162 / 1920, 166 / 1080)  # 相对于窗口的箭头中心位置（比例），默认在左上角稍微偏右下
        self._arrow_max_len_ratio = 0.08
        self._arrow_min_len_px = 20.0
        self._arrow_scale = 1.144
        self._nearby_marker_max_distance = 75.524
        self._nearby_marker_len_px = 12
        # 箭头样式参数（可调）
        self._arrow_color = (0, 255, 0)  # RGB
        self._arrow_alpha = 160  # 透明度 0-255，160 为半透明
        self._arrow_shaft_width_norm = 0.005  # 箭身宽度（细）
        self._height_arrow_start_rel = (0.02, 0.12)
        self._height_arrow_min_len_norm = 0.02
        self._height_arrow_max_len_norm = 0.08
        self._near_xz_threshold = 20.0
        self._height_arrow_max_abs_dy = 30.0

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
        # 标记所需的最短连续按住时长（秒）
        self._mark_lock_required = 2.0

    @staticmethod
    def _get_map_account_options() -> list[str]:
        data = load_overrides()
        registry = data.get('account_registry') or {}
        account_list_text = str(data.get('account_list_text') or '')
        names: list[str] = ['']

        for raw in account_list_text.splitlines():
            name = raw.strip().split(',', 1)[0].strip()
            if name and name not in names:
                names.append(name)

        for meta in registry.values():
            if isinstance(meta, dict):
                name = str(meta.get('username') or '').strip()
                if name and name not in names:
                    names.append(name)

        return names

    def _is_game_window_alive(self) -> bool:
        hwnd_window = getattr(getattr(self, 'executor', None), 'device_manager', None)
        hwnd_window = getattr(hwnd_window, 'hwnd_window', None)
        hwnd = getattr(hwnd_window, 'hwnd', None)
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
        direct_content = str(self.config.get('content') or '').strip()
        if direct_content:
            return direct_content

        selected_account = str(self.config.get('地图账号') or '').strip()
        if selected_account:
            account_id = resolve_account_id(selected_account, create_if_missing=False) or selected_account
            return get_account_map_content(account_id, account_name=selected_account)

        account_id = str(getattr(self, 'current_account_id', '') or '').strip()
        account_name = str(getattr(self, 'current_user', '') or '').strip()

        executor = getattr(self, 'executor', None)
        current_task = getattr(executor, 'current_task', None) if executor is not None else None
        if current_task is not None and current_task is not self:
            account_id = account_id or str(getattr(current_task, 'current_account_id', '') or '').strip()
            account_name = account_name or str(getattr(current_task, 'current_user', '') or '').strip()

        return get_account_map_content(account_id or account_name, account_name=account_name)

    def _draw_nearby_markers(
            self,
            px: float,
            pz: float,
            candidates: Dict[str, list],
            map_id: str,
    ):
        try:
            width, height = self._get_window_arrow_size()
            if width <= 0 or height <= 0:
                return

            center_x = width * self._arrow_center_rel[0]
            center_y = height * self._arrow_center_rel[1]

            max_distance = self._nearby_marker_max_distance
            marker_len = self._nearby_marker_len_px
            for item_name, pts in candidates.items():

                for pt in pts:

                    h = self._point_hash(pt, item_name)

                    if h in self._marked.get(map_id, set()):
                        continue

                    dx = pt.get('x', 0) - px
                    dz = pt.get('z', 0) - pz

                    dist = math.hypot(dx, dz)

                    if dist > max_distance:
                        continue

                    screen_x = center_x + dx * self._arrow_scale
                    screen_y = center_y - dz * self._arrow_scale

                    self.draw_window_arrow(
                        start_x_norm=screen_x / width,
                        start_y_norm=(screen_y - marker_len) / height,
                        end_x_norm=screen_x / width,
                        end_y_norm=screen_y / height,
                        shaft_width_norm=self._arrow_shaft_width_norm * 0.5,
                        color=(255, 255, 0),
                        alpha=180,
                        arrow_type=f'nearby_{h}',
                    )

        except Exception as e:
            self.log_error(f'[附近目标] 绘制失败: {e}')
    def open_userscript_help(self, *_):
        """打开浏览器油猴脚本使用帮助，并打开脚本目录。"""
        script_rel = Path('assets') / 'scripts' / 'endfield-ws-position-relay.user.js'
        script_abs = (Path.cwd() / script_rel).resolve()
        script_dir = script_abs.parent
        help_text = (
            '终末地坐标转发油猴脚本使用帮助\n\n'
            '1. 安装浏览器扩展 Tampermonkey（油猴）。\n'
            '2. 打开脚本目录并导入脚本文件：\n'
            f'   {script_abs}\n'
            '3. 在 Tampermonkey 中启用该脚本。\n'
            '4. 打开网页地图 https://game.skland.com/map/endfield ，确认脚本已运行。\n'
            '5. 启动物品导航任务后，程序会监听 ws://127.0.0.1:3001 的位置数据。\n\n'
            '提示：\n'
            '- 先确保本地未被防火墙拦截 3001 端口。\n'
            '- 如脚本无日志，检查 Tampermonkey 是否允许在目标网址运行。\n'
        )

        try:
            tf = tempfile.NamedTemporaryFile(delete=False, suffix='.txt', mode='w', encoding='utf-8')
            tf.write(help_text)
            tf.flush()
            tf.close()
            help_path = tf.name

            if os.name == 'nt':
                os.startfile(help_path)
            else:
                webbrowser.open(f'file://{help_path}')
            self.log_info(f'已打开油猴脚本帮助: {help_path}')
        except Exception as e:
            self.log_error(f'打开油猴脚本帮助失败: {e}')

        try:
            if os.name == 'nt':
                if script_abs.exists():
                    subprocess.Popen(['explorer', f'/select,{script_abs}'])
                else:
                    os.startfile(str(script_dir))
            else:
                webbrowser.open(f'file://{script_dir}')
            self.log_info(f'已打开油猴脚本目录: {script_dir}')
        except Exception as e:
            self.log_error(f'打开油猴脚本目录失败: {e}')

    # --- persistence for marked points ---
    def _load_marked(self):
        try:
            # 先尝试加载新路径
            if self._marked_store.exists():
                data = json.loads(self._marked_store.read_text(encoding='utf-8'))
                for k, v in (data or {}).items():
                    self._marked[k] = set(v or [])
                return

            # 如果新路径不存在，检查旧路径并迁移
            old_path = Path('assets') / 'items' / 'map' / 'marked_points.json'
            if old_path.exists():
                # 存储路径是运行时值不过 tr
                self.log_info(self.tr("发现旧路径的 marked_points.json，正在迁移到新路径: {path}").format(
                    path=self._marked_store))
                try:
                    data = json.loads(old_path.read_text(encoding='utf-8'))
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
        return f"{name}|{round(pt.get('x', 0), 3)}|{round(pt.get('y', 0), 3)}|{round(pt.get('z', 0), 3)}"

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
                arrow_type='default',
            )

            if not success:
                self.log_info("[箭头] 绘制失败")
                return

            if tooltip:
                self.info_set('导航箭头', tooltip)
        except Exception as e:
            self.log_error(f"[箭头] 异常: {e}")

    def _draw_height_arrow(self, dy_height: float, tooltip: str):
        try:
            abs_dy = abs(float(dy_height))
            if abs_dy <= 1e-6:
                return

            max_abs_dy = max(1e-6, float(self._height_arrow_max_abs_dy))
            t = min(abs_dy / max_abs_dy, 1.0)
            draw_len_norm = self._height_arrow_max_len_norm * t
            start_x_norm, start_y_norm = self._height_arrow_start_rel
            end_x_norm = start_x_norm
            end_y_norm = start_y_norm - draw_len_norm if dy_height > 0 else start_y_norm + draw_len_norm

            success = self.draw_window_arrow(
                start_x_norm=start_x_norm,
                start_y_norm=start_y_norm,
                end_x_norm=end_x_norm,
                end_y_norm=end_y_norm,
                shaft_width_norm=self._arrow_shaft_width_norm,
                color=self._arrow_color,
                alpha=self._arrow_alpha,
                arrow_type='height',
            )

            if not success:
                self.log_info('[箭头] 高差箭头绘制失败')
                return

            if tooltip:
                self.info_set('高差箭头', tooltip)
        except Exception as e:
            self.log_error(f'[箭头] 高差异常: {e}')

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
                self.info_set('导航', '游戏窗口不存在，等待重新选择/启动游戏')
                return

            self._navigator_window_missing_logged = False
            map_cred = self._get_account_map_content()

            if map_cred:
                if self._is_ws_position_server_enabled():
                    self._stop_ws_position_server()
                if (
                        not self._is_map_ws_client_enabled()
                        or self._map_ws_auth_source != map_cred
                ):
                    self.log_info("ItemNavigatorTask 启动 - 正在启动官方地图WS客户端")
                    self._start_map_ws_client(map_cred)
                    self._ws_server_start_logged = False
            else:
                if self._is_map_ws_client_enabled():
                    self._stop_map_ws_client()
                if not self._is_ws_position_server_enabled():
                    self.log_info("ItemNavigatorTask 启动 - 正在启动WS服务")
                    self._start_ws_position_server(host='127.0.0.1', port=3001)
                    self._ws_server_start_logged = False
                elif not self._ws_server_start_logged:
                    # 首次检测到 WS 服务已启动，仅记录一次
                    self.log_info("ItemNavigatorTask: WS服务已启动 ws://127.0.0.1:3001")
                    self._ws_server_start_logged = True

            # read current selected items from task config (this is user-facing)
            selected_items = list(self.config.get('选择物品') or [])

            # fetch player position from local websocket service
            # 优先获取最新数据，如果没有新数据则返回缓存的旧值（避免"数据不完整"错误）
            try:
                payload = self._recv_ws_position_payload_or_cached(timeout=0.1)
            except Exception:
                self.info_set('导航', '无法读取WS位置')
                self.sleep(1.0)
                return

            # parse payload (兼容扁平结构 / data 包裹)
            pos, map_id, px, py, pz = self._extract_position_payload(payload)
            if not pos or not map_id:
                if map_cred:
                    self.info_set('导航', '地图WS位置数据待接收...')
                else:
                    self.info_set('导航', 'WS位置数据待接收... (需要客户端连接到 ws://127.0.0.1:3001)')
                self.sleep(1.0)
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
                        continue  # 跳过已标记的物品，继续查找下一个
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
            target_y = best.get('y', 0)

            for cfg in SPECIAL_ITEM_Y_OFFSET.values():
                if cfg["pattern"].match(best_meta or ""):
                    target_y += cfg["value"]
                    break

            dy_height = target_y - py
            near_xz = best_dxz <= float(self._near_xz_threshold)

            # direction angle in degrees for XZ vector (player->target) relative to +X
            dx = best.get('x', 0) - px
            dz = best.get('z', 0) - pz
            angle = math.degrees(math.atan2(dz, dx))

            status = f"目标={best_meta} 距离XZ={best_dxz:.3f} 角度={angle:.3f}°"
            if near_xz:
                updown = '上方' if dy_height > 0 else '下方' if dy_height < 0 else '同高'
                status += f" 接近: 高差Y={dy_height:.3f} ({updown})"

            # publish minimal UI info (任务显示栏)
            self.info_set('导航', status)

            # overlay: 默认方向箭头 + 高差箭头
            self._draw_nearby_markers(
                px=px,
                pz=pz,
                candidates=candidates,
                map_id=map_id,
            )
            self._draw_nav_arrow(dx, dz, tooltip=f"{best_meta} | XZ:{best_dxz:.3f} | Y:{dy_height:.3f}")
            self._draw_height_arrow(dy_height, tooltip=f"{best_meta} | Y:{dy_height:.3f}")

            # 标记逻辑：锁定目标并要求连续按住指定时长（_mark_lock_required）才能标记
            mark_key = str(self.config.get('标记按键') or '').strip() or 'f'
            cur_key = self._is_key_pressed(mark_key)

            if near_xz:
                # 计算目标哈希并确保锁定目标为当前最近目标
                h = self._point_hash(best, best_meta)
                if self._mark_lock_target is None or self._mark_lock_target.get('hash') != h:
                    self._mark_lock_target = {'map_id': map_id, 'hash': h, 'start_time': None}

                # 如果按键被按下，开始/继续计时；若连续保持足够长则标记
                if cur_key:
                    now = self.active_time()
                    if self._mark_lock_target.get('start_time') is None:
                        self._mark_lock_target['start_time'] = now
                    else:
                        elapsed = now - self._mark_lock_target['start_time']
                        if elapsed >= float(self._mark_lock_required):
                            # 最终确认未被提前标记
                            if h not in self._marked.get(map_id, set()):
                                with self._marked_lock:
                                    self._marked.setdefault(map_id, set()).add(h)
                                    self._dirty = True
                                self.info_set('导航', f'已标记: {best_meta} ({h})')
                            # 标记完成或已标记，清除锁定
                            self._mark_lock_target = None
                else:
                    # 在按住计时期间若有一帧松开，则取消本次锁定
                    if self._mark_lock_target and self._mark_lock_target.get('start_time') is not None:
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
