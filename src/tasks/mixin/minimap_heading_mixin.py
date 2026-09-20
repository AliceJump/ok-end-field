"""小地图朝向能力（mixin）：读朝向角 + 转到指定罗盘方位。

朝向来自小地图中心的箭头，经 :func:`minimap_odometry.arrow_angle_to_bearing` 统一成
**罗盘方位角**（正北 0°、正东 90°、正南 180°、正西 270°，顺时针增大）。

游戏机制（决定了这里的做法）：转鼠标只转**视角**，角色的实际朝向要**按一次 W**
（且必须长按，瞬时按键不足以让角色转身）才会跟着视角转过去。所以"转到某个方位"是：

    读当前朝向 -> 算出还差多少 -> 转视角 -> 按 W 让角色转身 -> 再读一次校验

因为每次按 W 都会让角色向前走一小段（约 ``W长按时间 × 速度``，量级 1~1.5m），
位置敏感的场景要考虑到这个副作用。

用法::

    class MyTask(BaseEfTask, MinimapHeadingMixin):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._init_minimap_heading_mixin()
            self.default_config = {..., **self.minimap_heading_default_config()}

        def run(self):
            res = self.turn_to_bearing(90.0)          # 转到正东，±5°
            if res["ok"]:
                self.log_info(f"到位: {res['heading']:.1f}°（{res['rounds']} 轮）")

旋转系数 ``yaw_per_pixel``（度/像素）由「鼠标视角旋转系数标定」任务实测得到，
恒为正（鼠标右移 -> 方位角增大）；``MouseRotationCalibration`` 报出来的 k 是
"箭头角/像素"且恒为负，取绝对值即可。
"""

from __future__ import annotations

from src.tasks.mixin.minimap_odometry import angle_delta, arrow_angle_to_bearing

__all__ = [
    "CONFIG_ANGLE_REFRESH",
    "CONFIG_MIN_SCORE",
    "CONFIG_MOUSE_CHUNK",
    "CONFIG_MOUSE_CHUNK_DELAY",
    "CONFIG_TURN_SETTLE",
    "CONFIG_W_HOLD",
    "CONFIG_YAW_PER_PIXEL",
    "DEFAULT_ANGLE_REFRESH",
    "DEFAULT_MIN_SCORE",
    "DEFAULT_MOUSE_CHUNK",
    "DEFAULT_MOUSE_CHUNK_DELAY",
    "DEFAULT_TURN_SETTLE",
    "DEFAULT_W_HOLD",
    "DEFAULT_YAW_PER_PIXEL",
    "MinimapHeadingMixin",
]

CONFIG_YAW_PER_PIXEL = "yaw_per_pixel(度/像素)"
CONFIG_W_HOLD = "W长按时间(秒)"
CONFIG_TURN_SETTLE = "转向后等待(秒)"
CONFIG_ANGLE_REFRESH = "角度刷新等待(秒)"
CONFIG_MIN_SCORE = "朝向最低分数"
CONFIG_MOUSE_CHUNK = "单次鼠标位移上限(像素)"
CONFIG_MOUSE_CHUNK_DELAY = "分段间隔(秒)"

#: 旋转系数实测值（2026-09-05 标定：yaw_per_pixel=-0.0675，std=0.0025；取绝对值）
DEFAULT_YAW_PER_PIXEL = 0.07125
DEFAULT_W_HOLD = 0.3
DEFAULT_TURN_SETTLE = 0.3
DEFAULT_ANGLE_REFRESH = 0.1
DEFAULT_MIN_SCORE = 0.6
#: 单次相对位移过大可能被游戏/系统截断，拆成不超过这个像素数的多次发送
DEFAULT_MOUSE_CHUNK = 200
#: 分段之间的间隔：略大于一帧（60fps≈16.7ms），让每段各自生效
DEFAULT_MOUSE_CHUNK_DELAY = 0.02
#: 实测系数与配置值偏差超过这个相对比例就给出提示（见 _warn_if_ratio_off）
RATIO_HINT_TOLERANCE = 0.2
#: 位移小于这个像素数时，实测比例被量化噪声主导，不据此提示
RATIO_HINT_MIN_DX = 40


class MinimapHeadingMixin:
    """小地图朝向：读朝向角（罗盘方位）与「转到指定方位」。"""

    # ------------------------------------------------------------------ #
    # 配置（任务把这两个 dict merge 进自己的 default_config / config_description）
    # ------------------------------------------------------------------ #
    @staticmethod
    def minimap_heading_default_config() -> dict:
        """返回朝向检测和转向相关配置的默认值。"""
        return {
            CONFIG_YAW_PER_PIXEL: DEFAULT_YAW_PER_PIXEL,
            CONFIG_W_HOLD: DEFAULT_W_HOLD,
            CONFIG_TURN_SETTLE: DEFAULT_TURN_SETTLE,
            CONFIG_ANGLE_REFRESH: DEFAULT_ANGLE_REFRESH,
            CONFIG_MIN_SCORE: DEFAULT_MIN_SCORE,
            CONFIG_MOUSE_CHUNK: DEFAULT_MOUSE_CHUNK,
            CONFIG_MOUSE_CHUNK_DELAY: DEFAULT_MOUSE_CHUNK_DELAY,
        }

    @staticmethod
    def minimap_heading_config_description() -> dict:
        """返回朝向配置键的用户说明，供任务界面直接合并。"""
        return {
            CONFIG_YAW_PER_PIXEL: "转视角系数（度/像素，正数=鼠标右移方位角增大）；"
                                  "由「鼠标视角旋转系数标定」任务实测，取该任务 k 的绝对值",
            CONFIG_W_HOLD: "按 W 让角色转身时的长按时长；太短角色不会真正转身",
            CONFIG_TURN_SETTLE: "发完鼠标位移后、按 W 之前等待视角转完的时间",
            CONFIG_ANGLE_REFRESH: "按 W 之后等待画面刷新再读朝向的时间",
            CONFIG_MIN_SCORE: "箭头角度检测最低置信度，低于该值朝向判为不可用",
            CONFIG_MOUSE_CHUNK: "单次鼠标相对位移的上限（像素），超过则拆成多次发送",
            CONFIG_MOUSE_CHUNK_DELAY: "拆分发送时每段之间的间隔（秒）",
        }

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

    def _init_minimap_heading_mixin(self):
        """初始化朝向状态；必须在任务 ``__init__`` 中调用且不启动任何线程。"""
        self._last_turn_result = None

    # ------------------------------------------------------------------ #
    # 读朝向
    # ------------------------------------------------------------------ #
    def _read_arrow(self, frame=None):
        """读小地图朝向。返回 ``(bearing, score)``，失败返回 ``(None, 0.0)``。

        返回值是**罗盘方位角**（正北 0°、正东 90°、顺时针）：底层
        ``get_arrow_angle`` 给的是"屏幕上从正北逆时针"，这里用
        :func:`arrow_angle_to_bearing` 统一换算，下游一律按方位角理解。

        传 ``smoothing_threshold=None``：要本帧原始角度，低分时也不沿用上一帧，
        由调用方按「朝向最低分数」自行判可用性。
        """
        try:
            angle, score = self.get_arrow_angle(target_image=frame, smoothing_threshold=None)
            score = 0.0 if score is None else float(score)
            if angle is None:
                return None, score
            return arrow_angle_to_bearing(angle), score
        except Exception as e:
            self.log_warning(f"读取箭头角度失败: {e}")
            return None, 0.0

    def read_heading(self, frame=None, *, min_score=None):
        """读当前朝向（罗盘方位角）。返回 ``(bearing, score)``，置信度不足时 bearing 为 None。

        读朝向**不需要按 W**：小地图箭头一直显示角色当前朝向。
        """
        bearing, score = self._read_arrow(frame)
        limit = (self._cfg_float(CONFIG_MIN_SCORE, DEFAULT_MIN_SCORE)
                 if min_score is None else float(min_score))
        if bearing is None or score < limit:
            return None, score
        return bearing, score

    def yaw_per_pixel(self) -> float:
        """当前旋转系数（度/像素，正数=鼠标右移方位角增大）。"""
        return self._cfg_float(CONFIG_YAW_PER_PIXEL, DEFAULT_YAW_PER_PIXEL)

    def last_turn_result(self) -> dict | None:
        """最近一次 :meth:`turn_to_bearing` 的结果（没转过为 None）。"""
        return self._last_turn_result

    # ------------------------------------------------------------------ #
    # 转到指定方位
    # ------------------------------------------------------------------ #
    def turn_to_bearing(self, target_deg, *, tolerance=5.0, max_rounds=3,
                        frame=None, min_score=None) -> dict:
        """转到指定罗盘方位（正北 0°、正东 90°），闭环逼近到 ±``tolerance``。

        **目标是一轮到位**（只按一次 W，角色只前移一小步）：系数准的话第一轮就该进容差。
        多轮只是兜底——每多一轮就多按一次 W、角色再多前移一小段（约 1~1.5m）。
        一轮没到位时会把本轮的**实测系数**打进日志，据此更新配置即可下次一次到位。

        每轮：算残差 -> 发鼠标位移转视角 -> 等视角转完 -> 按一次 W 让角色转身
        -> 读回朝向校验；没到位就用当前残差再补一轮，最多 ``max_rounds`` 轮。

        Args:
            target_deg: 目标罗盘方位角（度）。
            tolerance: 允许误差（度），默认 5。
            max_rounds: 最多转几轮（兜底用；默认 3）。
            frame: 可选，读初始朝向用的帧；不传则自己抓。
            min_score: 覆盖「朝向最低分数」。

        Returns:
            dict: ``{ok, one_shot, target, heading, error, rounds, history}``

            - ``ok``：最终误差是否在容差内
            - ``one_shot``：是否一轮就到位（``ok`` 且轮数 ≤ 1）
            - ``heading``：最终实测朝向（读不到时为 None）
            - ``error``：最终误差（度，(-180, 180]）
            - ``rounds``：实际转了几轮
            - ``history``：每轮 ``{round, before, error_before, dx, after,
              error_after, ratio}``，``ratio`` 是本轮实测的度/像素（诊断系数是否准）
        """
        tolerance = max(0.0, float(tolerance))
        target = float(target_deg)
        result = {
            "ok": False, "target": target, "heading": None,
            "error": None, "rounds": 0, "history": [],
        }
        self._last_turn_result = result

        per_px = self.yaw_per_pixel()
        if per_px <= 0:
            self.log_warning(
                f"{CONFIG_YAW_PER_PIXEL} 必须为正数（鼠标右移=方位角增大），当前 {per_px}；"
                "请用「鼠标视角旋转系数标定」任务实测后再填")
            return result
        if not self._can_turn():
            return result

        before, score = self.read_heading(frame, min_score=min_score)
        if before is None:
            self.log_warning(f"读不到朝向（score={score:.3f}），无法转向")
            return result

        settle = self._cfg_float(CONFIG_TURN_SETTLE, DEFAULT_TURN_SETTLE)
        for i in range(1, max(1, int(max_rounds)) + 1):
            err = angle_delta(target, before)
            if abs(err) <= tolerance:
                break
            dx = round(err / per_px)
            if dx == 0:
                # 残差不足一个像素：只能这样了
                break

            self._send_rotation(dx)
            self.sleep(settle)
            after, score = self._arrow_after_w(min_score=min_score)

            entry = {
                "round": i, "before": before, "error_before": err, "dx": dx,
                "after": after, "error_after": None, "ratio": None,
            }
            if after is None:
                self.log_warning(f"第 {i} 轮：按 W 后读不到朝向（score={score:.3f}）")
                result["history"].append(entry)
                result["rounds"] = i
                break
            entry["error_after"] = angle_delta(target, after)
            entry["ratio"] = angle_delta(after, before) / dx if dx else None
            self._warn_if_ratio_off(entry["ratio"], per_px, dx)
            result["history"].append(entry)
            result["rounds"] = i
            before = after
            if abs(entry["error_after"]) <= tolerance:
                break

        result["heading"] = before
        result["error"] = angle_delta(target, before)
        result["ok"] = abs(result["error"]) <= tolerance
        # 目标是一轮到位（只按一次 W，角色只前移一小步）；多轮只是兜底
        result["one_shot"] = bool(result["ok"] and result["rounds"] <= 1)
        return result

    def aim_view_to_bearing(self, target_deg, *, tolerance=5.0, max_rounds=2,
                            frame=None, min_score=None) -> dict:
        """只转动视角到指定方位，不按 W，因此不会让角色向前移动。

        滑索场景下小地图仍能提供角色朝向，但没有俯仰角；横向目标可以利用
        世界坐标计算出的方位角先完成粗对准，再交给目标距离 OCR 做精细对中。
        """
        tolerance = max(0.0, float(tolerance))
        target = float(target_deg)
        result = {
            "ok": False,
            "target": target,
            "heading": None,
            "error": None,
            "rounds": 0,
            "history": [],
        }
        self._last_turn_result = result

        per_px = self.yaw_per_pixel()
        if per_px <= 0:
            self.log_warning(
                f"{CONFIG_YAW_PER_PIXEL} 必须为正数，当前 {per_px}；无法按世界坐标对准滑索")
            return result
        if not self._can_turn():
            return result

        before, score = self.read_heading(frame, min_score=min_score)
        if before is None:
            self.log_warning(f"读不到朝向（score={score:.3f}），无法对准滑索")
            return result

        settle = self._cfg_float(CONFIG_TURN_SETTLE, DEFAULT_TURN_SETTLE)
        for i in range(1, max(1, int(max_rounds)) + 1):
            err = angle_delta(target, before)
            if abs(err) <= tolerance:
                break
            dx = round(err / per_px)
            if dx == 0:
                break
            self._send_rotation(dx)
            self.sleep(settle)
            after, score = self.read_heading(min_score=min_score)
            entry = {
                "round": i,
                "before": before,
                "error_before": err,
                "dx": dx,
                "after": after,
                "error_after": None if after is None else angle_delta(target, after),
                "ratio": None,
            }
            if after is None:
                self.log_warning(f"第 {i} 轮：转视角后读不到朝向（score={score:.3f}）")
                result["history"].append(entry)
                result["rounds"] = i
                break
            if dx:
                entry["ratio"] = angle_delta(after, before) / dx
            result["history"].append(entry)
            result["rounds"] = i
            before = after
            if abs(entry["error_after"]) <= tolerance:
                break

        result["heading"] = before
        result["error"] = angle_delta(target, before)
        result["ok"] = abs(result["error"]) <= tolerance
        return result

    def _warn_if_ratio_off(self, ratio, configured: float, dx: int) -> None:
        """实测系数与配置值差得多时给出可执行的提示。

        目标是一轮到位，而一轮能不能到位几乎只取决于系数准不准：
        - 实测明显偏小：位移可能被游戏截断（调小「单次鼠标位移上限」或加大「分段间隔」），
          或者配置系数偏大；
        - 实测明显偏大：配置系数偏小，按实测值更新后就能一次到位。
        小位移的比例被量化噪声主导，不作数。
        """
        if ratio is None or configured <= 0 or abs(ratio) < 1e-9:
            return
        if abs(dx) < RATIO_HINT_MIN_DX:
            return
        rel = abs(ratio) / configured
        if rel < 1.0 - RATIO_HINT_TOLERANCE:
            self.log_warning(
                f"实测系数 {abs(ratio):.5f}°/px 明显低于配置 {configured:.5f}°/px："
                "位移可能被游戏截断（调小「单次鼠标位移上限」或加大「分段间隔」），"
                "或配置系数偏大；修正后即可一次到位")
        elif rel > 1.0 + RATIO_HINT_TOLERANCE:
            self.log_warning(
                f"实测系数 {abs(ratio):.5f}°/px 明显高于配置 {configured:.5f}°/px："
                f"配置系数偏小，改成实测值后即可一次到位")

    # ------------------------------------------------------------------ #
    # 输入
    # ------------------------------------------------------------------ #
    def _can_turn(self) -> bool:
        """后台模式下鼠标位移会被输入接口静默丢弃，这里提前拦下并说明。"""
        try:
            if self.input_mode() == "background":
                self.log_warning("后台模式无法转视角（鼠标位移被禁用），请在全局设置里切前台模式")
                return False
        except Exception:
            pass
        return True

    def _send_rotation(self, dx: int) -> None:
        """发送转视角的鼠标相对位移；过大时拆成多段，避免单次事件被截断。"""
        chunk = max(1, int(self._cfg_float(CONFIG_MOUSE_CHUNK, DEFAULT_MOUSE_CHUNK)))
        delay = max(0.0, self._cfg_float(CONFIG_MOUSE_CHUNK_DELAY, DEFAULT_MOUSE_CHUNK_DELAY))
        remaining = int(dx)
        while remaining:
            step = max(-chunk, min(chunk, remaining))
            self.active_and_send_mouse_delta(dx=step, dy=0, steps=1, delay=0)
            remaining -= step
            if remaining:
                self.sleep(delay)

    def _arrow_after_w(self, *, min_score=None):
        """按一次 W（长按）让角色转到视角方向，等画面刷新后读回朝向。

        必须长按：瞬时按键不足以让角色真正转身，小地图朝向不会刷新，会读到旧角度。
        """
        self.press_key("w", down_time=self._cfg_float(CONFIG_W_HOLD, DEFAULT_W_HOLD))
        self.sleep(self._cfg_float(CONFIG_ANGLE_REFRESH, DEFAULT_ANGLE_REFRESH))
        return self.read_heading(min_score=min_score)
