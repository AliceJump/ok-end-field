from src.data.world_map import goods_dict
from src.data.lang import LangAccessor
from src.tasks.daily.daily_credit_mixin import DailyCreditMixin
from src.tasks.daily.misc.daily_boat_mixin import DailyBoatMixin
from src.tasks.daily.misc.daily_craft_mixin import DailyCraftMixin
from src.tasks.daily.misc.daily_logistics_mixin import DailyLogisticsMixin
from src.tasks.daily.misc.daily_outpost_mixin import DailyOutpostMixin
from src.tasks.daily.misc.daily_reward_mixin import DailyRewardMixin


class DailyRoutineFeature(
    DailyCreditMixin,
    DailyCraftMixin,
    DailyLogisticsMixin,
    DailyOutpostMixin,
    DailyRewardMixin,
    DailyBoatMixin,
):
    # 类型提示：lang 等属性实际由 __getattr__ 转发到 self._task
    lang: LangAccessor
    BOAT_STAGES = ['收集线索', '制造舱', '培养舱']
    ACTIVITY_REWARDS = ['周常奖励', '理智补给', '刮刮乐']

    def __init__(self, task):
        self._task = task
        task.default_config.update({
            "⭐收邮件": True,
            "交易货品优先序列": [],
            "据点兑换仅购买优先商品": False,
            "⭐转交运送委托": True,
            "⭐造装备": True,
            "⭐简易制作": True,
            "⭐收信用": True,
            "尝试仅收培育室": True,
            "⭐帝江号收菜": self.BOAT_STAGES,
            "⭐活动奖励": self.ACTIVITY_REWARDS,
            "⭐日常奖励": True,
        })
        task.config_type["⭐帝江号收菜"] = {
            "type": "multi_selection",
            "options": self.BOAT_STAGES,
        }
        task.config_type["⭐活动奖励"] = {
            "type": "multi_selection",
            "options": self.ACTIVITY_REWARDS,
        }
        all_goods = []
        for goods_list in goods_dict.values():
            all_goods.extend(goods_list)
        task.config_type["交易货品优先序列"] = {
            "options_available": all_goods,
            "allow_duplication": False,
        }
        task.config_description.update({
            "⭐收邮件": "是否前往「邮箱」领取邮件。",
            "⭐简易制作": (
                "是否前往「帝江号/简易制作」制作物品。\n"
                "与「帝江号一键存放」合并执行，共享传送与开背包。"
            ),
            "交易货品优先序列": (
                "默认留空，交易货品顺序随机。\n"
                "更多用法参见 ./docs/日常任务.md > 优先货品交易序列 。"
            ),
            "据点兑换仅购买优先商品": (
                "启用后，仅当「交易货品优先序列」不为空时，据点兑换才只购买其中的商品；"
                "序列为空时按原逻辑兑换。"
            ),
            "⭐转交运送委托": (
                "是否在「地区建设/仓储结点」中转交全部运送委托，并领取一次转交委托奖励。"
            ),
            "⭐造装备": (
                "是否前往「装备制造/套组装备制造」并制作一件列表首位的装备。\n"
                "请确保有足够的装备原件和调度券。"
            ),
            "⭐收信用": (
                "是否前往好友的「帝江号」并在「访客终端」上进行助力获得信用。\n"
                "助力结束后，前往「采购中心/信用交易所」收取全部助力。"
            ),
            "尝试仅收培育室": (
                "若选项开启，则优先尝试仅助力好友「帝江号」上的「培养仓」。\n"
                "如果不能，至少助力一次其它舱室。"
            ),
            "⭐帝江号收菜": (
                "选择帝江号收菜内容：\n"
                "收集线索：前往会客室收集线索，集齐后开启情报交流。\n"
                "制造舱：收取培养材料并补足待制造数量。\n"
                "培养舱：收取培养材料并直接再次培养。"
            ),
            "⭐活动奖励": (
                "选择要领取的活动奖励：\n"
                "周常奖励：领取每周事务奖励。\n"
                "理智补给：领取理智补给。\n"
                "刮刮乐：执行刮刮乐。"
            ),
            "⭐日常奖励": (
                "是否领取「行动手册/日常」和「通行证」中的奖励。"
            ),
        })
        task.default_config_group.update({
            "⭐据点兑换": ["交易货品优先序列", "据点兑换仅购买优先商品"],
            "⭐收信用": ["尝试仅收培育室"],
        })

    def __getattr__(self, name):
        return getattr(self._task, name)
