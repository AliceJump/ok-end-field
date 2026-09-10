# -*- coding: utf-8 -*-
"""小地图转向测试任务：把角色转到指定罗盘方位，误差要求 ±N 度。

每个目标输出一段日志：起始朝向、每轮的残差/鼠标位移/实测朝向/实测系数、
最终误差与 PASS/FAIL。转向能力本身在 ``MinimapHeadingMixin.turn_to_bearing``。

坐标系：方位角为正北 0°、正东 90°、正南 180°、正西 270°（顺时针）。

注意：每轮转向都要按一次 W 让角色转身，角色会向前走一小段（量级 1~1.5m），
所以转多个目标时会累计位移，选点时留出空间。
"""

from qfluentwidgets import FluentIcon

from src.core.BaseEfTask import BaseEfTask
from src.tasks.mixin.minimap_heading_mixin import (
    CONFIG_ANGLE_REFRESH,
    CONFIG_MIN_SCORE,
    CONFIG_MOUSE_CHUNK,
    CONFIG_MOUSE_CHUNK_DELAY,
    CONFIG_TURN_SETTLE,
    CONFIG_W_HOLD,
    CONFIG_YAW_PER_PIXEL,
    DEFAULT_ANGLE_REFRESH,
    DEFAULT_MIN_SCORE,
    DEFAULT_MOUSE_CHUNK,
    DEFAULT_MOUSE_CHUNK_DELAY,
    DEFAULT_TURN_SETTLE,
    DEFAULT_W_HOLD,
    DEFAULT_YAW_PER_PIXEL,
    MinimapHeadingMixin,
)


class MinimapTurnToHeading(BaseEfTask, MinimapHeadingMixin):
    """小地图转向测试（工具与调试分组）。"""

    requires_foreground = True  # 转视角/按 W 需要前台

    # 旋转参数（可通过任务配置覆盖，集中在此处便于统一调整）
    YAW_PER_PIXEL = DEFAULT_YAW_PER_PIXEL            # 度/像素（鼠标右移=方位角增大），由标定任务实测
    W_HOLD_TIME = DEFAULT_W_HOLD                     # 按 W 让角色转身的长按时长（秒）
    TURN_SETTLE_DELAY = DEFAULT_TURN_SETTLE          # 发完鼠标位移后等视角转完（秒）
    ANGLE_REFRESH_DELAY = DEFAULT_ANGLE_REFRESH      # 按 W 后等画面刷新再读朝向（秒）
    MIN_SCORE = DEFAULT_MIN_SCORE                    # 箭头角度检测最低置信度
    MOUSE_CHUNK = DEFAULT_MOUSE_CHUNK                # 单次鼠标位移上限（像素）
    MOUSE_CHUNK_DELAY = DEFAULT_MOUSE_CHUNK_DELAY    # 拆分发送时每段间隔（秒）

    # 测试参数
    TARGET_HEADING = 90.0      # 目标方位角（度）：0=正北 90=正东 180=正南 270=正西
    TARGET_LIST = ""           # 多个目标（逗号分隔，如 "0, 90, 180, 270"）；留空则用单个目标
    TOLERANCE = 5.0            # 允许误差（度）
    MAX_ROUNDS = 3             # 每个目标最多转几轮

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "小地图转向"
        self.group_name = "工具与调试"
        self.group_icon = FluentIcon.DEVELOPER_TOOLS
        self.description = "把角色转到指定罗盘方位（正北0 正东90），闭环逼近到指定误差内"
        self.visible = self.debug

        self._init_minimap_heading_mixin()

        self.default_config = {
            "目标方位角(度)": self.TARGET_HEADING,
            "目标角度列表(逗号分隔)": self.TARGET_LIST,
            "误差容差(度)": self.TOLERANCE,
            "最大轮数": self.MAX_ROUNDS,
            CONFIG_YAW_PER_PIXEL: self.YAW_PER_PIXEL,
            CONFIG_W_HOLD: self.W_HOLD_TIME,
            CONFIG_TURN_SETTLE: self.TURN_SETTLE_DELAY,
            CONFIG_ANGLE_REFRESH: self.ANGLE_REFRESH_DELAY,
            CONFIG_MIN_SCORE: self.MIN_SCORE,
            CONFIG_MOUSE_CHUNK: self.MOUSE_CHUNK,
            CONFIG_MOUSE_CHUNK_DELAY: self.MOUSE_CHUNK_DELAY,
        }
        self.config_description = {
            "目标方位角(度)": "目标罗盘方位角：0=正北 90=正东 180=正南 270=正西",
            "目标角度列表(逗号分隔)": "一次转多个目标（如 0, 90, 180, 270），每个都实测；留空则只用「目标方位角」",
            "误差容差(度)": "最终朝向与目标的允许误差，超过判 FAIL",
            "最大轮数": "每个目标最多转几轮（每轮都会按一次 W，角色会前移一小段）",
            CONFIG_YAW_PER_PIXEL: "转视角系数（度/像素，正数=鼠标右移方位角增大）；"
                                  "由「鼠标视角旋转系数标定」任务实测，取该任务 k 的绝对值",
            CONFIG_W_HOLD: "按 W 让角色转身时的长按时长；太短角色不会真正转身",
            CONFIG_TURN_SETTLE: "发完鼠标位移后、按 W 之前等待视角转完的时间",
            CONFIG_ANGLE_REFRESH: "按 W 之后等待画面刷新再读朝向的时间",
            CONFIG_MIN_SCORE: "箭头角度检测最低置信度，低于该值朝向判为不可用",
            CONFIG_MOUSE_CHUNK: "单次鼠标相对位移的上限（像素），超过则拆成多次发送",
            CONFIG_MOUSE_CHUNK_DELAY: "拆分发送时每段之间的间隔（秒）",
        }

    # ------------------------------------------------------------------ #
    # 主流程
    # ------------------------------------------------------------------ #
    def run(self):
        self.log_info("=== Minimap Turn To Heading ===", notify=True)
        if not self.in_world():
            self.log_info("当前不在大世界画面，读不到小地图朝向。请先进入大世界。", notify=True)
            return

        targets = self._parse_targets(
            str(self.config.get("目标角度列表(逗号分隔)", self.TARGET_LIST)),
            float(self.config.get("目标方位角(度)", self.TARGET_HEADING)),
        )
        tolerance = max(0.0, float(self.config.get("误差容差(度)", self.TOLERANCE)))
        max_rounds = max(1, int(self.config.get("最大轮数", self.MAX_ROUNDS)))
        per_px = self.yaw_per_pixel()

        self.log_info(
            f"参数: 目标={targets}  容差=±{tolerance:.1f}°  最大轮数={max_rounds}  "
            f"yaw_per_pixel={per_px}°/px"
        )
        if per_px <= 0:
            self.log_warning("yaw_per_pixel 必须为正数，请先跑「鼠标视角旋转系数标定」任务", notify=True)
            return

        results = []
        for target in targets:
            results.append(self._turn_one(target, tolerance, max_rounds))

        ok_count = sum(1 for r in results if r["ok"])
        one_shot = sum(1 for r in results if r["one_shot"])
        self.log_info(
            f"===== 转向汇总: {ok_count}/{len(results)} 达标，其中一次到位 {one_shot}/{len(results)} "
            f"(容差 ±{tolerance:.1f}°) =====",
            notify=ok_count == len(results),
        )

    def _turn_one(self, target: float, tolerance: float, max_rounds: int) -> dict:
        """转到一个目标并输出逐轮日志。返回 turn_to_bearing 的结果 dict。"""
        start, start_score = self.read_heading()
        self.log_info(
            f"[转向] 目标={target:.2f}°  起始={('%0.2f°' % start) if start is not None else f'读不到(score={start_score:.3f})'}"
        )
        res = self.turn_to_bearing(target, tolerance=tolerance, max_rounds=max_rounds)

        for e in res["history"]:
            after_txt = f"{e['after']:.2f}°" if e["after"] is not None else "读不到"
            residual_txt = f"{e['error_after']:+.2f}°" if e["error_after"] is not None else "-"
            ratio_txt = f"  实测系数={e['ratio']:.5f}°/px" if e["ratio"] is not None else ""
            self.log_info(
                f"  第{e['round']}轮: 残差={e['error_before']:+.2f}°  dx={e['dx']:+d}  "
                f"-> 实测={after_txt}  残差={residual_txt}{ratio_txt}"
            )

        if res["heading"] is None:
            self.log_warning(f"  无法读取朝向，转向失败（目标 {target:.2f}°）", notify=True)
            return res
        if res["one_shot"]:
            way = "一次到位"
        elif res["ok"]:
            way = f"补正 {res['rounds'] - 1} 次"
        else:
            way = "未到位"
        self.log_info(
            f"  {'PASS' if res['ok'] else 'FAIL'}: 最终={res['heading']:.2f}°  "
            f"误差={res['error']:+.2f}°  轮数={res['rounds']}（{way}；"
            f"角色前移约 {res['rounds']} 小步）",
            notify=res["ok"],
        )
        return res

    @staticmethod
    def _parse_targets(raw: str, single: float) -> list[float]:
        """解析目标角度列表；留空时退化为单个目标。"""
        text = str(raw or "").strip()
        if not text:
            return [float(single)]
        out = []
        for part in text.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                out.append(float(part) % 360.0)
            except ValueError:
                continue
        return out or [float(single)]
