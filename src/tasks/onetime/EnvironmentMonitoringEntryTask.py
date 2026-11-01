from qfluentwidgets import FluentIcon  # 保留前往观察点调试入口的图标

from src.tasks.onetime.EnvironmentMonitoringPhotoTask import EnvironmentMonitoringPhotoTask  # 复用共用流程


class EnvironmentMonitoringEntryTask(EnvironmentMonitoringPhotoTask):  # 仅传送的调试入口
    def __init__(self, *args, **kwargs):  # 保持原有任务配置与分组
        super().__init__(*args, **kwargs)  # 初始化共用流程与导航组件
        self.name = "环境监测：前往观察点"  # 明确当前只完成前半流程
        self.description = "接取或继续一个环境监测委托，传送到观察点后停止，暂不移动或拍照。"  # 调试入口不执行导航
        self.group_name = "工具与调试"  # 保留原有调试分组
        self.group_icon = FluentIcon.DEVELOPER_TOOLS  # 保留工具分组图标
        self.icon = FluentIcon.SEARCH  # 保留查找图标

    def run(self):  # 调试入口仅执行共用的传送前流程
        return self.prepare_observation()  # 自动前往支持的观察点，到达后停止
