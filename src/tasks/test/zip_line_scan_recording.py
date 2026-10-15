import json  # 保存结构化扫描证据
import re  # 提取距离数字
from datetime import datetime  # 生成独立运行目录
from pathlib import Path  # 支持中文输入输出路径
from time import perf_counter  # 记录单轮检测耗时

import cv2  # 绘制与编码检测截图
import numpy as np  # 解码中文路径图片
from ok import Box  # 复用框架实时标注
from ok.feature.Box import relative_box  # 保持生产代码的坐标换算

from src.config import make_bottom_left_black  # 保存副本时遮挡 UID
from src.image.hsv_config import HSVRange  # 复用生产颜色范围
from src.tasks.mixin.zip_line_mixin import ZipLineMixin  # 复用 PR 检测算法

DEFAULT_SCAN_DIR = str(Path("tmp") / "zipline_scans")  # 避免启动时清空截图目录


class ZipLineFrameDetector(ZipLineMixin):  # 按输入图片尺寸检测而非当前游戏尺寸
    def __init__(self, frame, messages):  # 无需初始化交互任务
        self.scan_frame = frame  # 保留当前输入
        self.log_info = messages.append  # 收集生产检测日志

    @property  # 覆盖框架设备宽度
    def width(self):  # 回放图像使用自身宽度
        return self.scan_frame.shape[1]  # 不依赖设备连接

    @property  # 覆盖框架设备高度
    def height(self):  # 回放图像使用自身高度
        return self.scan_frame.shape[0]  # 不依赖设备连接

    def box_of_screen(self, x, y, to_x=1, to_y=1):  # 保留 BaseEfTask 三位小数舍入
        return relative_box(  # 按输入帧生成相同 OCR 区域
            self.width,  # 使用输入图片尺寸
            self.height,  # 使用输入图片尺寸
            round(x, 3),  # 保持生产坐标舍入
            round(y, 3),  # 使用图片真实尺寸
            to_x=round(to_x, 3),  # 保持生产坐标舍入
            to_y=round(to_y, 3),  # 与生产任务一致
        )  # 返回框架 Box


def read_scan_image(path):  # 支持含中文的截图路径
    frame = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)  # 读取彩色原图
    if frame is None:  # 明确报告损坏文件
        raise ValueError(f"Cannot decode image: {path}")  # 保留失败图片路径
    return frame  # 不对检测输入做脱敏修改


def replay_image_paths(path):  # 在运行前固定回放清单
    if path.is_file():  # 单图回放
        return [path]  # 由解码步骤验证文件内容
    if not path.is_dir():  # 不存在的路径
        return []  # 由任务报告输入错误
    return sorted(  # 目录回放顺序稳定
        item  # 传入当前步骤参数
        for item in path.rglob("*")  # 支持扫描结果的逐轮子目录
        if item.is_file()  # 传入当前步骤参数
        and item.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp", ".webp"}  # 仅图片
        and item.name != "boxed.png"  # 排除自身标注产物
        and not item.stem.endswith(("_boxed", "_crop", "_processed", "_white_gold"))  # 排除 OCR 输入副本
        and not any(part.endswith("_replay") for part in item.relative_to(path).parts[:-1])  # 排除旧回放结果目录
    )  # 新建输出不会加入当前回放清单


def _write_png(path, frame):  # 避免 OpenCV 中文路径写入失败
    success, encoded = cv2.imencode(".png", frame)  # 无损保存证据
    if not success:  # 不静默丢失截图
        raise OSError(f"Cannot encode image: {path}")  # 中止调试任务并报告错误
    encoded.tofile(path)  # 支持 Unicode 文件名


def _text_records(boxes):  # 保留所有 OCR 文本及置信度
    return [  # 保留原始顺序与杂字符证据
        {  # 传入当前步骤参数
            "text": item.name,  # 记录当前扫描数据
            "confidence": float(item.confidence),  # 不只保留匹配成功的数字
            "box": [int(item.x), int(item.y), int(item.width), int(item.height)],  # 记录当前扫描数据
        }  # 全图坐标
        for item in boxes  # 使用框架实际识别结果
    ]  # 转为可序列化数据


class ZipLineScanRecorder:  # 将调试存图与生产决策分离
    def __init__(self, task, output_dir, mode):  # 每次运行单独建目录
        self.task = task  # 使用应用当前 OCR 引擎与配置
        self.directory = Path(output_dir) / f"{datetime.now():%Y%m%d_%H%M%S_%f}_{mode}"  # 避免覆盖
        self.directory.mkdir(parents=True, exist_ok=False)  # 检查输出目录可写

    def record(self, frame, index, source=None):  # 每个有效扫描帧均保留证据
        directory = self.directory / f"{index:06d}"  # 零命中仍有独立轮次
        directory.mkdir()  # 不覆盖既有结果
        started = perf_counter()  # 包括检测和 OCR 耗时
        saved_frame = make_bottom_left_black(frame.copy())  # 仅遮挡保存副本的 UID
        _write_png(directory / "original.png", saved_frame)  # 在检测前保存输入证据
        annotated = saved_frame.copy()  # 不在检测输入上画框
        metadata = {  # 传入当前步骤参数
            "format_version": 1,  # 记录当前扫描数据
            "scan_index": index,  # 明确记录格式版本
            "captured_at": datetime.now().isoformat(),  # 记录当前扫描数据
            "source": str(source) if source else None,  # 记录来源
            "frame_size": [int(frame.shape[1]), int(frame.shape[0])],  # 记录当前扫描数据
            "candidates": [],  # 记录当前扫描数据
            "logs": [],  # 记录当前扫描数据
        }  # 零命中结构稳定
        candidates = metadata["candidates"]  # 异常时保留已经检测出的候选
        detector = ZipLineFrameDetector(frame, metadata["logs"])  # 复用生产检测与当前图像尺寸
        try:  # OCR 失败也留下原图、框图和 JSON
            candidates.extend(detector._detect_zip_line_icon_candidates(frame))  # 与 PR 相同的模板和阈值
            metadata["threshold"] = detector._ZIP_LINE_TEMPLATE_THRESHOLD  # 记录实际模板阈值
            cx, cy, rx, ry = detector._zip_line_ellipse_geometry()  # 使用生产搜索范围
            metadata["ellipse"] = [cx, cy, rx, ry]  # 可从 JSON 重建搜索区域
            cv2.ellipse(annotated, (cx, cy), (rx, ry), 0, 0, 360, (255, 160, 0), 1)  # 标出搜索范围
            cv2.rectangle(  # 使用实色背景保证结果文字在复杂画面上可读
                annotated,  # 传入当前步骤参数
                (10, 8),  # 传入当前步骤参数
                (  # 传入当前步骤参数
                    min(detector.width - 1, 1120),  # 传入当前步骤参数
                    min(detector.height - 1, 70 + len(candidates) * 24),  # 传入当前步骤参数
                ),  # 传入当前步骤参数
                (20, 20, 20),  # 传入当前步骤参数
                -1,  # 传入当前步骤参数
            )  # 只修改带框图，原图单独保存
            for number, candidate in enumerate(candidates, 1):  # 扫描全部候选
                color = (0, 255, 0) if candidate["state"] == "white" else (0, 220, 255)  # 区分未锁定与锁定图标
                x, y, w, h = (candidate[key] for key in ("x", "y", "width", "height"))  # 保留模板坐标
                cv2.rectangle(annotated, (x, y), (x + w, y + h), color, 2)  # OCR 失败也保留图标框
                box = detector._zip_line_distance_box(candidate)  # 完全复用 PR 数字区域
                candidate["ocr_box"] = [box.x, box.y, box.width, box.height]  # 保留生产 OCR 坐标
                crop = frame[box.y : box.y + box.height, box.x : box.x + box.width]  # 从未标注原帧取数字
                _write_png(directory / f"{number:02d}_crop.png", crop)  # 保存实际 OCR 原图输入
                ranges = HSVRange.WHITE if candidate["state"] == "white" else HSVRange.GOLD_TEXT  # 保持 PR 分色规则
                processors = {  # 传入当前步骤参数
                    "pr": detector.make_hsv_isolator(ranges),  # PR 生产颜色过滤
                    "white_gold": detector.make_hsv_isolator(  # 记录当前扫描数据
                        tuple(HSVRange.WHITE) + tuple(HSVRange.GOLD_TEXT)  # 合并白色与黄色范围
                    ),  # 白黄合并对照
                    "raw": None,  # 记录当前扫描数据
                }  # 原图对照不影响生产行为
                candidate["ocr"] = {}  # 三路结果分别记录
                for method, processor in processors.items():  # 保持模型与区域一致
                    if processor is not None:  # 原图已经保存裁剪输入
                        suffix = "processed" if method == "pr" else "white_gold"  # 区分过滤输入
                        _write_png(directory / f"{number:02d}_{suffix}.png", processor(crop))  # 保存实际处理图
                    boxes = self.task.ocr(box=box, frame=frame, frame_processor=processor)  # 不提前过滤识别文本
                    candidate["ocr"][method] = _text_records(boxes)  # 空结果同样保留
                cv2.rectangle(  # 传入当前步骤参数
                    annotated,  # 传入当前步骤参数
                    (box.x, box.y),  # 传入当前步骤参数
                    (box.x + box.width, box.y + box.height),  # 传入当前步骤参数
                    color,  # 传入当前步骤参数
                    1,  # 传入当前步骤参数
                )  # 标出数字区域
                texts = {  # 传入当前步骤参数
                    method: "/".join(  # 传入当前步骤参数
                        item["text"]  # 传入当前步骤参数
                        for item in records  # 遍历本轮检测结果
                        if re.search(r"\d", item["text"])  # 遍历本轮检测结果
                    )  # 遍历本轮检测结果
                    or "MISS"  # 只在图上显示数字
                    for method, records in candidate["ocr"].items()  # 遍历本轮检测结果
                }  # JSON 仍保留全部文本
                label = f"#{number} {candidate['state']} {candidate['score']:.2f} PR:{texts['pr']} RAW:{texts['raw']} WG:{texts['white_gold']}"  # 明确结果来源
                cv2.putText(  # 顶部逐行显示，避免边缘候选文字被裁掉
                    annotated,  # 传入当前步骤参数
                    label,  # 传入当前步骤参数
                    (20, 65 + (number - 1) * 24),  # 传入当前步骤参数
                    cv2.FONT_HERSHEY_SIMPLEX,  # 传入当前步骤参数
                    0.6,  # 传入当前步骤参数
                    color,  # 传入当前步骤参数
                    1,  # 传入当前步骤参数
                )  # 显示三路检测结果
                cv2.putText(  # 就近标注候选编号以便对应顶部结果
                    annotated,  # 传入当前步骤参数
                    f"#{number}",  # 传入当前步骤参数
                    (x + w + 3, y + h),  # 传入当前步骤参数
                    cv2.FONT_HERSHEY_SIMPLEX,  # 传入当前步骤参数
                    0.5,  # 传入当前步骤参数
                    color,  # 传入当前步骤参数
                    1,  # 传入当前步骤参数
                )  # 避免相邻图标的长标签相互覆盖
        except Exception as error:  # 保留诊断异常但不中断证据落盘
            metadata["error"] = f"{type(error).__name__}: {error}"  # 保留错误原因
            raise  # 停止请求与实际故障均交回框架处理
        finally:  # 正常、漏检、停止或 OCR 异常均保存当前轮
            metadata["elapsed_ms"] = round((perf_counter() - started) * 1000, 2)  # 记录性能证据
            cv2.putText(  # 传入当前步骤参数
                annotated,  # 传入当前步骤参数
                f"SCAN {index} | CANDIDATES {len(candidates)}",  # 传入当前步骤参数
                (20, 35),  # 传入当前步骤参数
                cv2.FONT_HERSHEY_SIMPLEX,  # 传入当前步骤参数
                0.8,  # 传入当前步骤参数
                (0, 255, 0),  # 传入当前步骤参数
                2,  # 传入当前步骤参数
            )  # 零命中也明确显示
            _write_png(directory / "boxed.png", annotated)  # 保存带框图
            (directory / "result.json").write_text(  # 传入当前步骤参数
                json.dumps(metadata, ensure_ascii=False, indent=2),  # 传入当前步骤参数
                encoding="utf-8",  # 传入当前步骤参数
            )  # 保存中文日志
        return candidates  # 提供实时框显示


def draw_scan_candidates(task, candidates):  # 实时叠加框与保存标注相互独立
    task.clear_box()  # 清除上一轮图标，避免零命中时残留
    for state, color in (("white", "green"), ("gold", "blue")):  # 仅使用框架支持的颜色
        boxes = []  # 当前颜色候选
        for candidate in candidates:  # 保留生产排序
            if candidate["state"] == state:  # 仅绘制对应颜色
                box = Box(candidate["x"], candidate["y"], candidate["width"], candidate["height"])  # 图标位置
                box.name = f"zipline_{state}_{candidate['score']:.2f}"  # 显示状态与分数
                box.confidence = candidate["score"]  # 保留检测置信度
                boxes.append(box)  # 当前帧候选
        task.draw_boxes(f"zipline_scan_{state}", boxes, color=color, debug=True)  # 更新实时叠加框
