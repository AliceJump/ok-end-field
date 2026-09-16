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
- 朝向：罗盘方位角（°；正北 0°、正东 90°、顺时针）；低置信时标注 (低置信)。
- 速度：当前移动速度（米/秒，世界系，与静止判定同一个量）。
- 状态：静止 / 移动 / ?(原因)（问号 = 本拍没有有效位移样本，括号里是原因，例如 anchor_init /
  low_response / exceed_max_shift；这些拍的位移被丢弃，是定位漂移的主要来源）。
  静止 = 小地图与 WS 同时显示没动，此时由 WS 校准、里程计清零。
- 误差：位置与"最新 WS 坐标"的距离（米）。静止校准后≈0；移动时≈WS 延迟，非误差。
- 最新WS / 位移：最近收到的官方地图坐标(可能滞后) / 里程计原始位移(地图系像素)。

坐标系：位置/WS 是"世界系"(x, z)；位移是"地图系"(图像 x 右、y 下，北向上)，
世界_z 与地图_y 符号相反（由矩阵处理）。用于验证"朝向 + 位移里程计 + WS 融合"
整条实时定位链路；不写入正式配置。

定位能力由 ``MinimapPositionTask`` 统一维护，本任务只负责采样与日志呈现。
"""

from qfluentwidgets import FluentIcon

from src.core.BaseEfTask import BaseEfTask
from src.tasks.mixin.minimap_heading_mixin import CONFIG_MIN_SCORE
from src.tasks.trigger.MinimapPositionTask import MinimapPositionTask


class MinimapRealtimePosition(BaseEfTask):
    """小地图实时位置测试（工具与调试分组）。"""

    requires_foreground = True  # 需要读取游戏画面/小地图

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "小地图实时位置"
        self.group_name = "工具与调试"
        self.group_icon = FluentIcon.DEVELOPER_TOOLS
        self.description = "每个采样拍输出 朝向 + 小地图计算位置 + 结合WS的融合位置"
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

        position_task = self.get_task_by_class(MinimapPositionTask)
        if position_task is None:
            self.log_warning("未注册「小地图定位」触发任务，无法读取实时位置", notify=True)
            return
        if not getattr(position_task, "enabled", True):
            self.log_warning("「小地图定位」触发任务未启用，无法读取实时位置", notify=True)
            return
        position_task.start_minimap_position(wait_stable=False)

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
            "字段说明: "
            "位置=融合后的实时坐标(x,z,米) | 朝向=罗盘方位角(°; 正北0 正东90 顺时针) | "
            "速度=移动速度(米/秒) | 状态=静止/移动/?(静止=小地图与WS都没动, 此时由WS校准; ?(原因)=本拍没有有效位移样本) | "
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

            try:
                frame = self.next_frame()
            except Exception as e:
                self.log_warning(f"next_frame 失败: {e}")
                continue
            if frame is None:
                continue

            # 采样一拍：里程计 + 可能的待定 WS 校准 + 同一帧的朝向
            st = position_task.minimap_position(frame=frame, now=now)

            if iteration % log_every != 0:
                continue

            # ---- 静止校准：输出校准前"小地图推算的坐标"与偏差 ----
            residual = st.get("sync_residual") if st.get("just_synced") else None
            if residual is not None:
                self.log_info(
                    f"[静止校准] #{iteration:03d} 小地图推算="
                    f"({residual['map_x']:.2f}, {residual['map_z']:.2f})m "
                    f"WS=({residual['ws_x']:.2f}, {residual['ws_z']:.2f})m "
                    f"偏差=({residual['dx']:+.2f}, {residual['dz']:+.2f})m "
                    f"距离={residual['dist']:.2f}m"
                )

            self.log_info(self._format_tick(
                iteration,
                st,
                min_score,
                position_task.minimap_rest_diag(),
            ))

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

        # 误差（位置与最新 WS 的距离）与两个原始量
        err = st.get("error")
        err_txt = f"{err:.2f}m" if err is not None else "-"
        ws = st.get("ws")
        ws_txt = f"({ws[0]:.2f}, {ws[1]:.2f})" if ws is not None else "-"
        od_px = st.get("dmap_px") or (0.0, 0.0)
        od_txt = f"({od_px[0]:.1f}, {od_px[1]:.1f})px"

        return (
            f"[实时位置] #{iteration:03d} | 位置={pos_txt} 朝向={heading_txt} "
            f"速度={speed_txt} 状态={rest_txt} | "
            f"误差={err_txt} 最新WS={ws_txt} 位移={od_txt}"
        )
