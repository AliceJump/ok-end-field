"""Check if all BATTLE_CONFIG_DESCRIPTION entries exist in .po files"""
import polib
import sys

source_descs = {
    "技能释放": '按列表顺序自动循环释放「战技」。\n可从 1/2/3/4 中选择并排序，至少保留一个。',
    "启动技能点数": '当「技力条」达到该数值时，\n开始执行技能序列。取值范围1-3。',
    "完成通知": '战斗结束后发送系统通知。',
    "无数字操作间隔": '战斗中周期触发锁敌+向前闪避的最小间隔秒数。\n取值不小于1。',
    "进入战斗后的初始等待时间": '进入战斗后开始自动操作前的等待秒数。',
    "启用排轴": '是否启用排轴功能。\n启用后会根据「排轴序列」配置的顺序优先释放对应角色的技能，\n当排轴失败时回退到非排轴状态。',
    "排轴序列": "仅接受'1,2,3,4,ult_1,ult_2,ult_3,ult_4,e,sleep_[n],normal_[n]'这些值的逗号分隔字符串，\nnormal_[n] 表示临时切换为普通战斗模式 n 秒，期间按「技能释放」顺序自动出技。",
}

for lang in ['en_US', 'zh_CN', 'zh_TW', 'es_ES', 'ja_JP', 'ko_KR']:
    po = polib.pofile(f'i18n/{lang}/LC_MESSAGES/ok.po')
    print(f'\n=== {lang} ===')
    for key, desc in source_descs.items():
        entry = po.find(desc)
        if entry:
            status = 'OK' if entry.msgstr else 'EMPTY'
            print(f'  ✅ {key}: {status}')
            if entry.msgstr:
                print(f'       → {entry.msgstr[:60]}...')
        else:
            print(f'  ❌ {key}: NOT FOUND')
            # fuzzy search
            first_line = desc.split('\n')[0][:20]
            matches = [e for e in po if first_line in e.msgid]
            if matches:
                for m in matches:
                    print(f'     Similar msgid: {m.msgid[:80]}')
