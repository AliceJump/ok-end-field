from src.tasks.daily.daily_battle_mixin import DailyBattleMixin
from src.tasks.daily.daily_buy_mixin import DailyBuyMixin
from src.tasks.daily.daily_liaison_mixin import DailyLiaisonMixin
from src.tasks.daily.daily_routine_mixin import DailyRoutineMixin
from src.tasks.daily.daily_shop_mixin import DailyShopMixin
from src.tasks.daily.daily_trade_mixin import DailyTradeMixin


class DailyModuleBase:
    def __init__(self, parent):
        self.parent = parent

    def __getattr__(self, item):
        """将模块未定义的共享能力（如OCR/导航/日志）委托给 DailyTask。"""
        try:
            return getattr(self.parent, item)
        except AttributeError as exc:
            raise AttributeError(f"{self.__class__.__name__} 与 parent 均不存在属性: {item}") from exc


class DailyBuyModule(DailyModuleBase, DailyBuyMixin):
    def __init__(self, parent):
        super().__init__(parent)
        self.setup_daily_buy_module()


class DailyBattleModule(DailyModuleBase, DailyBattleMixin):
    def __init__(self, parent):
        super().__init__(parent)
        self.setup_daily_battle_module()


class DailyTradeModule(DailyModuleBase, DailyTradeMixin):
    def __init__(self, parent):
        super().__init__(parent)
        self.setup_daily_trade_module()


class DailyShopModule(DailyModuleBase, DailyShopMixin):
    def __init__(self, parent):
        super().__init__(parent)
        self.setup_daily_shop_module()


class DailyRoutineModule(DailyModuleBase, DailyRoutineMixin):
    def __init__(self, parent):
        super().__init__(parent)
        self.setup_daily_routine_module()


class DailyLiaisonModule(DailyModuleBase, DailyLiaisonMixin):
    def __init__(self, parent):
        super().__init__(parent)
        self.setup_daily_liaison_module()
