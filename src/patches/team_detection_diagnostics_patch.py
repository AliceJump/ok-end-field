from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

_PATCH_INSTALLED = False
_DIAGNOSTIC_ATTR = "_team_detection_runtime_diagnostics_logged"


def _sha256_file(path: str | os.PathLike[str] | None) -> str:
    if not path:
        return "missing"
    file_path = Path(path)
    if not file_path.is_file():
        return "missing"
    digest = hashlib.sha256()
    try:
        with file_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        return f"error:{type(exc).__name__}"
    return digest.hexdigest()


def _format_box(box) -> tuple[int, int, int, int]:
    return int(box.x), int(box.y), int(box.width), int(box.height)


def _format_slot_results(slot_results) -> list[str]:
    formatted = []
    for index, (name, score, feature_name) in enumerate(slot_results, start=1):
        formatted.append(f"slot{index}={name}/{feature_name or '-'}:{score:.4f}")
    return formatted


def _log_team_detection_runtime_diagnostics(task, frame, slot_results) -> None:
    if getattr(task, _DIAGNOSTIC_ATTR, False):
        return
    if frame is None or getattr(frame, "size", 0) == 0:
        return

    setattr(task, _DIAGNOSTIC_ATTR, True)
    try:
        feature_set = getattr(getattr(task, "executor", None), "feature_set", None)
        coco_path = getattr(feature_set, "coco_json", None)
        resolved_coco = str(Path(coco_path).resolve()) if coco_path else "missing"
        coco_sha256 = _sha256_file(coco_path)

        frame_height, frame_width = frame.shape[:2]
        feature_width = getattr(feature_set, "width", 0) if feature_set is not None else 0
        feature_height = getattr(feature_set, "height", 0) if feature_set is not None else 0

        raw_boxes, valid_features = task._collect_team_candidate_boxes()
        search_boxes = task._build_search_boxes(raw_boxes, frame_width=frame_width, frame_height=frame_height)
        search_boxes.sort(key=lambda box: box.x)
        search_box_values = [_format_box(box) for box in search_boxes[:4]]

        task.log_info(
            "编队识别诊断环境: "
            f"frozen={bool(getattr(sys, 'frozen', False))}, "
            f"argv0={sys.argv[0]!r}, cwd={os.getcwd()!r}, "
            f"frame={frame_width}x{frame_height}, "
            f"feature_size={feature_width}x{feature_height}, "
            f"coco={resolved_coco!r}, coco_sha256={coco_sha256}"
        )
        task.log_info(
            "编队识别诊断搜索框: "
            f"battle_icon_candidates={len(valid_features)}, search_boxes={search_box_values}"
        )
        task.log_info(f"编队识别诊断结果: {_format_slot_results(slot_results)}")
    except Exception as exc:
        task.log_info(f"编队识别诊断采集失败: {type(exc).__name__}: {exc}")


def install_team_detection_diagnostics_patch() -> None:
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from src.tasks.mixin.battle_mixin import BattleMixin

    original_detect_team_core = BattleMixin._detect_team_core

    def detect_team_core_with_runtime_diagnostics(self, frame=None):
        slot_results = original_detect_team_core(self, frame)
        diagnostic_frame = frame if frame is not None else self.frame
        _log_team_detection_runtime_diagnostics(self, diagnostic_frame, slot_results)
        return slot_results

    BattleMixin._detect_team_core = detect_team_core_with_runtime_diagnostics
    _PATCH_INSTALLED = True
