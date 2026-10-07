"""生成角色毕业配置文件（第一阶段：静态伤害基准数据库）。

证据链（evidence_level 越小越权威）：
1 = 官方游戏内推荐（森空岛 WIKI 干员页「武器推荐」/ 武器页「推荐装备干员」互证
    / 装备页「推荐装备干员」完整四槽卡；旧导出回退 recommended_operator_ids 反查）
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
from src.data.wiki_snapshots import resolve_operator_snapshot  # noqa: E402

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

# 2026-10-07 定稿四件（部位顺序：护甲/护手/配件/配件，同名配件可装两件）。
# 值结构：(四件配装, 来源, 说明, evidence_level)。
# 固定基准优先于自动抓取的官方推荐与旧反查；官方完整四槽卡仍保留在 equipments.json 供审计/回退。
# 社区统计取自 EndSync 汇总页 https://endsync.vercel.app/zh/wiki/<slug>/ ，
# 「A/B」= 该套装出现在 B 套配装中的 A 套，「同四件 C」= 完全相同四件组合出现次数。
ENDSYNC = "EndSync 社区汇总"
CURATED_BUILDS = {
    "安塔尔": (["落潮轻甲", "长息护手", "长息蓄电核", "长息蓄电核"], ENDSYNC, "长息 5/6，同四件 3/6", 2),
    "诀": (["动火用辅助骨骼", "动火用护手", "动火用隔温板", "生物辅助护板"], ENDSYNC, "动火用 12/22，同四件 7/22；与官方一致", 2),
    "弧光": (["拓荒护甲", "拓荒纤维手套·壹型", "拓荒通信器", "拓荒供氧栓"], ENDSYNC, "拓荒 7/8，同四件 2/8", 2),
    "艾尔黛拉": (["碾骨披巾·壹型", "长息护手·壹型", "长息辅助臂", "长息辅助臂"], ENDSYNC, "长息 3/5，同四件 2/5", 2),
    "艾维文娜": (["碾骨披巾·壹型", "碾骨腕带·壹型", "碾骨小雕像", "碾骨小雕像"], ENDSYNC, "碾骨 3/3，同四件 2/3", 2),
    "卡缪": (["拓荒护服", "拓荒纤维手套·壹型", "拓荒通信器", "纾难印章"], ENDSYNC, "拓荒 6/8，同四件 3/8", 2),
    "陈千语": (["点剑重装甲", "点剑战术手甲", "点剑短刃", "点剑短刃"], ENDSYNC, "点剑 4/7，同四件 3/7", 2),
    "萤石": (["50式应龙重甲·壹型", "50式应龙护手", "纾难印章", "50式应龙雷达·贰型"], ENDSYNC, "50式应龙 2/3，同四件 2/3", 2),
    "洁尔佩塔": (["长息装甲", "长息护手·壹型", "长息辅助臂", "长息辅助臂"], ENDSYNC, "长息 14/16，同四件 8/16", 2),
    "莱万汀": (["落潮轻甲", "动火用手甲", "动火用测温镜", "动火用测温镜"], ENDSYNC, "动火用 3/3，同四件 2/3", 2),
    "梨诺": (["长息轻护甲", "长息手套", "长息辅助臂", "生物辅助护板"], ENDSYNC, "长息 5/9，同四件 4/9；与官方一致", 2),
    "弭弗": (["旧锋装甲", "旧锋手甲", "旧锋刺刃", "旧锋刺刃"], ENDSYNC, "旧锋 11/11，同四件 4/11", 2),
    "佩丽卡": (["脉冲式干扰服", "长息护手·壹型", "脉冲式校准器", "脉冲式校准器"], ENDSYNC, "脉冲式 15/26，同四件 10/26", 2),
    "骏卫": (["拓荒护服", "拓荒纤维手套·壹型", "拓荒供氧栓", "动火用电力匣"], ENDSYNC, "拓荒 5/9，同四件 3/9", 2),
    "噗切娜": (["长息轻护甲", "长息手套", "长息加固板", "长息加固板"], ENDSYNC, "长息 5/5，同四件 4/5", 2),
    "洛茜": (["M.I.警用护甲·壹型", "纾难护手", "M.I.警用瞄具·壹型", "M.I.警用瞄具·壹型"], ENDSYNC, "M.I.警用 7/7，同四件 5/7", 2),
    "汤汤": (["清波重甲", "清波手甲", "清波水罐", "清波定位仪"], ENDSYNC, "清波 3/5，同四件 2/5", 2),
    "狼卫": (["清波重甲", "清波护手", "清波竹刃", "纾难印章"], ENDSYNC, "清波 4/5，同四件 3/5", 2),
    "赛希": (["长息轻护甲·壹型", "涉渊护手", "长息加固板", "长息加固板"], ENDSYNC, "长息 8/15，同四件 2/15", 2),
    "伊冯": (["M.I.警用罩衣", "M.I.警用手环", "M.I.警用工具组", "M.I.警用工具组"], ENDSYNC, "M.I.警用 8/8，同四件 3/8", 2),
    "庄方宜": (["壤流轻甲", "壤流护手", "壤流短棍", "生物辅助护板"], ENDSYNC, "壤流 8/8，同四件 5/8", 2),
    "提弗洛斯": (["险关装甲", "险关手甲", "险关通信器", "险关通信器"], ENDSYNC, "险关 9/9，同四件 4/9", 2),
    # 社区样本不足（单套或仅精选），由两个以上独立来源的共同核心件确定。
    "卡契尔": (["长息装甲", "长息护手·壹型", "长息辅助臂", "长息辅助臂"], ENDSYNC + " + GameKee",
             "EndSync 长息 1/1；GameKee 同为长息护手·壹型 + 长息辅助臂×2", 2),
    "埃特拉": (["点剑轻装甲", "长息护手·壹型", "长息辅助臂", "长息辅助臂"], ENDSYNC + " + TapTap",
             "EndSync 长息 1/1；TapTap 一图流为 3 长息 + 1 散件", 2),
    "别礼": (["碾骨披巾", "潮涌手甲", "悬河供氧栓", "悬河供氧栓"], ENDSYNC + " + GameKee",
           "EndSync 潮涌 1/1；GameKee 各档均为潮涌手甲 + 悬河供氧栓×2", 2),
    "昼雪": (["碾骨披巾·壹型", "长息护手·壹型", "长息辅助臂", "长息辅助臂"], ENDSYNC + " + mrfzzmd",
           "EndSync 精选；mrfzzmd 同为长息护手 + 长息辅助臂×2", 2),
    "阿列什": (["拓荒护甲", "拓荒纤维手套·壹型", "拓荒通信器·壹型", "纾难印章"], "官方装备页推荐 + " + ENDSYNC,
             "保留官方拓荒护甲、拓荒通信器·壹型；护手与纾难印章取 EndSync 精选（3件 拓荒）", 1),
    "大潘": (["50式应龙重甲", "点剑战术手套", "50式应龙雷达", "50式应龙雷达"], "官方装备页推荐 + 111cn/ldcapple",
           "官方三件补成应龙雷达×2，与两份社区攻略一致", 1),
    "余烬": (["生物辅助重甲", "生物辅助臂甲", "生物辅助接驳器·壹型", "生物辅助接驳器·壹型"], "官方装备页推荐 + ldcapple",
           "官方三件补成接驳器·壹型×2，与 ldcapple 一致；EndSync 仅精选", 1),
    "管理员": (["点剑重装甲", "点剑战术手甲", "点剑火石", "点剑火石"], "官方装备页推荐",
             "EndSync 仅 1 套；官方三件补成点剑火石×2", 1),
    "黎风": (["M.I.警用护甲", "轻超域护手", "轻超域稳定盘", "轻超域稳定盘·壹型"], "官方装备页推荐",
           "EndSync 2 套分歧（长息/点剑）；按用户确认保留官方轻超域并补稳定盘·壹型", 1),
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

_OFFICIAL_LOADOUT_KEYS = ("armor_id", "gloves_id", "accessory_1_id", "accessory_2_id")


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
                    (el.get("text") or {}).get("text") or "" for el in (b.get("text") or {}).get("inlineElements") or []
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


def select_official_pieces(names: list[str], equipments: dict) -> list[str | None]:
    quotas = {"护甲": 1, "护手": 1, "配件": 2}
    names = sorted(set(names))
    sets = {equipments[name].get("set") for name in names if equipments[name].get("set")}

    def available_slots(set_name):
        return sum(
            min(
                limit,
                sum(equipments[name].get("part") == part and equipments[name].get("set") == set_name for name in names),
            )
            for part, limit in quotas.items()
        )

    preferred = min(sets, key=lambda name: (-available_slots(name), name)) if sets else None
    pieces = []
    for part, limit in quotas.items():
        candidates = sorted(
            (name for name in names if equipments[name].get("part") == part),
            key=lambda name: (equipments[name].get("set") != preferred, name),
        )
        pieces.extend(candidates[:limit] + [None] * max(0, limit - len(candidates)))
    return pieces


def collect_official_loadouts(equipments: dict, id_to_name: dict[str, str]) -> dict[str, list[str]]:
    """从任意装备页的完整推荐卡取得首张槽位有效的干员四槽配装。"""
    result: dict[str, list[str]] = {}
    for equipment in equipments.values():
        for loadout in equipment.get("recommended_loadouts") or []:
            operator_id = str(loadout.get("operator_id") or "")
            if not operator_id:
                continue
            op_name = _normalize_name(id_to_name.get(operator_id, operator_id))
            if op_name in result:
                continue
            piece_ids = [str(loadout.get(key) or "") for key in _OFFICIAL_LOADOUT_KEYS]
            if not all(piece_ids):
                continue
            pieces = [id_to_name.get(piece_id) for piece_id in piece_ids]
            if not all(pieces):
                continue
            parts = [equipments.get(str(piece), {}).get("part") for piece in pieces]
            if parts != ["护甲", "护手", "配件", "配件"]:
                continue
            result[op_name] = [str(piece) for piece in pieces]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", help="operator_details 快照目录名（默认 latest.json 指向的完整快照）")
    parser.add_argument("--allow-partial", action="store_true", help="显式允许不完整快照")
    args = parser.parse_args()
    try:
        snap_dir = resolve_operator_snapshot(SNAP_ROOT, args.snapshot, allow_partial=args.allow_partial)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"无法使用干员快照: {exc}", file=sys.stderr)
        return 1
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

    # 装备页 → 官方完整四槽卡；同一干员第一张槽位有效卡命中后不再从其他装备页重复构建。
    equip_rec_loadouts = collect_official_loadouts(equipments, id_to_name)

    # 兼容旧 equipments.json：只有 recommended_operator_ids 时继续使用反向拼件作为回退。
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

        # 套组/装备件：固定定稿 → 官方完整四槽卡 → 旧反查 → 社区攻略 → 元素模板
        set_main = set_off = None
        set_level = 3
        set_source = ""
        official_pieces: list[str | None] = []
        equip_note = ""
        if name in CURATED_BUILDS:
            curated = CURATED_BUILDS[name]
            if len(curated) == 3:
                official_pieces, set_source, equip_note = curated
                set_level = 2
            else:
                official_pieces, set_source, equip_note, set_level = curated
            official_pieces = list(official_pieces)
            missing = [p for p in official_pieces if p not in equipments]
            if missing:
                raise SystemExit(f"{name} 定稿配装含未知装备: {missing}")
            parts = [equipments[p].get("part") for p in official_pieces]
            if parts != ["护甲", "护手", "配件", "配件"]:
                raise SystemExit(f"{name} 定稿配装部位必须为 护甲/护手/配件/配件: {parts}")
            set_counts = Counter(equipments[p].get("set") for p in official_pieces if equipments[p].get("set"))
            top_set, top_n = set_counts.most_common(1)[0] if set_counts else (None, 0)
            if top_n < 3:
                raise SystemExit(f"{name} 定稿配装没有 3 件同套装: {dict(set_counts)}")
            set_main = top_set
        elif name in equip_rec_loadouts:
            official_pieces = list(equip_rec_loadouts[name])
            missing = [p for p in official_pieces if p not in equipments]
            if missing:
                raise SystemExit(f"{name} 官方四槽配装含未知装备: {missing}")
            parts = [equipments[p].get("part") for p in official_pieces]
            if parts != ["护甲", "护手", "配件", "配件"]:
                raise SystemExit(f"{name} 官方四槽配装部位异常: {parts}")
            set_counts = Counter(equipments[p].get("set") for p in official_pieces if equipments[p].get("set"))
            top_set, top_n = set_counts.most_common(1)[0] if set_counts else (None, 0)
            if top_set and top_n >= 3:
                set_main = top_set
            set_level = 1
            set_source = "官方 wiki 装备页「推荐装备干员」完整四槽卡"
            equip_note = "按官方卡片槽位原样读取（护甲/护手/配件Ⅰ/配件Ⅱ），保留同名双配件"
        elif name in equip_rec_pieces:
            official_pieces = select_official_pieces(equip_rec_pieces[name], equipments)
            set_counts = Counter(equipments[p].get("set") for p in official_pieces if p)
            top_set, top_n = set_counts.most_common(1)[0] if set_counts else (None, 0)
            if top_set and top_n >= 3:
                set_main = top_set
            set_level = 1
            set_source = "官方 wiki 装备页推荐（旧 recommended_operator_ids 反查回退）"
            count = sum(piece is not None for piece in official_pieces)
            if count < 4:
                equip_note = f"旧导出仅反查到 {count} 件不同装备，无法表达重复配件；请重建 equipments.json"
            if not count:
                official_pieces = []
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