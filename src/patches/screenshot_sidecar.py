# -*- coding: utf-8 -*-
"""截图画框信息的序列化与重绘（替代保存 _boxed.png 大图）。

ok-script 原本每张截图会额外保存一张画了模板匹配框的 ``_boxed.png``，
大小与原图几乎相同。本模块把框信息（坐标/名称/置信度/颜色，均为固定像素）
序列化到 ``_boxes.json`` 侧车文件，恢复时可精确重绘出与原 _boxed.png
完全一致的图片，见 ``scripts/restore_log_screenshots.py``。

本模块不依赖 ok-script / Qt，可被脚本独立调用。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

SIDECAR_SUFFIX = "_boxes.json"


def serialize_boxes(ui_dict: dict, x_offset: float = 0, y_offset: float = 0) -> list:
    """把 ok-script 的 ui_dict 画框快照序列化为可 JSON 化的列表。

    ui_dict 结构：``{key: [boxes, timestamp, QColor]}``，与
    ok.gui.debug.Screenshot 里绘制 _boxed.png 时的数据一致。

    Args:
        ui_dict: 画框快照。
        x_offset/y_offset: 截图时的 frame_box 偏移（-frame_box.x / -frame_box.y）。

    Returns:
        每个元素 ``{"name", "x", "y", "width", "height", "confidence",
        "color", "text"}``，坐标已加偏移，与 _boxed.png 上绘制的像素一致。
    """
    boxes = []
    for key, value in ui_dict.items():
        color = tuple([int(x) for x in value[2].getRgb()])
        for box in value[0]:
            width = box.width
            height = box.height
            if width <= 0 or height <= 0:
                continue
            name = box.name or key
            confidence = float(getattr(box, "confidence", 0) or 0)
            text = str(name)
            if confidence > 0:
                text += f"_{round(confidence * 100)}"
            boxes.append({
                "name": name,
                "x": box.x + x_offset,
                "y": box.y + y_offset,
                "width": width,
                "height": height,
                "confidence": confidence,
                "color": list(color),
                "text": text,
            })
    return boxes


def build_sidecar(image_name: str, boxes: list) -> dict:
    """生成 _boxes.json 侧车文件内容。"""
    return {
        "format": 1,
        "image": image_name,
        "boxes": boxes,
    }


def find_box_font():
    """与 ok-script 相同的字体查找顺序（微软雅黑 -> 宋体 -> Arial -> 默认）。"""
    from PIL import ImageFont

    fonts_dir = os.path.join(os.environ["WINDIR"], "Fonts")
    for candidate in ["msyh.ttc", "msyh.ttf", "simsun.ttc", "simsun.ttf",
                      "arial.ttf", "arial.ttc"]:
        path = os.path.join(fonts_dir, candidate)
        if os.path.exists(path):
            return ImageFont.truetype(path, 30)
    return ImageFont.load_default(size=30)


def render_boxed_image(sidecar: dict, original_path: str | Path, output_path: str | Path) -> None:
    """按侧车信息在原图上重绘画框，输出与 ok-script 的 _boxed.png 一致的图片。

    绘制参数与 ok.gui.debug.Screenshot.generate_screen_shot 保持一致：
    矩形 outline 颜色、线宽 2、框下方 8px 处写文本（stroke_width=1、
    stroke_fill=black）。
    """
    from PIL import Image, ImageDraw

    with Image.open(original_path) as img:
        draw = ImageDraw.Draw(img)
        font = find_box_font()
        for box in sidecar.get("boxes", []):
            color = tuple(box["color"])
            x, y = box["x"], box["y"]
            width, height = box["width"], box["height"]
            draw.rectangle([x, y, x + width, y + height], outline=color, width=2)
            draw.multiline_text((x, y + height + 8), box["text"], fill=color,
                                font=font, stroke_width=1, stroke_fill="black")
        img.save(output_path)


def sidecar_path_of(original_name: str | Path) -> Path:
    """由 _original.png 文件名推导对应的 _boxes.json 路径。"""
    original_name = Path(original_name)
    return original_name.with_name(original_name.stem[:-len("_original")] + SIDECAR_SUFFIX)


def rebuild_boxed_images(output_dir: str | Path) -> list[str]:
    """扫描目录下所有 _boxes.json，为对应的 _original.png 重绘 _boxed.png。

    Args:
        output_dir: 已解压/恢复后的目录（zip 全部内容所在目录）。

    Returns:
        已重绘的 _boxed.png 文件名列表。
    """
    output_dir = Path(output_dir)
    rebuilt = []
    for sidecar_path in sorted(output_dir.rglob(f"*{SIDECAR_SUFFIX}")):
        original_path = sidecar_path.with_name(
            sidecar_path.name[:-len(SIDECAR_SUFFIX)] + "_original.png")
        if not original_path.is_file():
            continue
        output_path = sidecar_path.with_name(
            sidecar_path.name[:-len(SIDECAR_SUFFIX)] + "_boxed.png")
        try:
            with open(sidecar_path, encoding="utf-8") as sidecar_file:
                sidecar = json.load(sidecar_file)
            render_boxed_image(sidecar, original_path, output_path)
        except Exception as exc:
            print(f"rebuild boxed failed for {sidecar_path}: {exc}")
            continue
        rebuilt.append(output_path.name)
    return rebuilt
