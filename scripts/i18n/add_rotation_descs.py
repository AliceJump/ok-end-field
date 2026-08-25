"""Add 2 missing BattleConfig descriptions (启用排轴, 排轴序列) to all .po files"""
import polib
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)) + '/../..')

# The EXACT source text from BattleConfig.py
NEW_ENTRIES = {
    "启用排轴": (
        '是否启用排轴功能。\n启用后会根据「排轴序列」配置的顺序优先释放对应角色的技能，\n当排轴失败时回退到非排轴状态。',
        'Enable skill rotation.\nPrioritizes skills per rotation sequence; falls back to non-rotation state on failure.',
        '是否启用排轴功能。\n启用后会根据「排轴序列」配置的顺序优先释放对应角色的技能，\n当排轴失败时回退到非排轴状态。',
        '是否啟用排軸功能。\n啟用後會根據「排軸序列」配置的順序優先釋放對應角色的技能，\n當排軸失敗時回退到非排軸狀態。',
        'Habilitar rotación de habilidades.\nPrioriza habilidades según secuencia; retrocede al estado normal si falla.',
        '排軸機能を有効化。\n「排軸シーケンス」の順にスキルを優先解放、失敗時は非排軸状態に戻る。',
        '전투 스킬 로테이션 활성화.\n「로테이션 시퀀스」순서로 스킬 우선 해제, 실패 시 비로테이션 상태로 복귀.',
    ),
    "排轴序列": (
        "仅接受'1,2,3,4,ult_1,ult_2,ult_3,ult_4,e,sleep_[n],normal_[n]'这些值的逗号分隔字符串，\nnormal_[n] 表示临时切换为普通战斗模式 n 秒，期间按「技能释放」顺序自动出技。",
        "Comma-separated values: '1,2,3,4,ult_1,ult_2,ult_3,ult_4,e,sleep_[n],normal_[n]'.\nnormal_[n] = temporary normal combat for n seconds, using skill release order.",
        "仅接受'1,2,3,4,ult_1,ult_2,ult_3,ult_4,e,sleep_[n],normal_[n]'这些值的逗号分隔字符串，\nnormal_[n] 表示临时切换为普通战斗模式 n 秒，期间按「技能释放」顺序自动出技。",
        "僅接受'1,2,3,4,ult_1,ult_2,ult_3,ult_4,e,sleep_[n],normal_[n]'這些值的逗號分隔字符串，\nnormal_[n] 表示臨時切換為普通戰鬥模式 n 秒，期間按「技能釋放」順序自動出技。",
        "Valores separados por coma: '1,2,3,4,ult_1,ult_2,ult_3,ult_4,e,sleep_[n],normal_[n]'.\nnormal_[n] = combate normal temporal durante n segundos.",
        "カンマ区切り値: '1,2,3,4,ult_1,ult_2,ult_3,ult_4,e,sleep_[n],normal_[n]'。\nnormal_[n] = 通常戦闘に n 秒間一時切替、スキル解放順に従う。",
        "쉼표로 구분된 값: '1,2,3,4,ult_1,ult_2,ult_3,ult_4,e,sleep_[n],normal_[n]'.\nnormal_[n] = 일반 전투로 n초간 전환, 스킬 해제 순서 따름.",
    ),
}

LANGS = ['en_US', 'zh_CN', 'zh_TW', 'es_ES', 'ja_JP', 'ko_KR']
TRANS_INDEX = {lang: i+1 for i, lang in enumerate(LANGS)}

for lang in LANGS:
    po = polib.pofile(f'i18n/{lang}/LC_MESSAGES/ok.po')
    added = 0
    for key, entry_tuple in NEW_ENTRIES.items():
        msgid = entry_tuple[0]
        translation = entry_tuple[TRANS_INDEX[lang]]
        
        existing = po.find(msgid)
        if existing:
            if not existing.msgstr:
                existing.msgstr = translation
                added += 1
                print(f'{lang}: filled empty "{key}"')
            else:
                # Check if existing translation is the old/wrong version
                old_len = len(existing.msgstr)
                if old_len < 20:  # suspiciously short
                    existing.msgstr = translation
                    added += 1
                    print(f'{lang}: replaced too-short "{key}": {existing.msgstr!r} → {translation!r}')
                else:
                    print(f'{lang}: already has "{key}": {existing.msgstr[:50]}...')
        else:
            entry = polib.POEntry(msgid=msgid, msgstr=translation)
            po.append(entry)
            added += 1
            print(f'{lang}: added "{key}"')

    if added > 0:
        po.save(f'i18n/{lang}/LC_MESSAGES/ok.po')
        po.save_as_mofile(f'i18n/{lang}/LC_MESSAGES/ok.mo')
        print(f'{lang}: saved {added} changes + .mo')
    else:
        print(f'{lang}: no changes')

# Final validation
print('\n=== Validation ===')
for lang in LANGS:
    po = polib.pofile(f'i18n/{lang}/LC_MESSAGES/ok.po')
    for key, entry_tuple in NEW_ENTRIES.items():
        msgid = entry_tuple[0]
        entry = po.find(msgid)
        if entry and entry.msgstr:
            print(f'  ✅ {lang}/{key}')
        else:
            print(f'  ❌ {lang}/{key}: MISSING')
