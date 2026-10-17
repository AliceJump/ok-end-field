"""Reviewed action effects on observed conditions, separate from damage rows."""

ANTAL_RECORDS = {
    'chr_0023_antal_ultimate_skill': '95ba6add40352915096f74a8fc04d921a9c6fef28c861db75223ecb75b4f2945',
    'buff_chr_0023_antal_utimate_skill': '9f91af7045dee6bba8bddc018cfc3e0ff69739a16566c7905d2b5b4f7d10a1a3',
    'buff_common_damage_immune_ult_skill': 'b742dfedad17bd70a8b051ce9ccab45fa231d4ff1e5ed363c58e3bbb38337436',
    'buff_chr_0023_antal_ultimate_icon': 'be55268fa75109fdf04c8f918781d814361a24936f3b30b6e12eb517193ccc85',
    'buff_chr_0023_antal_ultimate_icon_2': 'bdfd590551784fab066e8c7f3fa65c3d764edd1d119d1e4297c6b5f8c4f270e9',
}

XAIHI_RECORDS = {
    'chr_0011_seraph_ultimate_skill': 'aeb22f042668624c2de97fab95b2d8900eb6cb4017ef0eee1d0c786886f0c0da',
    'buff_common_damage_immune_ult_skill': 'b742dfedad17bd70a8b051ce9ccab45fa231d4ff1e5ed363c58e3bbb38337436',
    'buff_chr_0011_seraph_atk_buff': '1cc3a54f53c85fee01e7514fd14fc64e00ec9ecd00363b11d5d300f1339ad715',
    'buff_chr_0011_seraph_atk_buff_2': 'a83324c48558330c86e8e4f035a8d41001eb98d7e74c7eb5ba21a583fc64e5dc',
    'buff_chr_0011_seraph_ultimate_effect': 'c3450ba84128987ee93d9b7c8b921358fc330e91ca544fdf1870cdbdd1a9a4ee',
    'buff_chr_0011_seraph_ultimate_effect_2': 'f0f9a2e8c697b238b5418e44c54a5686129e604f99aa55afbf33dd1a8a3633b9',
}


def reviewed_transitions(store, native_record, tags):
    result = {}
    for actor, skill, records, evidence in (
            ('antal', 'chr_0023_antal_ultimate_skill', ANTAL_RECORDS,
             'ally-only team amplification and self immunity; no enemy status creation/consumption'),
            ('xaihi', 'chr_0011_seraph_ultimate_skill', XAIHI_RECORDS,
             'ally-only amplification, dispel and self immunity; no enemy status creation/consumption')):
        for name, digest in records.items():
            if native_record(store, name)['source']['sha256'] != digest:
                raise ValueError(f'Unreviewed {actor} condition preservation program')
        result[actor] = [{'kind': 'ult', 'skill_id': skill,
                          'scope': 'nominal_unchanged_enemy_tags; no_target_identity_or_remaining_lifetime',
                          'preserves_recommended_tags': sorted(tags), 'record_hashes': records, 'evidence': evidence,
                          'excludes': ['owner_resources', 'distance', 'entities', 'opaque_predicates', 'global_target_binding']}]
    return result
