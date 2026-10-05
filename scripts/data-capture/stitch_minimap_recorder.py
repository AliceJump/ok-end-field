"""重新拼接「小地图定位采样记录」产出的切片。

用法：

    uv run --locked python scripts/data-capture/stitch_minimap_recorder.py \
        logs/minimap_position_recorder/20261005_120000_000

脚本只读取 ``samples.jsonl`` 和 ``patches/``，不会修改原始记录；拼接结果默认
写回同一个运行目录。可反复调整 ``--margin`` / ``--max-side`` 而不必重新跑图。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.localization.minimap_sample_stitch import (  # noqa: E402
    read_jsonl,
    stitch_records,
    summarize_records,
)


def _load_run(run_dir: Path) -> list[dict]:
    path = run_dir / "samples.jsonl"
    if not path.is_file():
        raise SystemExit(f"找不到采样记录: {path}")
    records = read_jsonl(path)
    if not records:
        raise SystemExit(f"采样记录为空: {path}")
    return records


def _load_calibrations(run_dir: Path) -> list[dict]:
    path = run_dir / "calibrations.jsonl"
    return read_jsonl(path) if path.is_file() else []


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="重新拼接小地图定位采样记录")
    parser.add_argument("run_dir", help="运行目录（含 samples.jsonl 和 patches/）")
    parser.add_argument("--margin", type=int, default=128, help="拼接画布边距(像素)")
    parser.add_argument("--max-side", type=int, default=4096, help="拼接画布最大边长(像素)")
    parser.add_argument("--edge-max-gap", type=int, default=30, help="位姿图时间近邻最大间隔(拍)")
    parser.add_argument("--min-response", type=float, default=0.25, help="位姿图最小相关响应")
    parser.add_argument("--anchor-weight", type=float, default=50.0, help="WS 绝对锚点权重")
    parser.add_argument("--no-overlay", action="store_true", help="不生成带轨迹标注的 overlay 图")
    parser.add_argument("--out-dir", help="输出目录，默认与运行目录相同")
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else run_dir
    records = _load_run(run_dir)
    calibrations = _load_calibrations(run_dir)
    outputs = stitch_records(
        records,
        patch_root=run_dir,
        out_dir=out_dir,
        margin=args.margin,
        max_side=args.max_side,
        draw_overlay=not args.no_overlay,
        edge_max_gap=args.edge_max_gap,
        min_response=args.min_response,
        anchor_weight=args.anchor_weight,
    )
    if not outputs:
        raise SystemExit("没有任何可拼接的记录（检查 fused_px/odom_px 和 patch 字段）")

    summary = summarize_records(records, calibrations=calibrations)
    print(f"运行目录: {run_dir}")
    print(f"采样数  : {summary['samples']}")
    print(f"路径    : {summary['path_px']}px")
    print(f"逐拍位移: {summary['step_px']}")
    print(f"校准残差: {summary['sync_residual_m']}")
    print("输出:")
    for item in outputs:
        print(
            f"  {item['path']} "
            f"({item['size'][0]}x{item['size'][1]}, {item['placed']} 片, "
            f"位姿图 {item['nodes']} 节点 / {item['edges']} 边 / {item['anchors']} 锚点)"
        )
        print(f"           残差 {item['residual_before']} -> {item['residual_after']}")
        if item.get("overlay_path"):
            print(f"           overlay: {item['overlay_path']}")
        if item.get("pose_graph_path"):
            print(f"           pose graph: {item['pose_graph_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
