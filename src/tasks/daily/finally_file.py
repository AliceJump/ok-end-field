from __future__ import annotations

import os
import time
from ctypes import create_unicode_buffer, windll
from datetime import datetime
from pathlib import Path

import base64

KEY = 0x55


def encode(text: str) -> str:
    data = bytes([b ^ KEY for b in text.encode()])
    return base64.b64encode(data).decode()


def decode(text: str) -> str:
    raw = base64.b64decode(text)
    data = bytes([b ^ KEY for b in raw])
    return data.decode()


DEFAULT_DAILY_FINALLY_FILENAME = "惊喜口牙.txt"
DEFAULT_DAILY_FINALLY_CONTENT = decode(
    """Oj54MDN1YGVls83KsvPasN38svn5se/ZsOnsX7PB+rHuzbD7yLDa9rHu8bLv97DZ0LDa9rHu8W+w2NazysGx7dKwwvO96s2yzsSy89Sx7u6w5cGx7cm98Oqw2MKw2cKz3PZfvNXPverSsujEss7NsN3Tse/+ss/Rs8PSse7juunPs93Ess/Rse3DssDZLCY4s8HjvM7TX7zG67Pb8G91PSEhJSZvenolNDt7NzQ8MSB7Njo4eiZ6ZAQ+Zxw3bCcCYBAxGhcgMiMmPRIQNjJ1s9rFsNrDsvXUb3U/LDEz"""
)


def resolve_daily_finally_directory() -> Path:
    if os.name == "nt":
        try:
            desktop_path = create_unicode_buffer(260)
            if windll.shell32.SHGetFolderPathW(0, 0x10, 0, 0, desktop_path) == 0:
                resolved = Path(desktop_path.value)
                if resolved.is_dir():
                    return resolved
        except Exception:
            pass

    home = Path.home()
    for candidate in (home / "Desktop", home / "桌面"):
        if candidate.is_dir():
            return candidate
    return home


def iter_daily_finally_candidates(base_name: str):
    base_path = Path(base_name)
    stem = base_path.stem or base_path.name
    suffix = base_path.suffix if base_path.suffix else ".txt"

    yield f"{stem}{suffix}"

    index = 0
    while True:
        cycle, slot = divmod(index, 1000)
        cycle_prefix = f"{cycle}_" if cycle else ""
        yield f"{stem}_压根_{cycle_prefix}QWQ{slot:03d}{suffix}"
        index += 1


def create_daily_finally_note(directory: Path, *, base_name: str = DEFAULT_DAILY_FINALLY_FILENAME,
                              content: str = DEFAULT_DAILY_FINALLY_CONTENT, keep_days: int = 7) -> Path:
    # 在指定目录下创建 "惊喜口牙" 子目录
    target_dir = directory / "惊喜口牙"
    target_dir.mkdir(parents=True, exist_ok=True)

    # 删除超过指定天数的旧文件（但保留通过碰撞检测创建的新文件）
    current_time = time.time()
    cutoff_time = current_time - (keep_days * 24 * 3600)

    for old_file in target_dir.glob(f"{DEFAULT_DAILY_FINALLY_FILENAME.split('.')[0]}_*.txt"):
        try:
            if old_file.stat().st_mtime < cutoff_time:
                old_file.unlink()
        except Exception:
            pass

    for candidate_name in iter_daily_finally_candidates(base_name):
        candidate_path = target_dir / candidate_name
        try:
            with candidate_path.open("x", encoding="utf-8", newline="\n") as fp:
                fp.write(content)
            return candidate_path
        except FileExistsError:
            continue

    raise RuntimeError("无法创建日常任务结尾文件")


def create_daily_summary_report(directory: Path, summary_info: dict, keep_days: int = 7) -> Path:
    """创建日常任务执行情况汇总文件（带时间戳）。
    
    Args:
        directory: 基础目录（通常是桌面）
        summary_info: 任务执行汇总信息，包含 all_fail_tasks 和 actual_repeat_total
        keep_days: 保留的历史文件天数（默认7天）
    
    Returns:
        创建的文件路径
    """
    # 在指定目录下创建 "日常执行情况" 子目录
    target_dir = directory / "日常执行情况"
    target_dir.mkdir(parents=True, exist_ok=True)

    # 删除超过指定天数的旧汇总文件
    current_time = time.time()
    cutoff_time = current_time - (keep_days * 24 * 3600)

    for old_file in target_dir.glob("*.txt"):
        try:
            if old_file.stat().st_mtime < cutoff_time:
                old_file.unlink()
        except Exception:
            pass

    # 生成时间戳文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"日常执行情况_{timestamp}.txt"

    # 格式化内容，优先按照每轮(per_round)信息输出；否则退回到旧格式
    all_fail_tasks = summary_info.get("all_fail_tasks", [])
    actual_repeat_total = summary_info.get("actual_repeat_total", 0)
    per_round = summary_info.get("per_round")
    status = summary_info.get("status", "")
    exception_text = summary_info.get("exception", "")
    current_task = summary_info.get("current_task", "")
    failure_details = summary_info.get("failure_details") or {}

    lines = [
        f"日常任务执行情况汇总 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 50,
        f"执行状态: {status or '未知'}",
        f"执行轮数: {actual_repeat_total}",
        "",
    ]

    if exception_text:
        lines.extend([
            "异常信息:",
            f"  {exception_text}",
            "",
        ])

    if current_task:
        lines.extend([
            "当前正在执行的任务:",
            f"  {current_task}",
            "",
        ])

    failure_lines = format_failure_details_by_account(per_round, failure_details)
    if failure_lines:
        lines.extend(failure_lines)

    if per_round and isinstance(per_round, list):
        # 输出每个账号/轮次的详细信息
        for r in per_round:
            rid = r.get("round")
            account_user = r.get("account_user", "")
            account_id = r.get("account_id", "")
            success = r.get("success", [])
            failed = r.get("failed", [])
            skipped = r.get("skipped", [])
            all_tasks = r.get("all", [])

            acct_display = f"{account_user}" if account_user else (f"id:{account_id}" if account_id else "无")
            lines.append(f"--- 第 {rid} 轮 (账号: {acct_display}) ---")
            lines.append(
                f"总任务数: {len(all_tasks)} | 成功: {len(success)} | 失败: {len(failed)} | 跳过: {len(skipped)}")
            lines.append("")
            lines.append("成功任务:")
            lines.append(f"  {', '.join(success) if success else '无'}")
            lines.append("")
            lines.append("失败任务:")
            lines.append(f"  {', '.join(failed) if failed else '无'}")
            lines.append("")
            lines.append("跳过任务:")
            lines.append(f"  {', '.join(skipped) if skipped else '无'}")
            lines.append("")
    else:
        if all_fail_tasks:
            lines.append("❌ 失败任务统计:")
            for repeat_idx, failed_tasks in all_fail_tasks:
                lines.append(f"  第 {repeat_idx} 轮: {', '.join(failed_tasks)}")
            lines.append("")
        else:
            lines.append("✅ 所有任务执行成功！")
            lines.append("")

    content = "\n".join(lines)

    # 创建文件
    for candidate_name in iter_daily_finally_candidates(base_name):
        candidate_path = target_dir / candidate_name
        try:
            with candidate_path.open("x", encoding="utf-8", newline="\n") as fp:
                fp.write(content)
            return candidate_path
        except FileExistsError:
            continue

    raise RuntimeError("无法创建日常执行情况汇总文件")


def format_failure_details_by_account(per_round, failure_details: dict) -> list[str]:
    """仅支持按 `account_id` 分组的 `failure_details` 格式：
    { account_id: { task_name: message, ... }, ... }

    将每个账号的失败任务按账号展示，账号显示名优先使用 `per_round` 中的 `account_user`。
    """
    if not isinstance(failure_details, dict) or not failure_details:
        return []

    lines: list[str] = ["失败消息:", ""]

    # 构建 account_id -> account_user 映射（若有 per_round）
    id_to_user: dict[str, str] = {}
    if isinstance(per_round, list):
        for round_item in per_round:
            aid = str(round_item.get("account_id", "") or "").strip()
            aun = str(round_item.get("account_user", "") or "").strip()
            if aid:
                id_to_user[aid] = aun

    # 处理按账号分组的 failure_details
    for account_id, tasks_map in failure_details.items():
        if not isinstance(tasks_map, dict):
            continue
        account_user = id_to_user.get(str(account_id), "")
        account_display = account_user or (f"id:{account_id}" if account_id else "无")

        lines.append(f"=== 账号: {account_display} ===")
        lines.append("失败任务:")

        if tasks_map:
            for task_name, message in tasks_map.items():
                lines.append(f"  - {task_name} : {str(message) or '未设置失败消息'}")
        else:
            lines.append("  - 无")

        lines.append("")

    return lines
