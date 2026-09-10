# -*- coding: utf-8 -*-
"""小地图实时位置测试任务。

每个采样拍输出一行**易读**日志（开头有一次字段说明）：
  [实时位置] #003 | 位置=(-323.32, 461.00)m 朝向=353.5° 速度=0.96m/s 状态=静止 |
              误差=0.99m 最新WS=(-323.03, 463.64) 位移=(0.2, -1.5)px

判定"静止"需要**小地图里程计位移极小 且 WS 也没有位移**两者同时成立；静止时以
WS 坐标为基准重新校准。校准发生的那一拍额外输出一行，给出**校准前由小地图推算的
坐标**与它跟 WS 的偏差，用来观察小地图定位在两次校准之间漂了多少：
  [静止校准] #045 小地图推算=(569.00, -150.95)m WS=(569.58, -150.92)m
             偏差=(-0.58, -0.03)m 距离=0.58m

- 位置：融合后的实时坐标（WS 锚点 + 里程计增量 -> 世界 x/z, 米）。校准那一拍为
  校准后的值（= WS），校准前的推算值见上面的 [静止校准] 行。
- 朝向：小地图箭头角度（可信，直接使用）；低置信时标注 (低置信)。
- 速度：当前移动速度（米/秒，世界系，与静止判定同一个量）。
- 状态：静止 / 移动（静止 = 小地图与 WS 同时显示没动，此时由 WS 校准、里程计清零）。
- 误差：位置与"最新 WS 坐标"的距离（米）。静止校准后≈0；移动时≈WS 延迟，非误差。
- 最新WS / 位移：最近收到的官方地图坐标(可能滞后) / 里程计原始位移(地图系像素)。

坐标系：位置/WS 是"世界系"(x, z)；位移是"地图系"(图像 x 右、y 下，北向上)，
世界_z 与地图_y 符号相反（由 matrix 处理）。用于验证"朝向 + 位移里程计 + WS 融合"
整条实时定位链路；不写入正式配置。
"""

import math

from qfluentwidgets import FluentIcon

from src.core.BaseEfTask import BaseEfTask
from src.tasks.account.account_scope_store import get_account_map_content, resolve_account_id
from src.tasks.mixin.minimap_odometry import MinimapOdometry
from src.tasks.mixin.minimap_position_fusion import MinimapPositionFusion
from src.tasks.mixin.ws_position_mixin import WsPositionMixin

# 默认轴映射（来自标定：世界_x ≈ +scale*map_x，世界_z ≈ -scale*map_y）。逗号4值：a11,a12,a21,a22。
DEFAULT_SCALE = 0.6641
DEFAULT_MAP_TO_WORLD = "0.6641,0,0,-0.6641"


def _parse_matrix(value):
    """把 "a11,a12,a21,a22" 解析为 2x2 矩阵；非法时返回 None。"""
    parts = str(value or "").split(",")
    if len(parts) != 4:
        return None
    try:
        nums = [float(p) for p in parts]
    except (TypeError, ValueError):
        return None
    return [[nums[0], nums[1]], [nums[2], nums[3]]]


class MinimapRealtimePosition(BaseEfTask, WsPositionMixin):
    """小地图实时位置测试（工具与调试分组）。"""

    requires_foreground = True  # 需要读取游戏画面/小地图

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "小地图实时位置"
        self.group_name = "工具与调试"
        self.group_icon = FluentIcon.DEVELOPER_TOOLS
        self.description = "每个采样拍输出 朝向 + 小地图计算位置 + 结合WS的融合位置"
        self.visible = self.debug

        # 初始化 WS 状态；实时采样期间也需要持续读 WS，避免被空闲超时踢掉
        self._init_ws_position_mixin()
        self._map_ws_consumer_idle_timeout = 3600.0

        self.default_config = {
            "采样间隔(秒)": 0.5,
            "运行时长(秒)": 20.0,
            "比例尺(米/像素)": DEFAULT_SCALE,
            "轴映射(逗号4值)": DEFAULT_MAP_TO_WORLD,
            "真值content": "",
            "真值地图账号": "",
            "WS等待稳定秒数": 10.0,
            "WS稳定最小位置数": 3,
            "日志间隔(拍)": 1,
            "朝向最低分数": 0.6,
        }
        self.config_description = {
            "采样间隔(秒)": "固定采样周期(秒)：睡到下一个采样点，保证每拍间隔为该值（不会被单拍工作耗时撑大）",
            "运行时长(秒)": "本次采样运行的秒数",
            "比例尺(米/像素)": "小地图比例尺（里程计 position_m / 融合用）",
            "轴映射(逗号4值)": "地图系像素->世界系米的 2x2 矩阵，逗号4值 a11,a12,a21,a22；"
                               "默认 diag(+比例尺, -比例尺)（世界_z 与地图_y 符号相反）",
            "真值content": "可选。官方地图 hg/check 的 data.content，用于 WS 真值",
            "真值地图账号": "可选。content 为空时从账号配置页读取该账号的地图同步 content",
            "WS等待稳定秒数": "等 WS 真值流稳定（连续同一 mapId 有效位置）的最大等待秒数",
            "WS稳定最小位置数": "判为稳定所需连续有效位置个数",
            "日志间隔(拍)": "每几拍输出一行日志（1=每拍都输出）",
            "朝向最低分数": "箭头角度检测最低置信度，低于该值朝向判为不可用",
        }

        self._od: MinimapOdometry | None = None
        self._fusion: MinimapPositionFusion | None = None
        self._ws_map_id: str | None = None
        self._last_ws = None  # 最近一条 WS 位置 (x, z)，用于算 err

    # ------------------------------------------------------------------ #
    # 配置
    # ------------------------------------------------------------------ #
    def _cfg_float(self, key, default):
        try:
            return float(self.config.get(key, default))
        except (TypeError, ValueError):
            return float(default)

    def _cfg_int(self, key, default):
        try:
            return int(self.config.get(key, default))
        except (TypeError, ValueError):
            return int(default)

    # ------------------------------------------------------------------ #
    # 真值 WS（复用 WsPositionMixin 的底层能力）
    # ------------------------------------------------------------------ #
    def _resolve_ws_cred(self):
        content = str(self.config.get("真值content") or "").strip()
        if content:
            return content
        account = str(self.config.get("真值地图账号") or "").strip()
        if account:
            account_id = resolve_account_id(account, create_if_missing=False) or account
            return get_account_map_content(account_id, account_name=account)
        account_id = str(getattr(self, "current_account_id", "") or "").strip()
        account_name = str(getattr(self, "current_user", "") or "").strip()
        return get_account_map_content(account_id or account_name, account_name=account_name)

    def _start_ws_truth(self) -> bool:
        try:
            cred = self._resolve_ws_cred()
        except Exception as e:
            self.log_info(f"WS 真值不可用（读取 content 失败）: {e}")
            return False
        if not cred:
            self.log_info("未配置 WS 真值（content/地图账号为空），仅输出朝向与小地图位置")
            return False
        try:
            ok = self._start_map_ws_client(cred)
        except Exception as e:
            self.log_info(f"启动地图 WS 客户端失败: {e}")
            return False
        if ok:
            self.log_info("地图 WS 客户端已启动", notify=True)
        return ok

    def _poll_ws_fresh(self, timeout: float = 0.0):
        try:
            payload = self._recv_ws_position_payload(timeout=timeout)
        except Exception:
            return None
        if payload is None:
            return None
        try:
            pos, map_id, x, y, z = self._extract_position_payload(payload)
        except Exception:
            return None
        if pos is None or map_id is None:
            return None
        return (x, y, z, map_id)

    def _wait_ws_stable(self, timeout: float = 10.0, min_hits: int = 3):
        """等到 WS 真值流稳定，返回 (稳定?, mapId, 最后一条稳定位置)。

        最后一条稳定位置用于在稳定后立即设置绝对锚点，避免起步前几拍无锚点。
        """
        map_id = None
        hits = 0
        last_pos = None
        t0 = self.active_time()
        while self.active_time() - t0 < timeout:
            pos_ws = self._poll_ws_fresh(timeout=0.5)
            if pos_ws is None:
                continue
            mid = pos_ws[3]
            last_pos = pos_ws
            if map_id is None:
                map_id = mid
                hits = 1
            elif mid == map_id:
                hits += 1
            else:
                map_id = mid
                hits = 1
            if hits >= min_hits:
                return True, map_id, last_pos
        return False, map_id, last_pos

    # ------------------------------------------------------------------ #
    # 朝向
    # ------------------------------------------------------------------ #
    def _read_arrow(self, frame=None):
        try:
            angle, score = self.get_arrow_angle(target_image=frame, smoothing_threshold=None)
            return angle, 0.0 if score is None else float(score)
        except Exception as e:
            self.log_warning(f"读取箭头角度失败: {e}")
            return None, 0.0

    # ------------------------------------------------------------------ #
    # 主流程
    # ------------------------------------------------------------------ #
    def run(self):
        self.log_info("=== Minimap Realtime Position ===", notify=True)
        if not self.in_world():
            self.log_info("当前不在大世界画面，无法读取小地图。请先进入大世界。", notify=True)
            return

        scale = max(0.0, self._cfg_float("比例尺(米/像素)", DEFAULT_SCALE))
        matrix = _parse_matrix(self.config.get("轴映射(逗号4值)", DEFAULT_MAP_TO_WORLD))

        # 里程计（供"小地图计算位置"）
        self._od = MinimapOdometry(self, scale_m_per_px=(scale if scale > 0 else None))
        # 融合（结合 WS）：map_to_world 优先用矩阵，否则退回 scale*I
        self._fusion = MinimapPositionFusion(
            self._od,
            map_to_world_px=matrix if matrix is not None else None,
            scale_m_per_px=scale,
            arrow_func=self._read_arrow,
        )

        # 启动 WS 并等待稳定，作为绝对锚点
        ws_active = self._start_ws_truth()
        if ws_active:
            wait = self._cfg_float("WS等待稳定秒数", 10.0)
            min_hits = max(2, self._cfg_int("WS稳定最小位置数", 3))
            stable, map_id, last_pos = self._wait_ws_stable(timeout=wait, min_hits=min_hits)
            if not stable:
                self.log_warning(
                    f"WS 真值未在 {wait:.1f}s 内稳定（mapId={map_id}），"
                    "本任务仍输出朝向与小地图位置；融合位置需等待锚点",
                    notify=True,
                )
                ws_active = False
            else:
                self._ws_map_id = map_id
                if last_pos is not None and last_pos[3] == map_id:
                    # 用最后一条稳定位置立即设置锚点，避免起步无锚点
                    self._fusion.sync(last_pos, map_id=map_id, now=self.active_time())
                    self.log_info(
                        f"WS 已稳定并设置锚点: mapId={map_id} pos=({last_pos[0]:.2f},{last_pos[2]:.2f})",
                        notify=True,
                    )

        interval = max(0.05, self._cfg_float("采样间隔(秒)", 0.5))
        duration = max(interval, self._cfg_float("运行时长(秒)", 20.0))
        log_every = max(1, self._cfg_int("日志间隔(拍)", 1))
        min_score = max(0.0, min(1.0, self._cfg_float("朝向最低分数", 0.6)))

        start = self.active_time()
        next_at = start
        iteration = 0
        overruns = 0
        max_period = 0.0
        prev_tick = None
        self.log_info(
            f"开始实时采样: interval={interval:.2f}s duration={duration:.1f}s "
            f"scale={scale} matrix={matrix}"
        )
        self.log_info(
            "字段说明: "
            "位置=融合后的实时坐标(x,z,米) | 朝向=小地图箭头角度(°) | "
            "速度=移动速度(米/秒) | 状态=静止/移动(静止=小地图与WS都没动, 此时由WS校准) | "
            "误差=位置与最新WS的距离(米; 移动时≈WS延迟, 静止时≈0) | "
            "最新WS=最近收到的官方地图坐标(可能滞后) | 位移=里程计原始位移(地图系像素)"
        )
        while True:
            # 固定采样节拍：睡到下一个采样点（只睡剩余时间），而不是「sleep(interval) 后再干活」。
            # 后者实际周期 = 采样间隔 + 单拍工作耗时，会明显大于设定的采样间隔。
            next_at += interval
            now = self.active_time()
            if next_at > now:
                self.sleep(next_at - now)
            elif now - next_at > interval:
                # 单拍工作远超采样间隔：重新对齐，不追赶补采（避免连续快采）
                overruns += 1
                next_at = now
            now = self.active_time()
            if now - start >= duration:
                break
            if prev_tick is not None:
                max_period = max(max_period, now - prev_tick)
            prev_tick = now
            iteration += 1
            pos_ws = None  # 本次迭代可能没有新的 WS 位置

            try:
                frame = self.next_frame()
            except Exception as e:
                self.log_warning(f"next_frame 失败: {e}")
                continue
            if frame is None:
                continue

            # 融合：采样一拍（内部集成里程计 + 应用可能的待定 WS），并读朝向
            st = self._fusion.state(now=self.active_time(), frame=frame)

            # 若 WS 有新鲜位置，交给 try_sync（静止时才校准；移动时暂存）
            synced = False
            if ws_active:
                pos_ws = self._poll_ws_fresh(timeout=0.0)
                if pos_ws is not None and pos_ws[3] == self._ws_map_id:
                    synced = self._fusion.try_sync(pos_ws, map_id=pos_ws[3], now=self.active_time())
                    self._last_ws = (pos_ws[0], pos_ws[2])  # 记录最近 WS 用于误差
                    if synced:
                        # 校准后重读估计：否则本拍仍打印重锚前的旧位置，
                        # 与同一行的"位移"（已清零）自相矛盾（与 NavToPoint 处理一致）。
                        est = self._fusion.estimate()
                        if est is not None:
                            st.update(est)

            if iteration % log_every != 0:
                continue

            # ---- 静止校准：输出校准前"小地图推算的坐标"与偏差 ----
            residual = self._fusion.last_sync_residual if synced else None
            if residual is not None:
                self.log_info(
                    f"[静止校准] #{iteration:03d} 小地图推算="
                    f"({residual['map_x']:.2f}, {residual['map_z']:.2f})m "
                    f"WS=({residual['ws_x']:.2f}, {residual['ws_z']:.2f})m "
                    f"偏差=({residual['dx']:+.2f}, {residual['dz']:+.2f})m "
                    f"距离={residual['dist']:.2f}m"
                )

            # ---- 组装可读日志 ----
            od_px = self._od.position_px()
            heading = st.get("heading")
            heading_score = st.get("heading_score")
            fused_x = st.get("x")
            fused_z = st.get("z")
            rest = bool(st.get("rest"))

            # 位置（融合后世界坐标）
            if fused_x is not None:
                pos_txt = f"({fused_x:.2f}, {fused_z:.2f})m"
            else:
                pos_txt = "无(未锚定)"

            # 朝向（得分低于阈值时提示低置信）
            heading_txt = f"{heading:.1f}°" if heading is not None else "?"
            if heading_score is not None and heading_score < min_score:
                heading_txt += "(低置信)"

            # 速度（米/秒）：取静止判定用的同一个量（世界系位移/时间），单位与阈值一致
            spd = (self._fusion.rest_diag or {}).get("map_speed_m_s")
            speed_txt = f"{spd:.2f}m/s" if spd is not None else "-"

            # 状态
            rest_txt = "静止" if rest else "移动"

            # 误差（位置与最新 WS 的距离）
            err_txt = "-"
            if fused_x is not None and self._last_ws is not None:
                err = math.hypot(fused_x - self._last_ws[0], fused_z - self._last_ws[1])
                err_txt = f"{err:.2f}m"

            # 最新 WS 原始坐标 / 里程计原始位移
            ws_txt = f"({self._last_ws[0]:.2f}, {self._last_ws[1]:.2f})" if self._last_ws is not None else "-"
            od_txt = f"({od_px[0]:.1f}, {od_px[1]:.1f})px"

            self.log_info(
                f"[实时位置] #{iteration:03d} | 位置={pos_txt} 朝向={heading_txt} "
                f"速度={speed_txt} 状态={rest_txt} | "
                f"误差={err_txt} 最新WS={ws_txt} 位移={od_txt}"
            )

        # 收尾
        if ws_active:
            try:
                self._stop_map_ws_client()
            except Exception:
                pass
        elapsed = self.active_time() - start
        avg = elapsed / iteration if iteration > 0 else 0.0
        self.log_info(
            f"实时采样结束: {iteration} 拍 / {elapsed:.1f}s，"
            f"目标周期 {interval:.2f}s，实际平均 {avg:.3f}s，"
            f"最大周期 {max_period:.3f}s，超时重对齐 {overruns} 次",
            notify=True,
        )
