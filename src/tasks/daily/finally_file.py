from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import base64

KEY = 0x55


def decode(text: str) -> str:
    raw = base64.b64decode(text)
    data = bytes([b ^ KEY for b in raw])
    return data.decode()


def get_software_name() -> str:
    """从全局配置中读取软件名（gui_title）。"""
    try:
        from ok import og  # type: ignore
        return og.config.get('gui_title', 'ok-ef')
    except Exception:
        return 'ok-ef'


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


def create_task_summary_report(task, base_dir: Path, summary_info: dict, keep_days: int = 7) -> Path:
    """创建任务执行情况汇总文件（自动读取任务名和软件名构建目录）。
    
    目录结构: {base_dir}/{app_name}/{task_name}/
    
    Args:
        task: 任务实例（需要有 .name 属性）
        base_dir: 基础目录
        summary_info: 任务执行汇总信息，包含 all_fail_tasks、actual_repeat_total、
                      per_round、status、exception、current_task、failure_details 等
        keep_days: 保留的历史文件天数（默认7天）
    
    Returns:
        创建的文件路径
    """
    task_name = getattr(task, 'name', '未知任务')
    app_name = get_software_name()

    target_dir = base_dir / app_name / task_name
    target_dir.mkdir(parents=True, exist_ok=True)

    # 删除超过指定天数的旧文件
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
    base_name = f"{task_name}_{timestamp}.txt"

    # 格式化内容，优先按照每轮(per_round)信息输出；否则退回到旧格式
    all_fail_tasks = summary_info.get("all_fail_tasks", [])
    actual_repeat_total = summary_info.get("actual_repeat_total", 0)
    per_round = summary_info.get("per_round")
    status = summary_info.get("status", "")
    exception_text = summary_info.get("exception", "")
    current_task = summary_info.get("current_task", "")
    failure_details = summary_info.get("failure_details") or {}

    lines = [
        f"{task_name}执行情况汇总 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
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

    raise RuntimeError(f"无法创建{task_name}执行情况汇总文件")


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
