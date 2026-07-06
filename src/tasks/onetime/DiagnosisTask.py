from qfluentwidgets import FluentIcon

from ok.task.DiagnosisTask import DiagnosisTask as OkDiagnosisTask


class DiagnosisTask(OkDiagnosisTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.group_name = "工具与调试"
        self.group_icon = FluentIcon.SEARCH
