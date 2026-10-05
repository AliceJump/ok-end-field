"""小地图定位采样记录（工具与调试）。

本任务不参与导航，也不修改正式配置。它按固定节拍请求共享
``MinimapPositionTask`` 采样一帧，并把每拍的必要信息落到磁盘：

- 融合状态：``x/z``、WS、``error``、``position_trusted``、``sync_seq`` 等；
- 原始里程计结果：``dx_px/dy_px``、``dmap_px``、``response``、``dt``、``reason``、
  ``committed``、``reanchored``；
- 连续里程计轨迹：把融合重锚会清零的 ``position_px()`` 接成不断线的 ``odom_px``，
  并给出逐拍增量 ``step_px``；
- 每一次真实静止校准的原始融合残差 ``sync_residual.dist`` 和锚定链校正残差
  ``sync_residual.visual_anchor_chain_dist``；
- 每拍处理好的小地图圆环切片（BGRA PNG，alpha 是羽化 mask），用于按像素拼接。

产物目录（默认 ``logs/minimap_position_recorder/<时间戳>/``）：

- ``samples.jsonl``：逐拍记录；
- ``calibrations.jsonl``：每次真实校准（含首次锚定）的残差；
- ``patches/``：小地图圆环切片；
- ``stitch_*_fused*.png``：按融合绝对坐标放置的拼接图；
- ``stitch_*_odom*.png``：按连续里程计轨迹放置的拼接图；
- ``summary.json``：位移/残差/响应统计；
- ``run.json``：本次运行配置、比例尺和轴映射。

定位能力仍由 ``MinimapPositionTask`` 统一维护，本任务只负责采样、记录和离线参考。
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

from qfluentwidgets import FluentIcon

from src.core.BaseEfTask import BaseEfTask
from src.localization.minimap_odometry import _reraise_control_flow
from src.localization.minimap_sample_stitch import (
    ContinuousOdomTrack,
    crop_minimap_patch,
    fused_map_px,
    imwrite_unicode,
    stitch_records,
    summarize_records,
    write_jsonl,
)
from src.tasks.mixin.runtime_state_mixin import RuntimeStateMixin

CONFIG_INTERVAL = "采样间隔(秒)"
CONFIG_DURATION = "运行时长(秒)"
CONFIG_SAVE_DIR = "保存目录"
CONFIG_STITCH = "生成拼接图"
CONFIG_MAX_PATCHES = "最大切片数"
CONFIG_STITCH_MARGIN = "拼接图边距(像素)"
CONFIG_EDGE_MAX_GAP = "位姿图最大边间隔(拍)"
CONFIG_MIN_RESPONSE = "位姿图最小相关响应"
CONFIG_ANCHOR_WEIGHT = "位姿图锚点权重"

DEFAULT_SAVE_DIR = "logs/minimap_position_recorder"


class MinimapPositionRecorder(RuntimeStateMixin, BaseEfTask):
    """逐拍记录小地图位移、静止校准误差，并生成像素对齐参考图。"""

    requires_foreground = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._init_runtime_state_mixin()
        self.name = "小地图定位采样记录"
        self.group_name = "工具与调试"
        self.group_icon = FluentIcon.DEVELOPER_TOOLS
        self.description = "逐拍记录小地图位移、静止校准误差和融合状态，并拼接像素对齐的小地图参考图"
        self.visible = self.debug

        self.default_config = {
            CONFIG_INTERVAL: 0.2,
            CONFIG_DURATION: 60.0,
            CONFIG_SAVE_DIR: DEFAULT_SAVE_DIR,
            CONFIG_STITCH: True,
            CONFIG_MAX_PATCHES: 0,
            CONFIG_STITCH_MARGIN: 128,
            CONFIG_EDGE_MAX_GAP: 30,
            CONFIG_MIN_RESPONSE: 0.25,
            CONFIG_ANCHOR_WEIGHT: 50.0,
        }
        self.config_description = {
            CONFIG_INTERVAL: "固定采样周期；建议与「小地图定位」的触发周期一致（默认 0.2s）",
            CONFIG_DURATION: "本次记录持续多久后自动停止并生成报告",
            CONFIG_SAVE_DIR: "记录数据与拼接图的根目录；每次运行会在其下新建时间戳子目录",
            CONFIG_STITCH: "结束后把小地图切片按像素位置拼接成参考图",
            CONFIG_MAX_PATCHES: "最多保存多少张小地图切片，0=不限制",
            CONFIG_STITCH_MARGIN: "拼接画布四周保留的空白边距",
            CONFIG_EDGE_MAX_GAP: "位姿图时间近邻的最大间隔（拍）；越大越稳但计算越慢",
            CONFIG_MIN_RESPONSE: "切片对相位相关的最低响应；低于该值的边不进入位姿图",
            CONFIG_ANCHOR_WEIGHT: "静止校准 WS 绝对锚点的权重；越大越贴近 WS",
        }

    # ------------------------------------------------------------------ #
    # 配置读取
    # ------------------------------------------------------------------ #
    def _cfg_float(self, key, default: float) -> float:
        try:
            return float(self.config.get(key, default))
        except (TypeError, ValueError):
            return float(default)

    def _cfg_int(self, key, default: int) -> int:
        try:
            return int(self.config.get(key, default))
        except (TypeError, ValueError):
            return int(default)

    def _cfg_bool(self, key, default: bool) -> bool:
        value = self.config.get(key, default)
        if isinstance(value, str):
            return value.strip().lower() not in {"", "0", "false", "no", "off", "否"}
        return bool(value)

    # ------------------------------------------------------------------ #
    # 主流程
    # ------------------------------------------------------------------ #
    def run(self):
        self.log_info("=== 小地图定位采样记录 ===", notify=True)
        if not self.in_world():
            self.log_info("当前不在大世界画面，无法读取小地图。请先进入大世界。", notify=True)
            return

        position_task = self.ensure_runtime_position_service(force_start=True)
        if position_task is None:
            return

        interval = max(0.05, self._cfg_float(CONFIG_INTERVAL, 0.2))
        duration = max(interval, self._cfg_float(CONFIG_DURATION, 60.0))
        max_patches = max(0, self._cfg_int(CONFIG_MAX_PATCHES, 0))
        stitch_enabled = self._cfg_bool(CONFIG_STITCH, True)
        stitch_margin = max(0, self._cfg_int(CONFIG_STITCH_MARGIN, 128))
        edge_max_gap = max(1, self._cfg_int(CONFIG_EDGE_MAX_GAP, 30))
        min_response = max(0.0, self._cfg_float(CONFIG_MIN_RESPONSE, 0.25))
        anchor_weight = max(0.0, self._cfg_float(CONFIG_ANCHOR_WEIGHT, 50.0))
        save_root = Path(
            str(self.config.get(CONFIG_SAVE_DIR, DEFAULT_SAVE_DIR) or "").strip()
            or DEFAULT_SAVE_DIR
        )
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        run_dir = save_root / stamp
        patch_dir = run_dir / "patches"
        try:
            patch_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.log_error(f"创建记录目录失败: {run_dir}: {exc}", notify=True)
            return

        fusion = position_task.minimap_fusion
        if fusion is None:
            self.log_error("小地图融合器未初始化，无法记录。", notify=True)
            return
        map_to_world_px = fusion.map_to_world_px
        scale = float(getattr(position_task, "_minimap_scale", 0.0) or 0.0)
        task_w = int(getattr(position_task, "width", 0) or 0)
        task_h = int(getattr(position_task, "height", 0) or 0)

        records: list[dict] = []
        calibrations: list[dict] = []
        odom_track = ContinuousOdomTrack()
        prev_map_id = None
        prev_anchor_set = None
        prev_position_px = None
        prev_distance_since_sync = None
        profile_signature = None
        segment = 0
        saved_patches = 0
        patch_limit_logged = False
        start = self.active_time()
        started_wall = datetime.now().isoformat(timespec="milliseconds")
        next_at = start
        iteration = 0
        overruns = 0
        max_period = 0.0
        prev_tick = None

        self.log_info(
            f"开始记录: interval={interval:.2f}s duration={duration:.1f}s "
            f"scale={scale:.6f} matrix={map_to_world_px} out={run_dir}",
            notify=True,
        )

        try:
            while True:
                next_at += interval
                now = self.active_time()
                if next_at > now:
                    self.sleep(next_at - now)
                elif now - next_at > interval:
                    overruns += 1
                    next_at = now
                now = self.active_time()
                if now - start >= duration:
                    break
                if prev_tick is not None:
                    max_period = max(max_period, now - prev_tick)
                prev_tick = now
                iteration += 1

                try:
                    frame = self.next_frame()
                except Exception as exc:
                    _reraise_control_flow(exc)
                    self.log_warning(f"next_frame 失败: {exc}")
                    continue
                if frame is None:
                    continue
                if task_w <= 0 or task_h <= 0:
                    task_h, task_w = frame.shape[:2]

                try:
                    state = self.world_pose(
                        frame=frame,
                        max_age=max(0.5, interval * 3.0),
                        now=now,
                    )
                except Exception as exc:
                    _reraise_control_flow(exc)
                    self.log_warning(f"world_pose 失败: {exc}")
                    continue
                if state is None:
                    self.log_warning("运行时定位状态不可用或已过期")
                    continue

                odometry = position_task.minimap_odometry
                fusion_now = position_task.minimap_fusion
                if fusion_now is not None:
                    map_to_world_px = fusion_now.map_to_world_px
                scale_now = float(getattr(position_task, "_minimap_scale", 0.0) or 0.0)
                task_w_now = int(getattr(position_task, "width", 0) or task_w)
                task_h_now = int(getattr(position_task, "height", 0) or task_h)
                profile_now = (
                    int(task_w_now),
                    int(task_h_now),
                    float(scale_now),
                    tuple(tuple(float(v) for v in row) for row in map_to_world_px),
                )
                profile_changed = profile_signature is not None and profile_now != profile_signature
                if profile_changed:
                    segment += 1
                    odom_track.reset()
                    prev_position_px = None
                    prev_distance_since_sync = None
                    profile_signature = profile_now
                scale = scale_now
                task_w = task_w_now
                task_h = task_h_now
                last = odometry.last_result() if odometry is not None else None
                last = last if isinstance(last, dict) else {}
                last_sample = odometry.last_sample() if odometry is not None else None
                last_sample = last_sample if isinstance(last_sample, dict) else {}
                try:
                    position_px = (
                        odometry.position_px()
                        if odometry is not None
                        else (0.0, 0.0)
                    )
                except Exception:
                    position_px = (0.0, 0.0)

                map_id = state.get("map_id")
                anchor_set = bool(state.get("anchor_set"))
                just_synced = bool(state.get("just_synced"))
                map_changed = prev_map_id is not None and map_id != prev_map_id
                lost_anchor = bool(prev_anchor_set) and not anchor_set

                if map_changed:
                    segment += 1
                    odom_track.reset()
                    prev_position_px = None
                    prev_distance_since_sync = None
                reset_anchor = bool(just_synced or (lost_anchor and not map_changed))
                odom_px, step_px = odom_track.update(position_px, reset_anchor=reset_anchor)

                fused_px = fused_map_px(
                    map_to_world_px,
                    state.get("x"),
                    state.get("z"),
                )

                patch_rel = None
                patch_meta: dict = {}
                can_save = max_patches == 0 or saved_patches < max_patches
                if can_save:
                    try:
                        patch, patch_meta = crop_minimap_patch(frame, task_w, task_h)
                    except Exception as exc:
                        patch = None
                        self.log_warning(f"裁剪小地图切片失败: {exc}")
                    if patch is not None:
                        patch_name = f"{iteration:06d}_{now:.3f}.png"
                        patch_rel = f"patches/{patch_name}"
                        if not imwrite_unicode(run_dir / patch_rel, patch):
                            self.log_warning(f"切片写入失败: {run_dir / patch_rel}")
                            patch_rel = None
                        else:
                            saved_patches += 1
                elif not patch_limit_logged:
                    patch_limit_logged = True
                    self.log_info(f"已达到最大切片数 {max_patches}，后续只记录数值不再保存图片")

                rest_diag = position_task.minimap_rest_diag() or {}
                residual = state.get("sync_residual")
                if not isinstance(residual, dict):
                    residual = None
                step_len = math.hypot(float(step_px[0]), float(step_px[1]))
                record = {
                    "i": iteration,
                    "wall": datetime.now().isoformat(timespec="milliseconds"),
                    "t": float(now),
                    "segment": int(segment),
                    "map_id": map_id,
                    "width": int(task_w),
                    "height": int(task_h),
                    "scale_m_per_px": scale if scale > 0 else None,
                    "map_to_world_px": map_to_world_px,
                    "x": state.get("x"),
                    "z": state.get("z"),
                    "y": state.get("y"),
                    "heading": state.get("heading"),
                    "heading_score": state.get("heading_score"),
                    "ws": state.get("ws"),
                    "ws_xyz": state.get("ws_xyz"),
                    "error_m": state.get("error"),
                    "fused_px": None if fused_px is None else [fused_px[0], fused_px[1]],
                    "odom_px": [float(odom_px[0]), float(odom_px[1])],
                    "step_px": [float(step_px[0]), float(step_px[1])],
                    "step_len_px": float(step_len),
                    "step_m": None if scale <= 0 else float(step_len * scale),
                    "position_px": [float(position_px[0]), float(position_px[1])],
                    "anchor_dmap_px": last.get("dmap_px"),
                    "odom_dx_px": last.get("dx_px"),
                    "odom_dy_px": last.get("dy_px"),
                    "odom_response": last.get("response"),
                    "odom_dt": last.get("dt"),
                    "odom_ok": last.get("ok"),
                    "odom_sampled": last.get("sampled"),
                    "odom_reason": last.get("reason"),
                    "odom_committed": last.get("committed"),
                    "odom_reanchored": last.get("reanchored"),
                    "odom_benign_reanchor": last.get("benign_reanchor"),
                    "last_sample_reason": last_sample.get("reason"),
                    "last_sample_dmap_px": last_sample.get("dmap_px"),
                    "position_trusted": state.get("position_trusted"),
                    "trust_reason": state.get("trust_reason"),
                    "raw_x": state.get("raw_x"),
                    "raw_z": state.get("raw_z"),
                    "visual_anchor_chain_x": state.get("visual_anchor_chain_x"),
                    "visual_anchor_chain_z": state.get("visual_anchor_chain_z"),
                    "visual_anchor_chain_correction_px": state.get(
                        "visual_anchor_chain_correction_px"
                    ),
                    "visual_anchor_chain_correction_m": state.get(
                        "visual_anchor_chain_correction_m"
                    ),
                    "visual_anchor_chain_nodes": state.get("visual_anchor_chain_nodes"),
                    "visual_anchor_chain_edges": state.get("visual_anchor_chain_edges"),
                    "visual_anchor_chain_anchors": state.get("visual_anchor_chain_anchors"),
                    "visual_anchor_chain_iterations": state.get("visual_anchor_chain_iterations"),
                    "visual_anchor_chain_min_response": state.get(
                        "visual_anchor_chain_min_response"
                    ),
                    "visual_anchor_chain_median_response": state.get(
                        "visual_anchor_chain_median_response"
                    ),
                    "visual_anchor_chain_residual_median": state.get(
                        "visual_anchor_chain_residual_median"
                    ),
                    "visual_anchor_chain_residual_max": state.get(
                        "visual_anchor_chain_residual_max"
                    ),
                    "visual_anchor_chain_reason": state.get("visual_anchor_chain_reason"),
                    "visual_anchor_chain_keyframe_reason": state.get(
                        "visual_anchor_chain_keyframe_reason"
                    ),
                    "visual_anchor_chain_keyframe_correction_m": state.get(
                        "visual_anchor_chain_keyframe_correction_m"
                    ),
                    "visual_anchor_chain_keyframe_nodes": state.get(
                        "visual_anchor_chain_keyframe_nodes"
                    ),
                    "visual_anchor_chain_keyframe_edges": state.get(
                        "visual_anchor_chain_keyframe_edges"
                    ),
                    "visual_anchor_chain_keyframe_anchors": state.get(
                        "visual_anchor_chain_keyframe_anchors"
                    ),
                    "visual_anchor_chain_keyframe_iterations": state.get(
                        "visual_anchor_chain_keyframe_iterations"
                    ),
                    "visual_anchor_chain_keyframe_min_response": state.get(
                        "visual_anchor_chain_keyframe_min_response"
                    ),
                    "visual_anchor_chain_keyframe_median_response": state.get(
                        "visual_anchor_chain_keyframe_median_response"
                    ),
                    "visual_anchor_chain_keyframe_residual_median": state.get(
                        "visual_anchor_chain_keyframe_residual_median"
                    ),
                    "visual_anchor_chain_keyframe_residual_max": state.get(
                        "visual_anchor_chain_keyframe_residual_max"
                    ),
                    "anchor_set": anchor_set,
                    "rest": state.get("rest"),
                    "rest_reason": rest_diag.get("reason"),
                    "map_speed_m_s": rest_diag.get("map_speed_m_s"),
                    "ws_moved_m": rest_diag.get("ws_moved_m"),
                    "sync_seq": state.get("sync_seq"),
                    "just_synced": just_synced,
                    "sync_redundant": state.get("sync_redundant"),
                    "sync_checked": state.get("sync_checked"),
                    "sync_needed": state.get("sync_needed"),
                    "distance_since_sync_m": state.get("distance_since_sync"),
                    "sync_residual": residual,
                    "patch": patch_rel,
                    "patch_size": patch_meta.get("size"),
                    "patch_center": patch_meta.get("center"),
                    "patch_r_inner": patch_meta.get("r_inner"),
                    "patch_r_outer": patch_meta.get("r_outer"),
                    "region": patch_meta.get("region"),
                }
                records.append(record)

                if just_synced:
                    calibrations.append(
                        {
                            "i": iteration,
                            "wall": record["wall"],
                            "t": float(now),
                            "segment": int(segment),
                            "map_id": map_id,
                            "kind": state.get("trust_reason"),
                            "sync_seq": state.get("sync_seq"),
                            "residual_dist_m": None if residual is None else residual.get("dist"),
                            "residual_dx_m": None if residual is None else residual.get("dx"),
                            "residual_dz_m": None if residual is None else residual.get("dz"),
                            "residual_map_x": None if residual is None else residual.get("map_x"),
                            "residual_map_z": None if residual is None else residual.get("map_z"),
                            "residual_ws_x": None if residual is None else residual.get("ws_x"),
                            "residual_ws_z": None if residual is None else residual.get("ws_z"),
                            "distance_since_sync_m": prev_distance_since_sync,
                            "odom_before_sync_px": None
                            if prev_position_px is None
                            else [float(prev_position_px[0]), float(prev_position_px[1])],
                            "step_px": [float(step_px[0]), float(step_px[1])],
                        }
                    )
                    if residual is not None:
                        self.log_info(
                            f"[校准] #{iteration:04d} 残差={float(residual['dist']):.3f}m "
                            f"(dx={float(residual['dx']):+.3f}, "
                            f"dz={float(residual['dz']):+.3f})"
                        )

                prev_map_id = map_id
                prev_anchor_set = anchor_set
                prev_position_px = position_px
                prev_distance_since_sync = state.get("distance_since_sync")

                if iteration % 25 == 0:
                    self.info_set(
                        "定位采样",
                        f"{len(records)} 拍 / 校准 {len(calibrations)} 次",
                    )
        finally:
            try:
                elapsed = self.active_time() - start
                summary = self._finalize(
                    run_dir=run_dir,
                    records=records,
                    calibrations=calibrations,
                    scale=scale,
                    map_to_world_px=map_to_world_px,
                    stitch_enabled=stitch_enabled,
                    stitch_margin=stitch_margin,
                    edge_max_gap=edge_max_gap,
                    min_response=min_response,
                    anchor_weight=anchor_weight,
                    started_wall=started_wall,
                    config={
                        "interval_s": interval,
                        "duration_s": duration,
                        "max_patches": max_patches,
                        "stitch": stitch_enabled,
                        "stitch_margin": stitch_margin,
                        "edge_max_gap": edge_max_gap,
                        "min_response": min_response,
                        "anchor_weight": anchor_weight,
                    },
                )
                self.log_info(
                    f"记录结束: {len(records)} 拍 / {elapsed:.1f}s，"
                    f"校准 {len(calibrations)} 次，实际最大周期 {max_period:.3f}s，"
                    f"超时重对齐 {overruns} 次，输出 {run_dir}",
                    notify=True,
                )
                if summary is not None:
                    residual = summary.get("sync_residual_m") or {}
                    step_px_stat = summary.get("step_px") or {}
                    self.log_info(
                        "统计: "
                        f"路径={summary.get('path_m')}m / {summary.get('path_px')}px，"
                        f"逐拍位移中位={step_px_stat.get('median')}px，"
                        f"校准残差中位={residual.get('median')}m / 最大={residual.get('max')}m"
                    )
            except Exception as exc:
                _reraise_control_flow(exc)
                self.log_warning(f"记录收尾失败: {exc}")

    # ------------------------------------------------------------------ #
    # 收尾
    # ------------------------------------------------------------------ #
    def _finalize(
        self,
        *,
        run_dir: Path,
        records: list[dict],
        calibrations: list[dict],
        scale: float,
        map_to_world_px,
        stitch_enabled: bool,
        stitch_margin: int,
        edge_max_gap: int,
        min_response: float,
        anchor_weight: float,
        started_wall: str,
        config: dict,
    ) -> dict | None:
        """写 JSONL、统计和拼接图；任何一步失败都单独报告，不吞掉原始异常。"""

        if not records:
            self.log_warning("没有任何采样记录，跳过落盘。", notify=True)
            return None

        try:
            write_jsonl(run_dir / "samples.jsonl", records)
            write_jsonl(run_dir / "calibrations.jsonl", calibrations)
        except (OSError, TypeError, ValueError) as exc:
            self.log_error(f"写入采样记录失败: {exc}", notify=True)
            return None

        summary = summarize_records(
            records,
            calibrations=calibrations,
            scale_m_per_px=scale if scale > 0 else None,
        )
        stitches: list[dict] = []
        if stitch_enabled:
            try:
                stitches = stitch_records(
                    records,
                    patch_root=run_dir,
                    out_dir=run_dir,
                    margin=stitch_margin,
                    max_side=4096,
                    draw_overlay=True,
                    edge_max_gap=edge_max_gap,
                    min_response=min_response,
                    anchor_weight=anchor_weight,
                )
            except Exception as exc:
                self.log_warning(f"生成位姿图拼接图失败: {exc}")

        run_info = {
            "started": started_wall,
            "finished": datetime.now().isoformat(timespec="milliseconds"),
            "config": config,
            "scale_m_per_px": scale if scale > 0 else None,
            "map_to_world_px": map_to_world_px,
            "samples": len(records),
            "calibrations": len(calibrations),
            "stitches": stitches,
        }
        try:
            (run_dir / "run.json").write_text(
                json.dumps(run_info, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (run_dir / "summary.json").write_text(
                json.dumps(
                    {"run": run_info, "summary": summary},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except (OSError, TypeError, ValueError) as exc:
            self.log_warning(f"写入 summary/run 失败: {exc}")
            return summary

        for item in stitches:
            self.log_info(
                f"拼接图: {item['path']} "
                f"({item['size'][0]}x{item['size'][1]}, {item['placed']} 片)"
            )
        return summary
