"""生成角色毕业配置文件（第一阶段：静态伤害基准数据库）。

证据链（evidence_level 越小越权威）：
1 = 官方游戏内推荐（森空岛 WIKI 干员页「武器推荐」/ 武器页「推荐装备干员」互证
    / 装备页「推荐干员」反查）
2 = 高质量社区攻略（新浪全干员装备适配一图流 / ldcapple 精确四件套，见 references）
3 = 启发式默认（按元素/定位套用同源社区模板，标记 heuristic）

产出：assets/data/character_builds/<角色拼音>.json（与 character_skills 同名）。

用法：
    python scripts/skill-data/generate_character_builds.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.data.operator_names import _normalize_name  # noqa: E402

DATA_DIR = ROOT / "assets/data"
CHAR_SKILLS_DIR = DATA_DIR / "character_skills"
BUILD_DIR = DATA_DIR / "character_builds"
SNAP_ROOT = ROOT / "tools/wiki_catalog/operator_details"

# 社区毕业配装（证据等级 2）。
# 来源A：新浪「全干员装备适配一图流」（毕业列，3+1 格式）
#   http://www.sina.cn/news/detail/5260896580665756.html
# 来源B：ldcapple「角色搭配」（精确四件套，仅部分角色）
#   https://www.ldcapple.com/article-detail/1078
# 套组简称 → 官方套组全名
SET_ALIASES = {
    "点剑": "点剑装备组",
    "碾骨": "碾骨装备组",
    "动火": "动火用装备组",
    "落潮": "潮涌装备组",
    "生物辅助": "生物辅助装备组",
    "生命辅助": "生物辅助装备组",
    "警用": "M.I.警用装备组",
    "警用罩衣": "M.I.警用装备组",
    "纾难": "纾难装备组",
    "拓荒": "拓荒装备组",
    "应龙": "50式应龙装备组",
    "长息": "长息装备组",
    "潮涌": "潮涌装备组",
    "轻超域": "轻超域装备组",
    "超轻域": "轻超域装备组",
    "天灾防护": "天灾防护装备组",
    "蚀电屏蔽": "蚀电防护装备组",
    "矿场γ型": "矿场γ型装备组",
    "重装信使": "重装信使装备组",
    "巡行信使": "巡行信使装备组",
    "阿伯莉": "阿伯莉遗声装备组",
    "脉冲式": "脉冲式装备组",
    "集成轻型": "集成轻型装备组",
}

# 角色 → (毕业套组 3+1 简称, 来源A)；与 ldcapple 精确四件套（来源B）合并
COMMUNITY_SETS = {
    "莱万汀": ("动火", "落潮"),
    "余烬": ("生物辅助", "生物辅助"),
    "狼卫": ("动火", "生物辅助"),
    "秋栗": ("拓荒", "碾骨"),
    "伊冯": ("警用", "纾难"),
    "别礼": ("警用罩衣", "潮涌"),
    "赛希": ("长息", "生物辅助"),
    "阿列什": ("拓荒", "潮涌"),
    "昼雪": ("生物辅助", "纾难"),
    "埃特拉": ("应龙", "长息"),
    "骏卫": ("应龙", "警用"),
    "黎风": ("点剑", "碾骨"),
    "陈千语": ("点剑", "碾骨"),
    "大潘": ("应龙", "点剑"),
    "卡契尔": ("生命辅助", "潮涌"),
}

# ldcapple 精确四件套（部位顺序：护甲/护手/配件/配件）——证据等级 2
EXACT_PIECES = {
    "余烬": ["生物辅助胸甲·壹型", "生物辅助手甲·壹型", "生物辅助接驳器·壹型", "生物辅助接驳器·壹型"],
    "黎风": ["点剑重装甲·壹型", "点剑战术手甲·壹型", "点剑火石", "点剑火石"],
    "陈千语": ["点剑重装甲·壹型", "轻超域护手", "点剑火石", "点剑火石"],
    "大潘": ["50式应龙重甲", "点剑战术手甲·壹型", "50式应龙雷达", "50式应龙雷达"],
    "狼卫": ["动火用外骨骼", "生物辅助手甲·壹型", "动火用储能匣", "动火用储能匣"],
}

# 未被社区攻略覆盖的角色：按元素套用同源模板（证据等级 3，heuristic）
ELEMENT_DEFAULT_SETS = {
    "物理": ("点剑", "碾骨"),
    "灼热": ("动火", "落潮"),
    "寒冷": ("警用", "纾难"),
    "电磁": ("应龙", "警用"),
    "自然": ("拓荒", "潮涌"),
}


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_weapon_recs(payload: dict) -> list[str]:
    """按视觉顺序提取干员详情「武器推荐」区块的 card-big entry id。"""

    def block_entries(b: dict, ids: list[str], blocks: dict) -> None:
        if b.get("kind") == "text":
            for el in (b.get("text") or {}).get("inlineElements") or []:
                if el.get("kind") == "entry":
                    e = el["entry"]
                    eid = str(e.get("id"))
                    if e.get("showType") == "card-big" and eid not in ids:
                        ids.append(eid)
        elif b.get("kind") == "table":
            tb = b.get("table") or {}
            for rid in tb.get("rowIds") or []:
                for cid in tb.get("columnIds") or []:
                    cell = (tb.get("cellMap") or {}).get(f"{rid}_{cid}") or {}
                    for child in cell.get("childIds") or []:
                        block_entries(blocks.get(child) or {}, ids, blocks)

    doc_map = payload["data"]["item"]["document"].get("documentMap") or {}
    for doc in doc_map.values():
        blocks = doc.get("blockMap") or {}
        has_note = False
        for b in blocks.values():
            if b.get("kind") == "text":
                txt = "".join(
                    (el.get("text") or {}).get("text") or ""
                    for el in (b.get("text") or {}).get("inlineElements") or []
                )
                if txt.strip().startswith("注：武器推荐内容"):
                    has_note = True
                    break
        if not has_note:
            continue
        ids: list[str] = []
        for bid in doc.get("blockIds") or []:
            block_entries(blocks.get(bid) or {}, ids, blocks)
        return ids
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", help="operator_details 快照目录名（默认最新）")
    args = parser.parse_args()
    candidates = sorted(p for p in SNAP_ROOT.iterdir() if p.is_dir()) if SNAP_ROOT.is_dir() else []
    snap_dir = SNAP_ROOT / args.snapshot if args.snapshot else (candidates[-1] if candidates else SNAP_ROOT)
    if not list(snap_dir.glob("details/*.json")):
        print(f"快照缺少 details/*.json: {snap_dir}", file=sys.stderr)
        return 1

    weapons = _load_json(DATA_DIR / "weapons.json")
    equipments = _load_json(DATA_DIR / "equipments.json")
    catalogs = [snap_dir / "catalog.json", *sorted((ROOT / "tools/wiki_catalog/zh_cn").glob("*/m1_s*.json"))]
    id_to_name: dict[str, str] = {}
    op_names: set[str] = set()
    for f in catalogs:
        if not f.is_file():
            continue
        payload = _load_json(f)
        for catalog in payload.get("data", {}).get("catalog", []):
            for sub in catalog.get("typeSub", []):
                for it in sub.get("items") or []:
                    if it.get("itemId"):
                        id_to_name.setdefault(str(it["itemId"]), str(it.get("name") or "").strip())
                        if str(sub.get("id")) == "1":
                            op_names.add(str(it["itemId"]))

    for filename in ("matrices.json", "equipments.json"):
        source = DATA_DIR / filename
        if source.is_file():
            for item_name, item in _load_json(source).items():
                if item.get("item_id"):
                    id_to_name.setdefault(str(item["item_id"]), item_name)
    weapon_id_to_name = {w["item_id"]: n for n, w in weapons.items()}

    # 干员详情 → 武器推荐（干员侧）
    op_rec_weapons: dict[str, list[str]] = {}
    for f in sorted(snap_dir.glob("details/*.json")):
        payload = _load_json(f)
        item = payload["data"]["item"]
        op_id = str(item.get("itemId"))
        name = _normalize_name(id_to_name.get(op_id) or f.stem.split("_", 1)[-1])
        ids = _extract_weapon_recs(payload)
        names = [weapon_id_to_name.get(i, id_to_name.get(i, f"?{i}")) for i in ids]
        recs = op_rec_weapons.setdefault(name, [])
        recs.extend(weapon for weapon in names if weapon not in recs)

    # 武器页 → 推荐干员（武器侧，反向互证）
    weapon_rec_ops: dict[str, list[str]] = {}
    for wname, w in weapons.items():
        for oid in w.get("recommended_operator_ids") or []:
            weapon_rec_ops.setdefault(_normalize_name(id_to_name.get(oid, oid)), []).append(wname)

    # 装备页 → 推荐干员（装备侧官方链，反向反查干员的官方推荐装备件）
    equip_rec_pieces: dict[str, list[str]] = {}
    for ename, e in equipments.items():
        for oid in e.get("recommended_operator_ids") or []:
            op_name = _normalize_name(id_to_name.get(str(oid), str(oid)))
            equip_rec_pieces.setdefault(op_name, []).append(ename)

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    report = []
    for path in sorted(CHAR_SKILLS_DIR.glob("*.json")):
        char = _load_json(path)
        name = _normalize_name(str(char.get("name") or path.stem))
        element = str(char.get("element") or "")
        weapon = None
        evidence_note = ""

        recs = op_rec_weapons.get(name) or []
        recs = [r for r in recs if r in weapons]
        if recs:
            weapon = recs[0]
            cross = weapon in weapon_rec_ops.get(name, [])
            evidence_note = (
                f"官方游戏内武器推荐（森空岛WIKI 干员页），武器页反向互证={'一致' if cross else '未列出（备选）'}"
            )
            evidence_level = 1
        else:
            evidence_note = "未找到官方武器推荐"
            evidence_level = 3

        # 套组/装备件：官方装备页推荐链 → 社区攻略 → 元素模板
        set_main = set_off = None
        set_level = 3
        set_source = ""
        official_pieces: list[str] = []
        equip_note = ""
        if name in equip_rec_pieces:
            parts_order = {"护甲": 0, "护手": 1, "配件": 2}
            official_pieces = sorted(
                set(equip_rec_pieces[name]),
                key=lambda p: (parts_order.get(equipments[p].get("part"), 9), p),
            )[:4]
            set_counts = Counter(equipments[p].get("set") for p in official_pieces)
            top_set, top_n = set_counts.most_common(1)[0]
            if top_n >= 3:
                set_main = top_set
            set_level = 1
            set_source = "官方 wiki 装备页推荐（森空岛WIKI 装备页推荐卡片反查）"
            if len(official_pieces) < 4:
                equip_note = f"官方仅推荐 {len(official_pieces)} 件，其余槽位为自由散件"
        if not official_pieces:
            if name in COMMUNITY_SETS:
                m, o = COMMUNITY_SETS[name]
                set_main, set_off = SET_ALIASES[m], SET_ALIASES[o]
                set_level = 2
                set_source = "新浪全干员装备适配一图流（毕业列）"
            elif element in ELEMENT_DEFAULT_SETS:
                m, o = ELEMENT_DEFAULT_SETS[element]
                set_main, set_off = SET_ALIASES[m], SET_ALIASES[o]
                set_source = f"启发式默认（元素 {element} 模板，待专属攻略验证）"

        # 精确四件套（仅官方链未覆盖时社区有）→ 否则按套组在库内选件（部位默认取第一件，后续可换）
        exact = EXACT_PIECES.get(name) if not official_pieces else None
        pieces = None
        if official_pieces:
            pieces = official_pieces
        elif exact:
            pieces = exact
        elif set_main:
            eq = equipments
            by_part: dict[str, list[str]] = {}
            for ename, e in eq.items():
                if e.get("set") == set_main and e.get("quality") == "金色品质":
                    by_part.setdefault(e["part"], []).append(ename)
            main_parts = []
            for part in ("护甲", "护手", "配件"):
                candidates = sorted(by_part.get(part) or [])
                main_parts.append(candidates[0] if candidates else None)
            off_eq = [
                ename
                for ename, e in eq.items()
                if e.get("set") == set_off and e.get("part") == "配件" and e.get("quality") == "金色品质"
            ]
            pieces = [main_parts[0], main_parts[1], main_parts[2], sorted(off_eq)[0] if off_eq else None]

        matrix = None
        if weapon and weapons[weapon].get("recommended_matrix_ids"):
            mid = weapons[weapon]["recommended_matrix_ids"][0]
            matrix = id_to_name.get(mid, mid)

        build = {
            "character": name,
            "character_key": path.stem,
            "element": element,
            "stars": char.get("stars"),
            "weapon": {
                "name": weapon,
                "level_max": 90,
                "skill_rank": 9,
                "source": "官方游戏内武器推荐（森空岛WIKI）",
                "evidence_level": evidence_level if weapon else 3,
                "note": evidence_note,
            },
            "matrix": {
                "name": matrix,
                "source": "官方武器页「推荐武器基质」" if matrix else "未找到官方基质推荐",
                "evidence_level": 1 if matrix else 3,
                "note": "基质词条=武器技能等级提升（wiki.gg），面板取武器技能 Rank9 满级值",
            },
            "equipment": {
                "set_main": set_main,
                "set_off": set_off,
                "pieces": pieces,
                "level_max": 70,
                "refinement_max": 3,
                "source": set_source,
                "evidence_level": set_level,
                **({"note": equip_note} if equip_note else {}),
            },
        }
        out = BUILD_DIR / f"{path.stem}.json"
        text = json.dumps(build, ensure_ascii=False, indent=2) + "\n"
        out.write_bytes(text.encode("utf-8").replace(b"\r\n", b"\n"))
        report.append((name, weapon, matrix, set_main, set_off, set_level, evidence_level))

    print(f"{'角色':<8} {'武器':<10} {'基质':<14} {'主套组':<10} {'_off':<10} 证据(套/武)")
    for name, weapon, matrix, sm, so, sl, el in report:
        print(f"{name:<8} {weapon or '—':<10} {matrix or '—':<14} {sm or '—':<10} {so or '—':<10} {sl}/{el}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
