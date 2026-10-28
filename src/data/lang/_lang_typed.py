"""由 scripts/i18n/gen_lang_stubs.py 自动生成，请勿手改。

为 self.lang.<模块>.<key> 提供静态类型提示：
  - 自动补全：输入 self.lang.<模块>. 时列出全部 key
  - 悬浮提示：hover 显示该 key 在基准语言下的对应值

string 节点 -> str（运行时按当前 UI 语言取值，docstring 显示基准值）；
pattern 节点 -> re.Pattern[str]（docstring 显示文本）。
"""

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import LangModule as _LangModuleBase

    _LangModuleBaseT = _LangModuleBase
else:
    _LangModuleBaseT = object


class AutoSkipDialogTaskModule(_LangModuleBaseT):
    """AutoSkipDialogTask — OCR 语言节点（值取自 zh_CN）"""

    k_92399078: str
    """结束会话"""


class CharactersModule(_LangModuleBaseT):
    """characters — OCR 语言节点（值取自 zh_CN）"""

    zhuang_fangyi: str
    """庄方宜"""

    rossi: str
    """洛茜"""

    tangtang: str
    """汤汤"""

    endministrator: str
    """管理员"""

    lifeng: str
    """黎风"""

    ember: str
    """余烬"""

    gilberta: str
    """洁尔佩塔"""

    ardelia: str
    """艾尔黛拉"""

    pogranichnik: str
    """骏卫"""

    laevatain: str
    """莱万汀"""

    yvonne: str
    """伊冯"""

    last_rite: str
    """别礼"""

    chen_qianyu: str
    """陈千语"""

    snowshine: str
    """昼雪"""

    xaihi: str
    """赛希"""

    wulfgard: str
    """狼卫"""

    perlica: str
    """佩丽卡"""

    arclight: str
    """弧光"""

    alesh: str
    """阿列什"""

    avywenna: str
    """艾维文娜"""

    da_pan: str
    """大潘"""

    estella: str
    """埃特拉"""

    catcher: str
    """卡契尔"""

    antal: str
    """安塔尔"""

    fluorite: str
    """萤石"""

    akekuri: str
    """秋栗"""

    arcane: str
    """诀"""

    camille: str
    """卡缪"""

    mi_fu: str
    """弭弗"""

    liino: str
    """梨诺"""

    typhoeus: str
    """提弗洛斯"""

    purrchena: str
    """噗切娜"""

    si: str
    """祀"""


class DailyBattleMixinModule(_LangModuleBaseT):
    """daily_battle_mixin — OCR 语言节点（值取自 zh_CN）"""

    k_25e74dce: re.Pattern[str]
    """当前"""

    k_b8a81b7a: re.Pattern[str]
    """放弃"""

    k_bfe73e18: re.Pattern[str]
    """激发|放弃"""

    k_b0e3a2da: re.Pattern[str]
    """登上滑索架"""

    k_b56d9ac6: re.Pattern[str]
    """确认"""

    k_79f91106: re.Pattern[str]
    """索引"""

    k_a6ee3a67: re.Pattern[str]
    """自选"""

    k_4cc61900: re.Pattern[str]
    """触碰"""

    k_39d12e73_1: re.Pattern[str]
    """领取"""

    k_70b20820: re.Pattern[str]
    """选择"""

    k_unit_day: str
    """天"""

    k_unit_hour: str
    """小时"""

    k_reward_select: str
    """奖励选择"""


class DailyBuyMixinModule(_LangModuleBaseT):
    """daily_buy_mixin — OCR 语言节点（值取自 zh_CN）"""

    stable_materials_tab: re.Pattern[str]
    """稳定"""


class DailyLiaisonMixinModule(_LangModuleBaseT):
    """daily_liaison_mixin — OCR 语言节点（值取自 zh_CN）"""

    k_27d2b829: str
    """舰桥"""

    k_933056f0: re.Pattern[str]
    """信赖"""

    k_d661f6da: re.Pattern[str]
    """存放"""


class DailyRoutineMixinModule(_LangModuleBaseT):
    """daily_routine_mixin — OCR 语言节点（值取自 zh_CN）"""

    k_105cdd5a: re.Pattern[str]
    """任务$"""

    k_bf856c96: re.Pattern[str]
    """一键领取"""

    k_7be4248b: re.Pattern[str]
    """信用交易所"""

    k_3fef35d6: re.Pattern[str]
    """全部领取"""

    k_b693e51a: re.Pattern[str]
    """收取信用"""

    k_f646bcd5: re.Pattern[str]
    """无待领取信用"""

    k_573c7c18: re.Pattern[str]
    """货物装箱"""

    k_8f2058a8: re.Pattern[str]
    """查看报价"""

    k_23926d61: re.Pattern[str]
    """前往"""

    k_4d0b4688: re.Pattern[str]
    """取消"""

    k_cdb1d49b: re.Pattern[str]
    """可"""

    k_e84c3ae9: re.Pattern[str]
    """好友"""

    k_41a9fd98: re.Pattern[str]
    """我转交的委托"""

    k_ffb5655a: re.Pattern[str]
    """收取"""

    k_8d0e83fc: re.Pattern[str]
    """日常"""

    k_1c5ad36e: re.Pattern[str]
    """是否取消"""

    k_4a2ece6a: re.Pattern[str]
    """暂存区"""

    k_298d3284: re.Pattern[str]
    """本地仓储节点"""

    k_25d2b666: re.Pattern[str]
    """武器补给"""

    k_13eea5dd: re.Pattern[str]
    """每周事务"""

    k_557911d7: re.Pattern[str]
    """独立"""

    k_059a808c: re.Pattern[str]
    """理智补给|危机筹备"""

    k_401d58fa: re.Pattern[str]
    """的线索"""

    k_1faf3321: re.Pattern[str]
    """装备"""

    k_bb6c696b: re.Pattern[str]
    """货品"""

    k_70b20820: re.Pattern[str]
    """选择"""

    k_a730d877: re.Pattern[str]
    """选择拜访"""

    k_d7613f0e: re.Pattern[str]
    """通行证任务"""

    k_727d1bec: re.Pattern[str]
    """通行证奖励"""

    k_3ecdd4bb: re.Pattern[str]
    """键领取"""

    k_39d12e73_1: re.Pattern[str]
    """领取"""

    k_ed7e11a1: re.Pattern[str]
    """丰碑"""

    k_view_quote: str
    """查看报价"""

    k_2da75f4f: re.Pattern[str]
    """影拓|活动期间|普通模式"""

    k_ebbfb983: re.Pattern[str]
    """苦难"""


class DailyShopMixinModule(_LangModuleBaseT):
    """daily_shop_mixin — OCR 语言节点（值取自 zh_CN）"""

    k_9a0004ef: re.Pattern[str]
    """信用"""


class DailyTradeMixinModule(_LangModuleBaseT):
    """daily_trade_mixin — OCR 语言节点（值取自 zh_CN）"""

    k_b84e4cb0: re.Pattern[str]
    """出售"""

    k_23926d61: re.Pattern[str]
    """前往"""

    k_d6bdcc47: re.Pattern[str]
    """地区建设"""

    k_930f2e66: re.Pattern[str]
    """市场"""

    k_33fb3f9c: re.Pattern[str]
    """弹性"""

    k_cd3a269a: re.Pattern[str]
    """查看好友价格"""

    k_7cf40bbd: re.Pattern[str]
    """购买"""

    k_13f2c5a1: re.Pattern[str]
    """货组"""

    impending_overflow: re.Pattern[str]
    """即将|溢出"""

    k_fa04e4df: re.Pattern[str]
    """物资调度终端"""


class DeliveryTaskModule(_LangModuleBaseT):
    """DeliveryTask — OCR 语言节点（值取自 zh_CN）"""

    k_96b876e3: str
    """工业"""

    k_b0e3a2da: str
    """登上滑索架"""

    k_ae8fb114: str
    """运送委托列表"""

    k_6536f6f1: re.Pattern[str]
    """资源"""

    k_0c1ef9f5: re.Pattern[str]
    """交货"""


class MatrixCleanTaskModule(_LangModuleBaseT):
    """matrix_clean_task — OCR 语言节点（值取自 zh_CN）"""

    k_9ca99bd0: re.Pattern[str]
    """武器基质"""

    k_83c0c24d: re.Pattern[str]
    """想要分解"""

    k_52e36ffd: re.Pattern[str]
    """快捷放入"""

    k_7ba0a5b1: re.Pattern[str]
    """稳定基质"""

    k_16da2b84: re.Pattern[str]
    """净态基质"""

    k_1a40e234: re.Pattern[str]
    """高纯基质"""

    k_2396a6d9: re.Pattern[str]
    """未上锁"""

    k_fadf24db: re.Pattern[str]
    """保存"""

    k_9c61417d: re.Pattern[str]
    """无法分解"""

    k_75a84eb1: re.Pattern[str]
    """未拥有该类型"""

    k_feecb1e6: re.Pattern[str]
    """^退出$"""


class GameFlowMixinModule(_LangModuleBaseT):
    """game_flow_mixin — OCR 语言节点（值取自 zh_CN）"""

    k_8b2ca27a: re.Pattern[str]
    """点击空白处继续"""

    k_7cd2e0c0: re.Pattern[str]
    """结束拜访"""


class LiaisonMixinModule(_LangModuleBaseT):
    """liaison_mixin — OCR 语言节点（值取自 zh_CN）"""

    k_80b758b9: str
    """中央环厅"""


class LoginMixinModule(_LangModuleBaseT):
    """login_mixin — OCR 语言节点（值取自 zh_CN）"""

    ms: re.Pattern[str]
    """ms"""

    k_20275ef2: re.Pattern[str]
    """上次"""


class TakeDeliveryTaskModule(_LangModuleBaseT):
    """TakeDeliveryTask — OCR 语言节点（值取自 zh_CN）"""

    k_a72a252f: str
    """仓储节点"""

    k_046ed3ab: str
    """请尽快送达"""

    k_ae8fb114: str
    """运送委托列表"""


class WarehouseTransferTaskModule(_LangModuleBaseT):
    """WarehouseTransferTask — OCR 语言节点（值取自 zh_CN）"""

    k_3cb6baa6: str
    """仓库切换"""

    k_d661f6da: re.Pattern[str]
    """存放"""

    k_65fe35c4: re.Pattern[str]
    """已连接"""

    k_b56d9ac6: re.Pattern[str]
    """确认"""


class WorldMapModule(_LangModuleBaseT):
    """world_map — OCR 语言节点（值取自 zh_CN）"""

    d96: re.Pattern[str]
    """D96钢"""

    k_4e0048f0: re.Pattern[str]
    """三位一体"""

    k_a4f9b7a1: re.Pattern[str]
    """三相纳米片"""

    k_2b6994c2: re.Pattern[str]
    """中容武陵电池"""

    k_f87e5ddb: re.Pattern[str]
    """中容谷地电池"""

    k_0fc6c7ff: re.Pattern[str]
    """产物"""

    k_1205977f: re.Pattern[str]
    """优质柑实罐头"""

    k_fd92a736: re.Pattern[str]
    """优质芽针针剂"""

    k_85ad4a7c: re.Pattern[str]
    """优质荞愈胶囊"""

    k_5209de4f: re.Pattern[str]
    """优质锦草软饮"""

    k_3673bddf: re.Pattern[str]
    """低容武陵电池"""

    k_2181d9cd: re.Pattern[str]
    """供能高地"""

    k_afba31e6: re.Pattern[str]
    """冬虫夏笋货组"""

    k_3917d839: re.Pattern[str]
    """危境再现"""

    k_9d89b819: re.Pattern[str]
    """危境预演"""

    k_214efec8: re.Pattern[str]
    """四号谷地"""

    k_c8cc877d: re.Pattern[str]
    """团结牌口服液货组"""

    k_b3748114: re.Pattern[str]
    """基建前站"""

    k_0b813891: re.Pattern[str]
    """塞什卡髀石货组"""

    k_916bfc88: re.Pattern[str]
    """天使罐头货组"""

    k_be2958a8: re.Pattern[str]
    """天师龙泡泡货组"""

    k_9ee92701: re.Pattern[str]
    """天王坪援建点"""

    k_cb171c77: re.Pattern[str]
    """岳研避瘴茶货组"""

    k_f146718d: re.Pattern[str]
    """巫术矿钻货组"""

    k_e8648639: re.Pattern[str]
    """干员养成"""

    k_8c63385b: re.Pattern[str]
    """干员经验"""

    k_216c7def: re.Pattern[str]
    """干员进阶"""

    k_7dfd1882: re.Pattern[str]
    """心脏修缮站"""

    k_c2d3c6f9: re.Pattern[str]
    """快子遴捡晶格"""

    k_ae13a460: re.Pattern[str]
    """息壤净水芯货组"""

    k_d316ae88: re.Pattern[str]
    """息壤玉葫芦"""

    k_49b013b4: re.Pattern[str]
    """息壤葫芦"""

    k_015b12ba: re.Pattern[str]
    """悬空鼷兽骨雕货组"""

    k_d4f9d913: re.Pattern[str]
    """技能提升"""

    k_854f8a4f: re.Pattern[str]
    """星体晶块货组"""

    k_c55c669c: re.Pattern[str]
    """晶体外壳"""

    k_90252af0: re.Pattern[str]
    """枢纽区"""

    k_e1635e13: re.Pattern[str]
    """柑实罐头"""

    k_4ce45095: re.Pattern[str]
    """武侠电影货组"""

    k_5c6514da: re.Pattern[str]
    """武器养成"""

    k_3e316505: re.Pattern[str]
    """武器经验"""

    k_0903c052: re.Pattern[str]
    """武器进阶"""

    k_a4410e3f: re.Pattern[str]
    """武陵"""

    k_e128b089: re.Pattern[str]
    """武陵冻梨货组"""

    k_93e2bdac: re.Pattern[str]
    """武陵城"""

    k_ece5ec1b: re.Pattern[str]
    """清波寨"""

    k_0383e694: re.Pattern[str]
    """清波筏货组"""

    k_ae7b2918: re.Pattern[str]
    """源石树幼苗货组"""

    k_dff480f9: re.Pattern[str]
    """源石研究园"""

    k_37ad1f84: re.Pattern[str]
    """源矿"""

    k_5af845da: re.Pattern[str]
    """白垩界卫"""

    k_d26e1bbe: re.Pattern[str]
    """矿物"""

    k_2b92fbc9: re.Pattern[str]
    """矿脉源区"""

    k_55ed0a7b: re.Pattern[str]
    """硬脑壳头盔货组"""

    k_3cdec45b: re.Pattern[str]
    """精选柑实罐头"""

    k_adb42389: re.Pattern[str]
    """精选荞愈胶囊"""

    k_0d158460: re.Pattern[str]
    """罗丹"""

    k_fadfef4b: re.Pattern[str]
    """聂菲斯"""

    k_alaikeliusi: re.Pattern[str]
    """阿莱克琉斯"""

    k_e0c9b138: re.Pattern[str]
    """能量淤积点"""

    k_ccdf2110: re.Pattern[str]
    """致密源石粉末"""

    k_9aca6716: re.Pattern[str]
    """芽针针剂"""

    k_710b0237: re.Pattern[str]
    """荞愈胶囊"""

    k_a3c507b8: re.Pattern[str]
    """蓝铁矿"""

    k_308fd0c3: re.Pattern[str]
    """警戒者矿镐货组"""

    k_ba83829f: re.Pattern[str]
    """试验园区"""

    k_783931ed: re.Pattern[str]
    """谷地水培肉货组"""

    k_2cbc9cb6: re.Pattern[str]
    """象限拟合液"""

    k_ffeba46d: re.Pattern[str]
    """赫铜零件"""

    k_e82c9392: re.Pattern[str]
    """超距辉映管"""

    k_d75af357: re.Pattern[str]
    """边角料积木货组"""

    k_5a72ba02: re.Pattern[str]
    """重建指挥部"""

    k_428c533a: re.Pattern[str]
    """重息壤"""

    k_c18b3249: re.Pattern[str]
    """钱币收集"""

    k_ff00a312: re.Pattern[str]
    """锚点厨具货组"""

    k_b6666941: re.Pattern[str]
    """锦草软饮"""

    k_de7be865: re.Pattern[str]
    """阮一"""

    k_7a5505d0: re.Pattern[str]
    """难民暂居处"""

    k_50917f2a: re.Pattern[str]
    """首墩"""

    k_d5586b4b: re.Pattern[str]
    """高容谷地电池"""

    yingtuo_monument: re.Pattern[str]
    """影拓丰碑"""

    k_zhuoliu: re.Pattern[str]
    """浊流具现"""

    k_zhuotong: re.Pattern[str]
    """灼痛疤痕"""

    k_wuji: re.Pattern[str]
    """无机造物"""

    k_dadiqizi: re.Pattern[str]
    """大地的弃子"""

    k_zangjiangu: re.Pattern[str]
    """藏剑谷"""

    k_yinglongguan: re.Pattern[str]
    """应龙关"""

    k_beibujinqu: re.Pattern[str]
    """北部禁区"""

    k_727bb38b: re.Pattern[str]
    """死寂争鸣"""

    k_shanzhongjianhou: re.Pattern[str]
    """山中见犼"""

    k_825cdc26: re.Pattern[str]
    """灼铜零件"""

    k_871bc220: re.Pattern[str]
    """盈天台建设站"""

    k_xuesonglin: re.Pattern[str]
    """雪松林"""

    amethyst_bottle: re.Pattern[str]
    """紫晶质瓶"""

    amethyst_part: re.Pattern[str]
    """紫晶零件"""

    xiranite: re.Pattern[str]
    """息壤"""

    cuprium_part: re.Pattern[str]
    """赤铜零件"""

    separator_core: re.Pattern[str]
    """分离芯"""

    k_84e3e9d9: re.Pattern[str]
    """祀的画"""

    k_a2eb44e6: re.Pattern[str]
    """跃迁浆体涂片"""


class ZipLineMixinModule(_LangModuleBaseT):
    """zip_line_mixin — OCR 语言节点（值取自 zh_CN）"""

    k_b0e3a2da: str
    """登上滑索架"""

    k_2f4f4a2f: re.Pattern[str]
    """向目标移动"""

    k_0b1e4f35: re.Pattern[str]
    """离开滑索架"""


class _LangAccessorTyped:
    """self.lang 的类型化声明（仅类型提示，运行时由 __getattr__ 动态加载）"""

    AutoSkipDialogTask: AutoSkipDialogTaskModule
    characters: CharactersModule
    daily_battle_mixin: DailyBattleMixinModule
    daily_buy_mixin: DailyBuyMixinModule
    daily_liaison_mixin: DailyLiaisonMixinModule
    daily_routine_mixin: DailyRoutineMixinModule
    daily_shop_mixin: DailyShopMixinModule
    daily_trade_mixin: DailyTradeMixinModule
    DeliveryTask: DeliveryTaskModule
    matrix_clean_task: MatrixCleanTaskModule
    game_flow_mixin: GameFlowMixinModule
    liaison_mixin: LiaisonMixinModule
    login_mixin: LoginMixinModule
    TakeDeliveryTask: TakeDeliveryTaskModule
    WarehouseTransferTask: WarehouseTransferTaskModule
    world_map: WorldMapModule
    zip_line_mixin: ZipLineMixinModule
