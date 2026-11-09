"""导航途中停用 NCC，使用独立抓包轨迹评估失锚续航；默认仅检查条件。"""  # 需要显式运行
# ruff: noqa: E402

import argparse  # 解析试验距离和时限
import ctypes  # 检查运行权限
import json  # 输出有限前置状态
import sys  # 设置仓库路径
import time  # 生成独立输出目录
from pathlib import Path  # 操作证据路径

ROOT = Path(__file__).resolve().parents[2]  # 固定仓库根
sys.path.insert(0, str(ROOT))  # 允许直接启动脚本
from src.localization.capture_position_source import CapturePositionSource  # 独立只读参考源


def main():  # 命令行入口
    parser = argparse.ArgumentParser(  # 完成当前观测或统计步骤
        description=__doc__,  # 完成当前观测或统计步骤
        epilog="Administrator run: uv run --locked python -X utf8 scripts/nav/validate_anchor_dropout.py --run --distances 5 10 20 40 80 --repeats 2. Offline: --analyze OUTPUT. F8 or OUTPUT/STOP stops movement. --keep-sampling is a separate cadence experiment; map/tier identity freezes while NCC is off.",  # 完成当前观测或统计步骤
    )  # 默认不移动游戏
    parser.add_argument("--run", action="store_true")  # 显式启用正式导航
    parser.add_argument("--activate-game", action="store_true")  # 明确请求启动时切前台
    parser.add_argument("--port", type=int, default=8765)  # 读取现有后端
    parser.add_argument("--grid", type=Path, default=ROOT / "assets/nav-grid-data/map02_4.grid.npz")  # 当前主图网格
    parser.add_argument("--distances", type=float, nargs="+", default=[5, 10, 20, 40, 80])  # 视觉路程只作停止条件
    parser.add_argument("--repeats", type=int, default=2)  # 至少有机会检查重复性
    parser.add_argument("--keep-sampling", action="store_true")  # 对照组在导航等待期间继续采样，生产采样策略不变
    parser.add_argument("--warmup-m", type=float, default=3)  # 在导航中行走后才停用 NCC
    parser.add_argument("--seconds", type=float, default=900)  # 有限总时长
    parser.add_argument("--output", type=Path)  # 每次运行独立目录
    parser.add_argument("--analyze", type=Path)  # 无需游戏或权限即可重新评分已有证据
    args = parser.parse_args()  # 解析实际试验参数
    if args.analyze:  # 离线重算不连接抓包后端
        from scripts.nav.anchor_dropout_analysis import analyze_directory  # 延后加载评分工具

        analyze_directory(args.analyze)  # 保留原始日志并更新评分
        return 0  # 不发送游戏输入
    import math  # 校验外部数值

    if (  # 继续核验本组条件
        any(not math.isfinite(v) or v <= 0 for v in [*args.distances, args.warmup_m, args.seconds])
        or args.repeats < 1  # 完成当前观测或统计步骤
    ):  # 不接受无界实验
        parser.error("distances, repeats, warmup-m and seconds must be positive and finite")  # 在移动前拒绝错误
    sample = CapturePositionSource(args.port, timeout=0.5).poll()  # 不启动或改动后端
    if sample is None or not sample.flow_alive:  # 没有参考就不导航
        parser.error("capture reference has no live position")  # 不制造精度结论
    print(  # 完成当前观测或统计步骤
        json.dumps(  # 完成当前观测或统计步骤
            {  # 完成当前观测或统计步骤
                "live_reference": True,  # 保存 live_reference 字段
                "map_id": sample.map_id,  # 保存 map_id 字段
                "administrator": bool(ctypes.windll.shell32.IsUserAnAdmin()),  # 只打印必要元信息
                "run": args.run,  # 保存 run 字段
                "distances_m": args.distances,  # 保存 distances_m 字段
                "repeats": args.repeats,  # 保存 repeats 字段
            }  # 结束当前参数或记录构造
        ),  # 结束当前参数或记录构造
        flush=True,  # 完成当前观测或统计步骤
    )  # 不泄露账户数据
    if not args.run:  # 只读检查到此结束
        return 0  # 没有游戏输入
    if not ctypes.windll.shell32.IsUserAnAdmin():  # 游戏交互需要同级权限
        parser.error("run from an Administrator terminal")  # 不静默降低输入权限
    args.output = (  # 完成当前观测或统计步骤
        args.output or ROOT / "logs" / time.strftime("anchor_dropout_%Y%m%d_%H%M%S")  # 完成当前观测或统计步骤
    ).resolve()  # 唯一证据目录
    args.output.mkdir(parents=True, exist_ok=False)  # 不覆盖既有实验
    from src.patches.pre_config_patch import install_pre_config_patch  # 与正式入口一致

    install_pre_config_patch()  # 在导入完整任务前安装补丁
    from scripts.nav.anchor_dropout_task import run_experiment  # 延后创建正式运行时

    return run_experiment(args)  # 复用生产导航与截图


if __name__ == "__main__":  # 支持离线导入
    raise SystemExit(main())  # 向启动器返回真实状态
