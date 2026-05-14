from qfluentwidgets import FluentIcon

from src.config import config as app_config
from src.tasks.BaseEfTask import BaseEfTask
from src.yolo.model_registry import build_yolo_model_settings


class Test(BaseEfTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "YOLO实测扫描"
        self.description = "用于实测 YOLO 模型：可选模型、可选目标、可设置信度并循环扫描。"
        self.icon = FluentIcon.SEARCH

        default_model, model_settings = build_yolo_model_settings(app_config.get("yolo", {}))
        model_options = list(model_settings.keys())
        target_options = sorted(
            {
                str(name)
                for name in model_settings.get(default_model, {}).get("labels", {}).values()
                if name is not None
            }
        )
        default_target = target_options[0] if target_options else "battle_end"

        self.default_config.update(
            {
                "YOLO模型": default_model,
                "检测目标": default_target,
                "检测置信度": 0.7,
                "扫描间隔(秒)": 0.2,
            }
        )
        self.config_description.update(
            {
                "YOLO模型": "选择模型配置（来自 src/yolo/models.py）",
                "检测目标": "选择模型 labels 的 value 作为目标类别",
                "检测置信度": "0~1，值越高越严格",
                "扫描间隔(秒)": "每次检测后的等待时间",
            }
        )
        self.config_type["YOLO模型"] = {"type": "drop_down", "options": model_options}
        self.config_type["检测目标"] = {"type": "drop_down", "options": target_options}

    def run(self):
        model_key = str(self.config.get("YOLO模型", self.default_config.get("YOLO模型", ""))).strip()
        target_name = str(self.config.get("检测目标", self.default_config.get("检测目标", ""))).strip()
        conf = float(self.config.get("检测置信度", 0.7) or 0.7)
        conf = max(0.0, min(1.0, conf))
        interval = max(0.0, float(self.config.get("扫描间隔(秒)", 0.2) or 0.2))

        self.set_yolo_model(model_key)
        available_targets = self.list_yolo_targets(model_key)
        if target_name not in available_targets:
            raise ValueError(f"检测目标[{target_name}]不在模型[{model_key}]labels中，可选: {available_targets}")

        self.log_info(
            f"开始YOLO实测扫描: model={model_key}, target={target_name}, conf={conf:.2f}",
            notify=True,
        )

        scan_count = 0
        found_count = 0
        max_conf = 0.0
        try:
            while True:
                scan_count += 1
                results = self.yolo_detect(name=target_name, conf=conf, model_key=model_key)
                if results:
                    found_count += 1
                    top_conf = float(results[0].confidence or 0.0)
                    max_conf = max(max_conf, top_conf)
                    self.log_info(f"[{scan_count}] 检测到 {len(results)} 个目标, top_conf={top_conf:.3f}")
                else:
                    self.log_info(f"[{scan_count}] 未检测到目标")
                self.sleep(interval)
        finally:
            self.log_info(
                f"YOLO实测结束: 总扫描={scan_count}, 命中轮次={found_count}, 最高置信度={max_conf:.3f}",
                notify=True,
            )
