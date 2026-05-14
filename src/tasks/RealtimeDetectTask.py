from src.tasks.Test import Test


class RealtimeDetectTask(Test):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "实时检测"
        self.description = "实时循环执行 YOLO 检测，用于在线观察目标识别结果。"
