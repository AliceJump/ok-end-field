from src.tasks.account.account_identity import account_name_from_line
from src.tasks.account.account_scope_store import resolve_account_id
from src.tasks.mixin.login_mixin import LoginMixin


class AccountMixin(LoginMixin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config.update(
            {
                "多账户模式": False,
                "多账户独立配置": False,
                "账号列表": "账号1\n账号2\n账号3",
            }
        )
        self.config_description.update(
            {
                "多账户模式": ("是否启用多账户模式\n需要已登录任意账号,可能不支持全屏游戏"),
                "多账户独立配置": ("是否启用账号独立配置覆盖\n开启后同一任务可按账号使用不同参数"),
                "账号列表": (
                    "账号列表，每行一个手机号。\n"
                    "若一行包含逗号，只使用逗号前的账号内容，逗号后内容会直接忽略。\n"
                    "切换账号时按登录界面可见的手机号前三位和后四位匹配；若可见号码重复，则优先选择未标记为『最近』的账号。"
                ),
            }
        )
        self.default_config_group.update(
            {
                "多账户模式": ["多账户模式"],
            }
        )
        # 「多账户模式」开关: 开启后展开显示「多账户独立配置」和「账号列表」两个子选项
        if not hasattr(self, "config_type") or self.config_type is None:
            self.config_type = {}
        self.config_type["多账户模式"] = {
            "sub_configs": {
                True: ["多账户独立配置", "账号列表"],
            },
        }

    def get_account_list(self):
        account_str = self.config.get("账号列表", "")
        account_list = []

        if not account_str:
            return account_list

        for raw_line in str(account_str).splitlines():
            line = raw_line.strip()
            if not line:
                continue

            username = account_name_from_line(line)
            if not username:
                self.log_info(self.tr("账号格式错误，已跳过: {line}").format(line=line))
                continue

            account_id = resolve_account_id(username, create_if_missing=False)
            account_list.append(
                {
                    "account_id": account_id,
                    "username": username,
                }
            )

        return account_list
