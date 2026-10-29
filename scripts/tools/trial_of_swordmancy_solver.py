"""选剑演武求解器命令行入口：打印「手牌张数 × 战力点」的最优动作表。

算法本体在 ``src/core/trial_of_swordmancy_solver.py``（任务侧共用同一份实现），本脚本只
做参数解析与打印，不碰游戏窗口。

用法：
    python scripts/tools/trial_of_swordmancy_solver.py            # 打印默认设定的决策表
    python scripts/tools/trial_of_swordmancy_solver.py --deck 4,5,6,6,7 --overflow twice
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.trial_of_swordmancy_solver import (
    MAX_REMAIN_ABAND,
    MAX_REMAIN_CALC,
    Config,
    get_solver,
    strategy_table,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description="选剑演武最高期望策略求解器")
    parser.add_argument("--deck", default="4,5,6,6,7", help="点数 1..5 的牌库张数")
    parser.add_argument("--overflow", default="twice", choices=("none", "once", "twice"))
    parser.add_argument("--remain-calc", type=int, default=MAX_REMAIN_CALC)
    parser.add_argument("--remain-aband", type=int, default=MAX_REMAIN_ABAND)
    args = parser.parse_args(argv)

    cfg = Config(deck=tuple(int(x) for x in args.deck.split(",")), overflow=args.overflow)
    solver = get_solver(cfg)
    print(f"牌库={cfg.deck} 溢出模式={cfg.overflow} 状态数={len(solver.states)}")
    print(f"{'张数':<4}{'战力点':<6}{'最优动作':<8}{'期望奖励':>10}")
    for count, power, action, expected in strategy_table(cfg, args.remain_calc, args.remain_aband, solver=solver):
        print(f"{count:<4}{power:<6}{action:<8}{expected:>10.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
