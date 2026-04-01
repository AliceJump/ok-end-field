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
    def get_acount_list(self):
        account_str = self.config.get("账号列表", "")
        account_list = []
        if account_str:
            accounts = account_str.split("\n")
            for account in accounts:
                account = account.strip().split(",")
                if account:
                    account_list.append(account)
        return account_list
