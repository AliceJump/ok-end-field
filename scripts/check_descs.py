"""检查缺失的 BattleConfig 描述翻译"""
import polib

langs = ['en_US', 'zh_CN', 'zh_TW', 'es_ES', 'ja_JP', 'ko_KR']
descs_to_check = [
    '按列表顺序自动循环释放「战技」。\n可从 1/2/3/4 中选择并排序，至少保留一个。',
    '后台运行时，战斗结束后发送通知。',
    '进入战斗后开始自动操作前的等待秒数。',
]

for lang in langs:
    po = polib.pofile(f'i18n/{lang}/LC_MESSAGES/ok.po')
    print(f'=== {lang} ===')
    for desc in descs_to_check:
        entry = po.find(desc)
        if entry:
            if entry.msgstr:
                print(f'  ✅ {desc[:40]}...')
            else:
                print(f'  ⚠️ FOUND but EMPTY: {desc[:40]}...')
        else:
            print(f'  ❌ MISSING: {desc[:40]}...')

print('\n=== Now checking ALL descriptions ===')
# Show all unique msgids that contain Chinese description-like content
for lang in ['en_US']:
    po = polib.pofile(f'i18n/{lang}/LC_MESSAGES/ok.po')
    battle_keys = ['技能释放', '启动技能点数', '完成通知', '无数字操作间隔',
                   '进入战斗后的初始等待时间', '启用排轴', '排轴序列']
    for key in battle_keys:
        entry = po.find(key)
        if entry:
            print(f'  key[{key}] -> msgstr[{entry.msgstr[:60]}...]')
        else:
            print(f'  key[{key}] -> NOT FOUND in .po')
