from qfluentwidgets import FluentIcon  # 工具与调试任务图标

from src.tasks.mixin.zip_line_mixin import ZipLineMixin  # 复用滑索任务运行环境
from src.tasks.test.zip_line_scan_recording import (  # 独立诊断存储
    DEFAULT_SCAN_DIR,  # 复用扫描记录辅助
    ZipLineScanRecorder,  # 复用扫描记录辅助
    draw_scan_candidates,  # 复用扫描记录辅助
)  # 完成当前配置


class ZipLineScanDebugTask(ZipLineMixin):  # 连续扫描当前游戏画面
    def __init__(self, *args, **kwargs):  # 保持标准任务初始化
        super().__init__(*args, **kwargs)  # 注册任务配置和停止机制
        self.name = "滑索扫描实测"  # 保持现有任务名称
        self.group_name = "工具与调试"  # 放入调试任务分组
        self.description = (  # 传入当前步骤参数
            "逐轮保存原图、带框图、候选 JSON 和三路数字 OCR 输入，复用滑索检测，不执行游戏交互。"  # 说明可见行为
        )  # 完成当前配置
        self.icon = FluentIcon.SEARCH  # 使用搜索图标
        self.visible = self.debug  # 仅在调试模式显示
        self.default_config.update({"扫描间隔(秒)": 0.15, "保存目录": DEFAULT_SCAN_DIR})  # 保留原间隔并增加输出目录
        self.config_description.update(  # 传入当前步骤参数
            {  # 提示时间与存储语义
                "扫描间隔(秒)": "每轮检测及存图完成后的等待秒数。",  # OCR 和写盘时间不计入等待
                "保存目录": "每次运行创建独立子目录，逐轮保留零命中及 OCR 漏检样本。",  # 防止误以为只保存成功结果
            }  # 完成当前配置
        )  # 配置帮助

    def run(self):  # 不点击、不转视角、不移动
        value = self.config.get("扫描间隔(秒)", 0.15)  # 读取已有持久化配置
        interval = max(0.0, float(0.15 if value is None or value == "" else value))  # 容错空值
        recorder = ZipLineScanRecorder(self, self.config.get("保存目录") or DEFAULT_SCAN_DIR, "live")  # 创建本次记录
        self.log_info(  # 传入当前步骤参数
            self.tr("滑索扫描记录保存至：{path}").format(path=recorder.directory),  # 使用固定翻译模板
            notify=True,  # 使用固定翻译模板
        )  # 展示结果位置
        index = 0  # 轮次从一开始
        try:  # 手动停止时清理实时叠加框
            while True:  # 用户要求持续实时扫描
                frame = self.next_frame()  # 每轮只取一次新帧
                if frame is not None:  # 暂时无画面时仍允许停止
                    index += 1  # 仅有效帧计入轮次
                    candidates = recorder.record(frame, index)  # 含零命中帧全部保存
                    draw_scan_candidates(self, candidates)  # 实时展示本轮图标框
                    if index == 1 or index % 20 == 0:  # 避免每轮刷屏
                        self.log_info(  # 传入当前步骤参数
                            self.tr("滑索扫描第 {index} 轮：{count} 个候选").format(  # 使用固定翻译模板
                                index=index,  # 传入当前步骤参数
                                count=len(candidates),  # 传入当前步骤参数
                            )  # 使用固定翻译模板
                        )  # 稳定翻译模板
                self.sleep(interval)  # 限速并交给框架处理暂停和停止
        finally:  # 保留已落盘记录
            self.clear_box()  # 清除残留实时框
