"""小地图位移里程计标定（被动采集）。

本任务**不发送任何按键/鼠标**：由你自己在游戏里跑图，任务只做两件事——

1. **同拍采集**：每一拍同时取「里程计累计位移（地图系像素）」与「官方地图 WS 的绝对
   坐标真值 (x, z)」，成对记录；
2. **你手动停止后拟合**：用**段内相邻差分**最小二乘拟合出 比例尺 S(米/像素) 与
   地图系->世界系轴映射矩阵，打印可直接粘贴到「小地图网格导航」配置里的值，并把
   原始样本保存成 JSON。

拟合原理：相邻两拍取 (Δworld, Δpx)，最小二乘解 ``Δpx = A·Δworld``，再取 ``inv(A)``。
一直沿单一方向直走只激励一个轴，垂直轴是数值噪声——这种情况会判为**病态**、只给
比例尺不给轴映射。所以请**至少朝两个明显不同的方向各走一段**（如向东一段、再向北一段）。

为什么静止时重锚（``静止时重锚`` 默认开）
----------------------------------------
里程计是逐帧锚定的相位相关，锚帧长期不更新会持续劣化、误差随时间累积。静止时 WS 的
传输延迟没有影响、是最可信的绝对坐标，用它重锚并清零里程计等于**给每条采集段的漂移
封顶**；而且重锚后"短段之间"的数据形态，与导航任务实际运行的方式一致，标定结果更贴合
真实使用。重锚只在真正静止（里程计与 WS 同时显示没动）时发生。

重锚会把里程计清零、地图切换会重置融合——**两者都会让像素原点跳变**，所以都会**切段**
（``seg`` 递增），跨段差分在拟合时自动跳过；否则那一段差分是坏的。

结束方式：**你跑够了手动停止任务**（或到达「采集超时(秒)」）。手动停止时用已采数据
照常计算，不会白跑。任务不自动判停——中途短暂停顿不该提前收尾。
"""

import itertools
import json
import math
from datetime import datetime
from pathlib import Path

from qfluentwidgets import FluentIcon

from src.core.BaseEfTask import BaseEfTask
from src.tasks.mixin.minimap_odometry import _reraise_control_flow
from src.tasks.mixin.minimap_position_mixin import (
    CONFIG_WS_ACCOUNT,
    CONFIG_WS_CONTENT,
    CONFIG_WS_MIN_HITS,
    CONFIG_WS_WAIT,
    MinimapPositionMixin,
)

CONFIG_TICK = "采样间隔(秒)"
CONFIG_TIMEOUT = "采集超时(秒)"
CONFIG_REANCHOR_AT_REST = "静止时重锚"
CONFIG_RAW_DIR = "原始数据目录"


class MinimapDisplacementCalibration(BaseEfTask, MinimapPositionMixin):
    """小地图位移里程计标定：你跑图，任务被动采集，你手动停止后拟合（工具与调试分组）。"""

    requires_foreground = True  # 需要你在大世界里跑图，任务本身不发输入

    # 里程计比例尺键：本任务用它只是为了建里程计，标定结果是量出来的，所以默认不指定
    MINIMAP_SCALE_KEY = "比例尺(米/像素,0=自动)"
    MINIMAP_SCALE_DEFAULT = 0.0

    #: screenshots/ 每次启动会被清空，原始数据默认写到不会被清的 logs/ 下
    RAW_DIR_DEFAULT = "logs/minimap_calibration"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "小地图位移标定"
        self.group_name = "工具与调试"
        self.group_icon = FluentIcon.DEVELOPER_TOOLS
        self.description = "你自己跑图，任务被动采集里程计与 WS 真值，手动停止后拟合比例尺与轴映射"
        self.visible = self.debug

        self._init_minimap_position_mixin()
        # 标定期间会长时间不读 WS 也不会真正"空闲"，把消费空闲超时调大，避免中途被踢
        self._map_ws_consumer_idle_timeout = 3600.0

        self.default_config = {
            CONFIG_TICK: 0.25,
            CONFIG_TIMEOUT: 600.0,
            CONFIG_REANCHOR_AT_REST: True,
            CONFIG_RAW_DIR: self.RAW_DIR_DEFAULT,
            CONFIG_WS_CONTENT: "",
            CONFIG_WS_ACCOUNT: "",
            CONFIG_WS_WAIT: 10.0,
            CONFIG_WS_MIN_HITS: 3,
        }
        self.config_description = {
            CONFIG_TICK: "每拍采样的间隔；越小样本越密（也越吃 CPU）",
            CONFIG_TIMEOUT: "采集的最长时间上限（秒），到点用已采数据计算",
            CONFIG_REANCHOR_AT_REST: "静止时用 WS 重锚并清零里程计（每条采集段的漂移封顶，"
                                     "且更贴近导航的真实运行方式）。关掉则全程只用一条累积里程计",
            CONFIG_RAW_DIR: "原始样本 JSON 的保存目录（screenshots/ 每次启动会清空，勿填那里）",
            CONFIG_WS_CONTENT: "可选。官方地图 hg/check 的 data.content，提供绝对坐标真值",
            CONFIG_WS_ACCOUNT: "可选。content 为空时从账号配置页读取该账号的地图同步 content",
            CONFIG_WS_WAIT: "等 WS 真值流稳定（连续收到同一 mapId 的有效位置）的最长秒数",
            CONFIG_WS_MIN_HITS: "判为稳定所需的连续有效位置个数",
        }

        self._records = []
        self._map_id = None
        self._seg = 0
        self._last_recorded_ws = None
        self._travel_m = 0.0
        self._finished_by = ""

    # ------------------------------------------------------------------ #
    # 主流程
    # ------------------------------------------------------------------ #
    def run(self):
        self.log_info("=== 小地图位移标定（被动采集）===", notify=True)
        if not self.in_world():
            self.log_warning("当前不在大世界画面，无法采集小地图位移。请先进入大世界。", notify=True)
            return

        # 建里程计+融合、启动位置源、等 WS 真值流稳定
        if not self.start_minimap_position():
            self.log_warning(
                "没有可用的官方地图 WS 真值（未配置 content / 未选地图账号 / 认证失败 / 等稳定超时）。"
                "没有绝对坐标就无法标定比例尺与轴映射，请先配置真值来源。",
                notify=True,
            )
            self.stop_minimap_position()
            return

        self._reset_state()
        try:
            self._collect()
        finally:
            # 手动停止任务时也会走到这里：用已采数据照常计算，不会白跑
            self._stop_and_report()

    def _reset_state(self) -> None:
        self._records = []
        self._map_id = None
        self._seg = 0
        self._last_recorded_ws = None
        self._travel_m = 0.0
        self._finished_by = ""

    def _collect(self) -> None:
        tick = max(0.05, self._cfg_float(CONFIG_TICK, 0.25))
        timeout = max(1.0, self._cfg_float(CONFIG_TIMEOUT, 600.0))
        allow_sync = self._cfg_bool(CONFIG_REANCHOR_AT_REST, True)

        self.log_info(
            "开始被动采集：请自行跑图，建议朝两个以上不同方向各走一段；"
            + ("静止时会用 WS 重锚并切段。" if allow_sync else "采集期间不重锚。")
            + "走到足够多之后手动停止任务即可得到标定结果。",
            notify=True,
        )

        started = self.active_time()
        while self.active_time() - started < timeout:
            self._sample_once(allow_sync=allow_sync)
            self.info_set(
                "小地图标定",
                f"采样 {len(self._records)} 条 | 行程 {self._travel_m:.1f}m | 段 {self._seg + 1}",
            )
            self.sleep(tick)

        self._finished_by = "采集超时"
        self.log_warning(f"采集超时（{timeout:.0f}s），用已采数据计算", notify=True)

    def _sample_once(self, *, allow_sync: bool) -> None:
        """采一拍：里程计与 WS 真值同拍配对记录；重锚/换地图时切段。"""
        try:
            st = self.minimap_position(frame=self.next_frame(), allow_sync=allow_sync)
        except Exception as e:
            # 任务被停用/结束时抛的是框架控制流异常，必须放行交给外层收尾
            _reraise_control_flow(e)
            self.log_warning(f"采样失败: {e}")
            return

        # 静止重锚会把里程计清零（换地图会重置融合）——像素原点跳变，必须切段，
        # 否则跨这段的差分是坏的。注意 sync_checked 但非 just_synced 表示"已对齐、未重锚"，
        # 那种情况里程计没被清零，不能切段。
        if st.get("just_synced"):
            self._seg += 1
        map_id = st.get("map_id")
        if self._map_id is not None and map_id is not None and map_id != self._map_id:
            self._seg += 1
        if map_id is not None:
            self._map_id = map_id

        if not st.get("odom_ok"):
            return

        ws = st.get("ws")
        if ws is None or ws == self._last_recorded_ws:
            return
        if self._last_recorded_ws is not None:
            self._travel_m += math.hypot(
                ws[0] - self._last_recorded_ws[0], ws[1] - self._last_recorded_ws[1])
        self._last_recorded_ws = ws

        od = self.minimap_odometry
        px = od.position_px()
        self._records.append({
            "t": round(self.active_time(), 3),
            "x": ws[0],
            "z": ws[1],
            "px": [round(px[0], 4), round(px[1], 4)],
            "seg": self._seg,
            "map_id": self._map_id,
        })

    # ------------------------------------------------------------------ #
    # 计算与报告
    # ------------------------------------------------------------------ #
    def _stop_and_report(self) -> None:
        try:
            self.stop_minimap_position()
        except Exception as e:
            self.log_warning(f"停止位置源失败: {e}")

        records = self._records
        if len(records) < 2:
            self.log_warning(
                f"采集样本不足（{len(records)} 条），无法标定。"
                "请确认已配置 WS 真值，并真的在游戏里走动过。",
                notify=True,
            )
            return

        scale_info = self._fit_scale_axis(records)
        self._report_calibration(scale_info, records)
        self._dump_raw(records, scale_info)

    def _report_calibration(self, scale_info, records) -> None:
        self.log_info(
            f"采集结束（{self._finished_by or '手动停止'}）：样本 {len(records)} 条，"
            f"行程 {self._travel_m:.1f}m，段 {self._seg + 1} 个"
        )
        if scale_info is None:
            self.log_warning(
                f"有效差分对不足（{len(records)} 条样本）："
                "需要同段内至少 2 组相邻差分，请多走一段后重跑。",
                notify=True,
            )
            return

        self.log_info("===== 标定结果 =====", notify=True)
        self.log_info(
            f"有效差分对 {scale_info['n']} 组，"
            f"条件数 {scale_info['condition']}（>10 视为近单方向，轴映射不可靠）"
        )
        self.log_info(f"比例尺(米/像素) = {scale_info['scale_m_per_px']:.6f}", notify=True)
        self.log_info(f"（参考：逐点比值中位数 = {scale_info.get('median_m_per_px')}）")

        matrix = scale_info.get("map_to_world_px")
        if matrix is None:
            self.log_warning(
                "轴映射不可用：本次行走近单方向，垂直轴没有被激励。"
                "请换一个明显不同的方向再走一段后重跑。",
                notify=True,
            )
        else:
            flat = f"{matrix[0][0]:.6f},{matrix[0][1]:.6f},{matrix[1][0]:.6f},{matrix[1][1]:.6f}"
            self.log_info(f"轴映射(逗号4值) = {flat}", notify=True)
            self.log_info(
                "把上面两行分别填入「小地图网格导航」的「比例尺(米/像素)」与「轴映射(逗号4值)」。"
            )

    def _dump_raw(self, records, scale_info) -> None:
        raw_dir = str(self.config.get(CONFIG_RAW_DIR, self.RAW_DIR_DEFAULT) or "").strip()
        if not raw_dir:
            return
        try:
            directory = Path(raw_dir)
            directory.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = directory / f"calibration_{stamp}.json"
            path.write_text(
                json.dumps({
                    "created": stamp,
                    "map_id": self._map_id,
                    "segments": self._seg + 1,
                    "finished_by": self._finished_by or "手动停止",
                    "travel_m": round(self._travel_m, 3),
                    "result": scale_info,
                    "samples": records,
                }, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self.log_info(f"原始数据已保存: {path}", notify=True)
        except OSError as e:
            self.log_warning(f"原始数据保存失败: {e}")

    # ------------------------------------------------------------------ #
    # 拟合（段内相邻差分 + 病态检测）
    # ------------------------------------------------------------------ #
    @staticmethod
    def _fit_scale_axis(records):
        """从 (t, x, z, px, seg) 记录序列估算比例尺与地图系->世界系坐标映射。

        采用**段内相邻差分**：每条记录带 ``seg``（重锚/换地图时递增），只对同段内相邻
        两个记录取 (d_world, d_px)，跨段跳过。这样像素原点被清零也不会污染拟合。

        返回：
          - scale_m_per_px: 米/像素（|d_world_xz|/|d_px| 的总位移长度比）
          - map_to_world_px: 2x2，地图系像素 -> 世界系米（inv(A)，含轴/符号）；
            近单方向行走（ill_conditioned）时为 None——这时垂直轴完全没被激励，
            解出的映射是数值噪声，不能当标定结果用
          - world_to_map_px: 2x2，世界系 -> 地图系像素（A）；同样在病态时为 None
          - condition / ill_conditioned: 方向激励是否充足（病态检测）
          - n: 使用的相邻差分对数
        """
        import numpy as np

        dws = []
        dpxs = []
        for prev, cur in itertools.pairwise(records):
            # 跨段（重锚/换地图）或缺少坐标/像素则跳过
            if prev.get("seg") != cur.get("seg"):
                continue
            p = prev.get("px")
            c = cur.get("px")
            if p is None or c is None:
                continue
            dw = np.array([cur["x"] - prev["x"], cur["z"] - prev["z"]], dtype=np.float64)
            dp = np.array([c[0] - p[0], c[1] - p[1]], dtype=np.float64)
            if np.hypot(dw[0], dw[1]) > 1e-6 and np.hypot(dp[0], dp[1]) > 1e-6:
                dws.append(dw)
                dpxs.append(dp)
        if len(dws) < 2:
            return None
        dws = np.array(dws)
        dpxs = np.array(dpxs)
        # 比例尺（米/像素）：用"总位移长度比" Σ|d_world| / Σ|d_px|，抗小位移噪声。
        # （逐点比值的中位数会被几像素的小步噪声拉高，故不作为主值。）
        sum_w = float(np.sum(np.hypot(dws[:, 0], dws[:, 1])))
        sum_p = float(np.sum(np.hypot(dpxs[:, 0], dpxs[:, 1])))
        meters_per_px = (sum_w / sum_p) if sum_p > 1e-9 else 0.0
        # 参考：逐点比值中位数
        ratios = [np.hypot(dw[0], dw[1]) / np.hypot(dp[0], dp[1]) for dw, dp in zip(dws, dpxs, strict=True)]
        median_m_per_px = float(np.median(ratios)) if ratios else 0.0
        # 最小二乘拟合 d_px = A @ d_world（A: world系 -> map系像素）
        A, *_ = np.linalg.lstsq(dws, dpxs, rcond=None)
        # 病态检测：行走是否近单方向（dws 奇异值比）
        try:
            s = np.linalg.svd(dws, compute_uv=False)
            cond = float(s[0] / s[-1]) if s[-1] > 1e-9 else float("inf")
        except Exception:
            cond = float("inf")
        ill = cond > 10.0
        # 近单方向时垂直轴完全没被激励：lstsq 的解在那里不受约束，inv 会给出
        # 数值爆炸的映射。这种情况只保留比例尺供参考，轴映射置 None。
        map_to_world_px = None
        world_to_map_px = None
        if not ill:
            try:
                invA = np.linalg.inv(A)
            except np.linalg.LinAlgError:
                invA = np.linalg.pinv(A)
            map_to_world_px = [
                [round(float(invA[0, 0]), 6), round(float(invA[0, 1]), 6)],
                [round(float(invA[1, 0]), 6), round(float(invA[1, 1]), 6)],
            ]
            world_to_map_px = [
                [round(float(A[0, 0]), 6), round(float(A[0, 1]), 6)],
                [round(float(A[1, 0]), 6), round(float(A[1, 1]), 6)],
            ]
        return {
            "scale_m_per_px": round(meters_per_px, 6),
            "median_m_per_px": round(median_m_per_px, 6),
            "map_to_world_px": map_to_world_px,
            "world_to_map_px": world_to_map_px,
            "n": len(dws),
            "condition": round(cond, 2),
            "ill_conditioned": bool(ill),
        }
