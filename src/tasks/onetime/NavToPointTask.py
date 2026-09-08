# -*- coding: utf-8 -*-
"""导航到坐标点：使用 3D 体素网格自动走到目标坐标（导航执行测试任务）。

一次性任务：启用后循环执行 NavRunner（朝向对齐→行走→卡住重规划），
到达目标/失败后结束并自动禁用。

- 网格：按当前位置 mapId 与「网格模式」（自动/3D/2D）加载
  assets/nav/<mapId>.grid.json 或 <mapId>_*_2d.grid.json；
- 位置：与「物品导航」共用 WS 位置源；首次规划前等本会话新位置，
  不用上一会话的陈旧缓存坐标当起点；
- 朝向：小地图箭头角 get_arrow_angle + 「朝向偏移」映射到世界 yaw，
  转向用「yaw_per_pixel」换算鼠标位移；
- 行走：方向对准后按住 W 连续移动，转向/停止/战斗时松开；
- 规划：每次成功规划（含卡住重规划）输出拐点位置日志；
- 战斗：自动战斗任务运行期间自动暂停，结束后自动重规划；
- 滑索/传送：滑索走 ride_zip 钩子，传送走 teleport 钩子（任务层实现）。
"""

import math
import time
from pathlib import Path

from qfluentwidgets import FluentIcon

from src.config import config
from src.core.BaseEfTask import BaseEfTask
from src.icons import Icons
from src.image.arrow_heading import infer_arrow_heading
from src.nav.grid import GridMap
from src.nav.nav_runner import NavRunner, RunnerConfig
from src.nav.storage import pick_best_2d_grid
from src.tasks.mixin.minimap_odometry import MinimapOdometry
from src.tasks.mixin.minimap_position_fusion import MinimapPositionFusion
from src.tasks.mixin.ws_position_mixin import WsPositionMixin
from ok import Logger

logger = Logger.get_logger(__name__)

# 小地图融合位置默认参数（标定值，2560x1440 下）
_DEFAULT_SCALE = 0.642344
_DEFAULT_MATRIX = "0.671428,0.005240,0.040819,-0.639992"

# 转向后同步身体朝向的单次 W 按键时长（秒）
_WALK_PRESS_TIME = 0.4
# 执行看门狗：位置长时间无变化判执行失败（秒）
_WATCHDOG_SECONDS = 90.0
# 朝向日志间隔（秒）
_HEADING_LOG_INTERVAL = 5.0


class _GameNavControls:
    """NavControls 协议的真实游戏实现（绑定到任务实例）。"""

    def __init__(self, task: "NavToPointTask"):
        self.task = task
        self.last_arrow_angle: float | None = None  # 小地图箭头角（原始）
        self.last_world_yaw: float | None = None    # 世界朝向（箭头角 + 朝向偏移）
        self._w_down = False

    def heading(self) -> float | None:
        # 直接读当前帧箭头角，绝不按 W（按 W 会让角色真的移动，破坏转向闭环）
        try:
            frame = self.task.next_frame()
            # 几何法/模板法都返回屏幕角：0°=北/上，90°=右/东。
            # 世界角按游戏约定映射：正北 0°，正西 90°，逆时针增大。
            angle = infer_arrow_heading(frame)
            if angle is None:
                angle, _score = self.task.get_arrow_angle(benchmark_width=2560)
        except Exception as e:
            self.task.log_warning(f"朝向读取失败: {e}")
            return None
        if angle is None:
            return None
        self.last_arrow_angle = float(angle)
        world_angle = (-float(angle)) % 360.0
        offset = float(self.task.config.get('朝向偏移(度)', 0.0))
        self.last_world_yaw = (world_angle + offset) % 360.0
        return self.last_world_yaw

    def turn(self, delta_deg: float) -> None:
        # 用标定系数（必为负）换算鼠标位移，转完视角后按 W 迈一步让身体朝向跟上。
        # 关键：这个游戏转视角后角色身体不会立即转向，必须按前进迈一步才更新朝向。
        k_raw = float(self.task.config.get('yaw_per_pixel(负值)', -0.07))
        k = -abs(k_raw) if k_raw > 0 else k_raw  # 系数必为负
        if abs(k) < 1e-9:
            self.task.log_warning("yaw_per_pixel 系数无效，跳过转向")
            return
        dx = int(round(float(delta_deg) / k))
        try:
            self.task.active_and_send_mouse_delta(dx=dx, dy=0, steps=1, delay=0)
        except Exception as e:
            self.task.log_error(f"转向失败 dx={dx}: {e}", exception=e)
        # 等视角旋转完成
        self.task.sleep(float(self.task.config.get('转向后等待(秒)', 0.4)))
        # 按 W 迈一小步，让角色身体朝向跟到视角方向（转视角不转身体）
        self.task.press_key("w", down_time=_WALK_PRESS_TIME)

    def walk(self, held: bool) -> None:
        # 连续移动：方向确定后按住 W，需要停止/转向/战斗时再松开
        if held and not self._w_down:
            self.task.send_key_down("w")
            self._w_down = True
        elif not held and self._w_down:
            self.task.send_key_up("w")
            self._w_down = False

    def release(self) -> None:
        self.walk(False)

    def recover_stuck(self) -> None:
        """卡住脱困：左右走位(A/D)各一小段 + 向前(W)一小段，看能否移开或重新对准。

        用于被挡/卡住时先试"侧移+前进"再往前进；若仍卡住由 NavRunner 换方向/失败。
        """
        task = self.task
        dur = max(0.2, float(task.config.get('卡住脱困时长(秒)', 0.5)))
        try:
            task.send_key_down("a")
            task.sleep(dur)
            task.send_key_up("a")
            task.sleep(0.1)
            task.send_key_down("d")
            task.sleep(dur)
            task.send_key_up("d")
            task.sleep(0.1)
            task.send_key_down("w")
            task.sleep(dur)
            task.send_key_up("w")
        except Exception as e:
            task.log_warning(f"卡住脱困动作执行失败: {e}")

    def ride_zip(self, start_pos, end_pos) -> None:
        self.task.log_warning(
            f"测试版不自动执行滑索：请手动滑行 {start_pos} -> {end_pos}，"
            f"到达后导航自动继续", notify=True)

    def teleport(self, node_pos) -> None:
        self.task.log_warning(
            f"测试版不自动执行传送：请手动传送到 {node_pos} 附近，"
            f"到达后导航自动继续", notify=True)


class NavToPointTask(WsPositionMixin, BaseEfTask):
    """导航到目标坐标点（导航执行测试）。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.name = "导航到坐标点"
        self.description = "使用导航图自动走到目标坐标点（导航执行测试）"
        self.icon = Icons.Navigation

        self.default_config.update({
            '目标X': 0.0,
            '目标Y': 0.0,
            '目标Z': 0.0,
            '地图id': '',
            '网格模式': '自动',
            '允许传送': False,
            '吸附半径(米)': 8.0,
            '允许冒险': True,
            '起点坐标(留空用当前位置)': '',
            'yaw_per_pixel(负值)': -0.07,
            '朝向偏移(度)': 0.0,
            '转向后等待(秒)': 0.4,
            '朝向误差容差(度)': 25.0,
            '到达半径(米)': 1.0,
            '到达目标半径(米)': 3.0,
            '卡住判定时间(秒)': 3.0,
            '卡住最小位移(米)': 0.5,
            '卡住脱困次数': 2,
            '卡住脱困时长(秒)': 0.5,
            '离墙距离(格)': 2,
            '离墙代价': 0.6,
            'content': '',
            '地图账号': '',
            '比例尺(米/像素)': _DEFAULT_SCALE,
            '轴映射(逗号4值)': _DEFAULT_MATRIX,
            '位置日志间隔(秒)': 0.0,  # 0=每帧都打印当前位置与想去
        })
        self.config_type['地图账号'] = {
            'type': 'drop_down',
            'options': self._get_map_account_options(),
        }
        self.config_type['网格模式'] = {
            'type': 'drop_down',
            'options': ['自动', '3D(轨迹网格)', '2D(编辑器网格)'],
        }
        self.config_type['规划路线'] = {
            'type': 'button',
            'text': '规划路线',
            'icon': FluentIcon.SEND,
            'callback': self.open_route_dialog,
        }
        self.config_type['重新加载网格'] = {
            'type': 'button',
            'text': '重新加载网格',
            'icon': FluentIcon.SYNC,
            'callback': self.reload_grid,
        }
        self.config_description.update({
            '规划路线': '打开路线规划弹窗：输入目的地查看路线与标记，点击「行动」后开始导航。',
            '重新加载网格': (
                '清空当前导航网格缓存，任务下一轮主循环会从文件重新加载；\n'
                '用于改了网格文件（重建/编辑器导出）后不重启立即生效。'
            ),
            '目标X': '目标点的世界坐标 X（米，与 WS 位置流一致）。',
            '目标Y': '目标点的世界坐标 Y（米，高度）。',
            '目标Z': '目标点的世界坐标 Z（米，与 WS 位置流一致）。',
            '地图id': '目标所在地图 id（如 map01）；留空自动使用当前位置的地图。',
            '网格模式': (
                '选择导航使用的网格数据：\n'
                '自动：优先 <mapId>.grid.json（3D 轨迹网格），缺失时回退 <mapId>_*_2d.grid.json；\n'
                '3D(轨迹网格)：只用 build_nav_grid 生成的 3D 轨迹网格；\n'
                '2D(编辑器网格)：只用导航网格编辑器导出的二维网格（忽略高度，按 xz 平面寻路）。'
            ),
            '允许传送': (
                '寻路允许使用零成本传送（协议传送点）；\n'
                '测试版不自动执行传送，只提示目标位置。'
            ),
            '吸附半径(米)': (
                '坐标吸附到最近图节点的最大距离。\n'
                '报「起点附近没有图节点」时调大该值。'
            ),
            '允许冒险': (
                '吸附失败（超出吸附半径）时冒险规划：改用最近图节点继续走。'
            ),
            '起点坐标(留空用当前位置)': (
                '规划起点（x,y,z 逗号分隔）；留空用当前 WS 位置。\n'
                '人在图覆盖区外时填图内坐标仍可测试寻路（实际从当前位置走向路径）。'
            ),
            'yaw_per_pixel(负值)': (
                '鼠标位移→视角 yaw 的换算系数（度/像素，实测为负值）。\n'
                '用「鼠标视角旋转系数标定」任务测得；填正数会自动取负。'
            ),
            '朝向偏移(度)': (
                '小地图箭头角度 → 世界 yaw 的偏移（0-360）。\n'
                '走的方向不对时用「箭头角度实时读取」任务标定该值。'
            ),
            '转向后等待(秒)': (
                '发送鼠标转向后等待视角稳定的时间，太短会读到旋转中的瞬态角度导致乱转。'
            ),
            '朝向误差容差(度)': (
                '与目标方向夹角小于该值就前进，否则先转向。\n'
                '转向闭环有回摆时调大（默认 25），避免一直原地转。'
            ),
            '到达半径(米)': '距离目标该半径内判定到达（xz 平面）。',
            '到达目标半径(米)': (
                '距离最终目的地该半径内即判定完成（默认 3 米）。\n'
                '中间路径点仍用「到达半径」，目的地用更宽松的该值。'
            ),
            '卡住判定时间(秒)': (
                '行走中该时长内位移不足则判定卡住，禁用当前边并重规划。'
            ),
            '卡住最小位移(米)': '卡住判定时间窗内位移低于该值判定卡住。',
            '卡住脱困次数': '卡住时先尝试「按A/D走位+按W」脱困的最多次数；之后换方向/失败（0=不脱困）。',
            '卡住脱困时长(秒)': '脱困时每个键按下的时长（A/D/W 各一次）。',
            '离墙距离(格)': '寻路偏向走廊中线：free 格离墙小于该格数时按缺额加代价（0=关）。',
            '离墙代价': '离墙缺额每格加的代价；越大越贴中线、越不贴墙（0=关）。',
            '比例尺(米/像素)': '小地图比例尺（融合用）；用「小地图位移标定」任务测得。',
            '轴映射(逗号4值)': '地图系像素->世界系米的 2x2 矩阵（逗号4值 a11,a12,a21,a22）；来自「小地图位移标定」。',
            '位置日志间隔(秒)': '导航中实时打印一行位置信息的间隔（秒）。',
            'content': (
                '可选。直接填写 web-api.skland.com/account/info/hg/check 返回 JSON 里的 data.content 值。\n'
                '此项有值时优先使用，不再读取账号配置页。'
            ),
            '地图账号': (
                '可选。content 为空时，从账号配置页读取该账号保存的地图同步 content。\n'
                '账号列表来自账号配置页；留空则尝试使用当前任务账号上下文。'
            ),
        })
        self.default_config_group.update({
            '网页地图同步': ['content', '地图账号'],
            '网格': ['网格模式', '重新加载网格'],
            '目标': ['目标X', '目标Y', '目标Z', '地图id', '允许传送',
                      '吸附半径(米)', '允许冒险', '起点坐标(留空用当前位置)',
                      '到达半径(米)', '到达目标半径(米)'],
            '执行': ['yaw_per_pixel(负值)', '朝向偏移(度)', '转向后等待(秒)',
                      '朝向误差容差(度)', '卡住判定时间(秒)', '卡住最小位移(米)',
                      '卡住脱困次数', '卡住脱困时长(秒)',
                      '离墙距离(格)', '离墙代价', '规划路线',
                      '比例尺(米/像素)', '轴映射(逗号4值)'],
        })

        # internal constants (not user-facing)
        self._init_ws_position_mixin()
        self._grid_dir = Path("assets") / "nav"
        self._last_heading_log_at = 0.0
        self._grid: GridMap | None = None
        self._grid_map_id: str | None = None
        self._controls = _GameNavControls(self)
        self._runner: NavRunner | None = None
        self._window_missing_logged = False
        self._ws_wait_hint_logged = False
        self._fresh_hint_logged = False
        self._nav_fresh_pos_at: float | None = None
        self._last_moved_pos: tuple | None = None
        self._last_moved_at = 0.0
        # 导航坐标系：融合定位（WS 锚点 + 小地图里程计 + 朝向），构建于 run() 启动时
        self._odometry: MinimapOdometry | None = None
        self._fusion: MinimapPositionFusion | None = None
        self._use_fused = True
        self._fused_diag_counter = 0
        self._last_fused_st = None      # 最近一次融合 state() 结果（含 rest/heading）
        self._last_pos_log_at = 0.0

    @staticmethod
    def _parse_matrix(value):
        """把 "a11,a12,a21,a22" 解析为 2x2 矩阵；非法返回 None。"""
        parts = str(value or "").split(",")
        if len(parts) != 4:
            return None
        try:
            nums = [float(p) for p in parts]
        except (TypeError, ValueError):
            return None
        return [[nums[0], nums[1]], [nums[2], nums[3]]]

    def _build_fusion(self):
        """按配置构建小地图融合位置（位置源=融合时调用）。"""
        scale = max(0.0, float(self.config.get("比例尺(米/像素)", _DEFAULT_SCALE)))
        matrix = self._parse_matrix(self.config.get("轴映射(逗号4值)", _DEFAULT_MATRIX))
        self._odometry = MinimapOdometry(self, scale_m_per_px=(scale if scale > 0 else None))
        self._fusion = MinimapPositionFusion(
            self._odometry,
            map_to_world_px=matrix if matrix is not None else None,
            scale_m_per_px=scale,
        )
        self._use_fused = True
        return self._fusion

    def _fused_pos(self, map_id, px, py, pz) -> tuple:
        """返回执行器用当前位置（与「实时位置」任务同一套融合方法）。

        与 MinimapRealtimePosition 一致：先 state()（采集里程计增量并给出融合位置），
        再用 WS 位置设/校锚点；顺序不能反（先采样累积位移，再校准），否则里程计被反复重置。
        """
        if self._fusion is None:
            return (px, py, pz)
        try:
            frame = self.next_frame()
            st = self._fusion.state(now=self.active_time(), frame=frame)
            self._last_fused_st = st
            fx, fz = st.get("x"), st.get("z")

            if fx is None or fz is None:
                # 未锚定：强制用当前 WS 位置设锚点（起步时角色静止，WS 延迟无影响）
                self._fusion.sync((px, py, pz), map_id=map_id, now=self.active_time())
            else:
                # 已锚定：静止时用 WS 校准（移动中暂存）
                self._fusion.try_sync((px, py, pz), map_id=map_id, now=self.active_time())

            # 校准后若锚点被更新，重读一次融合位置，避免本拍使用旧锚点
            est = self._fusion.estimate()
            if est is not None:
                fx, fz = est.get("x"), est.get("z")

            if fx is not None and fz is not None:
                # 诊断：偶尔打印融合位置 vs WS 原始 vs 里程计位移，便于排查冻结/偏移
                self._fused_diag_counter += 1
                if self._fused_diag_counter % 10 == 1:
                    odpx = self._odometry.position_px()
                    self.log_info(
                        f"[融合位置] fused=({fx:.2f},{fz:.2f}) ws=({px:.2f},{pz:.2f}) "
                        f"差=({fx-px:.2f},{fz-pz:.2f})m 位移px=({odpx[0]:.1f},{odpx[1]:.1f})"
                    )
                return (float(fx), py, float(fz))
        except Exception as e:  # noqa: BLE001
            self.log_warning(f"融合位置计算失败，回退 WS 原始: {e}")
        return (px, py, pz)

    def _maybe_log_position(self, cur, px, py, pz):
        """按配置间隔，在导航中实时打印一行位置信息。

        格式（与实时位置任务一致）：
          位置=(x, z)m 朝向=..° 速度=..m/s 状态=静止/移动 | 误差=..m 最新WS=(x, z) 位移=(dx, dy)px
        """
        try:
            interval = float(self.config.get("位置日志间隔(秒)", 1.0))
        except (TypeError, ValueError):
            interval = 1.0
        if interval > 0 and time.time() - self._last_pos_log_at < interval:
            return
        self._last_pos_log_at = time.time()

        # 朝向（箭头角）
        heading = self._controls.last_arrow_angle
        heading_txt = f"{heading:.1f}°" if heading is not None else "?"

        # 状态（静止/移动）
        st = self._last_fused_st
        rest_txt = "静止" if bool(st.get("rest") if st else False) else "移动"

        # 速度
        speed_txt = "-"
        if self._odometry is not None:
            last = self._odometry.last_sample()
            if last and last.get("sampled") and last.get("ok"):
                dm = last.get("dmap_m") or (0.0, 0.0)
                dt = max(0.0, float(last.get("dt") or 0.0))
                if dt > 1e-6:
                    speed_txt = f"{math.hypot(float(dm[0]), float(dm[1])) / dt:.2f}m/s"

        # 误差（当前位置 vs 最新 WS）
        err_txt = "-"
        if self._odometry is not None:
            err = math.hypot(cur[0] - px, cur[2] - pz)
            err_txt = f"{err:.2f}m"

        # 位移（里程计原始像素）
        od_px = self._odometry.position_px() if self._odometry is not None else (0.0, 0.0)

        # 想去哪：当前目标拐点 + 目标朝向 / 是否转向
        target_txt = "-"
        if self._runner is not None:
            ti = self._runner.current_target_info()
            if ti is not None:
                wp = ti["waypoint"]
                act = "转向" if ti["turning"] else "前进"
                yaw_txt = f"{ti['yaw']:.1f}°" if ti["yaw"] is not None else "?"
                target_txt = (f"({wp[0]:.1f},{wp[2]:.1f})m 目标朝向{yaw_txt} "
                              f"({act}, 拐点{ti['idx']}/{ti['total']})")

        self.log_info(
            f"[导航位置] 位置=({cur[0]:.2f}, {cur[2]:.2f})m 朝向={heading_txt} "
            f"速度={speed_txt} 状态={rest_txt} | 误差={err_txt} "
            f"最新WS=({px:.2f}, {pz:.2f}) 位移=({od_px[0]:.1f}, {od_px[1]:.1f})px "
            f"| 想去={target_txt}"
        )

    def _is_combat_active(self) -> bool:
        """自动战斗任务正在运行期间暂停导航。"""
        try:
            from src.tasks.trigger.AutoCombatTask import AutoCombatTask

            combat_task = self.get_task_by_class(AutoCombatTask)
            return bool(combat_task is not None and getattr(combat_task, "running", False))
        except Exception:
            return False

    def _load_grid_for(self, map_id: str) -> GridMap | None:
        """按 mapId 与「网格模式」加载（或复用）导航网格。

        自动：优先 <mapId>.grid.json（3D 轨迹网格），缺失时回退
        <mapId>_*_2d.grid.json（导航网格编辑器的二维网格，取 free 格最多）；
        3D(轨迹网格)：只用 build_nav_grid 生成的三维轨迹网格；
        2D(编辑器网格)：只用二维网格（忽略高度，按 xz 平面寻路/执行）。
        """
        if self._grid is not None and self._grid_map_id == map_id:
            return self._grid
        mode = str(self.config.get('网格模式', '自动') or '自动').strip()
        path = self._grid_dir / f"{map_id}.grid.json"

        # 2D 模式：只加载二维网格
        if mode == '2D(编辑器网格)':
            best, best_path = pick_best_2d_grid(self._grid_dir, map_id)
            if best is None and path.exists():
                # 用户可能把 2D 文件直接命名为 <mapId>.grid.json
                try:
                    cand = GridMap.load(path)
                    if cand.is_2d:
                        best, best_path = cand, path
                except Exception as e:
                    self.log_warning(f"网格文件加载失败 {path}: {e}")
            if best is None:
                self._grid = None
                self._grid_map_id = None
                self.log_warning(
                    f"网格模式为「2D(编辑器网格)」但未找到 {map_id}_*_2d.grid.json，"
                    f"请从导航网格编辑器同步二维网格到 assets/nav/")
                return None
            self._grid = best
            self._grid_map_id = map_id
            self.log_info(
                f"已加载二维导航网格: {best_path}（{len(best.cells)} 个可行走格，"
                f"忽略高度按 xz 平面寻路）")
            return self._grid

        # 3D 模式：只用三维轨迹网格
        if mode == '3D(轨迹网格)':
            if not path.exists():
                self._grid = None
                self._grid_map_id = None
                self.log_warning(
                    f"网格模式为「3D(轨迹网格)」但未找到 {path}，"
                    f"请先运行 `uv run python scripts/build_nav_grid.py --map {map_id}`")
                return None
            grid = GridMap.load(path)
            if grid.is_2d:
                self.log_warning(
                    f"{path} 是二维网格（<mapId>.grid.json 应为 3D 轨迹网格产物），"
                    f"仍将按其数据导航", notify=True)
            self._grid = grid
            self._grid_map_id = map_id
            self.log_info(f"已加载导航网格: {path}（{len(grid.cells)} 个已知格）")
            return self._grid

        # 自动（默认）：3D 优先，缺失回退二维
        if path.exists():
            self._grid = GridMap.load(path)
            self._grid_map_id = map_id
            self.log_info(f"已加载导航网格: {path}（{len(self._grid.cells)} 个已知格）")
            return self._grid
        best, best_path = pick_best_2d_grid(self._grid_dir, map_id)
        if best is None:
            self._grid = None
            self._grid_map_id = None
            self.log_warning(
                f"未找到导航网格: {path}（也无 {map_id}_*_2d.grid.json），"
                f"请先运行 `uv run python scripts/build_nav_grid.py --map {map_id}`，"
                f"或把导航网格编辑器的二维网格同步到 assets/nav/")
            return None
        self._grid = best
        self._grid_map_id = map_id
        if best.is_2d:
            self.log_info(
                f"已加载二维导航网格: {best_path}（{len(best.cells)} 个可行走格，"
                f"忽略高度按 xz 平面寻路）")
        else:
            self.log_info(f"已加载导航网格: {best_path}（{len(best.cells)} 个已知格）")
        return self._grid

    def _get_current_position(self):
        """读取当前 WS 位置，返回 (map_id, x, y, z) 或 None。"""
        try:
            payload = self._recv_ws_position_payload_or_cached(timeout=0.1)
        except Exception:
            return None
        if payload is None:
            return None
        pos, map_id, px, py, pz = self._extract_position_payload(payload)
        if not pos or not map_id:
            return None
        return str(map_id), px, py, pz

    def open_route_dialog(self, *_):
        """打开路线规划弹窗（「规划路线」按钮回调）。"""
        from src.gui.NavRouteDialog import NavRouteDialog

        dialog = NavRouteDialog(self)
        dialog.exec()

    def reload_grid(self, *_):
        """清空导航网格缓存，任务下一轮主循环会从文件重新加载（「重新加载网格」按钮回调）。"""
        if self._grid is not None or self._grid_map_id is not None:
            self._grid = None
            self._grid_map_id = None
            self.log_info("已清空导航网格缓存，将在下一轮主循环重新加载", notify=True)
        else:
            self.log_info("导航网格缓存已为空，无需清空")

    def _runner_config(self) -> RunnerConfig:
        try:
            wall_margin = max(0, int(self.config.get('离墙距离(格)', 2)))
        except (TypeError, ValueError):
            wall_margin = 2
        try:
            wall_penalty = max(0.0, float(self.config.get('离墙代价', 0.6)))
        except (TypeError, ValueError):
            wall_penalty = 0.6
        return RunnerConfig(
            arrive_radius=float(self.config.get('到达半径(米)', 1.0)),
            goal_radius=float(self.config.get('到达目标半径(米)', 3.0)),
            heading_tol_deg=float(self.config.get('朝向误差容差(度)', 25.0)),
            stuck_window_s=float(self.config.get('卡住判定时间(秒)', 3.0)),
            stuck_min_dist=float(self.config.get('卡住最小位移(米)', 0.5)),
            allow_teleport=bool(self.config.get('允许传送', False)),
            snap_radius=float(self.config.get('吸附半径(米)', 8.0)),
            allow_risk=bool(self.config.get('允许冒险', True)),
            wall_margin=wall_margin,
            wall_penalty=wall_penalty,
            max_stuck_recovery=max(0, int(self.config.get('卡住脱困次数', 2))),
        )

    def _create_runner(self, grid: GridMap) -> NavRunner:
        runner = NavRunner(grid, self._controls, self._runner_config())
        runner.set_combat_check(self._is_combat_active)
        runner.on_plan = self._on_plan_log
        runner.recover_stuck_cb = self._controls.recover_stuck
        return runner

    def _on_plan_log(self, res):
        """每次成功规划输出拐点位置（初次规划/卡住重规划/战斗恢复都会输出）。"""
        wps = " -> ".join(f"({w[0]:.1f},{w[2]:.1f})" for w in res.waypoints[:16])
        if len(res.waypoints) > 16:
            wps += f" …共{len(res.waypoints)}点"
        flags = []
        if res.risky:
            flags.append("冒险")
        if res.via_teleport:
            flags.append("经传送")
        suffix = f"（{'、'.join(flags)}）" if flags else ""
        self.log_info(f"规划路线 {len(res.waypoints)} 个拐点: {wps}{suffix}")

    def _start_point(self) -> tuple | None:
        """解析「起点坐标(留空用当前位置)」，非法/为空返回 None。"""
        raw = str(self.config.get('起点坐标(留空用当前位置)', '') or '').strip()
        if not raw:
            return None
        parts = [p.strip() for p in raw.replace('，', ',').split(',')]
        if len(parts) != 3:
            self.log_warning(f"起点坐标格式错误（应为 x,y,z）: {raw}")
            return None
        try:
            return tuple(float(p) for p in parts)
        except ValueError:
            self.log_warning(f"起点坐标解析失败: {raw}")
            return None

    def pause(self):
        # 外部暂停时立即松开 W，避免任务暂停但游戏角色还在继续跑
        controls = getattr(self, '_controls', None)
        if controls is not None:
            controls.release()
        return super().pause()

    def unpause(self):
        # 恢复后不自动按 W，下一次 step 会按当前朝向决定是否继续走
        return super().unpause()

    def run(self):
        self.check_resolution()
        target = (
            float(self.config.get('目标X', 0.0)),
            float(self.config.get('目标Y', 0.0)),
            float(self.config.get('目标Z', 0.0)),
        )
        self.log_info(f"导航到坐标点: ({target[0]:.1f},{target[1]:.1f},{target[2]:.1f})", notify=True)

        # 每次任务开始都重置 runner，避免上一次导航的 done/moving 状态残留，
        # 导致新目标还没规划就直接被判定为“已到达”。
        if self._runner is not None:
            self._runner.abort()
        self._runner = None
        self._last_moved_pos = None
        self._last_moved_at = 0.0
        # 重新要求本会话的新位置（防陈旧缓存坐标当起点）
        self._nav_fresh_pos_at = None
        self._fresh_hint_logged = False

        # 导航坐标系：融合定位（WS 锚点 + 里程计 + 朝向）
        self._build_fusion()

        while True:
            if not self._is_game_window_alive():
                self._controls.release()
                self._stop_position_sources()
                if not self._window_missing_logged:
                    self.log_info("导航到坐标点：游戏窗口不存在或不可见，任务结束")
                    self._window_missing_logged = True
                self.info_set('导航', '游戏窗口不存在')
                return False

            self._window_missing_logged = False
            map_cred = self._get_account_map_content()
            self._ensure_ws_position_source(map_cred)

            try:
                fresh_payload = self._recv_ws_position_payload(timeout=0.1)
            except Exception:
                fresh_payload = None
            if fresh_payload is not None:
                # 记录本会话收到过新位置：首次规划前不信任上一会话的陈旧缓存
                self._nav_fresh_pos_at = time.time()
            payload = fresh_payload if fresh_payload is not None \
                else self._recv_ws_position_payload_or_cached(timeout=0.1)
            if payload is None:
                if not self._ws_wait_hint_logged:
                    self._ws_wait_hint_logged = True
                    if self._get_account_map_content():
                        self.log_warning(
                            "已配置 content，等待官方地图WS位置数据（确认账号已绑定终末地角色）")
                    else:
                        self.log_warning(
                            "未配置 content：等待油猴脚本连接 ws://127.0.0.1:3001；"
                            "或在本任务配置里填写 content / 地图账号")
                self._controls.release()
                self.info_set('导航', '无法读取WS位置')
                self.sleep(1.0)
                continue

            pos, map_id, px, py, pz = self._extract_position_payload(payload)
            if not pos or not map_id:
                self._controls.release()
                self.info_set('导航', 'WS位置数据待接收...')
                self.sleep(1.0)
                continue
            map_id = str(map_id)

            grid = self._load_grid_for(map_id)
            if grid is None:
                self._controls.release()
                self.info_set('导航', f'缺少导航网格 {map_id}，请先建网格')
                self.sleep(2.0)
                continue

            # 初始/空闲或地图切换时（重新）创建 runner
            if self._runner is None or self._runner.grid is not grid:
                self._controls.release()
                self._runner = self._create_runner(grid)
                self.log_info(f"导航执行器已就绪（到达半径 "
                              f"{self._runner.cfg.arrive_radius}m）")

            # 初始/空闲时规划：显式传入真实起点，避免使用 runner 的默认 (0,0,0)
            if self._runner.state == "idle":
                configured_start = self._start_point()
                # 首次规划前必须等到本会话的新位置；除非用户显式给了「起点坐标」
                if configured_start is None and self._nav_fresh_pos_at is None:
                    if not self._fresh_hint_logged:
                        self._fresh_hint_logged = True
                        self.log_warning(
                            "等待新的位置数据：首次规划前不使用上一会话的陈旧缓存坐标，"
                            "避免从错误起点规划（官方地图 WS 连接后几秒内自动开始）")
                    self._controls.release()
                    self.info_set('导航', '等待新位置数据…')
                    self.sleep(1.0)
                    continue
                start = configured_start or (px, py, pz)
                if configured_start is not None:
                    self.log_info(f"使用指定规划起点: {start}")
                if not self._runner.navigate_to(target, start=start):
                    self._controls.release()
                    self.log_warning(f"导航规划失败: {self._runner.reason}", notify=True)
                    self._stop_position_sources()
                    return False
                if self._runner.risky:
                    self.log_info("冒险导航：起点/终点偏离已走过区域，将冒险尝试", notify=True)

            # 执行位置：融合坐标（WS 锚点 + 里程计）
            cur = self._fused_pos(map_id, px, py, pz)
            self._runner.step(cur)
            self._maybe_log_position(cur, px, py, pz)

            # 看门狗：位置长时间无变化（如卡在滑索提示）判失败
            now = time.time()
            if self._last_moved_pos is None or (
                    abs(cur[0] - self._last_moved_pos[0]) > 0.3
                    or abs(cur[2] - self._last_moved_pos[2]) > 0.3):
                self._last_moved_pos = cur
                self._last_moved_at = now
            elif self._runner.state == "moving" and now - self._last_moved_at > _WATCHDOG_SECONDS:
                self.log_warning("导航执行超时：位置长时间无变化，任务结束", notify=True)
                self._controls.release()
                self._stop_position_sources()
                return False

            state = self._runner.state
            self.info_set(
                '导航',
                f"{state} | 当前({cur[0]:.1f},{cur[1]:.1f},{cur[2]:.1f}) "
                f"-> 目标({target[0]:.1f},{target[1]:.1f},{target[2]:.1f})"
            )

            # 输出当前朝向：世界朝向（箭头角+偏移）与原始箭头角
            wyaw = self._controls.last_world_yaw
            arrow = self._controls.last_arrow_angle
            if wyaw is not None:
                self.info_set('朝向', f"世界 {wyaw:.0f}° / 箭头 {arrow:.0f}°")
                if now - self._last_heading_log_at >= _HEADING_LOG_INTERVAL:
                    self._last_heading_log_at = now
                    self.log_info(
                        f"[朝向] 世界 {wyaw:.1f}° / 箭头 {arrow:.1f}° "
                        f"(朝向偏移 {float(self.config.get('朝向偏移(度)', 0.0)):+.1f}°)")
            else:
                self.info_set('朝向', '待读取')

            if state == "done":
                self._controls.release()
                self.log_info(f"已到达目标附近: ({cur[0]:.2f},{cur[1]:.2f},{cur[2]:.2f})", notify=True)
                self._stop_position_sources()
                return True
            if state == "failed":
                self._controls.release()
                self.log_warning(f"导航失败: {self._runner.reason}", notify=True)
                self._stop_position_sources()
                return False

            self.sleep(0.2)

    def on_destroy(self):
        try:
            self._controls.release()
        except Exception:
            pass
        try:
            self._stop_position_sources()
        except Exception as e:
            logger.error(f"导航到坐标点停止位置源异常: {e}")
        on_destroy = getattr(super(), "on_destroy", None)
        if callable(on_destroy):
            on_destroy()
