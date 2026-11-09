"""使用正式导航器执行 NCC 失锚距离试验。"""  # 不另写键鼠或跟随算法

import copy  # 隔离框架配置
import hashlib  # 固定被测代码摘要
import json  # 保存逐帧证据
import math  # 累计视觉路程与选择目标
import threading  # 复用紧急停止观察器
import time  # 分离帧墙钟与运行时限
from pathlib import Path  # 固定被测源码路径

import cv2  # 只保存脱敏 HUD
import numpy as np  # 在实际已知路网中选择有限候选

from scripts.nav.anchor_dropout_analysis import summarize  # 结束后独立评分
from scripts.nav.anchor_dropout_gate import NccDropoutGate  # 实际停用 NCC 调用
from scripts.nav.validate_visual_grid import ReferenceRecorder, write_json  # 复用只读抓包记录器
from scripts.nav.validation_task import GridValidationTask  # 复用正式任务配置和停止保护
from scripts.nav.visual_validation import connected_region  # 不跨越未知或不连通路面
from src.core.PositionConfig import CONFIG_EXPERIMENTAL_PORT, CONFIG_USE_EXPERIMENTAL  # 仅内存中选择视觉源
from src.localization.minimap_map_locator import MinimapGeometry  # 使用实际分辨率保存完整小地图
from src.localization.wgc_frame_timing import timing_for_frame  # 补充采样按实际呈现时刻限频
from src.nav.grid_io import load_grid  # 使用已有网格格式


class TrialFinished(RuntimeError):  # 有限试验终止，不是整个任务故障
    pass  # 导航栈退出后统一松键并恢复 NCC


class AnchorDropoutTask(GridValidationTask):  # 正式导航与输入保护全部继承
    experiment = None  # 在框架 after_init 后注入

    def sleep(self, timeout):  # 可选对照组把采样空档与视觉链误差分离
        if (  # 继续核验本组条件
            not getattr(getattr(self, "options", None), "keep_sampling", False)
            or self.phase != "outage"  # 完成当前观测或统计步骤
        ):  # 默认复用生产采样时序
            return super().sleep(timeout)  # 保留原等待行为
        until = time.monotonic() + timeout  # 不缩短导航所需等待
        while until - time.monotonic() > 0.5:  # 最后一段等待不额外采样，避免紧接下一次原生观测
            super().sleep(0.25)  # 大于里程计原有最小采样间隔
            self.world_pose(frame=self.frame, max_age=0.6)  # 同一任务线程采样，避免后台竞争截图
        super().sleep(max(0, until - time.monotonic()))  # 保留尾段等待，避免制造 too_soon 实验干扰
        return True  # 不改变跟随器状态机

    def _save_hud(self, suffix):  # 只保留小地图而不保存账户区域
        if self.last_frame is not None:  # 初始化失败时可能没有画面
            height, width = self.last_frame.shape[:2]  # 使用实际游戏分辨率
            geometry = MinimapGeometry.from_frame(width, height)  # 复用生产几何
            bottom = min(height, math.ceil(geometry.center_y + geometry.outer_radius * 1.2))  # 包含 HUD 圆框
            right = min(width, math.ceil(geometry.center_x + geometry.outer_radius * 1.2))  # 不截取主场景
            cv2.imwrite(  # 完成当前观测或统计步骤
                str(self.directory / f"trial_{self.trial['trial']:02d}_{suffix}.png"),
                self.last_frame[:bottom, :right],  # 完成当前观测或统计步骤
            )  # 对应试验编号

    def _event(self, reason, **extra):  # 保存终止边界和可重复的证据
        row = {  # 完成当前观测或统计步骤
            "time": time.time(),  # 保存 time 字段
            "phase": self.phase,  # 保存 phase 字段
            "trial": self.trial["trial"],  # 保存 trial 字段
            "reason": reason,  # 保存 reason 字段
            **extra,  # 完成当前观测或统计步骤
        }  # 时钟不混用
        self.events.write(json.dumps(row, ensure_ascii=False) + "\n")  # 每条边界独立落盘
        self.events.flush()  # 中断也保留

    def _choose_goal(self, pose):  # 选点只读取视觉坐标，不读取抓包
        start = (pose["x"], pose["z"])  # 当前可信视觉起点
        radius = max(35, self.trial["requested_m"] * 1.3 + self.options.warmup_m + 8)  # 终点在预算距离之外
        free = self.grid.nearest_free(*self.grid.index_of_world(*start), max_radius=2)  # 与正式规划同样有界接入
        if free is None:  # 不能接入已知路面
            raise RuntimeError("visual_start_not_near_free_grid")  # 不使用抓包纠偏
        region, _ = connected_region(self.grid, self.grid.world_of_index(*free))  # 仅选当前连通分量
        cells = np.argwhere(region)  # 实际路面而非几何圆上的偶然落点
        cells = cells[:: max(1, len(cells) // 2048)]  # 有界候选规模
        points = np.asarray([self.grid.world_of_index(*cell) for cell in cells])  # 复用网格坐标变换
        delta = points - np.asarray(start)  # 只使用视觉起点
        distances = np.linalg.norm(delta, axis=1)  # 候选直线距离只作筛选
        angles = np.arctan2(delta[:, 1], delta[:, 0])  # 分方向避免只挑同一条窄路
        choices = []  # 保存已通过正式规划器的路线
        for index in range(8):  # 有限方向检查
            sector = (index + self.trial["trial"] * 3) % 8  # 各次试验改变方向优先级
            eligible = np.flatnonzero(  # 完成当前观测或统计步骤
                (distances > self.options.warmup_m + 5)  # 完成当前观测或统计步骤
                & (((angles + math.pi) * 8 / math.tau).astype(int) % 8 == sector)  # 完成当前观测或统计步骤
            )  # 同分量已知自由格
            if not len(eligible):  # 该方向没有实际路面
                continue  # 不向未知位置发送导航
            selected = eligible[np.argmin(abs(distances[eligible] - radius))]  # 优先足够远的真实格
            result = self.plan_grid_path(  # 完成当前观测或统计步骤
                start,
                tuple(points[selected]),
                map_id=pose["map_id"],
                grid_path=str(self.options.grid),  # 完成当前观测或统计步骤
            )  # 正式规划器
            if result.ok and len(result.waypoints) > 1:  # 规划必须实际成功
                choices.append(result)  # 不能用几何连线替代通路
                if len(result.waypoints) <= 3:  # 优先少转弯路线降低采样空档干扰
                    break  # 不改变生产转向和采样策略
        if choices:  # 至少找到一条可审查正式路线
            chosen = min(  # 完成当前观测或统计步骤
                choices,
                key=lambda p: (len(p.waypoints), -math.dist(start, p.waypoints[-1])),  # 完成当前观测或统计步骤
            )  # 少转弯且尽量远
            self._event("route_selected", waypoints=chosen.waypoints)  # 固定实际计划
            return chosen.waypoints[-1]  # 原跟随器负责执行
        raise RuntimeError("no_known_free_route_from_visual_position")  # 不能为了试验盲走

    def _record_pose(self, state, *, control=True):  # 控制帧和服务独立采样统一记录
        if control:  # 只让控制线程终止导航栈
            self._check_stop()  # 保留 F8、失焦、时限停止
        latest, checked, _ = self.reference.snapshot()  # 参考只用于健康检查与身份标记
        if control and (latest is None or time.time() - checked > 1.5):  # 失去独立参考不继续实验
            raise RuntimeError("reference_unavailable")  # 不用陈旧真值评分
        latest = latest or {}  # 独立采样缺少参考时只留原始视觉记录
        row = {  # 完成当前观测或统计步骤
            "trial": self.trial["trial"],  # 保存 trial 字段
            "phase": self.phase,  # 保存 phase 字段
            "frame_t": state.get("frame_t", 0),  # 使用 WGC 帧时钟
            "flow": latest.get("flow"),  # 保存 flow 字段
            "scene": latest.get("scene"),  # 保存 scene 字段
            "observed_at": time.time(),  # 身份不传入视觉定位
            "pose": dict(state),  # 保存 pose 字段
            "ncc_disabled": bool(self.gate and self.gate.enabled),  # 保存 ncc_disabled 字段
            "measurement": self.service._visual_source.odometry.last_result()  # 保存 measurement 字段
            if self.service._visual_source is not None
            and self.service._visual_source.odometry is not None  # 继续核验本组条件
            else None,  # 保留 too_soon 等原始采样原因
            "sample_context": getattr(self, "sample_context", None),  # 门禁失败前的证据时刻
            "observer": "control" if control else "service_sampler",  # 不隐藏阻塞期间观测
        }  # 保留失败帧
        self.samples.append(row)  # 之后统一做时间对齐
        self.sample_log.write(json.dumps(row, ensure_ascii=False) + "\n")  # 原始数据不做误差补偿
        self.sample_log.flush()  # 导航中断也可重算
        if not control:  # 抓包和记录器不能从采样线程控制角色
            return state  # 导航仍只消费原 world_pose 契约
        if state.get("visual_duplicate_frame"):  # 重复画面不增加路程或确认次数
            return state  # 保留生产重复帧语义
        if state.get("position_trusted"):  # 只有可信视觉位置参与导航阶段停止条件
            if state.get("frame_time_source") != "wgc_system_relative_time":  # 实验需要准确时钟
                raise RuntimeError("missing_wgc_presentation_timestamp")  # 不用定位结束时间评分
            if state.get("map_id") != self.grid.meta.map_name or state.get("tier_id") != "__main__":  # 试验限同一主图
                raise RuntimeError("map_or_tier_not_supported")  # 不推断未知楼层
            point = (state["x"], state["z"])  # 此处不读取抓包坐标
            if self.phase in ("warmup", "outage") and self.previous_point is not None:  # 分阶段累计视觉路程
                self.visual_distance += math.dist(point, self.previous_point)  # 仅作为预设距离停止条件
            self.previous_point = point  # 不向定位器写入任何坐标
            if (  # 继续核验本组条件
                self.phase == "warmup"  # 完成当前观测或统计步骤
                and self.visual_distance >= self.options.warmup_m  # 继续核验本组条件
                and state.get("absolute_position_valid")  # 继续核验本组条件
                and self.service._visual_source.evidence.previous is not None  # 继续核验本组条件
                and self.service._visual_source.evidence.previous.time == row["frame_t"]  # 继续核验本组条件
            ):  # 行走中从真实强锚切换
                self.trial["anchor"] = row  # 最后强锚起点供独立事后分析
                self.anchor_time = self.service._visual_source._last_absolute_t  # 审计绝对锚没有刷新
                self.trial["disabled_call_start"] = self.gate.disabled_calls  # 每次试验独立计数
                self.trial["budget_limit_m"] = self.service._visual_source.evidence.max_error_m  # 记录实际预算
                self._save_hud("anchor")  # 保留停用前现场
                self.phase, self.visual_distance = "outage", 0.0  # 从下一帧开始完全停用 NCC
                self.gate.enabled = True  # 真正阻断原 tracker.update
                self._event("ncc_disabled", anchor_frame_t=row["frame_t"])  # 不通过测试候选坐标定位
            elif self.phase == "outage":  # 只检查当前发布的独立视觉链
                if (  # 继续核验本组条件
                    state.get("absolute_position_valid")  # 完成当前观测或统计步骤
                    or self.service._visual_source._last_absolute_t != self.anchor_time  # 继续核验本组条件
                ):  # 强锚不应更新
                    raise RuntimeError("absolute_anchor_changed_while_ncc_disabled")  # 实验失效不能计成功
                self.trial["last_trusted_frame_t"] = row["frame_t"]  # 可信终点独立于失败点
                self.trial["visual_distance_m"] = self.visual_distance  # 原始停止尺度公开
                if self.visual_distance >= self.trial["requested_m"]:  # 达到本次试验距离后停车
                    self.trial["stop_frame_t"] = row["frame_t"]  # 实际结果由抓包路程评分
                    raise TrialFinished("requested_visual_distance_reached")  # 不假装正式目标到达
        elif self.phase == "outage":  # 第一帧证据失效即停车
            self.trial["stop_frame_t"] = row["frame_t"]  # 记录失效边界
            self._save_hud("failure")  # 留下原始失效现场
            raise TrialFinished("visual_evidence_lost: " + str(state.get("trust_reason")))  # 不依赖抓包继续行走
        return state  # 正式跟随器仍只拿视觉状态

    def _acquire(self, timeout=30):  # 停车等待真实强绝对锚
        started = time.monotonic()  # 超时使用单调时钟
        while time.monotonic() - started < timeout:  # 有限获取与恢复
            pose = self.world_pose(frame=self.next_frame(), max_age=0.6)  # 使用正式姿态服务
            if (  # 继续核验本组条件
                pose  # 完成当前观测或统计步骤
                and pose.get("position_trusted")  # 继续核验本组条件
                and pose.get("absolute_position_valid")  # 继续核验本组条件
                and pose.get("tier_trusted")  # 继续核验本组条件
            ):  # 原门槛通过
                return pose  # 不用抓包位置帮助获取
            self.sleep(0.15)  # 框架负责刷新和停止
        raise RuntimeError("strong_anchor_acquisition_timeout")  # 不盲目导航

    def run(self):  # 有限多次试验编排
        self.options = self.experiment  # 独立命令行参数
        self.directory = self.options.output  # 输出目录由入口唯一创建
        self.grid = self.navigation_grid = load_grid(self.options.grid)  # 不改原网格
        self.validation = (self.options, self.grid)  # 复用父类的网格配置覆盖
        self.deadline, self.guard_done = time.monotonic() + self.options.seconds, threading.Event()  # 总时限
        self.movement_started, self.stop_reason, self.gate = False, "", None  # 初始无游戏输入
        self.samples, self.trials, self.phase, self.trial = [], [], "acquire", {"trial": 0}  # 原始结果
        self.previous_point, self.visual_distance, self.last_frame = None, 0.0, None  # 不沿用上一试验状态
        self.navigation_failure = {}  # 父类保留正式卡住诊断
        self.reference = ReferenceRecorder(self.options.port, self.directory)  # 后台只读独立抓包
        self.sample_log = (self.directory / "visual.jsonl").open("w", encoding="utf-8")  # 精简状态证据
        self.events = (self.directory / "events.jsonl").open("w", encoding="utf-8")  # 停用/恢复边界
        self.service = service = self.get_runtime_position_service()  # 使用正式共享定位服务
        old_config, original_sample, original_locate = (  # 完成当前观测或统计步骤
            service.config,  # 完成当前观测或统计步骤
            service.sample_world_pose,  # 完成当前观测或统计步骤
            service._locate_visual_frame,  # 完成当前观测或统计步骤
        )  # 退出恢复
        original_continuous = service._sample_continuous_visual  # 保留服务独立采样入口
        service.config = {  # 完成当前观测或统计步骤
            **dict(service.config),  # 完成当前观测或统计步骤
            CONFIG_USE_EXPERIMENTAL: False,  # 完成当前观测或统计步骤
            CONFIG_EXPERIMENTAL_PORT: self.options.port,  # 完成当前观测或统计步骤
        }  # 禁止抓包导航
        self.gates = []  # 保存每个实际定位器实例以完整恢复

        def observe_locate(frame, stamp, timing):  # 在单个定位实例边界停用 NCC
            self.last_frame = frame  # 只在边界保存脱敏截图
            tracker = service._visual_source.tracker  # 服务已完成初始化
            evidence = service._visual_source.evidence  # 仅记录是否已有采样证据
            self.sample_context = {  # 完成当前观测或统计步骤
                "previous_evidence_time": evidence.previous.time
                if evidence and evidence.previous
                else None,  # 保存 previous_evidence_time 字段
                "convinced": tracker._convinced is not None,  # 保存 convinced 字段
            }  # 不改定位流程
            if self.gate is None or tracker.update is not self.gate:  # 服务重建时重新观察
                if self.phase == "outage":  # 不允许静默重建锚链
                    raise TrialFinished("visual_source_recreated")  # 停止当前试验
                self.gate = NccDropoutGate(tracker)  # 默认正常定位
                self.gates.append((tracker, self.gate))  # 退出时恢复方法
                tracker.update = self.gate  # 仅绑定本实例
            return original_locate(frame, stamp, timing)  # 保留生产 WGC 时效与异常处理

        def observe_sample(*args, **kwargs):  # 不改变位置发布契约
            if self.options.keep_sampling and self.phase == "outage":  # 仅对照组限制补充观测频率
                source = service._visual_source  # 使用当前正式定位实例
                frame = args[0] if args else kwargs.get("frame")  # 保留原导航传来的帧
                for _ in range(3):  # 有限等待新的呈现帧，不回传旧坐标
                    frame = self.frame if frame is None else frame  # 通过框架获取实际画面
                    timing = timing_for_frame(self.executor.method, frame)  # 读取 WGC 时刻而非处理结束时刻
                    if (  # 继续核验本组条件
                        source._last_clock is None  # 完成当前观测或统计步骤
                        or timing is None  # 继续核验本组条件
                        or timing.frame_time - source._last_clock >= source.odometry._sample_min_dt  # 继续核验本组条件
                    ):  # 原最小采样间隔
                        break  # 只有实际新帧才执行完整定位
                    super(AnchorDropoutTask, self).sleep(source.odometry._sample_min_dt + 0.05)  # 保留框架停止与刷新
                    frame = self.frame  # 等待后缓存已失效，读取新的实际帧
                if args:  # 支持定位服务的原位置参数形式
                    args = (frame, *args[1:])  # 不改变其他实参
                else:  # 导航通常使用关键字传入帧
                    kwargs["frame"] = frame  # 外层服务会使用这张帧的真实时间戳
            with service._visual_sample_lock:  # 记录与正式采样共用顺序，避免观测乱序
                return self._record_pose(original_sample(*args, **kwargs))  # 所有导航观测都记录

        def observe_continuous(frame, stopped):  # 只记录生产独立采样，不另补实验帧
            with service._visual_sample_lock:  # 与控制观测和证据推进串行
                before = service._sampled_visual_state  # 区分新观测与服务合并的旧帧请求
                original_continuous(frame, stopped)  # 保留生产生命周期和停止检查
                if (  # 不把跳过采样误记成新的有效帧
                    not stopped.is_set()  # 退出后不写入记录
                    and service._sampled_visual_state is not None  # 必须有实际状态
                    and service._sampled_visual_state is not before  # 合并请求不会替换缓存对象
                ):  # 只有实际采样才记录
                    self._record_pose(dict(service._sampled_visual_state), control=False)  # 记录真实 WGC 时刻

        service._locate_visual_frame, service.sample_world_pose = observe_locate, observe_sample  # 本次实验观察边界
        service._sample_continuous_visual = observe_continuous  # 不改变定位输入或输出
        guard = threading.Thread(target=self._watch, daemon=True)  # 复用父类 F8 与失焦保护
        guard.start()  # 在同步匹配阻塞时也可以松键
        reason = "completed"  # 每次试验失败由 trials 明确记录
        try:  # 任何异常都恢复方法并生成报告
            if self.options.activate_game:  # 仅在明确参数开启时激活
                self.ensure_in_front()  # 使用正式窗口方法
                self.wait_until(lambda: self.hwnd.is_foreground(), time_out=5)  # 等待游戏确实在前台
            service.start_minimap_position(wait_stable=False)  # 创建正式视觉源
            for repeat in range(self.options.repeats):  # 独立重复并改变目标方向
                for distance in self.options.distances:  # 从短距离到长距离
                    self.trial = {  # 完成当前观测或统计步骤
                        "trial": len(self.trials) + 1,  # 保存 trial 字段
                        "repeat": repeat + 1,  # 保存 repeat 字段
                        "requested_m": distance,  # 保存 requested_m 字段
                    }  # 固定计划
                    self.trials.append(self.trial)  # 未完成试验也纳入报告
                    self.trial["sampling_mode"] = (  # 完成当前观测或统计步骤
                        "keep_sampling_during_waits"
                        if self.options.keep_sampling
                        else "native_navigation"  # 列出本次试验的统计或说明项
                    )  # 两种实验协议明确区分
                    self.phase, self.previous_point, self.visual_distance = "acquire", None, 0.0  # 不继承前次里程
                    pose = self._acquire()  # 每次重新确认强锚
                    self.goal = self._choose_goal(pose)  # 不使用抓包选点
                    route_ends = [(pose["x"], pose["z"]), self.goal]  # 多段试验只在已走过的端点间往返
                    self.phase, self.previous_point = "warmup", (pose["x"], pose["z"])  # 先正常导航
                    self.movement_started = True  # 开始要求前台连续性
                    try:  # 正式导航调用可能被试验终点主动中断
                        for leg in range(8):  # 连通区域有限时允许有限往返，不刷新强锚
                            self.goal = route_ends[(leg + 1) % 2]  # 只使用原视觉起点和已规划终点
                            arrived = self.navigate_grid_to(  # 完成当前观测或统计步骤
                                self.goal,  # 完成当前观测或统计步骤
                                map_id=self.grid.meta.map_name,  # 完成当前观测或统计步骤
                                grid_path=str(self.options.grid),  # 正式跟随器
                                timeout=min(150, max(1, self.deadline - time.monotonic())),  # 完成当前观测或统计步骤
                                stop_distance=0.8,  # 完成当前观测或统计步骤
                            )  # 有限调用
                            if not arrived or self.phase != "outage":  # 普通导航失败或尚未进入失锚实验
                                break  # 不隐去生产失败
                            self._event(  # 完成当前观测或统计步骤
                                "route_leg_completed",
                                leg=leg + 1,
                                visual_distance_m=self.visual_distance,  # 列出本次试验的统计或说明项
                            )  # 往返与单向位移区别公开
                        self.trial["reason"] = (  # 完成当前观测或统计步骤
                            "route_arrived_before_requested_distance"
                            if arrived
                            else "project_navigation_failed"  # 列出本次试验的统计或说明项
                        )  # 不把到达混同失锚极限
                    except TrialFinished as exc:  # 预算耗尽或达到试验距离
                        self.trial["reason"] = str(exc)  # 原因不合并为成功
                    finally:  # 恢复 NCC 前先停车
                        self._stop_inputs()  # 使用正式输入后端松键
                        if self.gate is not None:  # 初始化可能失败
                            self.gate.enabled = False  # 下一帧重新运行真实 NCC
                    self.trial["disabled_calls"] = self.gate.disabled_calls - self.trial.get(  # 完成当前观测或统计步骤
                        "disabled_call_start",
                        self.gate.disabled_calls,  # 列出本次试验的统计或说明项
                    )  # 单次停用调用数
                    self._event("ncc_restored", outcome=self.trial["reason"])  # 标记恢复边界
                    self.phase = "recovery"  # 不把恢复观测算到无锚漂移
                    recovery_started, before = time.monotonic(), len(self.samples)  # 单独统计恢复耗时
                    self._acquire(timeout=20)  # 生产确认门槛恢复
                    self.trial.update(  # 完成当前观测或统计步骤
                        recovery_s=time.monotonic() - recovery_started,
                        recovery_frames=len(self.samples) - before,  # 完成当前观测或统计步骤
                    )  # 实测恢复
                    self._save_hud("recovered")  # 固定恢复后的现场
                    write_json(self.directory / "trials.json", self.trials)  # 每次试验完成立即落盘
        except Exception as exc:  # 保留真实现场故障
            reason = self.stop_reason or f"{type(exc).__name__}: {exc}"  # 不包装为测试通过
            self._event("experiment_stopped", detail=reason)  # 中途失败可追溯
        finally:  # 不留下游戏输入或补丁
            self._stop_inputs()  # 首先停车
            self.guard_done.set()  # 结束紧急观察线程
            guard.join(timeout=1)  # 等最后一次松键结束
            for tracker, gate in self.gates:  # 所有曾持有的定位器都恢复
                tracker.update = gate.original  # 不污染后续导航
            service.sample_world_pose, service._locate_visual_frame, service.config = (  # 完成当前观测或统计步骤
                original_sample,  # 完成当前观测或统计步骤
                original_locate,  # 完成当前观测或统计步骤
                old_config,  # 完成当前观测或统计步骤
            )  # 恢复服务
            time.sleep(0.7)  # 等最后一个参考包围点到达
            self.reference.close()  # 刷新独立轨迹文件
            self.sample_log.close()  # 逐帧证据完整关闭
            self.events.close()  # 边界事件完整关闭
            service._sample_continuous_visual = original_continuous  # 恢复独立采样观察边界
        _, _, references = self.reference.snapshot()  # 只在定位与导航结束后评分
        report = summarize(self.samples, references, self.trials)  # 抓包不会影响导航位置
        report.update(  # 完成当前观测或统计步骤
            reason=reason,  # 完成当前观测或统计步骤
            sampling_mode="keep_sampling_during_waits"  # 完成当前观测或统计步骤
            if self.options.keep_sampling  # 继续核验本组条件
            else "native_navigation",  # 不能把加密采样当原生产结果
            runtime_trial_records=self.trials,  # 中途未完成试验也能重算
            ncc_calls_disabled=sum(g.disabled_calls for _, g in self.gates),  # 证明实际停用范围
            limitations=[  # 完成当前观测或统计步骤
                "NCC-off metadata freezes last confirmed map/tier; HUD texture gate remains active.",  # 无法用关闭的 NCC 重新认层
                "Distance targets stop on visual travel; reported distance bins use independent packet path.",  # 两种路程明确分开
                "Uses existing local visual-grid validation recording and emergency-stop helpers.",  # 列出本次试验的统计或说明项
                "Supplemental samples cover task sleeps only; blocking interaction/planning may still produce frame gaps.",  # 列出本次试验的统计或说明项
            ],  # 明确脚本依赖
            source_sha256={  # 完成当前观测或统计步骤
                str(p): hashlib.sha256(p.read_bytes()).hexdigest()  # 完成当前观测或统计步骤
                for p in [  # 固定被测实现
                    self.options.grid,  # 完成当前观测或统计步骤
                    Path(__file__),  # 网格与执行脚本
                    Path("src/localization/visual_position_source.py"),  # 正式定位源
                    Path("src/localization/minimap_visual_anchor_chain.py"),  # 完成当前观测或统计步骤
                    Path("src/localization/position_service.py"),  # 记录实际观测所有者
                    Path("src/localization/visual_pose_sampling.py"),  # 记录独立采样实现
                ]  # 结束当前参数或记录构造
            },  # 结束当前参数或记录构造
        )  # 正式视觉证据
        write_json(self.directory / "report.json", report)  # 完整实验结果
        self.report = report  # 运行时退出后由入口返回状态
        self.log_info(  # 完成当前观测或统计步骤
            json.dumps(  # 完成当前观测或统计步骤
                {  # 完成当前观测或统计步骤
                    "reason": reason,  # 保存 reason 字段
                    "trials": len(self.trials),  # 保存 trials 字段
                    "max_verified_distance_m": report["max_verified_distance_m"],  # 保存 max_verified_distance_m 字段
                }  # 结束当前参数或记录构造
            )  # 结束当前参数或记录构造
        )  # 精简日志


def run_experiment(args):  # 使用框架原生无界面任务入口
    import ok  # 正式截图与交互运行时

    from src.config import config  # 复用项目配置
    from src.localization.wgc_frame_timing import install_wgc_frame_timing  # 真实呈现时钟
    from src.patches.position_service_patch import install_position_service_patch  # 正式资源退出

    install_wgc_frame_timing()  # 与生产定位一致
    install_position_service_patch()  # 保证退出释放定位服务
    settings = copy.deepcopy(config)  # 不改应用全局配置
    settings.update(  # 完成当前观测或统计步骤
        use_gui=False,  # 完成当前观测或统计步骤
        check_mutex=True,  # 完成当前观测或统计步骤
        onetime_tasks=[],  # 完成当前观测或统计步骤
        trigger_tasks=[],  # 不与其他实例同时控制游戏
        config_folder=str(args.output / "runtime-config"),  # 完成当前观测或统计步骤
        screenshots_folder=str(args.output / "screenshots"),  # 完成当前观测或统计步骤
        log_upload_api="",  # 完成当前观测或统计步骤
    )  # 隔离输出与设置
    settings["windows"].update(start_exe=False, capture_method=["WGC"])  # 只连接已运行游戏
    runtime = ok.OK(settings)  # 使用生产 EfInteraction
    task = runtime.get_onetime_task(AnchorDropoutTask)  # 框架负责生命周期
    task.experiment = args  # 启动前注入有限实验参数
    try:  # 退出路径与项目验证器相同
        runtime.run_task(task, exit_after=False)  # 不退出游戏
    finally:  # 保证释放输入与设备
        runtime.quit()  # 设置正式退出事件
        runtime.task_executor.destroy()  # 清理截图和服务
    return 0 if getattr(task, "report", {}).get("reason") == "completed" else 1  # 真实终态
