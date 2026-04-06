from src.tasks.mixin.login_mixin import LoginMixin
from src.tasks.account.account_scope_store import resolve_account_id


class AccountMixin(LoginMixin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config.update({
            "多账户模式": False,
            "多账户独立配置": False,
            "账号列表":"账号1,密码1\n账号2,密码2\n账号3,密码3",
        })
        self.config_description.update({
            "多账户模式": (
                "是否启用多账户模式\n"
                "需要已登录任意账号"
            ),
            "多账户独立配置": (
                "是否启用账号独立配置覆盖\n"
                "开启后同一任务可按账号使用不同参数"
            ),
            "账号列表": (
                "账号密码列表\n"
                "每行一个账号，账号密码用逗号分隔"
            ),
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

            if "," not in line:
                self.log_info(f"账号格式错误，已跳过: {line}")
                continue  # ✅ 跳过非法格式

            username_part, password_part = line.split(",", 1)
            username = username_part.strip()
            password = password_part.strip()

            if not username:
                self.log_info(f"账号格式错误，已跳过: {line}")
                continue

            account_id = resolve_account_id(username, create_if_missing=False)
            account_list.append(
                {
                    "account_id": account_id,
                    "username": username,
                    "password": password,
                }
            )

        return account_list
