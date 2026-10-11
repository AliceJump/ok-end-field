from pathlib import Path  # 支持单张截图和目录输入

from src.tasks.test.zip_line_scan_recording import (  # 回放辅助
    DEFAULT_SCAN_DIR,  # 复用扫描记录辅助
    ZipLineScanRecorder,  # 复用扫描记录辅助
    read_scan_image,  # 复用扫描记录辅助
    replay_image_paths,  # 复用扫描记录辅助
)  # 完成当前配置
from src.tasks.test.ZipLineScanDebugTask import ZipLineScanDebugTask  # 复用调试任务配置


class ZipLineReplayDebugTask(ZipLineScanDebugTask):  # 离线截图回放任务
    def __init__(self, *args, **kwargs):  # 使用标准任务注册
        super().__init__(*args, **kwargs)  # 保留保存目录和调试可见性
        self.name = "滑索截图回放"  # 独立调试任务
        self.description = (  # 传入当前步骤参数
            "读取单张截图或截图目录，按原图尺寸检测滑索和距离，保存原图、带框图及检测信息。"  # 说明输入与输出
        )  # 完成当前配置
        self.needs_frame = False  # 无需游戏连接和新帧
        self.default_config.pop("扫描间隔(秒)", None)  # 回放不提供实时扫描间隔
        self.config_description.pop("扫描间隔(秒)", None)  # 移除对应帮助
        self.default_config.update({"截图路径": ""})  # 用户指定本地截图
        self.config_description.update(  # 传入当前步骤参数
            {"截图路径": "待检测图片或目录的完整路径；目录递归读取原图，排除带框图和 OCR 裁剪图。"}  # 传入当前步骤参数
        )  # 提示目录行为

    def run(self):  # 不读取当前游戏帧
        value = str(self.config.get("截图路径") or "").strip().strip('"')  # 接受复制的带引号路径
        if not value:  # 必须有输入
            raise ValueError(self.tr("请设置截图路径。"))  # 使用稳定提示
        paths = replay_image_paths(Path(value))  # 写盘前固定输入清单
        if not paths:  # 明确报告没有可检测图片
            raise ValueError(self.tr("截图路径中没有可回放图片。"))  # 不静默完成
        recorder = ZipLineScanRecorder(  # 传入当前步骤参数
            self,  # 传入当前步骤参数
            self.config.get("保存目录") or DEFAULT_SCAN_DIR,  # 传入当前步骤参数
            "replay",  # 传入当前步骤参数
        )  # 独立回放结果目录
        self.log_info(  # 传入当前步骤参数
            self.tr("滑索扫描记录保存至：{path}").format(path=recorder.directory),  # 使用固定翻译模板
            notify=True,  # 使用固定翻译模板
        )  # 展示可检查位置
        try:  # 正常完成或停止时都清除 OCR 自动叠加框
            for index, path in enumerate(paths, 1):  # 逐张完整保存
                recorder.record(read_scan_image(path), index, source=path)  # OCR 按图片尺寸执行
                self.sleep(0.01)  # 图片之间允许框架处理手动停止
        finally:  # 已保存结果不会被清除
            self.clear_box()  # 不遗留离线图片的 OCR 框
        self.log_info(  # 传入当前步骤参数
            self.tr("滑索截图回放完成：{count} 张图片，结果：{path}").format(  # 使用固定翻译模板
                count=len(paths),  # 传入当前步骤参数
                path=recorder.directory,  # 传入当前步骤参数
            ),  # 使用固定翻译模板
            notify=True,  # 传入当前步骤参数
        )  # 输出完成位置
