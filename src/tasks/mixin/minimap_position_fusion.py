# -*- coding: utf-8 -*-
"""小地图里程计 + WS 绝对坐标的融合定位。

设计动机：官方地图 WS 有传输/服务器延迟；单独用 WS 得到的坐标滞后，单独用小地图
里程计则随走随漂。融合思路（与延迟兼容）：

  1. 静止时用 WS 绝对坐标作为锚点 ``P0``（静止时位置不变，延迟无影响），并清零里程计；
  2. 移动期间用小地图里程计做高频相对增量：``P_live = P0 + world(里程计位移)``；
  3. 每走一段（再次静止）用新的 WS 坐标重新设 ``P0``，把里程计漂移限制在两次校准之间。

延迟的处理：移动中收到的 WS 样本代表的是"延迟 L 前的旧位置"，不能直接当当前锚点，
否则会引入 ``L * 速度`` 的偏差。因此本类提供 ``try_sync``——仅在里程计判定为静止
时立即校准；否则把 WS 样本存入 ``pending``，待回归静止后再应用。静止校准由调用方
（或 ``try_sync``）显式触发，符合"静止取点 -> 走 -> 静止校"的流程。

坐标系约定：
  - 里程计给出"地图系位移（像素）"，轴：图像 x 向右、y 向下，北向上。
  - ``map_to_world_px`` 是 2x2 矩阵，把"地图系像素位移"映射为"世界系(米)位移"：
        world_delta = map_to_world_px @ (map_px_delta)
    该矩阵可由标定得到（默认取 ``diag(scale, -scale)``：图右=东 +x、图下=南
    -z，与真机标定及消费方默认矩阵同号；一旦测得轴交换/其它符号，用
    ``map_to_world_px = inv(标定A)`` 覆盖）。
  - WS 绝对坐标取水平分量 (x, z)。

本模块不依赖 ``ok`` 框架，只依赖一个"里程计"鸭子类型对象（提供 ``sample``、
``position_px``、``reset_position``、``last_sample``）与数值库，便于单元测试。
"""

from __future__ import annotations

import math

import numpy as np

from src.tasks.mixin.minimap_odometry import _reraise_control_flow

__all__ = ["MinimapPositionFusion", "world_from_map_px"]


def world_from_map_px(
    map_px: tuple[float, float],
    map_to_world_px,
) -> tuple[float, float]:
    """把地图系位移（像素）映射为世界系位移（米）。"""
    m = np.asarray(map_to_world_px, dtype=np.float64)
    px = np.asarray([map_px[0], map_px[1]], dtype=np.float64)
    out = m @ px
    return (float(out[0]), float(out[1]))


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
        arrow_func=None,
    ):
        self._od = odometry
        self._scale = float(scale_m_per_px)
        if map_to_world_px is not None:
            self._map_to_world_px = np.asarray(map_to_world_px, dtype=np.float64).reshape(2, 2)
        else:
            # 默认轴：图右=东 +x、图下=南 -z（图像 y 向下），与真机标定同号
            self._map_to_world_px = np.diag([self._scale, -self._scale]).astype(np.float64)
        self._rest_speed_m_s = float(rest_speed_m_s)
        self._rest_ws_m = float(rest_ws_m)
        # 朝向来源回调：callable(frame) -> (angle, score)。用于同时取朝向。
        self._arrow_func = arrow_func

        self._anchor_world = np.zeros(2, dtype=np.float64)  # (x, z)
        self._anchor_set = False
        self._last_sync_t: float | None = None
        self._pending_ws = None
        # 静止判定：最近一次收到的 WS 坐标 + 相邻两次 WS 之间的位移（米）
        self._last_ws = None          # (x, z)
        self._ws_moved_m = None       # None = 还没收到过两次 WS 样本
        # 最近一次静止校准的残差：校准前"小地图推算坐标" vs WS 坐标
        self._last_sync_residual = None
        # 最近一次 is_rest() 的两个实测值（排查"为什么没判静止"）
        self._rest_diag = None

    # ------------------------------------------------------------------ #
    # 状态
    # ------------------------------------------------------------------ #
    def reset(self):
        """清空锚点、待应用 WS 样本、静止判定状态与校准残差。"""
        self._anchor_set = False
        self._pending_ws = None
        self._last_ws = None
        self._ws_moved_m = None
        self._last_sync_residual = None
        self._od.reset_position()

    def set_map_to_world_px(self, matrix):
        self._map_to_world_px = np.asarray(matrix, dtype=np.float64).reshape(2, 2)

    def set_from_calibration(self, scale_info: dict | None):
        """直接从标定输出配置比例尺与轴映射。

        ``scale_info`` 为 :class:`MinimapDisplacementCalibration` E3 输出的
        ``scale_info`` dict，含 ``scale_m_per_px`` 与 ``map_to_world_px``。
        若已提供 ``map_to_world_px``（含轴交换/符号），则优先使用它；否则
        用 ``scale_m_per_px`` 构建默认恒等轴。
        """
        if not scale_info:
            return
        m = scale_info.get("map_to_world_px")
        if m is not None and len(m) == 2:
            try:
                self.set_map_to_world_px(m)
            except Exception:
                pass
        s = scale_info.get("scale_m_per_px")
        if s:
            self._scale = float(s)
            # 仅在没有自定义轴映射时用 scale 重建默认轴（图下=南，-z）
            if not scale_info.get("map_to_world_px"):
                self._map_to_world_px = np.diag([self._scale, -self._scale]).astype(np.float64)

    def set_scale(self, scale_m_per_px: float):
        self._scale = float(scale_m_per_px)
        # 仅在未设置自定义轴映射时，用 scale 重建默认轴（图下=南，-z）
        self._map_to_world_px = np.diag([self._scale, -self._scale]).astype(np.float64)

    @property
    def map_to_world_px(self) -> tuple[tuple[float, float], tuple[float, float]]:
        return (tuple(self._map_to_world_px[0]), tuple(self._map_to_world_px[1]))

    @property
    def last_sync_residual(self) -> dict | None:
        """最近一次校准的残差：{map_x, map_z, ws_x, ws_z, dx, dz, dist}。

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
        """是否静止：小地图里程计位移极小 **且** WS 也没有位移，两者同时成立。

        小地图里程计给的是高频、准确的相对位移；WS 给的是绝对坐标但有传输延迟。
        只有两者同时显示"没动"才判静止——静止时 WS 的延迟无影响，此时用 WS
        校准（``sync``）才是安全的。任一条件不满足都不算静止：

          - 小地图条件：最近一次有效样本的世界系速度 < ``rest_speed_m_s``；
          - WS 条件：相邻两次 WS 坐标之差 < ``rest_ws_m``（无 WS 数据时不参与）。

        注意：里程计没有有效样本时返回 False——无法确认"没动"，不猜。
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
        self._pending_ws = None
        self._od.reset_position()
        return self.estimate()

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

    def try_sync(self, world_xyz, *, map_id=None, now=None) -> bool:
        """尝试用 WS 校准：静止（小地图没动 且 WS 没动）时立即同步；否则暂存。

        Returns:
            True 表示本次已同步；False 表示暂存为 pending（未同步）。
        """
        if world_xyz is None:
            return False
        x, z = float(world_xyz[0]), float(world_xyz[2])
        self._note_ws(x, z)
        if self.is_rest():
            self._apply_sync(x, z, now)
            return True
        self._pending_ws = {"world": world_xyz, "map_id": map_id, "now": now}
        return False

    # ------------------------------------------------------------------ #
    # 定位
    # ------------------------------------------------------------------ #
    def step(self, *, now=None, frame=None) -> dict | None:
        """采样一拍里程计，并在合适时应用待定 WS 校准，返回当前估计。

        若存在 pending 的 WS 样本且当前静止，会在本拍应用它（重新设锚点并清零）。
        """
        try:
            self._od.sample(frame=frame)
        except Exception as e:  # noqa: BLE001
            # 任务被停用/结束抛的是框架控制流异常，必须放行——里程计侧
            # _reraise_control_flow 特意让它们冒出来，这里不能又吞回去。
            _reraise_control_flow(e)
        if self._pending_ws is not None and self.is_rest():
            w = self._pending_ws["world"]
            self._apply_sync(float(w[0]), float(w[2]), now)
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
            except Exception:  # noqa: BLE001
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
