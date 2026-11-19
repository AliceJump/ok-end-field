"""开发者工具和 MCP 共用的手动路线采集任务。"""

from datetime import datetime

from qfluentwidgets import FluentIcon

import json  # 将可信坐标保存为 JSONL
from pathlib import Path  # 创建轨迹文件
from unittest.mock import patch  # 临时禁止定位服务自动重登

from src.data.FeatureList import FeatureList as fL  # 识别登录入口
from src.localization.capture_position_source import (  # 需要重登的抓包状态
    CAPTURE_HEALTH_MISSED_HANDSHAKE,
    CAPTURE_HEALTH_NO_CONNECTION,
)
from src.tasks.localization.MinimapRealtimePosition import MinimapRealtimePosition  # 复用共享定位服务


class ManualWalkRecorder(MinimapRealtimePosition):
    """在开发者工具中记录可信的手动行走轨迹。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "导航路线采集"
        self.description = "从当前位置记录手动路线。等起点就绪后再走，到达后停止任务。"
        self.icon = FluentIcon.DEVELOPER_TOOLS
        self.default_config = {}
        self.config_description = {}

    def _trajectory_path(self):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        return Path("logs/navigation") / f"manual_walk_{stamp}.jsonl"

    def reset_to_start(self):  # 从用户当前所在的位置开始
        wait_login = self.wait_login  # 保留普通登录弹窗处理

        def check_login():  # 每次恢复界面时检查是否进入登录页
            if self.find_one(fL.logout) or self.find_one(fL.login_in):  # 检测登录入口
                raise RuntimeError("采集前需要手动登录游戏")  # 不自动登录
            return wait_login()  # 其他状态继续使用正常恢复流程

        with patch.object(self, "wait_login", side_effect=check_login):  # 仅采集准备阶段拦截登录
            self.ensure_main()  # 必要时退出菜单并回到大世界

    def verify_start_state(self):  # 采集前检查游戏状态
        assert self.in_world(), "当前不在大世界"  # 不从菜单开始采集

    def run(self):  # 有界采集用户手动走过的位置
        self.info_set("采集状态", "等待可信起点")
        if not self.in_world():
            self.reset_to_start()
        self.verify_start_state()
        service = self.ensure_runtime_position_service(force_start=True)  # 启动共享定位
        assert service, "定位服务不可用"  # 无服务时停止采集

        def check_capture_session(*, reason):  # 采集时不允许自动重新登录
            health = service.capture_session_health()  # 读取抓包连接状态
            if health and health.state in (
                CAPTURE_HEALTH_NO_CONNECTION,
                CAPTURE_HEALTH_MISSED_HANDSHAKE,
            ):  # 确认需要重建连接
                raise RuntimeError(f"抓包定位需要重新登录：{health.state}")  # 停止并交由用户处理
            return False  # 其他状态保持等待，不触发重新登录

        path = self._trajectory_path()
        path.parent.mkdir(parents=True, exist_ok=True)  # 创建输出目录
        self.info_set("轨迹文件", str(path))
        count = 0
        origin = None
        started = self.active_time()  # 记录起始时间
        with (
            patch.object(service, "ensure_capture_session", side_effect=check_capture_session),
            path.open("w", encoding="utf-8") as stream,
        ):  # 退出时恢复服务方法
            while self.active_time() - started < 300:  # 最多记录五分钟
                elapsed = self.active_time() - started
                self.info_set("采集进度", f"{int(elapsed)} / 300 秒 · {count} 条")
                state = self.world_pose(frame=self.next_frame(), max_age=0.6)  # 使用新帧采样
                if (
                    state and state.get("source") == "capture" and not state.get("position_trusted")
                ):  # 旧坐标也可能来自失效连接
                    check_capture_session(reason="抓包定位不可信")  # 需要重登时停止
                if state and state.get("position_trusted"):  # 只保留可信位置
                    sample = {key: state.get(key) for key in ("map_id", "x", "z")}  # 保存地图和坐标
                    sample["elapsed"] = self.active_time() - started  # 记录相对时间
                    stream.write(json.dumps(sample, ensure_ascii=False) + "\n")  # 写入一条样本
                    stream.flush()  # 使运行中可检查起点样本
                    count += 1
                    if origin is None:
                        origin = (sample["x"], sample["z"])
                        self.log_info("起点已就绪，可以开始走路线", notify=True)
                    distance = ((sample["x"] - origin[0]) ** 2 + (sample["z"] - origin[1]) ** 2) ** 0.5
                    self.info_set("采集状态", "正在记录 · 已移动" if distance >= 1 else "起点已就绪 · 等待移动")
                    self.info_set("当前位置", f'{sample["map_id"]} · X {sample["x"]:.2f} · Z {sample["z"]:.2f}')
                elif origin is not None:
                    self.info_set("采集状态", "定位暂不可用 · 等待恢复")
                self.sleep(0.5)  # 限制采样频率并允许停止
