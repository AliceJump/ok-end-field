# -*- coding: utf-8 -*-
"""小地图实时位置能力（mixin）：一次调用同时给出「方向」和「坐标」。

坐标 = 小地图位移里程计（相对、高频） + 官方地图 WS 绝对坐标（绝对、有延迟）的融合：

  1. 静止时用 WS 绝对坐标设锚点 ``P0`` 并清零里程计（静止时延迟无影响）；
  2. 移动期间用里程计增量推位置：``P = P0 + M @ 里程计位移``；
  3. 再次静止时用新的 WS 坐标重新校准，把漂移限制在两次校准之间。

「静止」要求小地图里程计与 WS **同时**显示没动——WS 有传输延迟，只在两边都停住时
用它做基准才安全（判为静止时相邻两次 WS 样本已经一致，这条样本就不带延迟了）。
移动中收到的 WS 样本直接忽略、不做暂存：那是移动途中的旧坐标，静止后应用它只会
把里程计已经推进的位置拽回去。

用法::

    class MyTask(BaseEfTask, MinimapPositionMixin):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._init_minimap_position_mixin()
            self.default_config = {..., **self.minimap_position_default_config()}

        def run(self):
            self.start_minimap_position()
            try:
                while ...:
                    frame = self.next_frame()
                    st = self.minimap_position(frame=frame)   # 方向 + 坐标
                    if st["x"] is not None:
                        use(st["x"], st["z"], st["heading"])
            finally:
                self.stop_minimap_position()

``minimap_position()`` 返回 dict：

    x / z            融合坐标（米，世界系 x/z）。未设锚点时 None。
    heading          朝向（**罗盘方位角**，度）：正北 0°、正东 90°、正南 180°、正西 270°，
                     顺时针增大。由底层箭头角经
                     :func:`minimap_odometry.arrow_angle_to_bearing` 换算而来
                     （箭头角是屏幕上从正北逆时针，与之互为镜像）。
    heading_score    朝向置信度；低于配置的「朝向最低分数」应判为不可用。
    anchor_set       是否已用 WS 设过锚点（False 时 x/z 为 None）。
    rest             本拍是否静止（静止时才用 WS 校准并清零里程计）。
    last_sync_t      最近一次校准的时间戳。
    dmap_px          里程计累计位移（地图系像素，x 右 / y 下）。
    world_delta      上述位移映射到世界系的增量（米）。
    ws               最近收到的 WS 坐标 (x, z)，可能滞后；None = 还没收到。
    error            |融合坐标 - 最近 WS|（米）。移动时≈WS 延迟（不是误差），静止校准后≈0。
    odom_ok/odom_reason  本拍是否采到有效位移样本及原因（"ok"/"too_soon"/"no_frame"/
                     "low_response"/"exceed_max_shift"/"too_long_dt"/"speed_anomaly"，
                     未采到时 odom_ok 为 False，odom_reason 说明为什么），
                     用来区分"位置在动"和"位置源已经没有数据了"。
    just_synced/sync_residual  本拍是否刚触发静止校准、以及校准前的残差
                     （``{map_x, map_z, ws_x, ws_z, dx, dz, dist}``，即小地图推算偏了多少米）。

调用方注意：**务必把当前帧传进来**（``frame=frame``）。不传的话内部会用
``next_frame()`` 自己抓一帧，于是朝向你手里的帧与算位移的帧不是同一时刻，两者会错位。

本模块只做编排，几何/融合分别在 ``minimap_odometry`` 与 ``minimap_position_fusion``；
位置源（官方地图 WS 客户端 / 本地 WS 服务）的启停复用 ``WsPositionMixin``。
"""

from __future__ import annotations

import math

from src.tasks.account.account_scope_store import (
    get_account_map_content,
    resolve_account_id,
)
from src.tasks.mixin.minimap_heading_mixin import (
    CONFIG_MIN_SCORE,
    MinimapHeadingMixin,
)
from src.tasks.mixin.minimap_odometry import MinimapOdometry
from src.tasks.mixin.minimap_position_fusion import MinimapPositionFusion
from src.tasks.mixin.ws_position_mixin import WsPositionMixin

__all__ = [
    "CONFIG_MAP_TO_WORLD",
    "CONFIG_SCALE",
    "CONFIG_WS_ACCOUNT",
    "CONFIG_WS_CONTENT",
    "CONFIG_WS_MIN_HITS",
    "CONFIG_WS_WAIT",
    "DEFAULT_MAP_TO_WORLD",
    "DEFAULT_SCALE",
    "MinimapPositionMixin",
    "parse_map_to_world",
]

# 默认轴映射（世界_x ≈ +s_x*map_x，世界_z ≈ -s_z*map_y）。逗号4值：a11,a12,a21,a22。
# 标量来自 2026-09-10「小地图实时位置」30s 跑测的 4 个静止校准区间最小二乘拟合：
# 该组值 Σ|误差| ≈ 0.65m / 约 100m 行程；同批数据里带非对角项的拟合（如
# 0.671428,0.005240,0.040819,-0.639992）Σ|误差| 达 6.91m——非对角项是"行走近单方向"
# 导致的病态拟合噪声，会把 37m 的向东走算成 2.3m 的 z 偏移，故默认置 0。
DEFAULT_SCALE = 0.6703
DEFAULT_MAP_TO_WORLD = "0.6703,0,0,-0.6698"

CONFIG_SCALE = "比例尺(米/像素)"
CONFIG_MAP_TO_WORLD = "轴映射(逗号4值)"
CONFIG_WS_CONTENT = "真值content"
CONFIG_WS_ACCOUNT = "真值地图账号"
CONFIG_WS_WAIT = "WS等待稳定秒数"
CONFIG_WS_MIN_HITS = "WS稳定最小位置数"


def parse_map_to_world(value):
    """把 "a11,a12,a21,a22" 解析为 2x2 矩阵；非法时返回 None。"""
    parts = str(value or "").split(",")
    if len(parts) != 4:
        return None
    try:
        nums = [float(p) for p in parts]
    except (TypeError, ValueError):
        return None
    return [[nums[0], nums[1]], [nums[2], nums[3]]]


class MinimapPositionMixin(MinimapHeadingMixin, WsPositionMixin):
    """小地图实时位置：同时给出方向（朝向角）和坐标（小地图推算 + WS 校准）。

    朝向相关的能力（读朝向、转到指定方位）来自 :class:`MinimapHeadingMixin`。
    """

    #: 里程计比例尺读哪个配置键 / 缺省值；子类键名或语义不同（如「0=自动」）时覆盖。
    MINIMAP_SCALE_KEY = CONFIG_SCALE
    MINIMAP_SCALE_DEFAULT = DEFAULT_SCALE

    # ------------------------------------------------------------------ #
    # 配置（任务把这两个 dict merge 进自己的 default_config / config_description）
    # ------------------------------------------------------------------ #
    @staticmethod
    def minimap_position_default_config() -> dict:
        return {
            CONFIG_SCALE: DEFAULT_SCALE,
            CONFIG_MAP_TO_WORLD: DEFAULT_MAP_TO_WORLD,
            CONFIG_WS_CONTENT: "",
            CONFIG_WS_ACCOUNT: "",
            CONFIG_WS_WAIT: 10.0,
            CONFIG_WS_MIN_HITS: 3,
            CONFIG_MIN_SCORE: 0.6,
        }

    @staticmethod
    def minimap_position_config_description() -> dict:
        return {
            CONFIG_SCALE: "小地图比例尺（米/像素），里程计位移换算用",
            CONFIG_MAP_TO_WORLD: "地图系像素->世界系米的 2x2 矩阵，逗号4值 a11,a12,a21,a22；"
                                 "默认 diag(+比例尺, -比例尺)（世界_z 与地图_y 符号相反）",
            CONFIG_WS_CONTENT: "可选。官方地图 hg/check 的 data.content，提供绝对坐标（锚点/真值）",
            CONFIG_WS_ACCOUNT: "可选。content 为空时从账号配置页读取该账号的地图同步 content",
            CONFIG_WS_WAIT: "等 WS 位置流稳定（连续同一 mapId 的有效位置）的最大等待秒数",
            CONFIG_WS_MIN_HITS: "判为稳定所需连续有效位置个数",
            CONFIG_MIN_SCORE: "箭头角度检测最低置信度，低于该值朝向判为不可用",
        }

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def _init_minimap_position_mixin(self):
        """在任务 __init__ 里调用（只建状态，不起线程、不抓帧）。"""
        self._init_minimap_heading_mixin()
        self._init_ws_position_mixin()
        self._minimap_od: MinimapOdometry | None = None
        self._minimap_fusion: MinimapPositionFusion | None = None
        self._minimap_ws_map_id: str | None = None
        self._minimap_last_ws: tuple[float, float] | None = None
        self._minimap_scale = 0.0

    def start_minimap_position(self, *, wait_stable: bool = True) -> bool:
        """建里程计 + 融合、启动位置源、等 WS 稳定并立即设锚点。

        Returns:
            True = 已用稳定的 WS 真值锚定（x/z 立即可用）；
            False = 没有可用的官方 WS 真值（未配置 content / 认证失败 / 等稳定超时），
            此时若本地 WS 服务能提供位置，会在后续静止时自动完成首次锚定。
        """
        scale = max(0.0, self._cfg_float(self.MINIMAP_SCALE_KEY, self.MINIMAP_SCALE_DEFAULT))
        raw_matrix = self.config.get(CONFIG_MAP_TO_WORLD, DEFAULT_MAP_TO_WORLD)
        matrix = parse_map_to_world(raw_matrix)
        if str(raw_matrix or "").strip() and matrix is None:
            self.log_warning(
                f"{CONFIG_MAP_TO_WORLD} 无法解析（应为 4 个逗号分隔数字）: {raw_matrix!r}，"
                "将退回用「比例尺」构造默认轴（图右=东、图下=南）",
                notify=True,
            )
        if matrix is None and scale <= 0:
            self.log_warning(
                "轴映射不可用且比例尺为 0：融合坐标会恒等于 WS 锚点（不随移动变化）。"
                f"请填写有效的「{self.MINIMAP_SCALE_KEY}」或「{CONFIG_MAP_TO_WORLD}」",
                notify=True,
            )

        self._minimap_scale = scale
        self._minimap_od = MinimapOdometry(
            self, scale_m_per_px=(scale if scale > 0 else None))
        self._minimap_fusion = MinimapPositionFusion(
            self._minimap_od,
            map_to_world_px=matrix,
            scale_m_per_px=scale,
            arrow_func=self._read_arrow,
        )
        self._minimap_ws_map_id = None
        self._minimap_last_ws = None

        try:
            cred = self._resolve_ws_cred()
        except Exception as e:
            self.log_warning(f"读取地图 WS content 失败，改用本地 WS 位置源: {e}")
            cred = ""

        self._ensure_ws_position_source(cred)
        if not cred:
            self.log_info(
                "未配置地图 WS content（content/地图账号为空）：没有绝对基准，"
                "位置需等本地 WS 提供坐标并在静止时自动锚定")
            return False
        if not self._is_map_ws_client_enabled():
            self.log_warning("地图 WS 客户端未启动（认证失败或缺少设备ID），稍后由触发周期重试")
            return False

        self.log_info("地图 WS 客户端已启动，等待位置流稳定……", notify=True)
        if not wait_stable:
            return False
        wait = self._cfg_float(CONFIG_WS_WAIT, 10.0)
        min_hits = max(2, self._cfg_int(CONFIG_WS_MIN_HITS, 3))
        stable, map_id, last_pos = self._wait_ws_stable(timeout=wait, min_hits=min_hits)
        if not stable:
            self.log_warning(
                f"WS 位置流未在 {wait:.1f}s 内稳定（mapId={map_id}）："
                "先只用里程计与朝向，等 WS 稳定且静止时再自动锚定",
                notify=True,
            )
            return False
        self._minimap_ws_map_id = map_id
        if last_pos is not None and last_pos[3] == map_id:
            # 用最后一条稳定位置立即设锚点，避免起步前几拍没有坐标
            self._minimap_fusion.sync(last_pos, map_id=map_id, now=self.active_time())
            self._minimap_last_ws = (last_pos[0], last_pos[2])
            self.log_info(
                f"WS 已稳定并设置锚点: mapId={map_id} pos=({last_pos[0]:.2f},{last_pos[2]:.2f})",
                notify=True,
            )
        return True

    def stop_minimap_position(self):
        """停掉全部位置源（任务收尾/窗口丢失时调用）。锚点状态保留，可接着用。"""
        try:
            self._stop_position_sources()
        except Exception as e:
            self.log_warning(f"停止位置源失败: {e}")

    # ------------------------------------------------------------------ #
    # 位置源（官方地图 WS 客户端 / 本地 WS 服务）
    # ------------------------------------------------------------------ #
    def _resolve_ws_cred(self) -> str:
        """按配置优先级解析地图同步 content（content > 地图账号 > 当前账号上下文）。"""
        content = str(self.config.get(CONFIG_WS_CONTENT) or "").strip()
        if content:
            return content
        account = str(self.config.get(CONFIG_WS_ACCOUNT) or "").strip()
        if account:
            account_id = resolve_account_id(account, create_if_missing=False) or account
            return get_account_map_content(account_id, account_name=account)
        account_id = str(getattr(self, "current_account_id", "") or "").strip()
        account_name = str(getattr(self, "current_user", "") or "").strip()
        return get_account_map_content(account_id or account_name, account_name=account_name)

    def _poll_ws_position(self, timeout: float = 0.0):
        """取一条**新到**的 WS 位置（不从缓存取旧值）。

        Returns:
            (x, y, z, map_id) | None：仅当收到有效新位置时返回。
            用 ``_recv_ws_position_payload``（仅新消息）而非 ``_recv_ws_position_payload_or_cached``，
            避免把同一条旧位置重复当成新样本。
        """
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
        """等到 WS 位置流稳定，返回 ``(是否稳定, 最近的 mapId, 最后一条位置)``。

        连续收到有效位置、且 mapId 一致，累计达 ``min_hits`` 才算稳定。稳定前 WS 可能
        还没完成认证/账号解析，此时当作基准的坐标是空或陈旧的，会导致锚点/标定不准。
        最后一条位置用于稳定后立即设锚点，避免起步前几拍没有坐标。
        """
        map_id = None
        hits = 0
        last_pos = None
        t0 = self.active_time()
        while self.active_time() - t0 < timeout:
            pos_ws = self._poll_ws_position(timeout=0.5)
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
                # 地图已切换，重新累计
                map_id = mid
                hits = 1
            if hits >= min_hits:
                return True, map_id, last_pos
        return False, map_id, last_pos

    # ------------------------------------------------------------------ #
    # 朝向 / 位置
    # ------------------------------------------------------------------ #
    def minimap_position(self, frame=None, *, now=None, feed_ws: bool = True) -> dict:
        """采样一拍，返回本拍的「方向 + 坐标」。

        Args:
            frame: 当前帧。**强烈建议传入**——不传会自己 ``next_frame()`` 抓一帧，
                导致"算位移用的帧"和"读朝向用的帧"不是同一时刻。
            now: 可选时间戳（秒），默认用 ``active_time()``。
            feed_ws: 是否顺带消费一条新到的 WS 位置并做静止校准。默认 True；
                调用方要自己读原始 WS 样本时传 False（避免样本被这里吃掉）。

        Returns:
            见模块 docstring 的字段说明。``x``/``z`` 为 None 表示还没锚定。
        """
        if self._minimap_fusion is None or self._minimap_od is None:
            raise RuntimeError("请先调用 start_minimap_position() 初始化小地图位置源")

        st = self._minimap_fusion.state(now=now, frame=frame)
        st["just_synced"] = False
        st["sync_residual"] = None
        if feed_ws:
            self._feed_ws_position(st, self._now(now))

        # 本拍是否有**有效位移样本**（区分"位置在动"与"位置源已经没有数据了"）。
        # 注意必须同时看 sampled：锚帧初始化（anchor_init）与采样过密（too_soon）
        # 都会 ok=True 但 sampled=False，此时并没有新的位移数据。
        last = self._minimap_od.last_result() or {}
        st["odom_ok"] = bool(last.get("ok") and last.get("sampled"))
        st["odom_reason"] = last.get("reason")

        st["ws"] = self._minimap_last_ws
        st["error"] = None
        if st.get("x") is not None and st.get("z") is not None and self._minimap_last_ws is not None:
            st["error"] = math.hypot(
                st["x"] - self._minimap_last_ws[0], st["z"] - self._minimap_last_ws[1])
        return st

    def _now(self, now):
        return self.active_time() if now is None else now

    def _feed_ws_position(self, st: dict, now) -> bool:
        """消费一条新到的 WS 位置：静止时用它校准，否则忽略（不暂存）。返回本拍是否已校准。"""
        pos_ws = self._poll_ws_position(timeout=0.0)
        if pos_ws is None:
            return False
        x, _y, z, map_id = pos_ws
        if self._minimap_ws_map_id is None:
            self._minimap_ws_map_id = map_id
        elif self._minimap_ws_map_id != map_id:
            # 换地图：旧地图的锚点在新地图上没有意义，清掉重来（等再次静止自动锚定）。
            # 本拍必须把坐标字段改成"未锚定"，否则会返回上一张地图的坐标。
            self.log_info(f"地图已切换 {self._minimap_ws_map_id} -> {map_id}，清空锚点等待重新校准")
            self._minimap_ws_map_id = map_id
            self._minimap_fusion.reset()
            self._minimap_last_ws = (x, z)
            self._apply_estimate(st, self._minimap_fusion.estimate())
            return False

        synced = self._minimap_fusion.try_sync(pos_ws, map_id=map_id, now=now)
        self._minimap_last_ws = (x, z)
        # 空操作（估计已与 WS 重合）不算"刚校准"：不重读估计、也不产生新的残差，
        # 这样静止时不会每秒刷一条 [静止校准]，上一次真正有意义的残差也留得住。
        if synced and not self._minimap_fusion.last_sync_redundant:
            self._apply_estimate(st, self._minimap_fusion.estimate())
            st["just_synced"] = True
            st["sync_residual"] = self._minimap_fusion.last_sync_residual
        return synced

    @staticmethod
    def _apply_estimate(st: dict, est: dict | None) -> None:
        """把融合估计写回本拍结果；未锚定（``est is None``）时把坐标字段清成 None。"""
        if est is None:
            st.update({
                "x": None, "z": None, "anchor_set": False,
                "dmap_px": (0.0, 0.0), "world_delta": (0.0, 0.0),
            })
            return
        st.update(est)

    # ------------------------------------------------------------------ #
    # 只读访问（给需要更细控制的调用方）
    # ------------------------------------------------------------------ #
    @property
    def minimap_odometry(self) -> MinimapOdometry | None:
        """里程计实例（累计位移 ``position_px()`` / ``forward_strafe_m()`` 等）。"""
        return self._minimap_od

    @property
    def minimap_fusion(self) -> MinimapPositionFusion | None:
        """融合实例（``estimate()`` / ``last_sync_residual`` / ``rest_diag`` 等）。"""
        return self._minimap_fusion

    def minimap_rest_diag(self) -> dict | None:
        """最近一次静止判定的实测值（``map_speed_m_s`` / ``ws_moved_m`` / ``reason``）。"""
        if self._minimap_fusion is None:
            return None
        return self._minimap_fusion.rest_diag
