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
        """将未在模块实例上定义的共享能力（如OCR/导航/日志）委托给 DailyTask。"""
        return getattr(self.parent, item)


class DailyBuyModule(DailyBuyMixin, DailyModuleBase):
    def __init__(self, parent):
        DailyModuleBase.__init__(self, parent)
        self.setup_daily_buy_module()


class DailyBattleModule(DailyBattleMixin, DailyModuleBase):
    def __init__(self, parent):
        DailyModuleBase.__init__(self, parent)
        self.setup_daily_battle_module()


class DailyTradeModule(DailyTradeMixin, DailyModuleBase):
    def __init__(self, parent):
        DailyModuleBase.__init__(self, parent)
        self.setup_daily_trade_module()


class DailyShopModule(DailyShopMixin, DailyModuleBase):
    def __init__(self, parent):
        DailyModuleBase.__init__(self, parent)
        self.setup_daily_shop_module()


class DailyRoutineModule(DailyRoutineMixin, DailyModuleBase):
    def __init__(self, parent):
        DailyModuleBase.__init__(self, parent)
        self.setup_daily_routine_module()


class DailyLiaisonModule(DailyLiaisonMixin, DailyModuleBase):
    def __init__(self, parent):
        DailyModuleBase.__init__(self, parent)
        self.setup_daily_liaison_module()
