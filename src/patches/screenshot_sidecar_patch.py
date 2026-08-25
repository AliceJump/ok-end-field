from __future__ import annotations

import json

from ok import Logger
from ok.gui.debug.Screenshot import Screenshot

from src.patches.screenshot_sidecar import build_sidecar, serialize_boxes

_PATCH_INSTALLED = False

logger = Logger.get_logger(__name__)


def _generate_with_sidecar(self, frame, ui_dict, folder, name, show_box, frame_box,
                           processor=None):
    """替代 generate_screen_shot：不再保存 _boxed.png，改为写 _boxes.json。

    逻辑与原实现一致：保存 _original.png 后，若有画框则把固定像素的框信息
    序列化到同名 _boxes.json（体积为 KB 级），恢复时可重绘出完全相同的
    _boxed.png（见 scripts/maintenance/restore_log_screenshots.py）。
    """
    pil_image = self.to_pil_image(frame, processor=processor)
    if pil_image is None:
        return None

    if frame_box is None:
        x_offset, y_offset = 0, 0
    else:
        x_offset, y_offset = -frame_box.x, -frame_box.y

    original_name = self.save_pil_image(name + "_original", folder, pil_image)

    if show_box and ui_dict:
        try:
            sidecar_name = original_name[:-len("_original.png")] + "_boxes.json"
            with open(sidecar_name, "w", encoding="utf-8") as sidecar_file:
                json.dump(
                    build_sidecar(
                        original_name.rsplit("\\", 1)[-1].rsplit("/", 1)[-1],
                        serialize_boxes(ui_dict, x_offset, y_offset),
                    ),
                    sidecar_file,
                    ensure_ascii=False,
                    indent=2,
                )
        except Exception as exc:
            logger.warning(f"write box sidecar failed: {sidecar_name} ({exc})")

    return original_name


_original_generate = Screenshot.generate_screen_shot


def _patched_generate_screen_shot(self, frame, ui_dict, folder, name, show_box,
                                  frame_box, processor=None):
    try:
        return _generate_with_sidecar(self, frame, ui_dict, folder, name, show_box,
                                      frame_box, processor)
    except Exception:
        logger.warning("generate_screen_shot with sidecar failed, fallback to original",
                       exc_info=True)
        return _original_generate(self, frame, ui_dict, folder, name, show_box,
                                  frame_box, processor)


def install_screenshot_sidecar_patch():
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return
    Screenshot.generate_screen_shot = _patched_generate_screen_shot
    _PATCH_INSTALLED = True
