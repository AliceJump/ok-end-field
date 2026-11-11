"""MCP 手动行走轨迹采集示例；只读取定位，不发送游戏输入。"""

import json  # 将可信坐标保存为 JSONL
from pathlib import Path  # 创建轨迹文件

from src.tasks.localization.MinimapRealtimePosition import MinimapRealtimePosition  # 复用共享定位服务


class Task(MinimapRealtimePosition):  # MCP 临时采集任务
    def reset_to_start(self):  # 从用户当前所在的位置开始
        pass  # 不改变游戏状态

    def verify_start_state(self):  # 采集前检查游戏状态
        assert self.in_world(), "当前不在大世界"  # 不从菜单开始采集

    def run(self):  # 有界采集用户手动走过的位置
        assert self.ensure_runtime_position_service(force_start=True), "定位服务不可用"  # 启动共享定位
        path = Path("logs/navigation/manual_walk.jsonl")  # 每次运行覆盖上一次轨迹
        path.parent.mkdir(parents=True, exist_ok=True)  # 创建输出目录
        started = self.active_time()  # 记录起始时间
        with path.open("w", encoding="utf-8") as stream:  # 实时写入便于检查与中止后读取
            while self.active_time() - started < 300:  # 最多记录五分钟
                state = self.world_pose(frame=self.next_frame(), max_age=0.6)  # 使用新帧采样
                if state and state.get("position_trusted"):  # 只保留可信位置
                    sample = {key: state.get(key) for key in ("map_id", "x", "z")}  # 保存地图和坐标
                    sample["elapsed"] = self.active_time() - started  # 记录相对时间
                    stream.write(json.dumps(sample, ensure_ascii=False) + "\n")  # 写入一条样本
                    stream.flush()  # 使运行中可检查起点样本
                self.sleep(0.5)  # 限制采样频率并允许停止
