# -*- coding: utf-8 -*-
"""小地图位移里程计标定/验证任务。

通过真实游戏画面运行一组实验，验证"小地图位移里程计"（MinimapOdometry）：
  - E1 静止：累计位移应≈0（底噪）。
  - E2 原地转向：累计位移应≈0（验证地图固定不随视角转、内圈 mask 盖住箭头扫掠）。
  - E3 直走：位移方向应与箭头朝向一致、大小随时间线性；若配置了官方地图 WS
    真值，则用 WS 绝对坐标标定比例尺 S(米/像素) 与地图系->世界系的坐标轴映射。
  - E4 走弧线：验证转向+移动路线下的积分轨迹与净位移。
  - E5 界面切换：开/关大地图时里程计不崩溃、恢复后锚帧重置。

本任务只做测量与标定，不把结果写入正式配置（第一版不持久化）。
"""

from qfluentwidgets import FluentIcon

from src.core.BaseEfTask import BaseEfTask
from src.tasks.account.account_scope_store import get_account_map_content, resolve_account_id
from src.tasks.mixin.minimap_odometry import MinimapOdometry
from src.tasks.mixin.ws_position_mixin import WsPositionMixin


class MinimapDisplacementCalibration(BaseEfTask, WsPositionMixin):
    """小地图位移里程计标定测试（工具与调试分组）。"""

    requires_foreground = True  # 需要前台移动/视角操作

    # 基础采样参数
    SAMPLE_STEP = 0.25           # 每拍睡眠间隔（秒），同时控住 od 的采样 dt
    IDLE_DURATION = 2.0          # E1 静止时长（秒）
    TURN_STEP_DX = 40            # E2 每步鼠标位移量（正负交替，用于原地转向）
    TURN_STEPS = 20              # E2 转向步数
    STRAIGHT_DURATION = 3.0      # E3 直走时长（秒）
    ARC_DURATION = 3.0           # E4 走弧线时长（秒）
    ARC_TURN_DX = 60             # E4 每步转向的鼠标位移量
    ARC_TURN_EVERY = 2           # E4 每 N 拍转向一次
    ARROW_MIN_SCORE = 0.6        # 箭头角度最低置信度

    # 比例尺与真值（0/空=不用真值，仅像素相对测量）
    SCALE_M_PER_PX = 0.0         # 手动指定 米/像素；为 0 时若 WS 可用则自动标定，否则不标定
    WS_CONTENT = ""              # 直接填 hg/check 的 data.content 作为真值源
    WS_ACCOUNT = ""              # 从账号配置页读取对应账号地图同步 content

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "小地图位移标定"
        self.group_name = "工具与调试"
        self.group_icon = FluentIcon.DEVELOPER_TOOLS
        self.description = "实测小地图位移里程计（静止/转向/直走/弧线/界面切换）并可选标定比例尺"
        self.visible = self.debug

        # 为可选的真值（官方地图 WS 客户端）初始化内部状态。
        # 标定是连续跑 E1-E5 的实验，期间 E1/E2/E4 不读取 WS 位置，
        # 若沿用 mixin 默认的消费空闲超时（10s），WS 客户端会在 E3 前自动停止，
        # 导致真值采不到。这里把空闲超时调大，避免中途被踢。
        self._init_ws_position_mixin()
        self._map_ws_consumer_idle_timeout = 3600.0

        self.default_config = {
            "采样间隔(秒)": self.SAMPLE_STEP,
            "静止时长(秒)": self.IDLE_DURATION,
            "转向步数": self.TURN_STEPS,
            "转向鼠标dx(每步)": self.TURN_STEP_DX,
            "直走时长(秒)": self.STRAIGHT_DURATION,
            "弧线时长(秒)": self.ARC_DURATION,
            "弧线转向dx(每步)": self.ARC_TURN_DX,
            "弧线转向间隔(拍)": self.ARC_TURN_EVERY,
            "箭头最低置信度": self.ARROW_MIN_SCORE,
            "比例尺(米/像素,0=自动)": self.SCALE_M_PER_PX,
            "真值content": self.WS_CONTENT,
            "真值地图账号": self.WS_ACCOUNT,
            "WS等待稳定秒数": 10.0,
            "WS稳定最小位置数": 3,
            "启用E1静止": True,
            "启用E2转向": True,
            "启用E3直走": True,
            "启用E4弧线": True,
            "启用E5界面": True,
        }
        self.config_description = {
            "采样间隔(秒)": "每拍采样的睡眠间隔；同时也决定 od 的锚帧时间窗",
            "静止时长(秒)": "E1 原地不动的采样时长，用于测底噪",
            "转向步数": "E2 原地转向的鼠标步数（正负交替），用于验证地图固定",
            "转向鼠标dx(每步)": "E2 每步相对鼠标位移像素值",
            "直走时长(秒)": "E3 持续按住 W 的时长",
            "弧线时长(秒)": "E4 走弧线的总时长（边前进边转视角）",
            "弧线转向dx(每步)": "E4 每次转向的鼠标位移像素值",
            "弧线转向间隔(拍)": "E4 每几拍转向一次",
            "箭头最低置信度": "箭头角度检测最低置信度，低于该值该方向读数为不可用",
            "比例尺(米/像素,0=自动)": "手动指定比例尺；为 0 且配置了 WS 真值时自动标定，否则仅像素相对测量",
            "真值content": "可选。直接填官方地图 hg/check 返回的 data.content 作为真值来源",
            "真值地图账号": "可选。content 为空时从账号配置页读取该账号的地图同步 content",
            "WS等待稳定秒数": "等 WS 真值流稳定（连续收到同一 mapId 的有效位置）的最长等待秒数；未稳定则跳过比例尺标定",
            "WS稳定最小位置数": "判为稳定所需的连续有效位置个数（同一 mapId）",
            "启用E1静止": "是否执行 E1 静止实验",
            "启用E2转向": "是否执行 E2 原地转向实验",
            "启用E3直走": "是否执行 E3 直走实验",
            "启用E4弧线": "是否执行 E4 走弧线实验",
            "启用E5界面": "是否执行 E5 开/关大地图实验",
        }

        # 里程计实例（构建时按配置初始化；真正启用放在 run 开头）
        self._od: MinimapOdometry | None = None
        # 校准期间使用的 WS mapId（等 WS 稳定后确定；跨地图记录被丢弃）
        self._ws_map_id: str | None = None

    # ------------------------------------------------------------------ #
    # 配置读取
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

    def _cfg_bool(self, key, default):
        raw = self.config.get(key, default)
        if isinstance(raw, str):
            return raw.strip().lower() in ("1", "true", "yes", "on", "是", "开启", "开")
        return bool(raw)

    # ------------------------------------------------------------------ #
    # 里程计
    # ------------------------------------------------------------------ #
    def _build_odometry(self):
        scale = self._cfg_float("比例尺(米/像素,0=自动)", self.SCALE_M_PER_PX)
        self._od = MinimapOdometry(
            self,
            scale_m_per_px=(scale if scale > 0 else None),
        )

    def _next_ok_sample(self, frame=None):
        """采样一拍，返回 (sample, has_movement)。"""
        r = self._od.sample(frame=frame)
        return r

    # ------------------------------------------------------------------ #
    # 真值（可选 WS）
    # ------------------------------------------------------------------ #
    def _resolve_ws_cred(self):
        content = str(self.config.get("真值content") or self.WS_CONTENT).strip()
        if content:
            return content
        account = str(self.config.get("真值地图账号") or self.WS_ACCOUNT).strip()
        if account:
            account_id = resolve_account_id(account, create_if_missing=False) or account
            return get_account_map_content(account_id, account_name=account)
        account_id = str(getattr(self, "current_account_id", "") or "").strip()
        account_name = str(getattr(self, "current_user", "") or "").strip()
        return get_account_map_content(account_id or account_name, account_name=account_name)

    def _maybe_start_ws(self) -> bool:
        try:
            cred = self._resolve_ws_cred()
        except Exception as e:
            self.log_info(f"WS 真值不可用（读取 content 失败）: {e}")
            return False
        if not cred:
            self.log_info("未配置 WS 真值（content/地图账号为空），跳过比例尺自动标定")
            return False
        try:
            ok = self._start_map_ws_client(cred)
        except Exception as e:
            self.log_info(f"启动地图 WS 客户端失败: {e}")
            return False
        if ok:
            self.log_info("地图 WS 客户端已启动，将同步记录真值坐标", notify=True)
        return ok

    def _poll_ws_pos(self, timeout: float = 0.0):
        """取一条**新到**的 WS 位置（不从缓存取旧值）。

        Returns:
            (x, y, z, map_id) | None：仅当收到有效新位置时返回。
            用 ``_recv_ws_position_payload``（仅新消息）而不是
            ``_recv_ws_position_payload_or_cached``（可能返回缓存旧值），
            避免把同一旧位置重复当成真值点。
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

    def _wait_ws_stable(self, timeout: float = 10.0, min_hits: int = 3) -> tuple[bool, str | None]:
        """等到 WS 真值流稳定再开始标定。

        连续收到有效位置、且 mapId 一致，累计达 ``min_hits`` 才算稳定。
        否则返回 (False, 最近的 mapId)。

        目的：校准前 WS 可能还没完成认证/账号解析/首帧位置，此时记录到的
        "真值"是空或陈旧缓存，导致比例尺/轴映射不准确。等稳定后再采样。
        """
        map_id = None
        hits = 0
        t0 = self.active_time()
        while self.active_time() - t0 < timeout:
            pos_ws = self._poll_ws_pos(timeout=0.5)
            if pos_ws is None:
                continue
            _, _, _, mid = pos_ws
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
                return True, map_id
        return False, map_id

    # ------------------------------------------------------------------ #
    # 实验
    # ------------------------------------------------------------------ #
    def run(self):
        self.log_info("=== Minimap Displacement Calibration ===", notify=True)
        if not self.in_world():
            self.log_info("当前不在大世界画面，无法执行位移标定。请先进入大世界。", notify=True)
            return

        self._build_odometry()
        self._od.reset(reset_position=True)

        ws_active = self._maybe_start_ws()
        self._ws_map_id = None
        if ws_active:
            wait_timeout = self._cfg_float("WS等待稳定秒数", 10.0)
            min_hits = max(2, self._cfg_int("WS稳定最小位置数", 3))
            self.log_info("等待 WS 真值流稳定后再开始标定……", notify=True)
            stable, map_id = self._wait_ws_stable(timeout=wait_timeout, min_hits=min_hits)
            if not stable:
                self.log_warning(
                    f"WS 真值未在 {wait_timeout:.1f}s 内稳定（mapId={map_id}），"
                    "跳过比例尺自动标定，E3 仅做相对测量",
                    notify=True,
                )
                ws_active = False
            else:
                self._ws_map_id = map_id
                self.log_info(f"WS 真值流已稳定，mapId={map_id}，开始标定", notify=True)

        results = {}
        # 汇总 E3/E4 的 WS 真值点，用于一次 2D 拟合（单一直线行走只激励一个轴，
        # 合并转向/弧线数据能把两个方向都定住，避免非对角项成为噪声）。
        self._ws_records = []
        if self._cfg_bool("启用E1静止", True):
            results["E1_idle"] = self._exp_idle()
        if self._cfg_bool("启用E2转向", True):
            results["E2_turn"] = self._exp_turn()
        if self._cfg_bool("启用E3直走", True):
            results["E3_straight"] = self._exp_straight(ws_active)
        if self._cfg_bool("启用E4弧线", True):
            results["E4_arc"] = self._exp_arc(ws_active)
        if self._cfg_bool("启用E5界面", True):
            results["E5_ui"] = self._exp_ui()

        if ws_active and len(self._ws_records) >= 3:
            scale_info = self._fit_scale_axis(self._ws_records)
            if scale_info is not None:
                results["scale"] = scale_info
                cond = scale_info.get("condition")
                if scale_info.get("ill_conditioned"):
                    self.log_warning(
                        f"本次行走近单方向（条件数 {cond:.1f}），垂直轴不可靠；"
                        "建议启用 E4 弧线或换方向再走一段，以便把轴映射定准",
                        notify=True,
                    )

        self._report(results)

        if ws_active:
            try:
                self._stop_map_ws_client()
            except Exception:
                pass

    def _sample_for(self, duration):
        """按固定间隔采样 duration 秒，返回样本列表。"""
        step = max(0.05, self._cfg_float("采样间隔(秒)", self.SAMPLE_STEP))
        start = self.active_time()
        out = []
        while self.active_time() - start < duration:
            self.sleep(step)
            r = self._od.sample()
            out.append(r)
        return out

    def _exp_idle(self):
        duration = self._cfg_float("静止时长(秒)", self.IDLE_DURATION)
        self.log_info(f"=== E1 静止 ({duration:.1f}s) ===", notify=True)
        # 先重置，从清空位置开始
        self._od.reset_position()
        samples = self._sample_for(duration)
        pos = self._od.position_px()
        moved = sum(1 for s in samples if s.get("sampled") and s.get("ok"))
        return {
            "samples": len(samples),
            "moved": moved,
            "pos_px": pos,
            "len_px": (pos[0] ** 2 + pos[1] ** 2) ** 0.5,
        }

    def _exp_turn(self):
        steps = max(1, self._cfg_int("转向步数", self.TURN_STEPS))
        dx = self._cfg_int("转向鼠标dx(每步)", self.TURN_STEP_DX)
        self.log_info(f"=== E2 原地转向 ({steps} 步, dx={dx}) ===", notify=True)
        self._od.reset_position()
        before_angle, before_score = self._read_arrow()
        out = []
        for i in range(steps):
            direction = dx if i % 2 == 0 else -dx
            try:
                self.active_and_send_mouse_delta(dx=direction, dy=0, steps=2, delay=0.005)
            except Exception as e:
                self.log_warning(f"E2 鼠标位移失败: {e}")
                continue
            self.sleep(self._cfg_float("采样间隔(秒)", self.SAMPLE_STEP))
            out.append(self._od.sample())
        after_angle, after_score = self._read_arrow()
        pos = self._od.position_px()
        self.log_info(
            f"E2: before_angle={before_angle}  after_angle={after_angle}  "
            f"位移 len={ (pos[0]**2+pos[1]**2)**0.5 :.2f}px"
        )
        return {
            "steps": steps,
            "pos_px": pos,
            "len_px": (pos[0] ** 2 + pos[1] ** 2) ** 0.5,
            "d_yaw": after_angle - before_angle if after_angle is not None and before_angle is not None else None,
        }

    def _exp_straight(self, ws_active):
        duration = self._cfg_float("直走时长(秒)", self.STRAIGHT_DURATION)
        self.log_info(f"=== E3 直走 ({duration:.1f}s) ===", notify=True)
        self._od.reset_position()
        heading, h_score = self._read_arrow()
        ws_records = []
        try:
            self.send_key_down("w")
        except Exception as e:
            self.log_warning(f"E3 send_key_down('w') 失败: {e}")
            return {"error": str(e)}
        start = self.active_time()
        while self.active_time() - start < duration:
            self.sleep(self._cfg_float("采样间隔(秒)", self.SAMPLE_STEP))
            r = self._od.sample()
            if ws_active:
                pos_ws = self._poll_ws_pos(timeout=0.0)
                # 仅接受与稳定 mapId 一致的新位置，避免跨地图/陈旧数据污染标定
                if pos_ws is not None and pos_ws[3] == self._ws_map_id:
                    rec = {
                        "t": self.active_time(),
                        "x": pos_ws[0], "y": pos_ws[1], "z": pos_ws[2],
                        "px": self._od.position_px(),
                        "seg": "E3",
                    }
                    ws_records.append(rec)
                    self._ws_records.append(rec)
        try:
            self.send_key_up("w")
        except Exception as e:
            self.log_warning(f"E3 send_key_up('w') 失败: {e}")
        end_pos = self._od.position_px()
        fwd, strafe = self._od.forward_strafe_m(heading)
        result = {
            "heading": heading,
            "h_score": h_score,
            "pos_px": end_pos,
            "len_px": (end_pos[0] ** 2 + end_pos[1] ** 2) ** 0.5,
            "forward": fwd,
            "strafe": strafe,
            "ws_records": len(ws_records),
        }
        return result

    def _exp_arc(self, ws_active):
        duration = self._cfg_float("弧线时长(秒)", self.ARC_DURATION)
        turn_dx = self._cfg_int("弧线转向dx(每步)", self.ARC_TURN_DX)
        every = max(1, self._cfg_int("弧线转向间隔(拍)", self.ARC_TURN_EVERY))
        self.log_info(f"=== E4 走弧线 ({duration:.1f}s, turn_dx={turn_dx}, every={every}) ===", notify=True)
        self._od.reset_position()
        step = self._cfg_float("采样间隔(秒)", self.SAMPLE_STEP)
        try:
            self.send_key_down("w")
        except Exception as e:
            self.log_warning(f"E4 send_key_down('w') 失败: {e}")
            return {"error": str(e)}
        start = self.active_time()
        i = 0
        while self.active_time() - start < duration:
            self.sleep(step)
            if i % every == 0:
                try:
                    self.active_and_send_mouse_delta(dx=turn_dx, dy=0, steps=2, delay=0.005)
                except Exception as e:
                    self.log_warning(f"E4 转向失败: {e}")
            self._od.sample()
            if ws_active:
                pos_ws = self._poll_ws_pos(timeout=0.0)
                if pos_ws is not None and pos_ws[3] == self._ws_map_id:
                    self._ws_records.append({
                        "t": self.active_time(),
                        "x": pos_ws[0], "y": pos_ws[1], "z": pos_ws[2],
                        "px": self._od.position_px(),
                        "seg": "E4",
                    })
            i += 1
        try:
            self.send_key_up("w")
        except Exception as e:
            self.log_warning(f"E4 send_key_up('w') 失败: {e}")
        pos = self._od.position_px()
        self.log_info(f"E4 净位移: {pos[0]:.2f},{pos[1]:.2f}px  len={(pos[0]**2+pos[1]**2)**0.5:.2f}px")
        return {"pos_px": pos, "len_px": (pos[0] ** 2 + pos[1] ** 2) ** 0.5}

    def _exp_ui(self):
        self.log_info("=== E5 界面切换（开/关大地图） ===", notify=True)
        self._od.reset_position()
        before = self._od.sample()
        try:
            self.press_key("m")
        except Exception as e:
            self.log_warning(f"E5 开地图失败: {e}")
            return {"error": str(e)}
        self.sleep(0.6)
        in_map_samples = []
        for _ in range(3):
            self.sleep(self._cfg_float("采样间隔(秒)", self.SAMPLE_STEP))
            in_map_samples.append(self._od.sample())
        try:
            self.press_key("m")
        except Exception as e:
            self.log_warning(f"E5 关地图失败: {e}")
        self.sleep(0.6)
        after = self._od.sample()
        reasons = []
        for s in in_map_samples:
            reasons.append(s.get("reason"))
        return {
            "before_reason": before.get("reason"),
            "in_map_reasons": reasons,
            "after_reason": after.get("reason"),
            "pos_px": self._od.position_px(),
        }

    def _read_arrow(self):
        try:
            angle, score = self.get_arrow_angle(smoothing_threshold=None)
            return angle, 0.0 if score is None else float(score)
        except Exception as e:
            self.log_warning(f"读取箭头角度失败: {e}")
            return None, 0.0

    def _fit_scale_axis(self, records):
        """从 (t, x, y, z, px, seg) 记录序列估算比例尺与地图系->世界系坐标映射。

        采用**段内相邻差分**：每条记录带 ``seg``（如 E3/E4），只对同段内相邻
        两个记录取 (d_world, d_px)，跨段（实验间 reset_position 造成的跳变）跳过。
        这样不同段各自复位也不会污染坐标一致；对曲线路径同样适用。

        返回：
          - scale_m_per_px: 米/像素（|d_world_xz|/|d_px| 的中位数）
          - map_to_world_px: 2x2，地图系像素 -> 世界系米（inv(A)，含轴/符号）
          - world_to_map_px: 2x2，世界系 -> 地图系像素（A）
          - condition / ill_conditioned: 方向激励是否充足（病态检测）
          - n: 使用的相邻差分对数
        """
        import numpy as np

        dws = []
        dpxs = []
        for prev, cur in zip(records[:-1], records[1:]):
            # 跨段（复位）或缺少坐标/像素则跳过
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
        ratios = [np.hypot(dw[0], dw[1]) / np.hypot(dp[0], dp[1]) for dw, dp in zip(dws, dpxs)]
        median_m_per_px = float(np.median(ratios)) if ratios else 0.0
        # 最小二乘拟合 d_px = A @ d_world（A: world系 -> map系像素）
        A, *_ = np.linalg.lstsq(dws, dpxs, rcond=None)
        try:
            invA = np.linalg.inv(A)
        except np.linalg.LinAlgError:
            invA = np.linalg.pinv(A)
        # 病态检测：行走是否近单方向（dws 奇异值比）
        try:
            s = np.linalg.svd(dws, compute_uv=False)
            cond = float(s[0] / s[-1]) if s[-1] > 1e-9 else float("inf")
        except Exception:
            cond = float("inf")
        ill = cond > 10.0
        return {
            "scale_m_per_px": round(meters_per_px, 6),
            "median_m_per_px": round(median_m_per_px, 6),
            "map_to_world_px": [
                [round(float(invA[0, 0]), 6), round(float(invA[0, 1]), 6)],
                [round(float(invA[1, 0]), 6), round(float(invA[1, 1]), 6)],
            ],
            "world_to_map_px": [
                [round(float(A[0, 0]), 6), round(float(A[0, 1]), 6)],
                [round(float(A[1, 0]), 6), round(float(A[1, 1]), 6)],
            ],
            "n": len(dws),
            "condition": round(cond, 2),
            "ill_conditioned": bool(ill),
        }

    def _report(self, results):
        self.log_info("===== 标定结果汇总 =====", notify=True)
        for name, r in results.items():
            if r is None:
                self.log_info(f"{name}: (无数据)")
                continue
        # 逐项输出关键量
        if "E1_idle" in results:
            r = results["E1_idle"]
            self.log_info(
                f"E1 静止: 样本={r['samples']} 有位移样本={r['moved']} "
                f"累计位移=({r['pos_px'][0]:.2f},{r['pos_px'][1]:.2f})px  len={r['len_px']:.2f}px"
            )
        if "E2_turn" in results:
            r = results["E2_turn"]
            self.log_info(
                f"E2 转向: 步数={r['steps']} 位移len={r['len_px']:.2f}px "
                f"Δyaw={r['d_yaw']}"
            )
        if "E3_straight" in results:
            r = results["E3_straight"]
            self.log_info(
                f"E3 直走: heading={r['heading']} 位移=({r['pos_px'][0]:.2f},{r['pos_px'][1]:.2f})px "
                f"len={r['len_px']:.2f}px  forward={r['forward']:.2f}  strafe={r['strafe']:.2f}  "
                f"ws_records={r['ws_records']}"
            )
        if "scale" in results:
            si = results["scale"]
            self.log_info(
                f"比例尺标定(E3+E4合并): scale_m_per_px={si['scale_m_per_px']}  "
                f"(median={si.get('median_m_per_px')})  "
                f"map_to_world_px={si['map_to_world_px']}  n={si['n']}  "
                f"condition={si['condition']}  ill_conditioned={si['ill_conditioned']}",
                notify=True,
            )
        if "E4_arc" in results:
            r = results["E4_arc"]
            self.log_info(f"E4 弧线: 净位移=({r['pos_px'][0]:.2f},{r['pos_px'][1]:.2f})px  len={r['len_px']:.2f}px")
        if "E5_ui" in results:
            r = results["E5_ui"]
            self.log_info(f"E5 界面: before={r['before_reason']} in_map={r['in_map_reasons']} after={r['after_reason']}")
