# -*- coding: utf-8 -*-
"""邮件发送服务。

提供：
- 读取/生成 ``configs/Email Config.json`` 中的邮件配置
- 发送普通文本邮件、HTML 邮件、带附件邮件
- 发送测试邮件（供“设置 → 邮件发送配置”里的“发送测试邮件”按钮调用）
- 读取 ``html/`` 目录下的邮件 HTML 模板
"""
from __future__ import annotations

import base64
import html
import re
import smtplib
import ssl
import threading
from datetime import datetime
from email.header import Header
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path
from typing import Iterable

from ok.util.file import get_relative_path, read_json_file, write_json_file

from src.core.email_config import DEFAULT_EMAIL_CONFIG, EMAIL_CONFIG_NAME

# 项目根目录（src/core/email_service.py -> 上三级为项目根）
ROOT = Path(__file__).resolve().parents[2]
HTML_DIR = ROOT / "html"
DEFAULT_TEMPLATE_NAME = "test_email.html"


def _safe_int(value, default: int) -> int:
    """把配置值安全转成 int；非数字/None/空串时回退默认值，避免阻塞或报错。"""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def ensure_email_config_file() -> Path:
    """若 ``configs/Email Config.json`` 不存在，则自动生成默认配置项。"""
    path = Path(get_relative_path("configs", f"{EMAIL_CONFIG_NAME}.json"))
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json_file(str(path), DEFAULT_EMAIL_CONFIG)
    return path


def get_email_settings() -> dict:
    """读取邮件设置，缺失的配置项用默认值补齐。"""
    path = ensure_email_config_file()
    data = read_json_file(str(path)) or {}
    settings = {}
    for key, default in DEFAULT_EMAIL_CONFIG.items():
        # 配置文件里没有的键（例如旧版本升级）用默认值补齐；
        # 用户显式留空的可选项（如“主题前缀”）保持为空，尊重用户设置。
        settings[key] = data.get(key, default)
    return settings


def parse_recipients(raw) -> dict[str, str]:
    """解析设置中的“收件人列表”，每行格式：别名=邮箱 或 别名:邮箱。"""
    result: dict[str, str] = {}
    if not raw:
        return result
    for line in str(raw).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for sep in ("=", ":"):
            if sep in line:
                name, email = line.split(sep, 1)
                name = name.strip()
                email = email.strip()
                if name and email:
                    result[name] = email
                break
    return result


def resolve_recipient(to_user: str | None, settings: dict) -> str:
    """把收件人解析为邮箱地址。

    支持：
    - 直接传邮箱地址
    - 传设置“收件人列表”中的用户别名
    - 不传时使用“默认收件人”
    """
    target = (to_user or "").strip()
    if not target:
        target = str(settings.get("默认收件人", "") or "").strip()
    if not target:
        raise ValueError(
            "未指定收件人：请填写“默认收件人”或“收件人列表”，或传入邮箱/别名。"
        )

    recipients = parse_recipients(settings.get("收件人列表"))
    if target in recipients:
        return recipients[target]

    if "@" in target:
        return target

    raise ValueError(
        f"无法识别的收件人: {target}。请直接填写邮箱地址，"
        f"或在设置“收件人列表”中把该名称映射到邮箱。"
    )


def load_email_template(name: str = DEFAULT_TEMPLATE_NAME) -> str:
    """读取 ``html/`` 下的邮件 HTML 模板。

    把本地图片素材内嵌为 base64，并把 ``<link rel="stylesheet">`` 引用的
    共享 CSS（email_style.css）内联为 ``<style>``，保证邮件客户端也能渲染样式。
    """
    path = HTML_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"邮件模板不存在: {path}")
    html = path.read_text(encoding="utf-8")
    html = _inline_local_stylesheets(html)
    return _inline_local_assets(html)


_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
}


def _path_to_data_uri(ref: str) -> str | None:
    """把模板里引用的本地图片路径转成 data URI；文件不存在返回 None。"""
    candidate = Path(ref)
    if not candidate.is_absolute():
        candidate = (HTML_DIR / candidate).resolve()
    if not candidate.is_file():
        return None
    mime = _MIME_BY_SUFFIX.get(candidate.suffix.lower())
    if mime is None:
        return None
    encoded = base64.b64encode(candidate.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _inline_local_stylesheets(html: str) -> str:
    """把 HTML 中 ``<link rel="stylesheet" href="...">`` 引用的本地 CSS 内联为 ``<style>``。

    模板在浏览器里预览时通过相对链接加载共享样式（email_style.css），
    发送邮件时这里把 CSS 内容直接内联，避免部分邮件客户端忽略外部样式表。
    """
    def _replace_link(match: re.Match) -> str:
        href = match.group(1).strip()
        candidate = Path(href)
        if not candidate.is_absolute():
            candidate = (HTML_DIR / candidate).resolve()
        if not candidate.is_file() or candidate.suffix.lower() != ".css":
            return match.group(0)
        css = candidate.read_text(encoding="utf-8")
        return f"<style>\n{css}\n</style>"

    return re.sub(
        r'<link\s+rel="stylesheet"\s+href="([^"]+)"\s*/?>',
        _replace_link,
        html,
    )


def _inline_local_assets(html: str) -> str:
    """把 HTML 中的本地图片引用（src / url）替换为 base64 data URI。

    支持：
    - ``<img src="...">``
    - CSS ``url("...")`` / ``url('...')`` / ``url(...)``
    """
    # 先处理 img src
    def _replace_src(match: re.Match) -> str:
        ref = match.group(1).strip()
        data_uri = _path_to_data_uri(ref)
        if data_uri is None:
            return match.group(0)
        return f'src="{data_uri}"'

    html = re.sub(r'src="([^"]+)"', _replace_src, html)

    def _replace_url(match: re.Match) -> str:
        ref = match.group(1).strip()
        data_uri = _path_to_data_uri(ref)
        if data_uri is None:
            return match.group(0)
        return f'url("{data_uri}")'

    html = re.sub(r'url\(\s*["\']?([^"\')\s]+)["\']?\s*\)', _replace_url, html)
    return html


def _build_message(
    sender: str,
    recipient: str,
    subject: str,
    body: str = "",
    html_body: str | None = None,
    attachments: Iterable[str | Path] = (),
    prefix: str = "",
) -> MIMEMultipart:
    full_subject = subject
    if prefix and subject:
        full_subject = f"{prefix} {subject}"
    elif prefix:
        full_subject = prefix

    msg = MIMEMultipart()
    msg["From"] = formataddr((str(Header(sender, "utf-8")), sender))
    msg["To"] = recipient
    msg["Subject"] = Header(full_subject, "utf-8")

    if html_body:
        msg.attach(MIMEText(html_body, "html", "utf-8"))
    else:
        msg.attach(MIMEText(body or "", "plain", "utf-8"))

    for attachment in attachments or []:
        path = Path(attachment)
        if not path.is_file():
            raise FileNotFoundError(f"附件不存在: {path}")
        with path.open("rb") as fh:
            part = MIMEApplication(fh.read(), Name=path.name)
        part["Content-Disposition"] = f'attachment; filename="{path.name}"'
        msg.attach(part)

    return msg


def send_email(
    to_user: str | None,
    subject: str = "",
    body: str = "",
    attachments: Iterable[str | Path] | None = None,
    settings: dict | None = None,
    html_body: str | None = None,
) -> str:
    """发送邮件，返回实际收件人邮箱。

    Args:
        to_user: 收件人邮箱或设置中的用户别名；为空时使用“默认收件人”。
        subject: 邮件主题。
        body: 纯文本正文（未传 html_body 时使用）。
        attachments: 附件路径列表。
        settings: 邮件设置；不传时自动读取并补齐默认配置。
        html_body: HTML 正文；传入后邮件正文使用该 HTML。
    """
    settings = settings or get_email_settings()

    smtp_host = str(settings.get("SMTP服务器", "") or "").strip()
    smtp_port = _safe_int(settings.get("SMTP端口", 465), 465)
    use_ssl = bool(settings.get("启用SSL", True))
    sender = str(settings.get("发件邮箱", "") or "").strip()
    password = str(settings.get("授权码", "") or "").strip()
    timeout = _safe_int(settings.get("连接超时秒数", 30), 30)

    if not smtp_host or not sender or not password:
        raise ValueError(
            "邮件配置不完整，请在“设置 → 邮件发送配置”中填写 SMTP服务器、发件邮箱和授权码。"
        )

    recipient = resolve_recipient(to_user, settings)
    prefix = str(settings.get("主题前缀", "") or "").strip()
    msg = _build_message(
        sender=sender,
        recipient=recipient,
        subject=subject,
        body=body,
        html_body=html_body,
        attachments=attachments or [],
        prefix=prefix,
    )

    # 校验服务器证书，避免降级/中间人风险（SonarQube S4830）
    ssl_context = ssl.create_default_context()
    ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2

    if use_ssl:
        server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=timeout, context=ssl_context)
    else:
        server = smtplib.SMTP(smtp_host, smtp_port, timeout=timeout)
        # 非 SSL 模式必须通过 STARTTLS 升级为加密连接，失败即中止，
        # 避免 SMTP 授权码在明文连接上传输。
        try:
            server.starttls(context=ssl_context)
        except smtplib.SMTPException as exc:
            try:
                server.quit()
            except Exception:
                pass
            raise ValueError(
                f"STARTTLS 升级失败（服务器不支持加密连接），已中止发送: {exc}"
            ) from exc

    try:
        server.login(sender, password)
        server.sendmail(sender, [recipient], msg.as_string())
    finally:
        try:
            server.quit()
        except Exception:
            pass

    return recipient


def send_email_to_user(
    to_user: str | None,
    subject: str = "",
    body: str = "",
    attachments: Iterable[str | Path] | None = None,
    settings: dict | None = None,
    html_body: str | None = None,
) -> str:
    """发送邮件给指定用户，等价于 :func:`send_email`。"""
    return send_email(
        to_user=to_user,
        subject=subject,
        body=body,
        attachments=attachments,
        settings=settings,
        html_body=html_body,
    )


def send_test_email(to_user: str | None = None, settings: dict | None = None) -> str:
    """发送一封测试邮件。

    使用 ``html/test_email.html`` 模板，收件人优先取传入的 ``to_user``，
    否则使用设置中的“默认收件人”。
    """
    settings = settings or get_email_settings()
    recipient = resolve_recipient(to_user, settings)

    template = load_email_template(DEFAULT_TEMPLATE_NAME)
    html_body = template.replace(
        "{{send_time}}", datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )

    return send_email(
        to_user=recipient,
        subject="测试邮件",
        body="这是一封来自 ok-ef 的测试邮件。",
        settings=settings,
        html_body=html_body,
    )


def send_test_email_from_settings() -> None:
    """设置页“发送测试邮件”按钮回调：读取当前设置并发送测试邮件。

    发送在网络线程中执行，避免 SMTP 连接/超时阻塞设置页 UI；
    结果通过 ok 的弹窗提示给用户。
    """
    from ok.gui.util.Alert import alert_error, alert_info

    def _run():
        try:
            recipient = send_test_email()
        except Exception as exc:  # noqa: BLE001
            alert_error(f"测试邮件发送失败: {exc}")
            return
        alert_info(f"测试邮件已发送至: {recipient}")

    threading.Thread(target=_run, daemon=True).start()


def _esc(value) -> str:
    """HTML 转义后写入邮件模板，避免不可信文本被当作标记解析。"""
    return html.escape(str(value), quote=True)


def send_daily_summary_email(
    summary_text: str,
    subject: str = "日常任务汇总",
    settings: dict | None = None,
    status_data: dict | None = None,
) -> str:
    """发送日常任务最终汇总邮件。

    使用 ``html/daily_summary_email.html`` 模板，收件人取设置中的“默认收件人”。

    Args:
        summary_text: 汇总文本（写入终端日志区域）。
        status_data: 状态数据，支持字段：
            status / status_en / total_rounds / success_count /
            failed_count / skipped_count / system_name / report_type
    """
    settings = settings or get_email_settings()
    status_data = status_data or {}

    status = str(status_data.get("status", "完成") or "完成")
    status_en = str(status_data.get("status_en", "COMPLETED") or "COMPLETED").upper()
    if status_en in ("COMPLETED", "SUCCESS"):
        status_class = "ok"
    elif status_en in ("RUNNING", "PARTIAL", "WARN"):
        status_class = "warn"
    else:
        status_class = "fail"

    failed_details = status_data.get("failed_details") or []
    failed_details_html = _render_failed_details(failed_details)

    # 邮件内不显示 ⭐ 前缀（本地汇总文件保持原样）
    clean_summary = str(summary_text).replace("⭐", "")

    html_body = (
        load_email_template("daily_summary_email.html")
        .replace("{{summary_text}}", _esc(clean_summary))
        .replace("{{send_time}}", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        .replace("{{status_text}}", _esc(status))
        .replace("{{status_en}}", _esc(status_en))
        .replace("{{status_class}}", status_class)
        .replace("{{total_rounds}}", _esc(status_data.get("total_rounds", 0)))
        .replace("{{success_count}}", _esc(status_data.get("success_count", 0)))
        .replace("{{failed_count}}", _esc(status_data.get("failed_count", 0)))
        .replace("{{skipped_count}}", _esc(status_data.get("skipped_count", 0)))
        .replace("{{system_name}}", _esc(status_data.get("system_name", "OK-EF")))
        .replace("{{report_type}}", _esc(status_data.get("report_type", "DAILY")))
        # failed_details_html 内部已逐字段转义，不能整体再转义，否则会破坏生成的 HTML 结构。
        .replace("{{failed_details_html}}", failed_details_html)
    )
    return send_email(
        to_user=None,
        subject=subject,
        body=clean_summary,
        settings=settings,
        html_body=html_body,
    )


def _render_failed_details(failed_details) -> str:
    """把失败任务明细渲染为 HTML 行。"""
    if not failed_details:
        return '<div class="failed-empty">无失败任务</div>'

    rows = []
    for item in failed_details:
        account = html.escape(str(item.get("account", "无") or "无"))
        task = html.escape(str(item.get("task", "") or "").lstrip("⭐").strip())
        reason = html.escape(str(item.get("reason", "") or ""))
        head = account if not task else f"{account} · {task}"
        rows.append(
            '<div class="failed-row">'
            f'<div class="failed-head">{head}</div>'
            f'<div class="failed-reason">{reason}</div>'
            '</div>'
        )
    return "\n".join(rows)
