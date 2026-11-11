"""通过项目正式任务、定位服务与网格导航执行精度验证。"""  # 不实现第二套行走控制

import copy  # 隔离本次测试的运行配置
import json  # 保存逐帧原始记录
import math  # 计算独立参考误差
import threading  # 在导航阻塞期间仍能响应 F8
import time  # 同时记录墙钟和运行时限

import cv2  # 仅保存脱敏 HUD 诊断图
import numpy as np  # 枚举有限的可达网格

from scripts.nav.visual_validation import (
    NavigationSourcePolicy,
    ReferenceTimeline,
    align_reference,
    connected_region,
    navigation_pose_ready,
    nearby_cells,
    temporary_obstacle_grid,
)  # 复用纯计算测试工具
from src.core.GridNavConfig import (  # 使用项目配置键
    CONFIG_GRID_ALLOW_UNKNOWN,
    CONFIG_GRID_FILE,
    CONFIG_GRID_USE_ZIP_LINES,
)
from src.core.PositionConfig import CONFIG_EXPERIMENTAL_PORT, CONFIG_USE_EXPERIMENTAL  # 保持视觉观测独立，仅在内存覆盖
from src.nav.grid_io import save_grid  # 保存本次临时规划副本供复核
from src.nav.route_follower import GridRouteFollower  # 复用正式多目标路径代价计算
from src.runtime_state.world_pose import publish_world_pose  # 用项目状态契约发布选定的导航来源
from src.tasks.navigation.MinimapNavigateToPoint import MinimapNavigateToPoint  # 复用正式导航任务


def pose_scope_status(state, map_id):
    """Classify whether a visual pose is usable for this main-map validation."""

    if state.get("map_id") != map_id:
        return "map_changed"
    tier_id = state.get("tier_id")
    tier_trusted = bool(state.get("tier_trusted"))
    tier_required = bool(state.get("tier_evidence_required", True))
    if tier_required and (not tier_trusted or tier_id is None):
        return "tier_pending"
    if tier_trusted and tier_id not in (None, "__main__"):
        return "tier_changed"
    return "record"


class GridValidationTask(MinimapNavigateToPoint):  # 复用 GridNavigationMixin 与 EfInteraction
    validation = None  # 由命令行入口在启动前注入本次参数
    stop_reason = ""  # 不在任务未运行时启用测试限制
    navigation_running = False  # 仅记录当前正式导航调用的警告

    def log_warning(self, message, *args, **kwargs):  # 保留生产失败原因供结构化报告使用
        if self.navigation_running:  # 不把测试自身的诊断覆盖进原始原因
            self.last_navigation_warning = str(message)  # 保存最后一条项目警告
        return super().log_warning(message, *args, **kwargs)  # 生产日志行为不变

    def load_grid_for_map(self, map_id="", **kwargs):  # 复用正式规划器，仅替换本次测试网格数据
        if hasattr(self, "navigation_grid") and map_id == self.grid.meta.map_name:  # 只覆盖当前主图
            return self.navigation_grid  # 原网格文件保持不变
        return super().load_grid_for_map(map_id, **kwargs)  # 其他情况沿用项目加载逻辑

    def _grid_nav_state_stuck(self, ctx):  # 观察生产脱困最终结果，不改变其策略
        result = super()._grid_nav_state_stuck(ctx)  # 完整执行原有恢复次数
        if result is False:  # 只有恢复耗尽才记录通行障碍
            self.navigation_failure = {
                "position": ctx.position,
                "waypoint": ctx.step.waypoint,
                "reason": ctx.step.reason,
                "recoveries": ctx.session.recoveries,
                "obstructed": True,
            }  # 保存实际受阻证据
        return result  # 保留正式状态机返回值

    def _save_obstacles(self):  # 每次失败立即落盘，支持再次续跑
        from scripts.nav.validate_visual_grid import write_json  # 使用原子写入

        self.navigation_grid = temporary_obstacle_grid(
            self.grid, self.failures if self.options.skip_blocked else []
        )  # 只创建本次副本
        self.target_planner = GridRouteFollower(
            self.navigation_grid, self._grid_follower_config()
        ).planner  # 复用生产规划配置
        write_json(self.directory / "blocked_points.json", self.failures)  # 记录位置与具体原因
        write_json(self.directory / "skipped_cells.json", sorted(self.skipped))  # 未验证区域单列
        write_json(self.directory / "covered_cells.json", sorted(self.covered))  # 失败后也保存已完成部分
        write_json(self.directory / "traversed_cells.json", sorted(self.traversed))  # 抓包经过范围不冒充视觉覆盖
        save_grid(self.directory / "temporary.grid.npz", self.navigation_grid)  # 可检查临时避让限制

    def _skip_failed_goal(self, point):  # 将失败转为有限范围的暂时跳过
        self._stop_inputs()  # 确保选择下个目标前已经松键
        failure = {
            "position": point,
            "reason": "project_navigation_failed",
            "obstructed": False,
            **self.navigation_failure,
            "goal": self.goal,
            "time": time.time(),
            "source_run": str(self.directory.resolve()),
        }  # 超时不伪装已确认障碍
        self.failures.append(failure)  # 保留每次失败而非覆盖同坐标记录
        self.skipped.update(
            nearby_cells(self.region, self.grid.index_of_world(*self.goal), self.coverage_steps)
        )  # 目标附近暂缓验证
        if self.last_frame is not None:  # 附带脱敏定位画面
            filename = f"blocked_{len(self.failures):04d}_hud.png"  # 与记录序号对应
            cv2.imwrite(str(self.directory / filename), self.last_frame[:280, :340])  # 不保存账户区域
            failure["hud"] = filename  # 可从日志直接找到截图
        self._save_obstacles()  # 更新临时路网供正式规划器绕行
        self.log_warning("验证点暂时跳过：" + json.dumps(failure, ensure_ascii=False))  # 明确下一步继续

    def _grid_nav_config(self):  # 不修改用户持久化设置
        config = dict(super()._grid_nav_config() or self.grid_navigation_default_config())  # 使用正式默认配置
        if self.validation is not None:  # 验证仅限制路径范围
            config.update(
                {
                    CONFIG_GRID_ALLOW_UNKNOWN: False,
                    CONFIG_GRID_USE_ZIP_LINES: False,
                    CONFIG_GRID_FILE: str(self.validation[0].grid.resolve()),
                }
            )  # 排除不连通跳转
        return config  # 其余导航、转向与恢复参数原样复用

    def _stop_inputs(self):  # 清理仍使用项目已有输入后端
        self._set_grid_walking(False)  # 调用正式导航松键逻辑
        interaction = self.executor.interaction  # 与实际导航共用同一个后端
        for key, count in list(getattr(interaction, "_pressed_keys", {}).items()):  # 只释放本后端实际按下的键
            for _ in range(count):  # 保持配对计数
                interaction.send_key_up(key)  # EfInteraction 负责失焦时安全松键

    def _check_stop(self):  # 任务可中断的等待点与导航观测点共用此检查
        if self.stop_reason:  # F8、失焦或误差保护已触发
            self._stop_inputs()  # 先保证停止移动
            raise RuntimeError(self.stop_reason)  # 由本任务保存部分结果

    def sleep(self, timeout):  # 保留框架的暂停、禁用与画面刷新行为
        self._check_stop()  # 等待前检查现场停止请求
        super().sleep(timeout)  # 不绕过任务生命周期
        self._check_stop()  # 等待后再次检查

    def _watch(self):  # 不实现输入，仅在紧急情况触发生产松键
        import win32api  # 读取用户紧急停止键
        import win32gui  # 读取实际前台窗口

        while not self.guard_done.wait(0.05):  # 有明确结束事件
            if win32api.GetAsyncKeyState(0x77) & 0x8000 or (self.directory / "STOP").exists():  # F8 或停止文件
                self.stop_reason = "manual_stop"  # 用户停止优先
            if (
                self.movement_started
                and win32gui.GetForegroundWindow() != self.executor.device_manager.hwnd_window.hwnd
            ):  # 不在其他应用中继续导航
                self.stop_reason = "game_not_foreground"  # 留下明确停止原因
            if time.monotonic() > self.deadline:  # 整个区域测试有总时限
                self.stop_reason = "time_limit"  # 不隐藏部分覆盖
            if self.stop_reason:  # 即使主线程正在匹配也先松键
                self._stop_inputs()  # 复用同一输入后端的配对释放

    def _observe(self, state, navigation_source="visual_map"):  # 始终只记录独立视觉定位结果
        self._check_stop()  # 用户请求停止后不再发布新动作
        latest, checked, _ = self.reference.snapshot()  # 对照源不参与定位器输入
        if latest is None or time.time() - checked > 1.5:  # 活连接与后端健康都是必要条件
            self.stop_reason = "reference_unavailable_or_dead_flow"  # 不用旧会话当真值
            self._check_stop()  # 停下并留存现场
        stamp = float(state.get("frame_t") or 0)  # 使用生产服务的 WGC 呈现时间
        row = {
            "frame_t": stamp,
            "observed_at": time.time(),
            "frame_age_s": time.time() - stamp,
            "pose": dict(state),
            "absolute": self.absolute,
            "navigation_source": navigation_source,  # 区分独立视觉观测与实际控制来源
            "flow": latest.get("flow"),
            "stationary_reference": not self._grid_nav_w_held
            and float(latest.get("speed") or 0) <= 0.1
            and float(latest.get("time") or 0) <= stamp,
        }  # 静止保留独立标记
        self.samples.append(row)  # 包括失败帧用于可用率统计
        self.sample_log.write(json.dumps(row, ensure_ascii=False) + "\n")  # 原始证据不因停止丢失
        self.sample_log.flush()  # 每帧落盘
        if latest.get("mapId") != self.grid.meta.map_name:
            # Reference identity is a run invariant, independent of visual readiness.
            self.stop_reason = "map_or_tier_changed"
            self._check_stop()
        if not state.get("position_trusted") or state.get("visual_duplicate_frame"):  # 失败帧不能算覆盖
            return  # 正式导航负责停车与重新定位
        if state.get("frame_time_source") != "wgc_system_relative_time":  # 本测试需要准确时间轴
            self.stop_reason = "missing_wgc_presentation_timestamp"  # 不用采样结束时间伪装图像时间
            self._check_stop()  # 停止不可靠的精度验证
        scope = pose_scope_status(state, self.grid.meta.map_name)
        if scope == "tier_pending":  # 未知楼层不是换层，等待可靠楼层证据
            return
        if scope != "record":
            self.stop_reason = "map_or_tier_changed"  # 保留地图身份
            self._check_stop()  # 不跨场景继续
        point = (float(state["x"]), float(state["z"]))  # 视觉验证覆盖只读取视觉输出
        observed_cells = nearby_cells(self.region, self.grid.index_of_world(*point), self.coverage_steps)  # 不跨墙计数
        self.covered.update(observed_cells)  # 可信视觉独立覆盖
        self.traversed.update(observed_cells)  # 总遍历包含所有可信视觉观测
        if (
            abs(stamp - float(latest.get("time") or 0)) <= 0.5 or row["stationary_reference"]
        ):  # 原始最新值仅用于误差保护
            distance = math.dist(point, (latest["x"], latest["z"]))  # 不回灌真值修正定位
            self.error_hits = self.error_hits + 1 if distance > self.options.max_error else 0  # 避免单帧时差误停
            if self.error_hits >= 3:  # 连续偏差才停止
                self.stop_reason = f"sustained_position_error: {distance:.2f}m"  # 写明实测偏差
                self._check_stop()  # 不带着持续错误继续跑

    def _write_progress(self, point):  # 视觉失败时仍报告抓包续跑进度
        if time.monotonic() - self.last_progress_log >= 5:  # 进度日志限频
            from scripts.nav.validate_visual_grid import write_json  # 复用原子结果写入

            progress = {
                "status": "running",
                "covered_cells": len(self.covered),
                "total_cells": len(self.region_cells),
                "coverage_fraction": len(self.covered) / len(self.region_cells),
                "traversed_cells": len(self.traversed),  # 总测试经过范围
                "traversal_fraction": len(self.traversed) / len(self.region_cells),  # 不等于视觉验证通过率
                "visual_unavailable_cells": len(self.traversed - self.covered),  # 保留未验证区域
                "navigation_source": self.route_source,  # 当前实际控制来源
                "capture_navigation_frames": self.capture_frames,  # 抓包接管次数独立统计
                "arrived_goals": len(self.arrived),
                "samples": len(self.samples),
                "position": point,
                "goal": self.goal,
                "blocked_events": len(self.failures),  # 受阻次数与成功覆盖分开
                "skipped_uncovered_cells": len(self.skipped - self.covered),  # 不计入完成率
                "deferred_disconnected_cells": len(self.deferred - self.covered),  # 临时限制导致暂不可达
            }  # 明确实际覆盖进度
            write_json(self.directory / "progress.json", progress)  # 支持外部监控
            self.log_info(json.dumps(progress))  # 进入正式任务日志
            self.last_progress_log = time.monotonic()  # 推进日志时间

    def _navigation_pose(self, service, visual):  # 复用项目抓包状态生产器，不向视觉匹配器传递真值
        from scripts.nav.validate_visual_grid import write_json  # 记录每次来源切换

        source = self.source_policy.choose(visual)  # 连续失败才接管，连续恢复才切回
        selected = dict(
            service._capture_world_pose() if source == "capture" else visual
        )  # 使用项目现有数据源与信任检查
        if source == "capture":  # 静止转镜头时角色抓包朝向可能不更新
            selected.update(
                heading=visual.get("heading"), heading_score=visual.get("heading_score"), heading_source="visual_arrow"
            )  # 复用生产实时箭头识别与原有置信度门槛
        if selected.get("position_trusted") and selected.get("map_id") != self.grid.meta.map_name:  # 不跨地图继续测试
            self.stop_reason = "navigation_map_changed"  # 不猜测坐标空间
            self._check_stop()  # 先停止输入
        if source != self.route_source:  # 只在切换时写事件和截图
            event = {
                "time": time.time(),
                "from": self.route_source,
                "to": source,
                "visual_reason": visual.get("trust_reason"),
                "visual_pose": visual,
                "navigation_position": [selected.get("x"), selected.get("z")],
            }  # 记录故障现场与恢复
            self.source_events.append(event)  # 不隐藏反复失败
            if source == "capture" and self.last_frame is not None:  # 留存需要后续研究的密集图标场景
                event["hud"] = f"visual_fallback_{len(self.source_events):04d}.png"  # 截图对应事件
                cv2.imwrite(str(self.directory / event["hud"]), self.last_frame[:280, :340])  # 不含账户标识
            write_json(self.directory / "source_events.json", self.source_events)  # 原子保存接管记录
            self.log_info("验证导航来源切换：" + json.dumps(event, ensure_ascii=False))  # 控制来源透明可见
            self.route_source = source  # 更新实际来源
        self._observe(visual, source)  # 失败视觉帧仍进入精度和可用率原始记录
        token = (source, selected.get("sync_seq", 0))  # 来源或真实锚点变化才刷新导航序号
        if token != self.route_token:  # 避免每个抓包点都让导航作废历史路径
            self.route_seq += 1  # 单调发布切换序号
            self.route_token = token  # 保存原始来源的锚点标记
        selected.update(sync_seq=self.route_seq, source=source, sample_t=visual["sample_t"])  # 复用当前采样时钟
        if selected.get("position_trusted"):  # 总遍历以实际用于导航的可信状态计数
            point = (selected["x"], selected["z"])  # 不将抓包点写进视觉结果
            self.traversed.update(
                nearby_cells(self.region, self.grid.index_of_world(*point), self.coverage_steps)
            )  # 原始连通区域内计数
        else:  # 无可用来源时交给项目等待逻辑
            point = None  # 不沿用旧坐标
        if source == "capture":  # 抓包控制证据单独落盘
            self.capture_frames += 1  # 不计入视觉成功帧
            self.capture_log.write(json.dumps({"time": time.time(), "pose": selected}) + "\n")  # 保存来源和信任状态
            self.capture_log.flush()  # 及时落盘
        service._minimap_last_state = dict(selected)  # 其他导航消费者读取同一个选定状态
        publish_world_pose(
            service.runtime_state_hub, selected, source=source, now=selected["sample_t"]
        )  # 用项目正式发布契约
        self._write_progress(point)  # 两种来源下都可监控
        return selected  # 原导航状态机无需修改

    def run(self):  # 任务只编排验证点，不实现行走状态机
        from scripts.nav.validate_visual_grid import (  # 共用记录与统计工具
            ReferenceRecorder,
            report_accuracy,
            write_json,
        )

        self.options, self.grid, self.region, targets, self.directory, plan = self.validation  # 获取本次有限计划
        self.samples, self.covered, self.arrived = [], set(), []  # 保留真实采样进度
        self.covered.update(self.options.resume_covered)  # 续跑仅继承已验证覆盖
        self.traversed = set(self.options.resume_traversed) | self.covered  # 总遍历范围包括历史视觉覆盖
        self.source_policy = NavigationSourcePolicy(self.options.capture_fallback)  # 只有显式授权才接管
        self.source_events, self.route_source, self.route_token, self.route_seq, self.capture_frames = (
            [],
            None,
            None,
            0,
            0,
        )  # 来源切换单独计数
        self.failures, self.skipped = (
            list(self.options.resume_failures),
            set(self.options.resume_skipped),
        )  # 继承失败证据
        self.deferred, self.navigation_failure = set(), {}  # 当前暂不可达范围独立统计
        self.navigation_failures, unready_since, transient_failures = [], None, 0  # 定位失败不混入受阻点
        self.region_cells = set(map(tuple, np.argwhere(self.region).tolist()))  # 完成条件是有限分量全覆盖
        self.coverage_steps = math.floor(self.options.spacing / self.grid.meta.cell_size)  # 与规划定义一致
        if self.options.skip_blocked:  # 将旧失败目标加入本次跳过范围
            for failure in self.failures:  # 历史失败不需要重复尝试
                self.skipped.update(
                    nearby_cells(self.region, self.grid.index_of_world(*failure["goal"]), self.coverage_steps)
                )  # 使用相同覆盖半径
        self._save_obstacles()  # 启动前保留完整续跑证据
        self.goal, self.absolute, self.last_frame = None, None, None  # 尚无采样与目标
        self.last_progress_log, self.error_hits = 0.0, 0  # 初始化诊断节流
        self.movement_started = False  # 只在发起导航后检查前台失焦
        self.deadline = time.monotonic() + self.options.seconds  # 整体任务时间限制
        started = time.monotonic()  # 统计实测耗时
        self.guard_done = threading.Event()  # 管理紧急停止观察线程
        self.reference = ReferenceRecorder(self.options.port, self.directory)  # 独立只读后端
        self.sample_log = (self.directory / "visual.jsonl").open("w", encoding="utf-8")  # 独立保存视觉输出
        self.capture_log = (self.directory / "capture_navigation.jsonl").open("w", encoding="utf-8")  # 不混入视觉证据
        service = self.get_runtime_position_service()  # 使用正式 WorldPoseService
        original_config = service.config  # 退出后恢复本实例的定位配置
        service.config = {
            **dict(service.config),
            CONFIG_USE_EXPERIMENTAL: False,
            CONFIG_EXPERIMENTAL_PORT: self.options.port,
        }  # 独立视觉链保持开启，抓包仅作为已授权导航后备
        original_sample, original_locate = (
            service.sample_world_pose,
            service._locate_visual_frame,
        )  # 保存原始行为以便恢复

        def observe_locate(frame, stamp, timing):  # 只截取绝对匹配证据，不改候选与阈值
            self.last_frame, self.absolute = frame, None  # 防止沿用旧匹配记录
            tracker = service._visual_source.tracker  # 使用服务实际持有的定位器
            original_update = tracker.update  # 保留正式跟踪器行为

            def observe_update(*args, **kwargs):  # 本实例的只读观察器
                result = original_update(*args, **kwargs)  # 原样执行跟踪与确认
                self.absolute = result.position.to_dict() if result.position is not None else None  # 区分绝对与融合结果
                return result  # 不改变定位返回值

            tracker.update = observe_update  # 临时绑定到本次采样
            try:  # 即使定位异常也恢复方法
                return original_locate(frame, stamp, timing)  # 生产视觉帧校验和融合照常执行
            finally:  # 不把观察器遗留到退出后的服务
                tracker.update = original_update  # 恢复方法

        def observe_sample(*args, **kwargs):  # 导航所有 world_pose 采样都统一记录
            state = original_sample(*args, **kwargs)  # 不另建视觉定位链
            return self._navigation_pose(service, state)  # 持续记录视觉并选择用户授权的导航来源

        service._locate_visual_frame, service.sample_world_pose = observe_locate, observe_sample  # 仅替换本实例观察入口
        guard = threading.Thread(target=self._watch, daemon=True)  # 独立响应用户停止
        guard.start()  # 开始有限任务保护
        status, reason = "incomplete", "time_limit"  # 不能把未完成当作完成
        try:  # 保证导航失败也产生报告
            if self.options.activate_game and self.options.mode == "run":  # 仅在用户授权的启动阶段激活
                self.ensure_in_front()  # 复用框架窗口接口，不发送模拟按键抢焦点
                self.wait_until(lambda: self.hwnd.is_foreground(), time_out=5)  # 等待窗口确实进入前台
            self.ensure_runtime_position_service(force_start=True)  # 保留正式服务生命周期
            remaining = set(targets)  # 未进入实际覆盖范围的验证点
            while time.monotonic() < self.deadline:  # 有限连通区域与时间上限
                self._check_stop()  # 外部停止优先
                pose = self.world_pose(frame=self.next_frame(), max_age=0.6)  # 使用项目正式姿态入口
                if not navigation_pose_ready(pose):  # 初次坐标与楼层均必须经过原确认门槛
                    unready_since = time.monotonic() if unready_since is None else unready_since  # 记录连续不可用起点
                    if time.monotonic() - unready_since > 30:  # 持续不可定位时不能盲目换目标
                        reason = "visual_navigation_unavailable: " + str(
                            (pose or {}).get("trust_reason", "no_pose")
                        )  # 说明真实阻断原因
                        latest, _, _ = self.reference.snapshot()  # 只用于标记诊断地点，不控制导航
                        write_json(
                            self.directory / "localization_failure.json",
                            {"reason": reason, "pose": pose, "reference_diagnostic_only": latest, "time": time.time()},
                        )  # 保留分数和失败位置
                        break  # 保存进度，不能把定位故障当作道路受阻
                    self.sleep(0.2)  # 框架自动刷新并保持可停止
                    continue  # 等待可信初始点
                unready_since = None  # 完整恢复后重新计时
                if self.options.mode == "record":  # 只观察时完全不调用导航
                    self.sleep(0.15)  # 保持稳定采样
                    continue  # 可由用户手动移动
                if len(self.traversed) == len(self.region_cells):  # 总遍历完成不等于视觉全通过
                    status = (
                        "completed" if self.traversed == self.covered else "completed_with_visual_gaps"
                    )  # 明确未通过视觉验证的范围
                    reason = "connected_region_traversed"  # 输出真实完成条件
                    break  # 结束区域遍历
                remaining.difference_update(self.traversed)  # 已尝试视觉但需抓包通过的点不无限重试
                point = (pose["x"], pose["z"])  # 选点仍只使用视觉坐标
                free = self.navigation_grid.nearest_free(
                    *self.navigation_grid.index_of_world(*point), max_radius=2
                )  # 复用项目有界起点处理
                if free is None:  # 不在可规划区域则不能继续猜测路线
                    reason = "no_free_start_after_temporary_obstacles"  # 保留具体原因
                    break  # 让用户处理当前位置
                reachable_region, _ = connected_region(
                    self.navigation_grid, self.navigation_grid.world_of_index(*free)
                )  # 重新计算暂时可达区域
                available = self.region_cells.intersection(
                    map(tuple, np.argwhere(reachable_region).tolist())
                )  # 不扩大原始测试范围
                self.deferred = self.region_cells - available  # 分开记录临时隔离区域
                pending = (
                    available - self.traversed - (self.skipped if self.options.skip_blocked else set())
                )  # 跳过不等于已验证
                choices = remaining.intersection(pending) or pending  # 补齐尚未覆盖的细小分支
                if not choices:  # 剩余范围只有暂不可通行部分
                    status, reason = "completed_with_skips", "remaining_reachable_region_tested"  # 不宣称全图通过
                    break  # 输出所有跳过明细
                reachable = [
                    p for p in choices if math.dist(point, self.grid.world_of_index(*p)) > 2.0
                ]  # 网格边缘起点需走入而非提前判到达
                selection = self.target_planner.plan_multi(
                    [free], sorted(reachable or choices), time_budget_s=10
                )  # 复用项目多目标搜索，按实际通路代价选点
                if not selection.ok:  # 不将规划超时或搜索超限误认为完成
                    reason = "target_selection_failed: " + selection.reason  # 保留项目具体失败原因
                    break  # 保存部分覆盖结果
                target = selection.goal_cell  # 使用项目实际选出的最近可达目标
                self.goal = self.grid.world_of_index(*target)  # 所有目标都在同一已知可走分量
                self.movement_started = True  # 后续失焦立即停止
                self.navigation_failure = {}  # 不复用前一目标的失败原因
                self.last_navigation_warning, self.navigation_running = "", True  # 观察正式导航的失败诊断
                if not self.navigate_grid_to(
                    self.goal,
                    map_id=self.grid.meta.map_name,
                    grid_path=str(self.options.grid.resolve()),
                    timeout=min(180, max(1, self.deadline - time.monotonic())),
                    stop_distance=0.8,
                ):  # 正式路径规划、转向、卡住与恢复全量复用
                    reason = self.stop_reason or "project_navigation_failed"  # 保留正式导航失败日志
                    if self.stop_reason or not self.options.skip_blocked:  # 安全停止不能当普通障碍绕过
                        break  # 保留原有停止行为
                    self.navigation_running = False  # 结束本次项目警告观察
                    if not self.navigation_failure.get("obstructed"):  # 未经过卡住确认不能认定目标不可通行
                        event = {
                            "goal": self.goal,
                            "time": time.time(),
                            "reason": self.last_navigation_warning or reason,
                            "pose": self.samples[-1]["pose"] if self.samples else pose,
                        }  # 保存真实失败上下文
                        self.navigation_failures.append(event)  # 独立记录定位或规划失败
                        write_json(
                            self.directory / "navigation_failures.json", self.navigation_failures
                        )  # 不加入临时障碍清单
                        transient_failures += 1  # 防止同一初始化故障无限循环
                        if transient_failures >= 3:  # 重试次数有上限
                            reason = "navigation_unavailable: " + event["reason"]  # 向最终报告保留项目原因
                            break  # 不伪造覆盖完成
                        self.sleep(0.5)  # 让正式定位服务刷新楼层证据
                        continue  # 原目标仍保留，恢复后可以再次选择
                    current = next(
                        (r["pose"] for r in reversed(self.samples) if r["pose"].get("position_trusted")), pose
                    )  # 采用最近视觉位置
                    self._skip_failed_goal((current["x"], current["z"]))  # 记录并更新临时避让
                    continue  # 用项目导航尝试剩余目标
                self.navigation_running, transient_failures = False, 0  # 成功后清除暂时故障计数
                self.arrived.append(self.goal)  # 只有实际成功才计入到达
        except Exception as exc:  # 任务中断统一保留部分结果
            status, reason = "stopped", self.stop_reason or f"{type(exc).__name__}: {exc}"  # 记录准确终态
            self.log_warning(reason)  # 进入正式日志
        finally:  # 先结束游戏输入，再结束采样
            self._stop_inputs()  # 调用项目后端清理本次输入
            self.navigation_running = False  # 结束失败原因采集
            self.guard_done.set()  # 停止紧急观察线程
            guard.join(timeout=1)  # 等待最后一拍松键完成
            service.sample_world_pose, service._locate_visual_frame = original_sample, original_locate  # 不污染后续任务
            service.config = original_config  # 恢复运行前的来源设置
            if self.last_frame is not None:  # 只保存无账户标识的小地图区域
                cv2.imwrite(str(self.directory / "last_hud.png"), self.last_frame[:280, :340])  # 留下定位证据
            time.sleep(0.4)  # 等待最后的参考包围点到达
            self.reference.close()  # 刷新独立抓包记录
            self.sample_log.close()  # 关闭原始采样文件
            self.capture_log.close()  # 关闭抓包控制证据
        _, _, references = self.reference.snapshot()  # 使用完整轨迹重新时间对齐
        timeline, verified = ReferenceTimeline(references), set()  # 真值覆盖单独计算，不影响导航
        for row in self.samples:  # 只计入能和视觉帧对齐的真值观测
            if not row["pose"].get("position_trusted"):  # 无可信定位不能算验证成功
                continue  # 失败仍体现在可用率
            ref = align_reference(
                timeline,
                row["frame_t"],
                self.grid.meta.map_name,
                flow=row["flow"],
                stationary=row["stationary_reference"],
            )  # 不做空间平移补偿
            if ref is not None:  # 有独立配对才计真值覆盖
                verified.update(
                    nearby_cells(self.region, self.grid.index_of_world(ref["x"], ref["z"]), self.coverage_steps)
                )  # 不跨墙统计
        self.report = {
            **plan,
            "status": status,
            "reason": reason,
            "elapsed_s": time.monotonic() - started,
            "covered_cells": len(self.covered),
            "reference_verified_cells": len(verified),
            "coverage_fraction": len(self.covered) / len(self.region_cells),
            "traversed_cells": len(self.traversed),  # 总遍历单独汇总
            "traversal_fraction": len(self.traversed) / len(self.region_cells),  # 不冒充视觉通过率
            "visual_unavailable_cells": len(self.traversed - self.covered),  # 后续研究重点区域
            "capture_navigation_frames": self.capture_frames,  # 不进入视觉精度统计
            "arrived_goals": len(self.arrived),
            "blocked_events": len(self.failures),  # 包括历史失败便于追溯
            "navigation_failure_events": len(self.navigation_failures),  # 定位或规划失败单独统计
            "skipped_uncovered_cells": len(self.skipped - self.covered),  # 跳过格不算验证通过
            "deferred_disconnected_cells": len(self.deferred - self.covered),  # 临时路网隔离格
            "reference_verified_scope": "current_run_only",  # 真值覆盖与累积视觉覆盖明确区分
            "last_goal": self.goal,  # 保留失败时正在尝试的目标
            "last_visual_position": next(  # 倒序查找最后可信视觉坐标
                ([r["pose"]["x"], r["pose"]["z"]] for r in reversed(self.samples) if r["pose"].get("position_trusted")),
                None,
            ),
            "frames": len(self.samples),
            "trusted_frames": sum(bool(r["pose"].get("position_trusted")) for r in self.samples),
            "accuracy": report_accuracy(self.samples, references),
            "navigation_implementation": "MinimapNavigateToPoint.navigate_grid_to / WorldPoseService / EfInteraction",
        }  # 明确代码复用与统计口径
        write_json(self.directory / "report.json", self.report)  # 输出可复核的结果
        write_json(self.directory / "progress.json", self.report)  # 监控文件同步终态，避免退出后仍显示运行中
        write_json(self.directory / "covered_cells.json", sorted(self.covered))  # 保存实际覆盖网格
        write_json(self.directory / "traversed_cells.json", sorted(self.traversed))  # 支持续跑总遍历
        write_json(
            self.directory / "visual_unavailable_cells.json", sorted(self.traversed - self.covered)
        )  # 保留视觉验证缺口
        self.log_info(json.dumps(self.report, ensure_ascii=False))  # 便于外部监控终态
        return status == "completed"  # 未完成不误报成功


def run_validation_task(args, grid, region, targets, directory, plan):  # 使用框架原生无界面任务入口
    import ok  # 复用框架设备与任务生命周期

    from src.config import config  # 使用项目正式截图和交互配置
    from src.localization.wgc_frame_timing import install_wgc_frame_timing  # 复用真实帧时间戳
    from src.patches.position_service_patch import install_position_service_patch  # 复用服务退出清理

    install_wgc_frame_timing()  # 与生产定位使用同一时间轴
    install_position_service_patch()  # 不遗留定位线程和资源
    settings = copy.deepcopy(config)  # 不改变应用原全局字典
    settings.update(
        use_gui=False,
        check_mutex=False,
        onetime_tasks=[],
        trigger_tasks=[],
        config_folder=str(directory / "runtime-config"),
        screenshots_folder=str(directory / "screenshots"),
        log_upload_api="",
    )  # 隔离配置与输出，禁止自动触发其他任务
    settings["windows"].update(start_exe=False, capture_method=["WGC"])  # 只连接已运行游戏并使用准确 WGC 时间
    runtime = ok.OK(settings)  # 由框架创建 EfInteraction、截图和执行器
    task = runtime.get_onetime_task(GridValidationTask)  # 框架负责 after_init 与 post_init
    task.validation = (args, grid, region, targets, directory, plan)  # 启动前注入有限计划
    try:  # 无论任务结果如何都清理运行时
        runtime.run_task(task, exit_after=False)  # 不退出游戏或其他已运行应用
    finally:  # 使用框架正式资源退出路径
        runtime.quit()  # 设置执行器退出事件
        runtime.task_executor.destroy()  # 释放输入、设备和定位服务
    report = getattr(task, "report", {})  # 任务可能在初始化阶段就失败
    return (
        0
        if report.get("status") == "completed"
        or (args.mode == "record" and report.get("reason") == "time_limit" and report.get("frames", 0) > 0)
        else 1
    )  # 部分遍历明确返回失败码
