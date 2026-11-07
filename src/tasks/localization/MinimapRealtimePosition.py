"""按需采样共享世界定位服务，输出视觉或抓包坐标、朝向和可信状态。

视觉源独立匹配标定地图并运行视觉锚定链；抓包源直接提供绝对坐标。
本任务只负责诊断呈现，不把另一来源的坐标当成视觉锚点。
"""

from qfluentwidgets import FluentIcon

from src.core.BaseEfTask import BaseEfTask
from src.localization.minimap_heading_mixin import CONFIG_MIN_SCORE
from src.tasks.mixin.runtime_state_mixin import RuntimeStateMixin


class MinimapRealtimePosition(RuntimeStateMixin, BaseEfTask):
    """小地图实时位置测试（工具与调试分组）。"""

    requires_foreground = True  # 需要读取游戏画面/小地图

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._init_runtime_state_mixin()
        self.name = "小地图实时位置"
        self.group_name = "工具与调试"
        self.group_icon = FluentIcon.DEVELOPER_TOOLS
        self.description = "逐拍输出视觉或抓包位置、朝向、速度和可信状态"
        self.visible = self.debug

        self.default_config = {
            "采样间隔(秒)": 0.5,
            "运行时长(秒)": 20.0,
            "日志间隔(拍)": 1,
        }
        self.config_description = {
            "采样间隔(秒)": "固定采样周期(秒)：睡到下一个采样点，保证每拍间隔为该值（不会被单拍工作耗时撑大）",
            "运行时长(秒)": "本次采样运行的秒数",
            "日志间隔(拍)": "每几拍输出一行日志（1=每拍都输出）",
        }

    # ------------------------------------------------------------------ #
    # 主流程
    # ------------------------------------------------------------------ #
    def run(self):
        self.log_info("=== Minimap Realtime Position ===", notify=True)
        if not self.in_world():
            self.log_info("当前不在大世界画面，无法读取小地图。请先进入大世界。", notify=True)
            return

        position_task = self.ensure_runtime_position_service(force_start=True)
        if position_task is None:
            return

        interval = max(0.05, self._cfg_float("采样间隔(秒)", 0.5))
        duration = max(interval, self._cfg_float("运行时长(秒)", 20.0))
        log_every = max(1, self._cfg_int("日志间隔(拍)", 1))
        min_score = max(
            0.0,
            min(1.0, float(position_task.config.get(CONFIG_MIN_SCORE, 0.6))),
        )

        start = self.active_time()
        next_at = start
        iteration = 0
        overruns = 0
        max_period = 0.0
        prev_tick = None
        fusion = position_task.minimap_fusion
        self.log_info(
            f"开始实时采样: interval={interval:.2f}s duration={duration:.1f}s "
            f"scale={getattr(position_task, '_minimap_scale', 0.0)} "
            f"matrix={fusion.map_to_world_px if fusion is not None else None}"
        )
        self.log_info(
            "字段说明: 位置=世界坐标(x,z,米) | 来源=视觉/抓包 | 分层=已确认楼层或未知 | NCC=视觉匹配置信度 | 位移=地图系像素"
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

            try:
                frame = self.next_frame()
            except Exception as e:
                self.log_warning(f"next_frame 失败: {e}")
                continue
            if frame is None:
                continue

            # 采样独立位置源与同一帧的朝向
            st = self.world_pose(
                frame=frame,
                max_age=max(0.5, interval * 2.0),
                now=now,
            )
            if st is None:
                self.log_warning("运行时定位状态不可用或已过期")
                continue

            if iteration % log_every != 0:
                continue

            self.log_info(
                self._format_tick(
                    iteration,
                    st,
                    min_score,
                    position_task.minimap_rest_diag(),
                )
            )

        elapsed = self.active_time() - start
        avg = elapsed / iteration if iteration > 0 else 0.0
        self.log_info(
            f"实时采样结束: {iteration} 拍 / {elapsed:.1f}s，"
            f"目标周期 {interval:.2f}s，实际平均 {avg:.3f}s，"
            f"最大周期 {max_period:.3f}s，超时重对齐 {overruns} 次",
            notify=True,
        )

    def _format_tick(
        self,
        iteration: int,
        st: dict,
        min_score: float,
        rest_diag: dict | None,
    ) -> str:
        """把一拍的位置状态组装成一行可读日志。"""
        heading = st.get("heading")
        heading_score = st.get("heading_score")
        fused_x = st.get("x")
        fused_z = st.get("z")

        # 位置（融合后世界坐标）
        pos_txt = f"({fused_x:.2f}, {fused_z:.2f})m" if fused_x is not None else "无(未锚定)"

        # 朝向（得分低于阈值时提示低置信）
        heading_txt = f"{heading:.1f}°" if heading is not None else "?"
        if heading_score is not None and heading_score < min_score:
            heading_txt += "(低置信)"

        # 速度（米/秒）：取静止判定用的同一个量（世界系位移/时间），单位与阈值一致
        spd = (rest_diag or {}).get("map_speed_m_s")
        speed_txt = f"{spd:.2f}m/s" if spd is not None else "-"
        # 本拍没有有效的位移样本时状态是"未知"，打 "?" 并附上原因（刚建锚帧/相关失败/…），
        # 别把"不知道"显示成"在动"；原因也能直接指出"位移被丢掉"的时刻。
        if not st.get("odom_ok"):
            rest_txt = f"?({st.get('odom_reason') or 'no_data'})"
        else:
            rest_txt = "静止" if st.get("rest") else "移动"

        confidence = st.get("visual_confidence")
        confidence_txt = f"{confidence:.3f}" if confidence is not None else "-"
        tier = st.get("tier_id") if st.get("tier_trusted") else "未知"
        od_px = st.get("dmap_px") or (0.0, 0.0)
        od_txt = f"({od_px[0]:.1f}, {od_px[1]:.1f})px"

        return (
            f"[实时位置] #{iteration:03d} | 位置={pos_txt} 朝向={heading_txt} "
            f"速度={speed_txt} 状态={rest_txt} | "
            f"来源={st.get('source')} 可信={bool(st.get('position_trusted'))} 分层={tier} NCC={confidence_txt} 位移={od_txt}"
        )
