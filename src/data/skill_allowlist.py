"""自动技能列表：根据队伍 4 角色的增强链依赖，自动判定哪些战技有意义释放。

使用方式（战斗配置中启用"自动技能列表"后）：
    frame = task.next_frame()           # 战斗中截帧
    members = detect_team_from_frame(frame)  # battle_icon 模板匹配自动识别
    # → ["别礼", "伊冯", "洁尔佩塔", "余烬"]
    seq = ["1", "2", "3"]
    filtered = filter_skill_sequence(members, seq)
    # → ["1", "3"]  （2号位伊冯、4号位余烬的战技被禁止）

参见 tools/team_composer.py --allowlist 查看命令行版本。
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

# ── 路径 ──────────────────────────────────────────────────────────────────────

_ROOT = Path(__file__).resolve().parent.parent.parent
_DATA_DIR = _ROOT / "assets" / "data" / "character_skills"
_CHARACTERS_JSON = _ROOT / "assets" / "data" / "characters.json"
_ASSETS_DIR = _ROOT / "assets"
_COCO_JSON = _ASSETS_DIR / "coco_annotations.json"

_SHRED_STACK_PRODUCERS = {
    "STATUS_SHRED",
    "STATUS_HEAVY_HIT",
    "STATUS_KNOCKDOWN",
}

# ── 数据加载 ──────────────────────────────────────────────────────────────────

_cached_characters: dict[str, dict] | None = None


def load_characters(data_dir: Path | None = None) -> dict[str, dict]:
    """加载所有 character_skills/*.json，返回 {name: skill_dict} 缓存。"""
    global _cached_characters
    if _cached_characters is not None:
        return _cached_characters

    d = data_dir or _DATA_DIR
    result: dict[str, dict] = {}
    if not d.exists():
        return result
    for fp in sorted(d.glob("*.json")):
        with open(fp, encoding="utf-8") as f:
            data = json.load(f)
        name = data.get("name", fp.stem)
        result[name] = data
    _cached_characters = result
    return result


def clear_cache():
    """清除缓存（测试用）。"""
    global _cached_characters
    _cached_characters = None


# ── 核心逻辑：两阶段构建允许列表 ────────────────────────────────────────────


def _effect_id(effect) -> str:
    if isinstance(effect, str):
        return effect
    if isinstance(effect, dict):
        return effect.get("effect_id", "")
    return ""


def _produced_effect_id(effect) -> str:
    """只把实际施加/增加的效果计为 producer。"""
    effect_id = _effect_id(effect)
    if not effect_id:
        return ""
    if isinstance(effect, dict):
        count = effect.get("count", 1)
        if count is not None and count <= 0:
            return ""
    return effect_id


def _static_release_gate(enhancement: dict) -> dict | None:
    """读取显式静态 release gate；动态阈值和非 gate 分支返回 None。"""
    gate = enhancement.get("release_gate")
    if not isinstance(gate, dict) or gate.get("static") is False:
        return None
    for operator in ("all", "any"):
        if operator not in gate:
            continue
        requires = [effect_id for effect in gate.get(operator) or [] if (effect_id := _effect_id(effect))]
        if requires:
            return {"operator": operator, "requires": requires}
    return None


def _branch_can_trigger(branch: dict, producers: dict, skill_key: tuple[str, str]) -> bool:
    """判断一个静态 gate 分支是否能由其它队内技能满足。"""
    checks = [
        any(
            (producer_name, producer_skill_id) != skill_key
            for producer_name, producer_skill_id, _ in producers.get(effect_id, [])
        )
        for effect_id in branch["requires"]
    ]
    return all(checks) if branch["operator"] == "all" else any(checks)


def _build_team_skill_context(
    team_members: list[str],
    characters: dict[str, dict],
) -> dict:
    """第一阶段：扫描队伍所有技能，建立依赖关系图。

    Returns:
        {
            "producers": {effect_id: [(char_name, skill_id, skill_type), ...]},
            "release_gates": {(char_name, skill_id): [gate_branch, ...]},
        }
    """
    team_set = set(team_members)

    # 收集队内所有技能
    all_skills: dict[tuple[str, str], dict] = {}
    for name, cdata in characters.items():
        if name not in team_set:
            continue
        for s in cdata.get("skills", []):
            all_skills[(name, s.get("skill_id", ""))] = s

    # ── producers: 哪些技能在施放后产出什么状态 ──
    producers: dict[str, list[tuple[str, str, str]]] = {}
    for (sname, sid), sdata in all_skills.items():
        producer = (sname, sid, sdata.get("skill_type", ""))
        for eff in sdata.get("effects") or []:
            effect_id = _produced_effect_id(eff)
            if not effect_id:
                continue
            effect_producers = producers.setdefault(effect_id, [])
            if producer not in effect_producers:
                effect_producers.append(producer)
            if effect_id in _SHRED_STACK_PRODUCERS:
                shred_producers = producers.setdefault("STACK_SHRED", [])
                if producer not in shred_producers:
                    shred_producers.append(producer)

    # ── release gates: 每个 enhancement 保持独立分支，分支间按 OR 判定 ──
    release_gates: dict[tuple[str, str], list[dict]] = {}
    for (sname, sid), sdata in all_skills.items():
        if sdata.get("skill_type") != "战技":
            continue
        skill_enhancements = sdata.get("enhancements") or []
        if not skill_enhancements and sdata.get("enhancement"):
            skill_enhancements = [sdata["enhancement"]]
        if not skill_enhancements:
            continue

        branches = []
        for enh in skill_enhancements:
            if gate := _static_release_gate(enh):
                branches.append(gate)
        if branches:
            release_gates[(sname, sid)] = branches

    return {"producers": producers, "release_gates": release_gates}


def build_skill_allowlist(
    team_members: list[str],
    characters: dict[str, dict] | None = None,
) -> dict[int, tuple[bool, str]]:
    """判断队伍中每个角色的战技是否允许手动释放。

    两阶段架构：
      1. 建立队伍技能依赖图（producers / release gates）
      2. 逐个角色判定战技是否应被增强态接管

    Args:
        team_members: 4 个角色名，索引 0-3 对应技能键 "1"-"4"
        characters:   角色数据字典（可选，默认加载）

    Returns:
        {位置索引: (是否允许, 阻止原因)}
    """
    if characters is None:
        characters = load_characters()

    if not characters:
        return {i: (True, "") for i in range(len(team_members))}

    # ── 第一阶段：建立队伍技能依赖图 ──
    ctx = _build_team_skill_context(team_members, characters)

    # ── 第二阶段：逐个判定战技 ──
    result: dict[int, tuple[bool, str]] = {}

    for idx, char_name in enumerate(team_members):
        char_data = characters.get(char_name)
        if not char_data:
            result[idx] = (True, "")
            continue

        # 找到该角色的战技
        skill = None
        for s in char_data.get("skills", []):
            if s.get("skill_type") == "战技":
                skill = s
                break
        if not skill:
            result[idx] = (True, "")
            continue

        key = (char_name, skill.get("skill_id", ""))
        release_gates = ctx["release_gates"].get(key)
        if not release_gates:
            result[idx] = (True, "")
            continue

        if any(_branch_can_trigger(branch, ctx["producers"], key) for branch in release_gates):
            result[idx] = (False, "增强态优先")
        else:
            result[idx] = (True, "")

    return result


# ── 生成技能释放序列 ────────────────────────────────────────────────

# 普通战技 token 集合（不含 ult_/e/sleep_/normal_）
_NORMAL_SKILL_TOKENS = {"1", "2", "3", "4"}


def generate_skill_sequence(
    team_members: list[str],
    characters: dict[str, dict] | None = None,
) -> list[str]:
    """根据队伍编队直接生成允许的战技释放序列（生成式，不依赖用户配置）。

    自动构建 allowlist，仅返回被允许的数字战技 token，按索引升序排列。

    Args:
        team_members: 4 个角色名，索引 i 对应技能键 "i+1"
        characters:   角色数据（可选）

    Returns:
        生成的战技释放序列，如 ["1", "3"]
    """
    allowlist = build_skill_allowlist(team_members, characters)
    result = [str(i + 1) for i, (ok, _) in allowlist.items() if ok]
    return result if result else [str(i + 1) for i in range(len(team_members))]


def filter_skill_sequence(
    team_members: list[str],
    skill_sequence: list[str],
    characters: dict[str, dict] | None = None,
) -> list[str]:
    """根据自动技能列表过滤技能释放序列。

    保留原始序列中的非数字 token（ult_/e/sleep_/normal_ 等），
    仅对数字战技 token（1-4）根据允许列表进行过滤。

    Args:
        team_members:   4 个角色名，索引 i 对应技能键 "i+1"
        skill_sequence: 原始技能序列
        characters:     角色数据（可选）

    Returns:
        过滤后的技能释放序列
    """
    allowlist = build_skill_allowlist(team_members, characters)
    allowed_digits = {
        str(i + 1) for i, (ok, _) in allowlist.items() if ok
    }

    filtered = []
    for token in skill_sequence:
        if token in _NORMAL_SKILL_TOKENS:
            # 数字战技 token：仅保留允许列表中的
            if token in allowed_digits:
                filtered.append(token)
        else:
            # 非数字 token（ult_/e/sleep_/normal_ 等）：保留
            filtered.append(token)

    return filtered if filtered else list(skill_sequence)


# ── 编队头像自动识别 ────────────────────────────────────────────────────────
# 复用 TeamCompositionDetectTask 的 battle_icon 模板匹配逻辑

# 左下角4个头像固定区域（归一化坐标，基于 1920x1080）
_PORTRAIT_ROIS = [
    (40 / 1920, 927 / 1080),
    (156 / 1920, 927 / 1080),
    (273 / 1920, 927 / 1080),
    (390 / 1920, 927 / 1080),
]
_PORTRAIT_SIZE = (54 / 1920, 46 / 1080)
_MATCH_SCALES = [0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2]
_MIN_MATCH_SCORE = 0.45

# 模板别名：变体 annotation 名 → 主模板名
# COCO annotations 中变体名保持唯一，代码侧在此声明同角色归并关系。
_TEMPLATE_ALIASES: dict[str, str] = {
    "battle_icon_endministrator_female": "battle_icon_endministrator",
    "battle_icon_endministrator_male": "battle_icon_endministrator",
}

# 缓存
_char_name_map: dict[str, str] | None = None   # en → zh
_battle_icons: dict[str, list[np.ndarray]] | None = None


def _load_char_name_map() -> dict[str, str]:
    """加载 characters.json，返回 en→zh 映射（如 ember→余烬）。"""
    global _char_name_map
    if _char_name_map is not None:
        return _char_name_map
    if not _CHARACTERS_JSON.exists():
        _char_name_map = {}
        return _char_name_map
    with open(_CHARACTERS_JSON, encoding="utf-8") as f:
        data = json.load(f)
    _char_name_map = {}
    for info in data.values():
        en = info.get("en", "")
        zh = info.get("zh", "")
        if en and zh:
            _char_name_map[en] = zh
    return _char_name_map


def _load_battle_icons() -> dict[str, list[np.ndarray]]:
    """从 coco_annotations.json 加载 battle_icon 模板（带缓存）。

    每条 annotation 使用独立的 category name（不重复）。
    加载后按 ``_TEMPLATE_ALIASES`` 将变体名归并到主模板名下，
    返回 ``dict[str, list[np.ndarray]]``。
    """
    global _battle_icons
    if _battle_icons is not None:
        return _battle_icons
    if not _COCO_JSON.exists():
        _battle_icons = {}
        return _battle_icons
    with open(_COCO_JSON, encoding="utf-8") as f:
        data = json.load(f)
    cat_map = {c["id"]: c["name"] for c in data["categories"]}
    img_map = {i["id"]: i for i in data["images"]}
    icons: dict[str, list[np.ndarray]] = {}
    for ann in data["annotations"]:
        name = cat_map.get(ann["category_id"], "")
        if not name.startswith("battle_icon_"):
            continue
        img = img_map.get(ann["image_id"])
        if img is None:
            continue
        path = _ASSETS_DIR / img["file_name"]
        if not path.exists():
            continue
        shot = cv2.imread(str(path))
        if shot is None:
            continue
        x, y, w, h = [int(v) for v in ann["bbox"]]
        icons.setdefault(name, []).append(shot[y:y + h, x:x + w].copy())
    # 按别名表归并变体模板到主模板名
    for variant, primary in _TEMPLATE_ALIASES.items():
        if variant in icons:
            icons.setdefault(primary, []).extend(icons.pop(variant))
    _battle_icons = icons
    return icons


def _hist_sim(img1: np.ndarray, img2: np.ndarray) -> float:
    """HSV 直方图相关性。"""
    h1 = cv2.calcHist([cv2.cvtColor(img1, cv2.COLOR_BGR2HSV)], [0, 1], None, [30, 32], [0, 180, 0, 256])
    h2 = cv2.calcHist([cv2.cvtColor(img2, cv2.COLOR_BGR2HSV)], [0, 1], None, [30, 32], [0, 180, 0, 256])
    cv2.normalize(h1, h1)
    cv2.normalize(h2, h2)
    return cv2.compareHist(h1, h2, cv2.HISTCMP_CORREL)


def _best_match_portrait(portrait: np.ndarray, templates: list[np.ndarray]) -> float:
    """对多张模板取最佳匹配分数（同角色不同外观）。"""
    return max((_match_portrait(portrait, t) for t in templates), default=-1.0)


def _phash(img: np.ndarray, size: int = 16) -> np.ndarray:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (size, size))
    return small > small.mean()


def _match_portrait(portrait: np.ndarray, template: np.ndarray) -> float:
    """多尺度模板匹配 + HSV 直方图 + pHash 综合评分。"""
    t_h, t_w = template.shape[:2]
    p_h, p_w = portrait.shape[:2]
    t_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    p_gray = cv2.cvtColor(portrait, cv2.COLOR_BGR2GRAY)
    best = -1.0
    best_pos = None
    best_scale = 1.0
    for scale in _MATCH_SCALES:
        tw = max(10, int(p_w * scale))
        th = max(10, int(p_h * scale))
        if tw > t_w or th > t_h:
            continue
        tmpl = cv2.resize(p_gray, (tw, th))
        result = cv2.matchTemplate(t_gray, tmpl, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        if max_val > best:
            best = max_val
            best_scale = scale
            best_pos = max_loc
    hist = 0.0
    phash_sim = 0.0
    if best_pos is not None:
        tw = int(p_w * best_scale)
        th = int(p_h * best_scale)
        region = template[best_pos[1]:best_pos[1] + th, best_pos[0]:best_pos[0] + tw]
        region = cv2.resize(region, (p_w, p_h))
        hist = _hist_sim(portrait, region)
        p1 = _phash(portrait)
        p2 = _phash(region)
        phash_sim = 1.0 - np.count_nonzero(p1 != p2) / p1.size
    return (best + hist + phash_sim) / 3


def detect_team_from_frame(frame: np.ndarray) -> list[str]:
    """从战斗帧的左下角头像识别当前队伍角色名（中文）。

    自动兼容少人情况：识别完成后去掉尾部未识别的 "?" 位，
    因此 3 人队返回 3 个、2 人队返回 2 个。

    Args:
        frame: BGR 格式 numpy 数组（由 task.next_frame() 获取）

    Returns:
        角色名列表（按位置顺序），识别失败的位置用 "?" 代替。
        例: ["别礼", "伊冯", "洁尔佩塔", "余烬"]
    """
    icons = _load_battle_icons()
    name_map = _load_char_name_map()

    if not icons:
        return ["?"] * 4

    height, width = frame.shape[:2]
    pw = int(_PORTRAIT_SIZE[0] * width)
    ph = int(_PORTRAIT_SIZE[1] * height)

    team: list[str] = []
    for rx, ry in _PORTRAIT_ROIS:
        x1, y1 = int(rx * width), int(ry * height)
        portrait = frame[y1:y1 + ph, x1:x1 + pw]
        if portrait.size == 0:
            team.append("?")
            continue

        best_name: str | None = None
        best_score = -1.0
        for label, templates in icons.items():
            score = _best_match_portrait(portrait, templates)
            if score > best_score:
                best_score = score
                best_name = label

        if best_name and best_score >= _MIN_MATCH_SCORE:
            # battle_icon_ember → ember → 余烬
            en_key = best_name.replace("battle_icon_", "")
            zh_name = name_map.get(en_key, en_key)
            team.append(zh_name)
        else:
            team.append("?")

    # 兼容少人：去掉尾部 "?" 位
    while team and team[-1] == "?":
        team.pop()
    return team or ["?"] * 4


def detect_team_stable(
    frame_getter,
    task=None,
    max_attempts: int = 6,
    interval: float = 0.2,
    confidence: int = 2,
    deadline: float | None = None,
) -> tuple[list[str], bool]:
    """多帧稳定识别：连续 confidence 次识别出相同队伍才视为稳定。

    用于战斗开始前的稳定检测，避免单帧误识别。
    优先使用 task.sleep（兼容任务框架），无 task 时回退 time.sleep。

    Args:
        frame_getter:  无参 callable，返回 BGR numpy 帧（如 task.next_frame）
        task:          任务实例（可选，用于 task.sleep / task.active_time）
        max_attempts:  最大采样次数
        interval:      每次采样间隔秒数
        confidence:    连续多少次相同结果视为稳定
        deadline:      可选截止时间戳（同 task.active_time() 单位），超时立即停止

    Returns:
        (team, stable) 二元组：
        - team:   角色名列表；无有效结果时返回 ["?"]
        - stable: 是否达到 confidence 连续相同（True=稳定，False=超时/未达条件）
    """
    import time

    _sleep = getattr(task, 'sleep', None) or time.sleep
    _now = getattr(task, 'active_time', None) or time.time

    last_result: list[str] = []
    streak = 0

    for i in range(max_attempts):
        if deadline is not None and _now() >= deadline:
            break

        frame = frame_getter()
        if frame is None or (hasattr(frame, 'size') and frame.size == 0):
            if deadline is None:
                _sleep(interval)
            else:
                remaining = deadline - _now()
                if remaining <= 0:
                    break
                _sleep(min(interval, remaining))
            continue

        current = detect_team_from_frame(frame)
        if current == last_result:
            streak += 1
            if streak >= confidence:
                return (current, True)
        else:
            last_result = current
            streak = 1

        if i < max_attempts - 1:
            if deadline is None:
                _sleep(interval)
            else:
                remaining = deadline - _now()
                if remaining <= 0:
                    break
                _sleep(min(interval, remaining))

    return (last_result or ["?"], False)


def detect_team_from_frame_with_scores(frame: np.ndarray) -> list[tuple[str, float]]:
    """同 detect_team_from_frame，但同时返回匹配备分数（调试用）。

    Returns:
        [(角色名, 分数), ...]
    """
    icons = _load_battle_icons()
    name_map = _load_char_name_map()

    if not icons:
        return [("-", 0.0)] * 4

    height, width = frame.shape[:2]
    pw = int(_PORTRAIT_SIZE[0] * width)
    ph = int(_PORTRAIT_SIZE[1] * height)

    team: list[tuple[str, float]] = []
    for rx, ry in _PORTRAIT_ROIS:
        x1, y1 = int(rx * width), int(ry * height)
        portrait = frame[y1:y1 + ph, x1:x1 + pw]
        if portrait.size == 0:
            team.append(("-", 0.0))
            continue

        best_name: str | None = None
        best_score = -1.0
        for label, templates in icons.items():
            score = _best_match_portrait(portrait, templates)
            if score > best_score:
                best_score = score
                best_name = label

        if best_name and best_score >= _MIN_MATCH_SCORE:
            en_key = best_name.replace("battle_icon_", "")
            zh_name = name_map.get(en_key, en_key)
            team.append((zh_name, best_score))
        else:
            team.append(("?", best_score))

    return team
