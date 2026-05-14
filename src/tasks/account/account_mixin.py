from src.tasks.mixin.login_mixin import LoginMixin
from src.tasks.account.account_scope_store import resolve_account_id


class AccountMixin(LoginMixin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config.update({
            "多账户模式": False,
            "多账户独立配置": False,
            "账号列表":"账号1\n账号2\n账号3",
        })
        self.config_description.update({
            "多账户模式": (
                "是否启用多账户模式\n"
                "需要已登录任意账号,可能不支持全屏游戏"
            ),
            "多账户独立配置": (
                "是否启用账号独立配置覆盖\n"
                "开启后同一任务可按账号使用不同参数"
            ),
            "账号列表": (
                "账号列表，每行一个账号（手机号）。\n"
                "兼容旧格式：每行可写成 `账号,密码`，但密码字段会被忽略且不会被存储。\n"
                "登录时也可使用手机号后四位进行匹配（若唯一）。"
            ),
        })
        self.default_config_group.update({
            "多账户模式":["多账户独立配置", "账号列表"],
        })

    def get_account_list(self):
        account_str = self.config.get("账号列表", "")
        account_list = []

        if not account_str:
            return account_list

        lines = account_str.splitlines()

        for line in lines:
            line = line.strip()
            if not line:
                continue  # ✅ 跳过空行

            if "," in line:
                username_part, password_part = line.split(",", 1)
                username = username_part.strip()
            else:
                username = line.strip()  # ✅ 兼容只有账号的情况

            if not username:
                self.log_info(f"账号格式错误，已跳过: {line}")
                continue

            account_id = resolve_account_id(username, create_if_missing=False)
            account_list.append(
                {
                    "account_id": account_id,
                    "username": username,
                }
            )

        return account_list
