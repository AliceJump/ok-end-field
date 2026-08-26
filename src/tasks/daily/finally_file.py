from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import base64

KEY = 0x55


def decode(text: str) -> str:
    """解码经过 base64 编码并 XOR 混淆的文本。"""
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
    """生成报告文件名候选序列，用于避免文件名冲突。"""
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
    # 报告文案走项目 gettext（msgid 入 ok.po）；task 缺失时退化为原文
    _tr = getattr(task, 'tr', None) or (lambda s: s)

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
        f"{_tr(task_name)}{_tr('执行情况汇总')} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 50,
        _tr("执行状态: {status}").format(status=_tr(status) if status else _tr("未知")),
        _tr("执行轮数: {rounds}").format(rounds=actual_repeat_total),
        "",
    ]

    if exception_text:
        lines.extend([
            _tr("异常信息:"),
            f"  {exception_text}",
            "",
        ])

    if current_task:
        lines.extend([
            _tr("当前正在执行的任务:"),
            f"  {_tr(current_task)}",
            "",
        ])

    failure_lines = format_failure_details_by_account(per_round, failure_details, translate=getattr(task, 'tr', None))
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

            acct_display = f"{account_user}" if account_user else (f"id:{account_id}" if account_id else _tr("无"))
            lines.append(_tr("--- 第 {round} 轮 (账号: {account}) ---").format(round=rid, account=acct_display))
            lines.append(
                _tr("总任务数: {total} | 成功: {success} | 失败: {failed} | 跳过: {skipped}").format(
                    total=len(all_tasks), success=len(success), failed=len(failed), skipped=len(skipped)))
            lines.append("")
            lines.append(_tr("成功任务:"))
            lines.append(f"  {', '.join(_tr(t) for t in success) if success else _tr('无')}")
            lines.append("")
            lines.append(_tr("失败任务:"))
            lines.append(f"  {', '.join(_tr(t) for t in failed) if failed else _tr('无')}")
            lines.append("")
            lines.append(_tr("跳过任务:"))
            lines.append(f"  {', '.join(_tr(t) for t in skipped) if skipped else _tr('无')}")
            lines.append("")
    else:
        if all_fail_tasks:
            lines.append("❌ " + _tr("失败任务统计:"))
            for repeat_idx, failed_tasks in all_fail_tasks:
                lines.append(_tr("第 {round} 轮: {tasks}").format(
                    round=repeat_idx, tasks=', '.join(_tr(t) for t in failed_tasks)))
            lines.append("")
        else:
            lines.append("✅ " + _tr("所有任务执行成功！"))
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


def _build_account_id_to_user(per_round) -> dict[str, str]:
    """从 per_round 列表构建 account_id 到 account_user 的映射字典。

    Args:
        per_round: 每轮执行信息列表，每项包含 account_id 和 account_user 字段。

    Returns:
        account_id -> account_user 映射字典；per_round 非列表时返回空字典。
    """
    id_to_user: dict[str, str] = {}
    if not isinstance(per_round, list):
        return id_to_user
    for item in per_round:
        aid = str(item.get("account_id", "") or "").strip()
        aun = str(item.get("account_user", "") or "").strip()
        if aid:
            id_to_user[aid] = aun
    return id_to_user


def _format_account_failure_lines(account_id, tasks_map, id_to_user, _tr) -> list[str]:
    """格式化单个账号的失败任务明细为报告文本行。

    Args:
        account_id: 账号标识。
        tasks_map: 该账号的 {task_name: message} 字典。
        id_to_user: account_id -> account_user 映射，用于显示账号名。
        _tr: 翻译函数（如 task.tr），用于翻译 UI 标签和失败消息。

    Returns:
        格式化后的文本行列表。
    """
    account_user = id_to_user.get(str(account_id), "")
    account_display = account_user or (f"id:{account_id}" if account_id else _tr("无"))
    lines = [
        f"=== {_tr('账号')}: {account_display} ===",
        _tr("失败任务:"),
    ]
    if tasks_map:
        for task_name, message in tasks_map.items():
            message_text = str(message).strip() if message is not None else ""
            lines.append(f"  - {_tr(task_name)} : {_tr(message_text) or _tr('未设置失败消息')}")
    else:
        lines.append(f"  - {_tr('无')}")
    lines.append("")
    return lines


def format_failure_details_by_account(per_round, failure_details: dict, translate=None) -> list[str]:
    """仅支持按 `account_id` 分组的 `failure_details` 格式：
    { account_id: { task_name: message, ... }, ... }

    将每个账号的失败任务按账号展示，账号显示名优先使用 `per_round` 中的 `account_user`。
    translate: 可选的翻译函数（如 task.tr），用于翻译失败消息。
    """
    if not isinstance(failure_details, dict) or not failure_details:
        return []

    _tr = translate or (lambda s: s)
    id_to_user = _build_account_id_to_user(per_round)

    lines: list[str] = [_tr("失败消息:"), ""]
    for account_id, tasks_map in failure_details.items():
        if isinstance(tasks_map, dict):
            lines.extend(_format_account_failure_lines(account_id, tasks_map, id_to_user, _tr))
    return lines
