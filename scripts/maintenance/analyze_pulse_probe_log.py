"""聚合分析脉冲探针日志（pulse_probe_log.jsonl），产出脉冲实测清单。

数据源：``configs/pulse_probe_log.jsonl``（src/image/pulse_probe.py 落盘，
字段 time / active_t / label / slot / key / char / team / ratio /
member_count / task）。用途：统计「哪些槽位/角色在实战中出现过官方推荐
脉冲、每场战斗何时首次出现、间隔多久」，为把推荐时机反哺排轴提供依据
（BASELINE_DATA.md 脉冲探针小节）。

战斗切分：``active_t`` 为任务内相对秒；相邻记录出现回退（active_t 变小）
即视为新一场战斗。

用法：
    python scripts/maintenance/analyze_pulse_probe_log.py             # 全量
    python scripts/maintenance/analyze_pulse_probe_log.py --char 洛茜
    python scripts/maintenance/analyze_pulse_probe_log.py --slot 2
    python scripts/maintenance/analyze_pulse_probe_log.py --json out.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG = ROOT / "configs" / "pulse_probe_log.jsonl"


def default_log_path() -> Path:
    """默认日志路径：优先走应用的 config_folder 解析，失败回退仓库 configs。"""
    try:
        sys.path.insert(0, str(ROOT))
        from src.core.paths import config_path  # noqa: E402

        return Path(config_path("pulse_probe_log.jsonl"))
    except Exception:
        return DEFAULT_LOG


def load_entries(path: Path) -> list[dict]:
    """读取 JSONL；损坏行跳过。"""
    entries: list[dict] = []
    if not path.is_file():
        return entries
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                entries.append(entry)
    return entries


def split_battles(entries: list[dict]) -> list[list[dict]]:
    """按 active_t 回退切分战斗（active_t 变小 = 新一场）。"""
    battles: list[list[dict]] = []
    current: list[dict] = []
    prev_t: float | None = None
    for entry in entries:
        try:
            t = float(entry.get("active_t"))
        except (TypeError, ValueError):
            continue
        if prev_t is not None and t < prev_t:
            battles.append(current)
            current = []
        current.append(entry)
        prev_t = t
    if current:
        battles.append(current)
    return battles


def _identity(entry: dict) -> str:
    """记录归属：有角色映射用角色名，否则退回槽位号。"""
    char = entry.get("char")
    if char:
        return str(char)
    return f"槽位{entry.get('slot', '?')}(未识别)"


def aggregate(battles: list[list[dict]]) -> dict:
    """聚合同一身份（角色/槽位）的脉冲触发统计。

    返回 {identity: {pulses, battles, first_seen:[...], intervals:[...],
    ratios:[...], teams:set}}；intervals 为同一场战斗内同身份相邻两次
    脉冲的 active_t 差。
    """
    stats: dict[str, dict] = defaultdict(lambda: {
        "pulses": 0, "battles": 0, "first_seen": [], "intervals": [],
        "ratios": [], "teams": [],
    })
    for records in battles:
        seen_in_battle: set[str] = set()
        last_t: dict[str, float] = {}
        for entry in records:
            ident = _identity(entry)
            bucket = stats[ident]
            bucket["pulses"] += 1
            try:
                ratio = float(entry.get("ratio"))
            except (TypeError, ValueError):
                ratio = None
            if ratio is not None:
                bucket["ratios"].append(ratio)
            team = entry.get("team")
            if isinstance(team, list) and team:
                team_key = "/".join(str(x) for x in team)
                if team_key not in bucket["teams"]:
                    bucket["teams"].append(team_key)
            try:
                t = float(entry.get("active_t"))
            except (TypeError, ValueError):
                continue
            if ident not in seen_in_battle:
                seen_in_battle.add(ident)
                bucket["battles"] += 1
                bucket["first_seen"].append(round(t, 1))
            elif ident in last_t:
                bucket["intervals"].append(round(t - last_t[ident], 1))
            last_t[ident] = t
    return dict(stats)


def format_report(stats: dict, battle_count: int, total: int) -> str:
    """人类可读报表（UTF-8 控制台）。"""
    lines = [
        f"脉冲探针日志分析：{total} 条记录 / {battle_count} 场战斗",
        "",
        f"{'身份':<14}{'触发':>6}{'场次':>6}{'首发(active_t)中位':>18}{'间隔中位(s)':>12}{'ratio均值':>10}",
    ]
    for ident, b in sorted(stats.items(), key=lambda kv: (-kv[1]["pulses"], kv[0])):
        first_med = f"{statistics.median(b['first_seen']):.1f}" if b["first_seen"] else "-"
        gap_med = f"{statistics.median(b['intervals']):.1f}" if b["intervals"] else "-"
        ratio_avg = f"{statistics.mean(b['ratios']):.3f}" if b["ratios"] else "-"
        lines.append(
            f"{ident:<14}{b['pulses']:>6}{b['battles']:>6}{first_med:>18}{gap_med:>12}{ratio_avg:>10}"
        )
    teams: dict[str, int] = defaultdict(int)
    for b in stats.values():
        for team in b["teams"]:
            teams[team] += 1
    if teams:
        lines.append("")
        lines.append("队伍构成（按出现次数）：")
        for team, count in sorted(teams.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {team} ×{count}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="聚合分析脉冲探针日志")
    parser.add_argument("--log", type=Path, default=None, help="日志路径（默认 configs/pulse_probe_log.jsonl）")
    parser.add_argument("--char", default=None, help="只统计角色名包含该子串的记录")
    parser.add_argument("--slot", type=int, default=None, help="只统计指定槽位（1-4）")
    parser.add_argument("--max-battles", type=int, default=None, help="只分析最近 N 场战斗")
    parser.add_argument("--json", type=Path, default=None, help="把聚合结果写成 JSON 文件")
    args = parser.parse_args(argv)

    path = args.log or default_log_path()
    entries = load_entries(path)
    if args.char:
        entries = [e for e in entries if args.char in str(e.get("char") or "")]
    if args.slot is not None:
        entries = [e for e in entries if e.get("slot") == args.slot]

    battles = split_battles(entries)
    if args.max_battles is not None and args.max_battles > 0:
        battles = battles[-args.max_battles:]
    if not battles:
        print(f"没有可分析的记录：{path}")
        return 1

    stats = aggregate(battles)
    print(format_report(stats, len(battles), sum(len(b) for b in battles)))
    if args.json:
        payload = {
            "log": str(path),
            "battles": len(battles),
            "records": sum(len(b) for b in battles),
            "identities": {
                ident: {
                    "pulses": b["pulses"],
                    "battles": b["battles"],
                    "first_seen": b["first_seen"],
                    "intervals": b["intervals"],
                    "ratio_avg": round(statistics.mean(b["ratios"]), 3) if b["ratios"] else None,
                    "teams": b["teams"],
                }
                for ident, b in stats.items()
            },
        }
        args.json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"聚合结果已写入 {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
