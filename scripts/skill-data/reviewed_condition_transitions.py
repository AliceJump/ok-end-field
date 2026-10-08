"""Reviewed action effects on observed conditions, separate from damage rows."""

ANTAL_RECORDS = {
    'chr_0023_antal_ultimate_skill': '95ba6add40352915096f74a8fc04d921a9c6fef28c861db75223ecb75b4f2945',
    'buff_chr_0023_antal_utimate_skill': '9f91af7045dee6bba8bddc018cfc3e0ff69739a16566c7905d2b5b4f7d10a1a3',
    'buff_common_damage_immune_ult_skill': 'b742dfedad17bd70a8b051ce9ccab45fa231d4ff1e5ed363c58e3bbb38337436',
    'buff_chr_0023_antal_ultimate_icon': 'be55268fa75109fdf04c8f918781d814361a24936f3b30b6e12eb517193ccc85',
    'buff_chr_0023_antal_ultimate_icon_2': 'bdfd590551784fab066e8c7f3fa65c3d764edd1d119d1e4297c6b5f8c4f270e9',
}


def reviewed_transitions(store, native_record, tags):
    for name, digest in ANTAL_RECORDS.items():
        if native_record(store, name)['source']['sha256'] != digest:
            raise ValueError('Unreviewed Antal condition preservation program')
    return {'antal': [{'kind': 'ult', 'skill_id': 'chr_0023_antal_ultimate_skill',
                       'scope': 'nominal_unchanged_enemy_tags; no_target_identity_or_remaining_lifetime',
                       'preserves_recommended_tags': sorted(tags),
                       'record_hashes': ANTAL_RECORDS,
                       'evidence': 'ally-only team amplification and self immunity; no enemy status creation/consumption',
                       'excludes': ['owner_resources', 'distance', 'entities', 'opaque_predicates', 'global_target_binding']} ]}
