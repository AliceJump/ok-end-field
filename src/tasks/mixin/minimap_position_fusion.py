# -*- coding: utf-8 -*-
"""融合小地图里程计增量与 WS 绝对坐标。

WS 坐标绝对但存在网络/服务延迟，里程计高频但会随时间漂移。融合策略是：

1. 静止时用 WS ``(x, z)`` 建立锚点 ``P0``，并清零里程计；
2. 移动时按 ``P = P0 + map_to_world @ odometry_delta`` 输出位置；
3. 下一次静止时重新锚定，把累计漂移限制在两个校准点之间。

移动期间收到的 WS 样本代表旧位置，不能立即作为锚点，也不应缓存到静止后补用，否则会
把已经由里程计推进的位置拉回。:meth:`MinimapPositionFusion.try_sync` 只在静止时执行
重锚；:meth:`is_rest` 要求小地图低速，并在已有至少两次 WS 样本时要求 WS 也低速。

坐标约定：

- 里程计输出地图系像素位移，图像 ``x`` 向右、``y`` 向下；
- ``map_to_world_px`` 把地图系像素位移映射为世界 ``(x, z)`` 米位移；
- WS 只取水平分量 ``(x, z)``，高度不参与融合。

本模块不依赖 ``ok`` 框架，只依赖里程计鸭子类型与数值库，便于单元测试。完整链路见
``docs/dev/导航与小地图定位.md``。
"""

from __future__ import annotations

import math

import numpy as np

from src.tasks.mixin.minimap_odometry import _reraise_control_flow

__all__ = ["MinimapPositionFusion"]


class MinimapPositionFusion:
    """WS 绝对锚点 + 小地图里程计增量的融合定位。

    Args:
        odometry: 里程计对象（鸭子类型），需提供：
            - ``sample(frame=None)``：采样一拍，返回带 ``dmap_px``/``ok``/``sampled``/``dt`` 的 dict；
            - ``position_px()``：累计地图系位移（像素）；
            - ``reset_position()``：清零累计位移；
            - ``last_sample()``：最近一次有效样本（用于静止判定）。
        map_to_world_px: 2x2 矩阵，地图系像素 -> 世界系米。None 时用
            ``diag(scale, -scale)``（图右=东 +x、图下=南 -z，见模块文档）。
        scale_m_per_px: 比例尺（米/像素），仅在 ``map_to_world_px`` 为 None 时用于构造默认轴。
        rest_speed_m_s: 静止判定阈值（米/秒，世界系），低于该值算"小地图没动"。
        rest_ws_m: 静止判定的 WS 位移阈值（米）：相邻两次 WS 坐标之差低于该值算"WS 没动"。
    """

    def __init__(
        self,
        odometry,
        *,
        map_to_world_px=None,
        scale_m_per_px: float = 1.0,
        rest_speed_m_s: float = 0.2,
        rest_ws_m: float = 0.5,
        redundant_sync_m: float = 0.1,
        arrow_func=None,
    ):
        """创建融合定位器。

        Args:
            odometry: 提供 ``sample/position_px/reset_position/last_sample`` 的里程计。
            map_to_world_px: 地图像素位移到世界米位移的 2x2 矩阵。
            scale_m_per_px: 未显式给矩阵时，用于构造默认轴缩放。
            rest_speed_m_s: 小地图静止速度阈值。
            rest_ws_m: 相邻 WS 样本允许的最大位移，用于过滤延迟样本。
            redundant_sync_m: 估计与 WS 小于该距离时跳过重复重锚。
            arrow_func: 可选的同帧朝向读取函数，签名为 ``f(frame) -> (angle, score)``。
        """
        self._od = odometry
        scale = float(scale_m_per_px)
        if map_to_world_px is not None:
            self._map_to_world_px = np.asarray(map_to_world_px, dtype=np.float64).reshape(2, 2)
        else:
            # 默认轴：图右=东 +x、图下=南 -z（图像 y 向下），与真机标定同号
            self._map_to_world_px = np.diag([scale, -scale]).astype(np.float64)
        self._rest_speed_m_s = float(rest_speed_m_s)
        self._rest_ws_m = float(rest_ws_m)
        # 当前估计已经与 WS 坐标差不到这个距离时，重新校准是空操作，跳过（见 _is_sync_redundant）
        self._redundant_sync_m = float(redundant_sync_m)
        # 朝向来源回调：callable(frame) -> (angle, score)。用于同时取朝向。
        self._arrow_func = arrow_func

        self._anchor_world = np.zeros(2, dtype=np.float64)  # (x, z)
        self._anchor_set = False
        self._last_sync_t: float | None = None
        # 静止判定：最近一次收到的 WS 坐标 + 相邻两次 WS 之间的位移（米）
        self._last_ws = None          # (x, z)
        self._ws_moved_m = None       # None = 还没收到过两次 WS 样本
        # 最近一次静止校准的残差：校准前"小地图推算坐标" vs WS 坐标
        self._last_sync_residual = None
        # 最近一次 try_sync 是否因"空操作"而跳过（见 _is_sync_redundant）
        self._last_sync_redundant = False
        # 最近一次 is_rest() 的两个实测值（排查"为什么没判静止"）
        self._rest_diag = None

    # ------------------------------------------------------------------ #
    # 状态
    # ------------------------------------------------------------------ #
    def reset(self):
        """清空绝对锚点、静止判定、残差和累计里程计。"""
        self._anchor_set = False
        self._last_ws = None
        self._ws_moved_m = None
        self._last_sync_residual = None
        self._last_sync_redundant = False
        self._od.reset_position()

    @property
    def map_to_world_px(self) -> tuple[tuple[float, float], tuple[float, float]]:
        """当前轴映射。返回普通 float（不要泄漏 numpy 标量，否则日志里会打成 np.float64(...)）。"""
        return tuple(tuple(float(v) for v in row) for row in self._map_to_world_px)

    @property
    def last_sync_redundant(self) -> bool:
        """最近一次 :meth:`try_sync` 是否因"锚点没变、里程计也没漂"而跳过了校准。

        静止时 WS 会持续重复推送同一坐标；不跳过的话每秒都会重设一次锚点、
        清一次里程计，并把上一次真正有意义的校准残差覆盖掉。
        """
        return self._last_sync_redundant

    @property
    def last_sync_residual(self) -> dict | None:
        """最近一次**实际执行**的校准的残差：{map_x, map_z, ws_x, ws_z, dx, dz, dist}。

        ``map_*`` 是校准前"用小地图推算的坐标"，``ws_*`` 是以此为准的 WS 坐标，
        ``dist`` 是两者距离（米）——即小地图定位在这次静止前的累计偏差。
        首次校准（此前没有锚点）时为 None。
        """
        return self._last_sync_residual

    @property
    def ws_moved_m(self) -> float | None:
        """相邻两次 WS 坐标的位移（米）；从未收到过两次样本时为 None。"""
        return self._ws_moved_m

    @property
    def rest_diag(self) -> dict | None:
        """最近一次 :meth:`is_rest` 的实测值与判定理由（排查为何未判静止）。"""
        return self._rest_diag

    def is_rest(self) -> bool:
        """判断当前是否静止。

        小地图里程计给的是高频、准确的相对位移；WS 给的是绝对坐标但有传输延迟。
        有 WS 样本时必须两边都显示"没动"；没有 WS 样本时只能依据小地图里程计：

          - 小地图条件：最近一次有效样本的世界系速度 < ``rest_speed_m_s``；
          - WS 条件：相邻两次 WS 坐标之差 < ``rest_ws_m``（无 WS 数据时不参与）。

        注意：里程计没有有效样本时返回 False——无法确认"没动"，不猜；本方法只判断是否
        静止，实际重锚还要求调用方提供 WS 坐标。
        判定过程与两个实测值记录在 :attr:`rest_diag`，供日志排查"为什么没判静止"。
        """
        diag = {"map_speed_m_s": None, "ws_moved_m": self._ws_moved_m, "reason": "ok"}
        self._rest_diag = diag

        # 条件 1：小地图里程计位移极小（-> 世界系米/秒，避免 px/s 与 m/s 混淆）
        last = self._od.last_sample()
        if not last or not last.get("sampled") or not last.get("ok"):
            diag["reason"] = "no_odom_sample"
            return False
        dt = float(last.get("dt") or 0.0)
        if dt <= 1e-6:
            diag["reason"] = "bad_dt"
            return False
        dpx = last.get("dmap_px") or (0.0, 0.0)
        world = self._map_to_world_px @ np.asarray(
            [float(dpx[0]), float(dpx[1])], dtype=np.float64)
        speed = math.hypot(float(world[0]), float(world[1])) / dt
        diag["map_speed_m_s"] = speed
        if speed >= self._rest_speed_m_s:
            diag["reason"] = "map_moving"
            return False

        # 条件 2：WS 也没有位移（没有 WS 数据时该条件不参与判定）
        if self._ws_moved_m is not None and self._ws_moved_m >= self._rest_ws_m:
            diag["reason"] = "ws_moving"
            return False
        return True

    # ------------------------------------------------------------------ #
    # 校准
    # ------------------------------------------------------------------ #
    def _note_ws(self, x: float, z: float) -> None:
        """记录一次 WS 坐标，并更新"相邻两次 WS 之间的位移"（静止判定条件 2）。"""
        if self._last_ws is not None:
            self._ws_moved_m = math.hypot(float(x) - self._last_ws[0], float(z) - self._last_ws[1])
        else:
            self._ws_moved_m = None   # 还没有两次样本，条件 2 暂不参与
        self._last_ws = (float(x), float(z))

    def _apply_sync(self, ws_x: float, ws_z: float, now) -> dict | None:
        """执行锚点替换：先记录校准前残差，再以 WS 为基准重设锚点并清零里程计。"""
        pre = self.estimate()          # 校准前的"小地图推算位置"
        self._last_sync_residual = None
        if pre is not None:
            mx, mz = float(pre["x"]), float(pre["z"])
            self._last_sync_residual = {
                "map_x": mx,
                "map_z": mz,
                "ws_x": float(ws_x),
                "ws_z": float(ws_z),
                "dx": mx - float(ws_x),
                "dz": mz - float(ws_z),
                "dist": math.hypot(mx - float(ws_x), mz - float(ws_z)),
            }
        self._anchor_world = np.array([float(ws_x), float(ws_z)], dtype=np.float64)
        self._anchor_set = True
        self._last_sync_t = now
        self._last_sync_redundant = False
        self._od.reset_position()
        return self.estimate()

    def _is_sync_redundant(self, ws_x: float, ws_z: float) -> bool:
        """当前估计已经与 WS 坐标几乎重合 → 重新校准是空操作。

        静止时 WS 每秒都重复推送同一坐标，若每次都重锚+清零，日志会被
        "静止校准"刷屏，而且上一次真正有意义的校准残差会被 0 残差覆盖掉。
        阈值取 ``redundant_sync_m``：它同时是漂移修正的死区（漂移超过它才重锚），
        所以位置不会因此无界漂移。
        """
        if not self._anchor_set:
            return False
        est = self.estimate()
        if est is None:
            return False
        return math.hypot(est["x"] - float(ws_x), est["z"] - float(ws_z)) <= self._redundant_sync_m

    def sync(self, world_xyz, *, map_id=None, now=None):
        """用 WS 绝对坐标设置锚点并清零里程计（无条件执行，调用方负责保证静止）。

        校准前的小地图推算坐标与偏差记录在 :attr:`last_sync_residual`，
        调用方据此输出"小地图算出来的位置偏了多少"。
        """
        if world_xyz is None:
            return self.estimate()
        x, z = float(world_xyz[0]), float(world_xyz[2])
        self._note_ws(x, z)
        return self._apply_sync(x, z, now)

    def try_sync(self, world_xyz, *, map_id=None, now=None, allow_sync=True) -> bool:
        """尝试用 WS 校准：只有**判定为静止**时才用这条样本重锚。

        不做"移动中暂存、静止后再应用"：暂存的是移动途中的陈旧坐标，静止后应用
        它会把里程计已经推进的位置拽回去。收到过两次 WS 样本时，静止判定要求这两次
        坐标一致；没有 WS 样本时只能确认小地图没动，仍不会执行重锚。

        Returns:
            True = 本次静止、锚点有效（可能是真正重锚，也可能是"已对齐、跳过"，
            见 :attr:`last_sync_redundant`）；False = 未静止，本次不校准。

        ``allow_sync=False`` 时仍记录 WS 样本并更新静止判定，但不会重锚。调用方
        可用它先确认连续稳定时间，排除 WS 传输延迟尚未排空的样本。
        """
        if world_xyz is None:
            return False
        x, z = float(world_xyz[0]), float(world_xyz[2])
        self._note_ws(x, z)
        if not self.is_rest():
            return False
        if not allow_sync:
            return False
        if self._is_sync_redundant(x, z):
            self._last_sync_redundant = True
            return True
        self._apply_sync(x, z, now)
        return True

    # ------------------------------------------------------------------ #
    # 定位
    # ------------------------------------------------------------------ #
    def step(self, *, now=None, frame=None) -> dict | None:
        """采样一拍里程计，返回当前估计。"""
        try:
            self._od.sample(frame=frame)
        except Exception as e:
            # 任务被停用/结束抛的是框架控制流异常，必须放行——里程计侧
            # _reraise_control_flow 特意让它们冒出来，这里不能又吞回去。
            _reraise_control_flow(e)
        return self.estimate()

    def estimate(self) -> dict | None:
        """返回当前融合位置估计；未设锚点时返回 None。"""
        if not self._anchor_set:
            return None
        px = np.asarray(self._od.position_px(), dtype=np.float64)
        world_delta = self._map_to_world_px @ px
        pos = self._anchor_world + world_delta
        return {
            "x": float(pos[0]),
            "z": float(pos[1]),
            "dmap_px": (float(px[0]), float(px[1])),
            "world_delta": (float(world_delta[0]), float(world_delta[1])),
            "anchor_set": True,
            "last_sync_t": self._last_sync_t,
            "rest": self.is_rest(),
        }

    def state(self, *, now: float | None = None, frame=None) -> dict:
        """同时取出：朝向（小地图箭头）+ 计算位置（融合定位）。

        这是给导航/送达判断用的统一入口：用同一帧 ``frame`` 先做里程计采样，
        再用箭头回调读取该帧朝向，保证朝向与位置属于同一时刻。

        Args:
            now: 可选时间戳（秒），默认取里程计 active_time。
            frame: 可选帧（BGR）；不传则里程计自己取帧。箭头回调使用同一个 frame。

        Returns:
            dict，包含融合估计全部字段 + ``heading`` / ``heading_score``：
              - 若未注入 ``arrow_func``，heading 为 None；
              - 若未设置 WS 锚点，``x``/``z`` 为 None（仍返回 heading）。
        """
        # 采样一拍里程计并应用可能的待定 WS 校准
        self.step(now=now, frame=frame)

        heading = None
        heading_score = None
        if self._arrow_func is not None:
            try:
                angle, score = self._arrow_func(frame)
                heading = angle
                heading_score = 0.0 if score is None else float(score)
            except Exception:
                heading = None
                heading_score = None

        est = self.estimate()
        out = {
            "x": None,
            "z": None,
            "dmap_px": (0.0, 0.0),
            "world_delta": (0.0, 0.0),
            "anchor_set": False,
            "last_sync_t": self._last_sync_t,
            "rest": self.is_rest(),
            "heading": heading,
            "heading_score": heading_score,
        }
        if est is not None:
            out.update(est)
        out["heading"] = heading
        out["heading_score"] = heading_score
        return out
