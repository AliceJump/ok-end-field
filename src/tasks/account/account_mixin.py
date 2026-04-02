from src.tasks.mixin.login_mixin import LoginMixin
class AccountMixin(LoginMixin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config.update({
            "多账户模式": False,
            "账号列表":"账号1,密码1\n账号2,密码2\n账号3,密码3",
        })
        self.config_description.update({
            "多账户模式": "是否启用多账户模式",
            "账号列表": "多账户模式下，账号密码列表，每行一个账号，账号密码用逗号分隔",
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

            parts = [x.strip() for x in line.split(",")]

            if len(parts) != 2:
                self.log_info(f"账号格式错误，已跳过: {line}")
                continue  # ✅ 跳过非法格式

            username, password = parts
            account_list.append((username, password))  # ✅ 用 tuple 更清晰

        return account_list
