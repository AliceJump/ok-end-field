"""Validate all 7 BATTLE_CONFIG_DESCRIPTION entries in en_US .po"""
import polib

po = polib.pofile('i18n/en_US/LC_MESSAGES/ok.po')
print(f'Total entries: {len(po)}')

descs = [
    '按列表顺序自动循环释放「战技」。\n可从 1/2/3/4 中选择并排序，至少保留一个。',
    '当「技力条」达到该数值时，\n开始执行技能序列。取值范围1-3。',
    '战斗结束后发送系统通知。',
    '战斗中周期触发锁敌+向前闪避的最小间隔秒数。\n取值不小于1。',
    '进入战斗后开始自动操作前的等待秒数。',
    '是否启用排轴功能。\n启用后会根据「排轴序列」配置的顺序优先释放对应角色的技能，\n当排轴失败时回退到非排轴状态。',
    "仅接受'1,2,3,4,ult_1,ult_2,ult_3,ult_4,e,sleep_[n],normal_[n]'这些值的逗号分隔字符串，\nnormal_[n] 表示临时切换为普通战斗模式 n 秒，期间按「技能释放」顺序自动出技。",
]

names = [
    '技能释放', '启动技能点数', '完成通知', '无数字操作间隔',
    '进入战斗后的初始等待时间', '启用排轴', '排轴序列'
]

for name, desc in zip(names, descs):
    e = po.find(desc)
    if e and e.msgstr:
        print(f'  ✅ {name}: {e.msgstr[:70]}...')
    elif e:
        print(f'  ❌ {name}: found but empty')
    else:
        print(f'  ❌ {name}: NOT FOUND')

# Also check all langs have no empties
print()
for lang in ['en_US','zh_CN','zh_TW','es_ES','ja_JP','ko_KR']:
    po2 = polib.pofile(f'i18n/{lang}/LC_MESSAGES/ok.po')
    empty = [e for e in po2 if not e.msgstr and e.msgid]
    print(f'{lang}: {len(po2)} entries, {len(empty)} empty')
