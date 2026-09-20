# -*- coding: utf-8 -*-
"""小地图实时位置能力（mixin）：一次调用同时给出「方向」和「坐标」。

坐标 = 小地图位移里程计（相对、高频） + 官方地图 WS 绝对坐标（绝对、有延迟）的融合：

  1. 静止时用 WS 绝对坐标设锚点 ``P0`` 并清零里程计（静止时延迟无影响）；
  2. 移动期间用里程计增量推位置：``P = P0 + M @ 里程计位移``；
  3. 再次静止时用新的 WS 坐标重新校准，把漂移限制在两次校准之间。

「静止」要求小地图里程计低速；若已经收到至少两次 WS 样本，还要求 WS 也没有明显位移。
WS 有传输延迟，只在两边都停住时才能安全地用它做基准。移动中收到的 WS 样本直接忽略、
不做暂存：那是移动途中的旧坐标，静止后应用它只会把里程计已经推进的位置拽回去。

定位状态由 ``MinimapPositionTask`` 这样的单一所有者维护，导航等消费方只读取
``latest_minimap_state()``；需要主动采样时把当前帧交给同一个 owner，禁止每个任务
各自启动一套里程计和 WS 位置源。

``minimap_position()`` 返回 dict：

    x / z            融合坐标（米，世界系 x/z）。未设锚点时 None。
    heading          朝向（**罗盘方位角**，度）：正北 0°、正东 90°、正南 180°、正西 270°，
                     顺时针增大。由底层箭头角经
                     :func:`minimap_odometry.arrow_angle_to_bearing` 换算而来
                     （箭头角是屏幕上从正北逆时针，与之互为镜像）。
    heading_score    朝向置信度；低于配置的「朝向最低分数」应判为不可用。
    anchor_set       是否已用 WS 设过锚点（False 时 x/z 为 None）。
    rest             本拍是否静止；静止时才允许用 WS 校准并清零里程计。
    last_sync_t      最近一次校准的时间戳。
    dmap_px          里程计累计位移（地图系像素，x 右 / y 下）。
    world_delta      上述位移映射到世界系的增量（米）。
    ws               最近收到的 WS 坐标 (x, z)，可能滞后；None = 还没收到。
    map_id           最近 WS 位置所属地图；None = 还没收到。
    error            |融合坐标 - 最近 WS|（米）。移动时≈WS 延迟（不是误差），静止校准后≈0。
    odom_ok/odom_reason  本拍是否采到有效位移样本及原因（"ok"/"too_soon"/"no_frame"/
                     "shift_too_small"/"low_response"/"exceed_max_shift"/"too_long_dt"/
                     "speed_anomaly"，
                     未采到时 odom_ok 为 False，odom_reason 说明为什么），
                     用来区分"位置在动"和"位置源已经没有数据了"。
    position_trusted  当前绝对坐标是否可信。重新锚定、换地图或里程计守卫触发后会变为
                     False，必须等到下一次静止 WS 校准才恢复。**例外**：良性换锚
                     （``too_long_dt``，相关可信、只是基线到了上限）不撤销信任——
                     位移提交阈值开启后它会是常态，撤销信任会让导航反复停车等重新校准。
    trust_reason      position_trusted 的最近状态来源。
    just_synced/sync_residual  本拍是否刚触发静止校准、以及校准前的残差
                     （``{map_x, map_z, ws_x, ws_z, dx, dz, dist}``，即小地图推算偏了多少米）。
    sync_checked/sync_redundant  本拍收到 WS 后是否检查了静止校准，以及检查结果是否为
                     “当前估计已与 WS 对齐，无需重锚”。

调用方注意：**务必把当前帧传进来**（``frame=frame``）。不传的话内部会用
``next_frame()`` 自己抓一帧，于是朝向你手里的帧与算位移的帧不是同一时刻，两者会错位。

本模块只做编排，几何/融合分别在 ``minimap_odometry`` 与 ``minimap_position_fusion``；
位置源（官方地图 WS 客户端 / 本地 WS 服务）的启停复用 ``WsPositionMixin``。完整链路见
``docs/dev/导航与小地图定位.md``。
"""

from __future__ import annotations

import math

from src.core.global_config_store import get_global_config
from src.core.NavConfig import (
    NAV_CONFIG_NAME,
    NAV_CONTENT_KEY,
    NAV_MATRIX_SUFFIX,
    NAV_RESOLUTION_TIERS,
    NAV_SCALE_CONSTANT_KEY,
    NAV_SCALE_SUFFIX,
    NAV_WS_ACCOUNT_KEY,
    NavProfile,
    nav_profile_for_width,
)
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
    "CONFIG_COMMIT_MIN_SHIFT",
    "CONFIG_SYNC_DISTANCE",
    "CONFIG_WS_MIN_HITS",
    "CONFIG_WS_WAIT",
    "DEFAULT_COMMIT_MIN_SHIFT_PX",
    "MinimapPositionMixin",
    "parse_map_to_world",
]

CONFIG_WS_WAIT = "WS等待稳定秒数"
CONFIG_WS_MIN_HITS = "WS稳定最小位置数"
CONFIG_SYNC_DISTANCE = "航点校准最小距离(米)"
CONFIG_COMMIT_MIN_SHIFT = "位移提交阈值(像素)"

#: 位移提交阈值默认值（1920 宽下约 1.8m）。0 = 关闭，回到"按时间提交"的旧行为。
#: 误差按采样次数累积、每样本误差大致是绝对量，而每样本位移 = 速度 * 采样间隔，
#: 所以慢走 / 原地小步走时每米提交次数暴涨、漂移更大。设阈值让每米提交次数与速度脱钩。
DEFAULT_COMMIT_MIN_SHIFT_PX = 2.0


def parse_map_to_world(value):
    """把 "a11,a12,a21,a22" 解析为 2x2 矩阵；非法或含非有限值时返回 None。

    非有限值（nan/inf）必须在这里挡掉：否则融合坐标会变成 nan（而不是 None），
    下游的 ``x is None`` 守卫失效，规划阶段才在 ``index_of_world`` 深处炸栈。
    """
    parts = str(value or "").split(",")
    if len(parts) != 4:
        return None
    try:
        nums = [float(p) for p in parts]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(n) for n in nums):
        return None
    return [[nums[0], nums[1]], [nums[2], nums[3]]]


class MinimapPositionMixin(MinimapHeadingMixin, WsPositionMixin):
    """小地图实时位置：同时给出方向（朝向角）和坐标（小地图推算 + WS 校准）。

    朝向相关的能力（读朝向、转到指定方位）来自 :class:`MinimapHeadingMixin`。

    **比例尺、轴映射与 WS 真值来源都在全局「导航配置」里**（``src/core/NavConfig.py``），
    任务侧不再配置：比例尺随画面宽变化（``比例尺 = C / 画面宽``），配在任务里必然会在
    换分辨率时过期——历史上 2560 宽量出的 0.6703 被拿到 1920 下用，距离就系统性偏了 27%。
    """

    # ------------------------------------------------------------------ #
    # 配置（任务把这两个 dict merge 进自己的 default_config / config_description）
    # ------------------------------------------------------------------ #
    @staticmethod
    def minimap_position_default_config() -> dict:
        """返回小地图定位配置的默认值（不含比例尺/轴映射/真值——那些在全局导航配置）。"""
        return {
            CONFIG_WS_WAIT: 10.0,
            CONFIG_WS_MIN_HITS: 3,
            CONFIG_MIN_SCORE: 0.6,
            CONFIG_SYNC_DISTANCE: 100.0,
            CONFIG_COMMIT_MIN_SHIFT: DEFAULT_COMMIT_MIN_SHIFT_PX,
        }

    @staticmethod
    def minimap_position_config_description() -> dict:
        """返回定位配置键的用户说明，包含静止校准语义。"""
        return {
            CONFIG_WS_WAIT: "等 WS 位置流稳定（连续同一 mapId 的有效位置）的最大等待秒数",
            CONFIG_WS_MIN_HITS: "判为稳定所需连续有效位置个数",
            CONFIG_MIN_SCORE: "箭头角度检测最低置信度，低于该值朝向判为不可用",
            CONFIG_SYNC_DISTANCE: "累计移动达到该距离后，请求导航暂停并等待静止自动校准",
            CONFIG_COMMIT_MIN_SHIFT: "里程计位移提交阈值（像素）。误差是按采样次数累积的，"
                                     "而每样本位移 = 速度 * 采样间隔，所以慢走 / 原地挪时每米要积更多样本、"
                                     "漂移更大。设成 2~3 像素可让每米提交次数与速度脱钩（原地小步走几乎不提交），"
                                     "同时位置仍连续（未提交部分照常计入位置）。"
                                     "0 = 关闭，回到按时间提交的旧行为，便于 A/B 对比",
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
        self._minimap_started = False
        self._minimap_last_state: dict | None = None
        self._minimap_sync_seq = 0
        # 距离上次真实校准的累计位移，用于触发周期性停车校准。
        self._minimap_distance_since_sync = 0.0
        self._minimap_prev_position: tuple[float, float] | None = None
        self._minimap_prev_map_id: str | None = None
        # 绝对坐标信任位：重锚/换图/里程计守卫触发后清零，下一次静校准恢复。
        self._minimap_position_trusted = False
        self._minimap_trust_reason = "uninitialized"

    def _nav_config(self):
        """读全局「导航配置」。取不到时返回 None（由调用方决定怎么报）。"""
        try:
            return get_global_config(NAV_CONFIG_NAME)
        except Exception as e:  # 该对象是框架 Config，异常类型不可控
            self.log_warning(f"读取全局「{NAV_CONFIG_NAME}」失败: {e}")
            return None

    def _nav_profile(self) -> NavProfile | None:
        """按**当前画面宽度**从全局「导航配置」选出比例尺与轴映射。

        比例尺随画面宽变化是物理事实（``比例尺 = C / 画面宽``），所以这里永远按当前分辨率
        现算，任务侧不再有"比例尺"这个配置项——配在任务里必然会在换分辨率时过期。
        """
        config = self._nav_config()
        if config is None:
            return None
        values = {
            NAV_SCALE_CONSTANT_KEY: config.get(NAV_SCALE_CONSTANT_KEY),
            **{
                f"{name}{suffix}": config.get(f"{name}{suffix}")
                for name, _ in NAV_RESOLUTION_TIERS
                for suffix in (NAV_SCALE_SUFFIX, NAV_MATRIX_SUFFIX)
            },
        }
        return nav_profile_for_width(int(getattr(self, "width", 0) or 0), values)

    def start_minimap_position(self, *, wait_stable: bool = True) -> bool:
        """建里程计 + 融合、启动位置源、等 WS 稳定并立即设锚点。

        Returns:
            True = 已用稳定的 WS 真值锚定（x/z 立即可用）；
            False = 没有可用的官方 WS 真值（未配置 content / 认证失败 / 等稳定超时），
            此时若本地 WS 服务能提供位置，会在后续静止时自动完成首次锚定。
        """
        if self._minimap_started and self._minimap_fusion is not None:
            if self._map_ws_auth_source and not self._is_map_ws_client_enabled():
                self._start_map_ws_client(self._map_ws_auth_source)
            estimate = self._minimap_fusion.estimate()
            return estimate is not None

        profile = self._nav_profile()
        if profile is None:
            self.log_warning(
                "无法确定导航比例尺：拿不到画面分辨率（游戏窗口还没就绪），或全局"
                f"「{NAV_CONFIG_NAME}」里的档位与比例尺常数都是无效值。"
                "请确认游戏窗口已连接，并检查全局配置里的导航配置。",
                notify=True,
            )
            return False
        matrix = parse_map_to_world(profile.map_to_world)
        if matrix is None:
            self.log_warning(
                f"全局「{NAV_CONFIG_NAME}」的轴映射无法解析（应为 4 个逗号分隔数字）: "
                f"{profile.map_to_world!r}",
                notify=True,
            )
            return False
        scale = profile.scale
        self.log_info(
            f"导航比例尺 {scale:.6f} 米/像素（{profile.source}），"
            f"轴映射 {profile.map_to_world}"
        )

        self._minimap_scale = scale
        commit_min_shift = max(
            0.0,
            self._cfg_float(CONFIG_COMMIT_MIN_SHIFT, DEFAULT_COMMIT_MIN_SHIFT_PX),
        )
        self._minimap_od = MinimapOdometry(
            self,
            scale_m_per_px=(scale if scale > 0 else None),
            commit_min_shift_px=commit_min_shift,
        )
        self._minimap_fusion = MinimapPositionFusion(
            self._minimap_od,
            map_to_world_px=matrix,
            scale_m_per_px=scale,
            arrow_func=self._read_arrow,
        )
        self._minimap_ws_map_id = None
        self._minimap_last_ws = None
        self._minimap_distance_since_sync = 0.0
        self._minimap_prev_position = None
        self._minimap_prev_map_id = None
        self._minimap_position_trusted = False
        self._minimap_trust_reason = "uninitialized"

        try:
            cred = self._resolve_ws_cred()
        except Exception as e:
            self.log_warning(f"读取地图 WS content 失败，改用本地 WS 位置源: {e}")
            cred = ""

        self._ensure_ws_position_source(cred)
        self._minimap_started = True
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
            self._minimap_position_trusted = True
            self._minimap_trust_reason = "initial_sync"
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
        finally:
            self._minimap_started = False

    # ------------------------------------------------------------------ #
    # 位置源（官方地图 WS 客户端 / 本地 WS 服务）
    # ------------------------------------------------------------------ #
    def _resolve_ws_cred(self) -> str:
        """按优先级解析地图同步 content（全局导航配置的 content > 地图账号 > 当前账号上下文）。"""
        config = self._nav_config()
        content = account = ""
        if config is not None:
            content = str(config.get(NAV_CONTENT_KEY) or "").strip()
            account = str(config.get(NAV_WS_ACCOUNT_KEY) or "").strip()
        if content:
            return content
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
    def minimap_position(
        self,
        frame=None,
        *,
        now=None,
        feed_ws: bool = True,
        allow_sync: bool = True,
    ) -> dict:
        """采样一拍，返回本拍的「方向 + 坐标」。

        Args:
            frame: 当前帧。**强烈建议传入**——不传会自己 ``next_frame()`` 抓一帧，
                导致"算位移用的帧"和"读朝向用的帧"不是同一时刻。
            now: 可选时间戳（秒），默认用 ``active_time()``。
            feed_ws: 是否顺带消费一条新到的 WS 位置并做静止校准。默认 True；
                调用方要自己读原始 WS 样本时传 False（避免样本被这里吃掉）。
            allow_sync: 为 False 时仍消费并记录 WS 样本、更新静止判定，但不重锚。

        Returns:
            见模块 docstring 的字段说明。``x``/``z`` 为 None 表示还没锚定。
        """
        if self._minimap_fusion is None or self._minimap_od is None:
            raise RuntimeError("请先调用 start_minimap_position() 初始化小地图位置源")

        st = self._minimap_fusion.state(now=now, frame=frame)
        st["just_synced"] = False
        st["sync_residual"] = None
        st["sync_checked"] = False
        st["sync_redundant"] = False
        if feed_ws:
            self._feed_ws_position(st, self._now(now), allow_sync=allow_sync)
            st["rest"] = self._minimap_fusion.is_rest()
        st["map_id"] = self._minimap_ws_map_id

        # 本拍是否有**有效位移样本**（区分"位置在动"与"位置源已经没有数据了"）。
        # 注意必须同时看 sampled：锚帧初始化（anchor_init）与采样过密（too_soon）
        # 都会 ok=True 但 sampled=False，此时并没有新的位移数据。
        last = self._minimap_od.last_result() or {}
        st["odom_ok"] = bool(last.get("ok") and last.get("sampled"))
        st["odom_reason"] = last.get("reason")
        # 守卫换锚才撤销信任；"良性换锚"（too_long_dt：相关可信、只是基线到了上限）不算。
        # 位移提交阈值开启后锚帧会被扣住等位移，dt 涨到 sample_max_dt 是**常态**——
        # 若把它也当成"测坏了"，导航会停车等重新校准，一次停 6 秒，比漂移更影响体感。
        if last.get("reanchored") and not last.get("benign_reanchor"):
            self._minimap_position_trusted = False
            self._minimap_trust_reason = str(last.get("reason") or "reanchored")

        st["ws"] = self._minimap_last_ws
        st["error"] = None
        if st.get("x") is not None and st.get("z") is not None and self._minimap_last_ws is not None:
            st["error"] = math.hypot(
                st["x"] - self._minimap_last_ws[0], st["z"] - self._minimap_last_ws[1])
        st["sync_seq"] = self._minimap_sync_seq
        st["sample_t"] = self._now(now)
        st["position_trusted"] = bool(
            self._minimap_position_trusted and st.get("anchor_set")
        )
        st["trust_reason"] = self._minimap_trust_reason
        self._update_sync_request(st)
        self._minimap_last_state = dict(st)
        return st

    def _update_sync_request(self, st: dict) -> None:
        """更新“距离上次校准的累计移动量”，供导航决定何时停车请求校准。"""
        x, z = st.get("x"), st.get("z")
        map_id = st.get("map_id")
        if bool(st.get("just_synced")) or bool(st.get("sync_checked")):
            self._minimap_distance_since_sync = 0.0
            self._minimap_prev_position = (
                (float(x), float(z)) if x is not None and z is not None else None
            )
            self._minimap_prev_map_id = str(map_id) if map_id is not None else None
        elif x is not None and z is not None:
            position = (float(x), float(z))
            if (
                self._minimap_prev_position is not None
                and map_id == self._minimap_prev_map_id
            ):
                moved = math.hypot(
                    position[0] - self._minimap_prev_position[0],
                    position[1] - self._minimap_prev_position[1],
                )
                if moved <= 10.0:
                    self._minimap_distance_since_sync += moved
            self._minimap_prev_position = position
            self._minimap_prev_map_id = str(map_id) if map_id is not None else None
        elif self._minimap_prev_map_id != map_id:
            self._minimap_distance_since_sync = 0.0
            self._minimap_prev_position = None
            self._minimap_prev_map_id = str(map_id) if map_id is not None else None

        threshold = max(0.0, self._cfg_float(CONFIG_SYNC_DISTANCE, 100.0))
        st["distance_since_sync"] = self._minimap_distance_since_sync
        st["sync_needed"] = bool(
            self._minimap_last_ws is not None
            and threshold > 0
            and self._minimap_distance_since_sync >= threshold
        )

    def latest_minimap_state(
        self,
        *,
        max_age: float | None = None,
        now: float | None = None,
    ) -> dict | None:
        """返回最近一次定位快照；超过 ``max_age`` 时返回 None。"""
        state = self._minimap_last_state
        if state is None:
            return None
        if max_age is not None:
            sample_t = state.get("sample_t")
            if sample_t is None or self._now(now) - float(sample_t) > max(0.0, float(max_age)):
                return None
        return dict(state)

    def _now(self, now):
        return self.active_time() if now is None else now

    def _feed_ws_position(self, st: dict, now, *, allow_sync: bool = True) -> bool:
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
            self._minimap_position_trusted = False
            self._minimap_trust_reason = "map_changed"
            self._apply_estimate(st, self._minimap_fusion.estimate())
            return False

        synced = self._minimap_fusion.try_sync(
            pos_ws,
            map_id=map_id,
            now=now,
            allow_sync=allow_sync,
        )
        self._minimap_last_ws = (x, z)
        st["sync_checked"] = synced
        st["sync_redundant"] = bool(synced and self._minimap_fusion.last_sync_redundant)
        # 空操作（估计已与 WS 重合）不算"刚校准"：不重读估计、也不产生新的残差，
        # 这样静止时不会每秒刷一条 [静止校准]，上一次真正有意义的残差也留得住。
        if synced and not self._minimap_fusion.last_sync_redundant:
            self._minimap_sync_seq += 1
            self._apply_estimate(st, self._minimap_fusion.estimate())
            st["just_synced"] = True
            st["sync_residual"] = self._minimap_fusion.last_sync_residual
        if synced:
            self._minimap_position_trusted = True
            self._minimap_trust_reason = "sync"
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
