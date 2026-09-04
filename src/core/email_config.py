"""邮件发送服务的全局设置定义。

邮件配置以 ``Email Config`` 全局配置的形式保存在 ``configs/Email Config.json``。
首次运行发送工具或在“设置 → 邮件配置”中打开时，会自动按
``DEFAULT_EMAIL_CONFIG`` 生成这些配置项。
"""

from __future__ import annotations

from ok import ConfigOption
from qfluentwidgets import FluentIcon

EMAIL_CONFIG_NAME = "Email Config"

DEFAULT_EMAIL_CONFIG = {
    "SMTP服务器": "smtp.qq.com",
    "SMTP端口": 465,
    "启用SSL": True,
    "发件邮箱": "",
    "授权码": "",
    "默认收件人": "",
    "收件人列表": "",
    "主题前缀": "[ok-ef]",
    "连接超时秒数": 30,
}

EMAIL_CONFIG_DESCRIPTION = {
    "SMTP服务器": "SMTP 服务器地址，例如 smtp.qq.com / smtp.163.com / smtp.gmail.com",
    "SMTP端口": "SMTP 端口。SSL 通常为 465，STARTTLS 通常为 587。",
    "启用SSL": "使用 SSL/TLS 加密连接发送邮件。",
    "发件邮箱": "用于登录 SMTP 的邮箱地址，同时也是发件人地址。",
    "授权码": "SMTP 授权码/密码。请勿在公开仓库中提交真实授权码。",
    "默认收件人": "调用发送工具但未指定 --to 时使用的默认收件人邮箱。",
    "收件人列表": (
        "用户别名到邮箱的映射，每行一个，格式：别名=邮箱（或 别名:邮箱）。\n"
        "例如：\n"
        "洛茜=rossi@endfiled.com\n"
        "洁尔佩塔=gilberta@endfiled.com\n"
        "发送时可用 --to 洛茜 直接按别名发送。"
    ),
    "主题前缀": "发送时自动添加到邮件主题前面的前缀。",
    "连接超时秒数": "SMTP 连接与发送的超时时间（秒）。",
    "发送测试邮件": "填写完上方 SMTP/发件邮箱/授权码/收件人后，点击此按钮发送一封测试邮件。",
}


def _send_test_email_callback(*args):
    """设置页“发送测试邮件”按钮回调。"""
    from src.core.email_service import send_test_email_from_settings

    send_test_email_from_settings()


# “发送测试邮件”按钮：在设置页填写 SMTP/发件邮箱/授权码/收件人后可直接测试。
EMAIL_CONFIG_TYPE = {
    "发送测试邮件": {
        "type": "button",
        "text": "发送测试邮件",
        "icon": FluentIcon.SEND,
        "callback": _send_test_email_callback,
    },
}

# 该配置注册到 ok 的 global_configs，因此会显示在“设置”（可调整 UI 颜色/主题）页，
# 而不是本项目的“全局配置”页。
email_config_option = ConfigOption(
    EMAIL_CONFIG_NAME,
    DEFAULT_EMAIL_CONFIG,
    description="邮件发送配置",
    config_description=EMAIL_CONFIG_DESCRIPTION,
    config_type=EMAIL_CONFIG_TYPE,
    icon=FluentIcon.MAIL,
)
