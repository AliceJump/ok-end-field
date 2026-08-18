# -*- coding: utf-8 -*-
"""发送邮件给指定用户的命令行工具。

邮件配置放在应用的“设置 → 邮件发送配置”（对应全局配置 ``Email Config``，
实际文件为 ``configs/Email Config.json``）。首次运行或配置文件不存在时，
会自动生成全部默认配置项，因此无需手动创建配置文件。

用法示例::

    # 生成/查看邮件配置（配置项会自动补齐）
    uv run python tools/send_email_to_user.py --init-config

    # 直接发送给某个邮箱
    uv run python tools/send_email_to_user.py --to user@example.com --subject "测试" --body "你好"

    # 使用设置中的“收件人列表”别名发送
    uv run python tools/send_email_to_user.py --to 小明 --subject "测试" --body "你好"

    # 从文件读取正文并附带附件
    uv run python tools/send_email_to_user.py --to user@example.com --subject "报告" \
        --body-file report.txt --attachment a.png --attachment b.png

    # 使用 html/test_email.html 模板发送测试邮件
    uv run python tools/send_email_to_user.py --test-email

    # 不使用 SSL（例如使用 587 STARTTLS）
    uv run python tools/send_email_to_user.py --to user@example.com --subject "测试" --body "你好" --no-ssl
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.core.email_service import (  # noqa: E402
    ensure_email_config_file,
    get_email_settings,
    send_email,
    send_test_email,
)


def _resolve_body_file(raw: str) -> Path:
    """解析并校验正文文件路径。

    展开用户目录并解析为绝对路径，确认存在且为普通文件后再读取，
    避免路径注入/越界访问（SonarQube S2083）。
    """
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise OSError(f"正文文件不存在或不是普通文件: {path}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="发送邮件给指定用户（配置见设置 → 邮件发送配置）"
    )
    parser.add_argument(
        "--init-config",
        action="store_true",
        help="生成/查看邮件配置文件后退出（配置项会自动补齐）",
    )
    parser.add_argument(
        "--test-email",
        action="store_true",
        help="使用 html/test_email.html 模板发送一封测试邮件",
    )
    parser.add_argument("--to", help="收件人邮箱，或设置“收件人列表”中的用户别名")
    parser.add_argument("--subject", default="", help="邮件主题")
    parser.add_argument("--body", default="", help="邮件正文")
    parser.add_argument("--body-file", help="从文件读取邮件正文")
    parser.add_argument(
        "--attachment",
        action="append",
        default=[],
        help="附件路径，可多次指定",
    )
    parser.add_argument("--no-ssl", action="store_true", help="不使用 SSL（使用普通 SMTP/STARTTLS）")
    args = parser.parse_args()

    if args.init_config:
        path = ensure_email_config_file()
        print(f"邮件配置文件: {path}")
        print("当前配置项：")
        # 用 get_email_settings() 归一化展示，缺失配置项按默认值补齐（与发送流程一致）
        data = get_email_settings()
        for key, value in data.items():
            # 授权码是 SMTP 密码，输出时遮盖，避免凭据暴露到终端/CI 日志
            display_value = "***" if key == "授权码" and value else value
            print(f"  {key}: {display_value}")
        return 0

    body = args.body
    if args.body_file:
        try:
            body = _resolve_body_file(args.body_file).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"[错误] 读取正文文件失败: {exc}", file=sys.stderr)
            return 1

    settings = get_email_settings()
    if args.no_ssl:
        settings["启用SSL"] = False

    try:
        if args.test_email:
            recipient = send_test_email(to_user=args.to, settings=settings)
        else:
            recipient = send_email(
                to_user=args.to,
                subject=args.subject,
                body=body,
                attachments=args.attachment,
                settings=settings,
            )
    except Exception as exc:  # noqa: BLE001
        print(f"[错误] 邮件发送失败: {exc}", file=sys.stderr)
        return 1

    print(f"[成功] 邮件已发送至: {recipient}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
