"""
BattleMixin

自动战斗相关逻辑模块。

主要功能：
- 战斗状态检测
- 技能释放
- 自动普通攻击
- 自动索敌与位移
- 战斗结束判断
- 技能条识别
- 自动战斗流程控制

依赖：
    AutoCombatLogic
    OpenCV (cv2)
    numpy
"""

import json
from pathlib import Path

import cv2
import numpy as np
from ok import Box

from src.core.BaseEfTask import BaseEfTask
from src.core.BattleConfig import (
    BATTLE_CONFIG_DESCRIPTION,
    BATTLE_CONFIG_MODE_KEY,
    BATTLE_CONFIG_NAME,
    BATTLE_CONFIG_TYPE,
    BATTLE_ROOT_CONFIGS,
    DEFAULT_BATTLE_CONFIG,
    DEFAULT_LEGACY_COMBAT_MODE,
    KEY_BATTLE_INITIAL_WAIT,
    KEY_COND_ENABLED,
    KEY_DAMAGE_ROTATION,
    KEY_ENABLE_ROTATION,
    KEY_LEGACY_COMBAT_MODE,
    KEY_RECOMMEND_SKILL,
    KEY_SKILL_ALLOWLIST,
    KEY_ULT_RELEASE_MODE,
    LEGACY_COMBAT_MODE_AUTO_FILTER,
    LEGACY_COMBAT_MODE_CONDITIONAL,
    LEGACY_COMBAT_MODE_DAMAGE,
    LEGACY_COMBAT_MODE_NORMAL,
    LEGACY_COMBAT_MODE_ROTATION,
    RECOMMEND_SKILL_REGIONS,
    ULT_RELEASE_MODE_ALT,
    ULT_RELEASE_MODE_HOLD,
    BattleConfigManager,
)
from src.core.config_migration import legacy_battle_mode_to_bool
from src.core.global_config_store import get_global_config
from src.core.sequence_parser import parse_sequence
from src.data.FeatureList import FeatureList as fL
from src.data.combat_observation import ActionBlockReason, EnemyPresence
from src.image.hsv_config import HSVRange as hR
from src.image.recommend_skill_detector import PULSE_ON_RATIO, get_recommend_skill_detector
from src.tasks.onetime.AutoCombatLogic import AutoCombatLogic

# ── 编队识别：模块级常量与工具函数 ─────────────────────────────────────────

_ROOT = Path(__file__).resolve().parents[3]
_CHARACTERS_JSON = _ROOT / "assets" / "data" / "characters.json"

_char_name_map: dict[str, str] | None = None  # en → zh

# 模板别名：变体 annotation 名 → 主模板名
# 同角色有多个 battle_icon 外观时，在此声明归并关系，
# 框架 feature_set 中仍保持独立条目。
_TEMPLATE_ALIASES: dict[str, str] = {
    "battle_icon_endministrator_female": "battle_icon_endministrator",
    "battle_icon_endministrator_male": "battle_icon_endministrator",
}

# ── 切人图标（main_char 箭头）搜索框组：判定当前是第几个角色 ──────────────
# 数据来源：ok_templates 子模块标注（基准分辨率 1920x1080）。
# main_char 箭头标注框为 (80, 898, 8, 16)，指向编队槽位上方的当前角色；
# 出现位置与 battle_icon 槽位一一平行（battle_icon 标注框 y=927、
# w=37、h=46，x 槽位 57/173/290/407），x 公差 = (407-57)/3 ≈ 116.67px，
# 归一化 ≈ 0.0608。箭头随当前角色切换在最多 4 个位置间移动。
# 搜索区域 = 归一化标注框四周各向外扩大 1/8（相对框自身宽/高）。
SWITCH_CHAR_BASE_X = 80 / 1920  # 箭头槽 0 归一化 x（≈0.041667）
SWITCH_CHAR_X_PITCH = 0.0608  # 相邻槽位 x 公差（归一化）
SWITCH_CHAR_Y = 898 / 1080  # 归一化 y（≈0.831481）
SWITCH_CHAR_W = 8 / 1920  # 归一化宽
SWITCH_CHAR_H = 16 / 1080  # 归一化高
SWITCH_CHAR_SLOTS = 4  # 最多 4 个出现位置
SWITCH_CHAR_EXPAND = 1 / 8  # 搜索框向外扩大的比例（相对框自身宽/高）

# ── 战斗顶部反馈文本带：固定位置 + 白色几何，不走 OCR ───────────────────────
# 1920x1080 实测样本「离目标太远」完整文本带；归一化后可直接覆盖 4K/2K。
COMBAT_TOO_FAR_TEXT_REGION = (0.4703, 0.1593, 0.5266, 0.1815)
COMBAT_TOO_FAR_STABLE_FRAMES = 2
COMBAT_TOO_FAR_STABLE_MIN_GAP = 0.01
COMBAT_TOO_FAR_STABLE_MAX_GAP = 0.20
COMBAT_TOO_FAR_SIGNATURE_TOLERANCE = (0.03, 0.08, 0.08, 0.025)


def _measure_combat_too_far_text_band(frame):
    """Return a cheap geometry signature for the fixed white feedback band.

    Signature = (width_ratio, height_ratio, white_fill_ratio, center_x_ratio).
    The accepted ranges are intentionally wider than the single 1080p sample,
    while still requiring the band to nearly span this tight fixed region.
    """
    if frame is None or getattr(frame, "size", 0) == 0 or frame.ndim != 3:
        return None

    mask = cv2.inRange(frame, (180, 180, 180), (255, 255, 255))
    points = cv2.findNonZero(mask)
    if points is None:
        return None

    x, y, width, height = cv2.boundingRect(points)
    frame_height, frame_width = mask.shape
    if frame_width <= 0 or frame_height <= 0:
        return None

    width_ratio = width / frame_width
    height_ratio = height / frame_height
    fill_ratio = cv2.countNonZero(mask) / (frame_width * frame_height)
    left_ratio = x / frame_width
    right_ratio = (x + width) / frame_width
    top_ratio = y / frame_height
    bottom_ratio = (y + height) / frame_height

    if not (
        0.88 <= width_ratio <= 0.98
        and 0.60 <= height_ratio <= 0.92
        and 0.12 <= fill_ratio <= 0.34
        and left_ratio <= 0.10
        and right_ratio >= 0.90
        and top_ratio <= 0.20
        and bottom_ratio >= 0.72
    ):
        return None

    center_x_ratio = (x + width / 2) / frame_width
    return width_ratio, height_ratio, fill_ratio, center_x_ratio


# ── 技力条：4K 基准坐标 ──────────────────────────────────────────────────────
# 技力从左向右填充。完整格检测从右向左查，可以在满 3 格时只做一次颜色检测；
# 若第 n 格完整，只额外读取第 n+1 格的部分填充。部分填充识别等待样图补全。
SKILL_BAR_AREA_4K = (1586, 1940, 2266, 1983)
SKILL_BAR_Y_4K = (1958, 1970)
SKILL_BAR_X_4K = ((1604, 1796), (1824, 2013), (2043, 2231))


def _resolve_skill_bar_progress(is_full, read_fill):
    """Resolve 0..3 bars with at most 3 full checks + 1 partial check.

    is_full(index) and read_fill(index) use zero-based bar indices. read_fill
    returns a 0..1 fraction or None when the partial detector is unavailable.
    """
    for index in (2, 1, 0):
        if not is_full(index):
            continue
        whole = index + 1
        if whole == 3:
            return 3.0
        fraction = read_fill(whole)
        if fraction is None:
            return float(whole)
        return min(3.0, whole + max(0.0, min(float(fraction), 1.0)))

    fraction = read_fill(0)
    if fraction is None:
        return None
    return max(0.0, min(float(fraction), 1.0))


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


class BattleMixin(BaseEfTask):
    """
    自动战斗 Mixin。

    提供完整战斗能力：

    功能包括：
        - 战斗状态识别
        - 技能释放
        - 自动普通攻击
        - 自动闪避
        - 自动索敌
        - 战斗结束检测
    """

    # 配置键迁移：旧「战斗配置」下拉框 → 新「使用独立配置」开关
    config_key_migrations = {
        "战斗配置": BATTLE_CONFIG_MODE_KEY,
    }
    config_value_migrations = {
        BATTLE_CONFIG_MODE_KEY: legacy_battle_mode_to_bool,
    }

    def __init__(self, *args, **kwargs):
        """初始化战斗状态变量"""
        super().__init__(*args, **kwargs)

        self.last_no_number_action_time = 0
        self.exit_check_count = 0
        self._battle_member_count = 0
        self._last_ult_release_time = 0
        self.battle_config_manager = BattleConfigManager(get_global_config(BATTLE_CONFIG_NAME))
        self._register_battle_config()

    def _register_battle_config(self):
        if not hasattr(self, "default_config") or self.default_config is None:
            self.default_config = {}
        if not hasattr(self, "config_description") or self.config_description is None:
            self.config_description = {}
        if not hasattr(self, "config_type") or self.config_type is None:
            self.config_type = {}
        # 「使用独立配置」只引用 Battle Config 的根节点。模式子项继续由
        # 「技能时间排轴」/「战斗模式」各自展开，避免同一个配置被两个父项引用。
        battle_mode_type = {
            "sub_configs": {
                True: BATTLE_ROOT_CONFIGS,
            },
        }

        # 每个使用战斗能力的任务都可以选择自己的战斗参数来源。
        self.default_config.update(
            {
                BATTLE_CONFIG_MODE_KEY: False,
                **DEFAULT_BATTLE_CONFIG,
            }
        )
        self.config_description.update(BATTLE_CONFIG_DESCRIPTION)
        self.config_description[BATTLE_CONFIG_MODE_KEY] = "勾选后使用当前任务的独立战斗配置，否则使用全局战斗配置。"
        self.config_type.update(BATTLE_CONFIG_TYPE)
        self.config_type[BATTLE_CONFIG_MODE_KEY] = battle_mode_type

    def _raw_battle_config(self, key: str, default=None):
        """Read one battle value from the active global/task config source."""
        global_value = self.battle_config_manager.get(key, DEFAULT_BATTLE_CONFIG.get(key, default))
        # config.get 在运行中已绑定账号覆盖；日常子任务按当前账号取战斗模式和参数。
        raw_value = self.config.get(BATTLE_CONFIG_MODE_KEY, False)
        if not self._parse_use_independent(raw_value):
            return global_value
        return self.config.get(key, global_value)

    @staticmethod
    def _legacy_mode_flag(mode: str, key: str):
        """Map the single legacy-mode selector back to old internal booleans."""
        if mode not in {
            LEGACY_COMBAT_MODE_NORMAL,
            LEGACY_COMBAT_MODE_AUTO_FILTER,
            LEGACY_COMBAT_MODE_DAMAGE,
            LEGACY_COMBAT_MODE_ROTATION,
            LEGACY_COMBAT_MODE_CONDITIONAL,
        }:
            mode = DEFAULT_LEGACY_COMBAT_MODE

        if key == KEY_COND_ENABLED:
            return mode == LEGACY_COMBAT_MODE_CONDITIONAL
        if key == KEY_ENABLE_ROTATION:
            return mode == LEGACY_COMBAT_MODE_ROTATION
        if key == KEY_SKILL_ALLOWLIST:
            return mode in {LEGACY_COMBAT_MODE_AUTO_FILTER, LEGACY_COMBAT_MODE_DAMAGE}
        if key == KEY_DAMAGE_ROTATION:
            return mode == LEGACY_COMBAT_MODE_DAMAGE
        return None

    def get_battle_config(self, key: str, default=None):
        # 旧执行逻辑仍读取四个历史 bool；它们不再由 UI 独立控制，而是统一
        # 从「战斗模式」推导，保证同一时间只有一个旧策略生效。
        if key in {
            KEY_COND_ENABLED,
            KEY_ENABLE_ROTATION,
            KEY_SKILL_ALLOWLIST,
            KEY_DAMAGE_ROTATION,
        }:
            mode = self._raw_battle_config(KEY_LEGACY_COMBAT_MODE, DEFAULT_LEGACY_COMBAT_MODE)
            return self._legacy_mode_flag(mode, key)
        return self._raw_battle_config(key, default)

    def _parse_use_independent(self, value):
        """解析「使用独立配置」值，支持布尔值、旧字符串格式和未识别值回退。

        Args:
            value: 配置值（可能是 bool、str 或其他类型）

        Returns:
            bool: True 表示使用独立配置，False 表示使用全局配置
        """
        # 已经是布尔值，直接返回
        if isinstance(value, bool):
            return value

        # 处理字符串值（包括旧的下拉框格式和其他可能的字符串表示）
        if isinstance(value, str):
            # 旧下拉框格式："使用独立配置" / "使用全局配置"
            if value == "使用独立配置":
                return True
            if value == "使用全局配置":
                return False

            # 其他常见字符串布尔表示
            normalized = value.strip().lower()
            if normalized in ("true", "1", "yes", "on"):
                return True
            if normalized in ("false", "0", "no", "off", ""):
                return False

        # 未识别的值：回退到默认值 False（使用全局配置）
        # 记录日志以便排查
        if value not in (None, False, ""):
            self.log_debug(f"未识别的「使用独立配置」值: {value!r}，回退到 False（使用全局配置）")
        return False

    def get_account_config_base_value(self, key: str, default=None):
        return dict.get(self.config, key, default)

    def _parse_skill_sequence(self, raw_config) -> list[str]:
        """
        解析技能释放顺序（逗号分隔格式，用于排轴序列）：
            "ult_1,1,2,e,sleep_2,3"
        """
        if not raw_config:
            return ["1", "2", "3"]

        sequence = []
        valid_skills = {"1", "2", "3", "4", "e"}

        for token in parse_sequence(raw_config):
            if token in valid_skills:
                sequence.append(token)
            elif token.startswith("ult_"):
                if token[4:] in {"1", "2", "3", "4"}:
                    sequence.append(token)
                else:
                    self.log_info(f"无效 ult 技能: {token}")
            elif token.startswith("sleep_"):
                try:
                    float(token[6:])
                    sequence.append(token)
                except ValueError:
                    self.log_info(f"无效 sleep 参数: {token}")
            elif token.startswith("normal_"):
                try:
                    val = float(token[7:])
                    if val <= 0:
                        raise ValueError
                    sequence.append(token)
                except ValueError:
                    self.log_info(f"无效 normal 持续时间: {token}")
            else:
                self.log_info(f"忽略无效技能: {token}")

        return sequence if sequence else ["1", "2", "3"]

    # 终结技释放后延迟退出检查的时间（秒）
    ULT_EXIT_DELAY = 3.0

    def use_ult(self, ult_sequence: str | None = None, wait_for_team_recovery: bool = True):
        """
        尝试释放终极技。

        依次检测技能键：
            1 -> 2 -> 3 -> 4

        Args:
            ult_sequence: 指定要释放的终结技槽位；None 时按 1..4 检测。
            wait_for_team_recovery: Alt 释放后是否同步等待头像消失并恢复。
                默认 True 保持旧行为；时间排轴传 False，由外层时间保护和
                HUD 监测继续推进。长按模式仍保留旧同步等待以保证按键释放。

        Returns:
            bool
                True  : 成功释放
                False : 未找到可释放技能
        """
        ults = ["1", "2", "3", "4"] if ult_sequence is None else [ult_sequence]

        release_mode = self.get_battle_config(KEY_ULT_RELEASE_MODE, ULT_RELEASE_MODE_HOLD)

        for ult in ults:
            if self._find_battle_ult("ult_" + ult):
                self.log_info(f"检测到终极技 ult_{ult}，尝试释放（模式: {release_mode}）")
                if release_mode == ULT_RELEASE_MODE_ALT:
                    # Alt + 技能按键：按住 Alt 的同时点按技能键释放终结技
                    self.send_key_down("alt")
                    self.send_key(ult)  # 确认使用send_key：终极技键位为游戏固定不可配置键，不经过KeyConfigManager管理
                    self.send_key_up("alt")
                    # 从实际完成按键操作的时刻开始计算退出延迟
                    self._last_ult_release_time = self.active_time()
                    # 旧策略需要等待终结技动画期间 HUD 消失再恢复；时间排轴模式
                    # 已有技能时间保护，可跳过这段同步等待，让外层监测继续刷新。
                    if wait_for_team_recovery:
                        self._has_detected_team_member(time_out=1, require_four_unknown=True)
                        self._has_detected_team_member()
                    return True
                self.send_key_down(ult)  # 确认使用send_key：终极技键位为游戏固定不可配置键，不经过KeyConfigManager管理
                # 等待技能释放导致战斗状态变化
                self._has_detected_team_member(time_out=1, require_four_unknown=True)
                self.send_key_up(ult)  # 确认使用send_key：终极技键位为游戏固定不可配置键，释放按键
                # 从实际完成按键操作的时刻开始计算退出延迟
                self._last_ult_release_time = self.active_time()
                # wait_until 每轮会刷新 self.frame，再重新识别当前编队
                self._has_detected_team_member()
                return True

        return False

    # ── 编队头像识别 ────────────────────────────────────────────────────────

    # 归一化距离阈值：同一槽位人工框选偏差的允许范围。
    # 相邻战斗槽位中心距约 116px / 1920 ≈ 0.06，阈值取 0.025
    # 确保同一槽位的微小偏差能合并，相邻槽位不会被错误合并。
    BATTLE_ICON_GROUP_DISTANCE_THRESHOLD = 0.025

    @staticmethod
    def _union_find_cluster(n, pairs):
        """并查集聚类。

        Args:
            n: 元素总数
            pairs: 可合并的元素对列表 [(i, j), ...]

        Returns:
            各元素所属连通分量的根节点列表
        """
        parent = list(range(n))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(i, j):
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[ri] = rj

        for i, j in pairs:
            union(i, j)

        return [find(i) for i in range(n)]

    def _build_search_boxes(self, boxes, frame_width, frame_height):
        """将所有 battle_icon bbox 按中心距离聚类，为每个簇生成外接搜索框。

        全程使用归一化坐标进行距离判断，最终转回像素坐标生成 Box。

        Args:
            boxes: 原始 Box 列表（像素坐标）
            frame_width: 帧宽度
            frame_height: 帧高度

        Returns:
            聚类后的搜索 Box 列表（像素坐标）
        """
        if not boxes:
            return []

        # 计算归一化中心点
        centers = []
        for b in boxes:
            cx = (b.x + b.width / 2) / frame_width
            cy = (b.y + b.height / 2) / frame_height
            centers.append((cx, cy))

        # 找出所有中心距离 < 阈值的配对
        threshold = self.BATTLE_ICON_GROUP_DISTANCE_THRESHOLD
        pairs = []
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                dx = centers[i][0] - centers[j][0]
                dy = centers[i][1] - centers[j][1]
                if (dx * dx + dy * dy) ** 0.5 < threshold:
                    pairs.append((i, j))

        # 并查集聚类
        roots = self._union_find_cluster(len(boxes), pairs)

        # 按簇分组
        groups: dict[int, list] = {}
        for i, root in enumerate(roots):
            groups.setdefault(root, []).append(i)

        # 每个簇生成外接矩形（归一化坐标）
        search_boxes = []
        for indices in groups.values():
            nxs = [(boxes[i].x / frame_width) for i in indices]
            nys = [(boxes[i].y / frame_height) for i in indices]
            nxe = [((boxes[i].x + boxes[i].width) / frame_width) for i in indices]
            nye = [((boxes[i].y + boxes[i].height) / frame_height) for i in indices]

            x1 = min(nxs) * frame_width
            y1 = min(nys) * frame_height
            x2 = max(nxe) * frame_width
            y2 = max(nye) * frame_height
            search_boxes.append(
                Box(
                    int(x1),
                    int(y1),
                    int(x2 - x1),
                    int(y2 - y1),
                    name=f"battle_slot_{len(search_boxes)}",
                )
            )

        return search_boxes

    def _collect_team_candidate_boxes(self) -> tuple[list[Box], list[str]]:
        """收集有效的战斗头像候选框及对应模板名。"""
        raw_boxes = []
        valid_features = []
        for f in fL:
            feature_name = f.value
            if not feature_name.startswith("battle_icon_"):
                continue
            try:
                box = self.get_box_by_name(feature_name)
            except (ValueError, AttributeError):
                continue
            if box is not None:
                raw_boxes.append(box)
                valid_features.append(feature_name)

        return raw_boxes, valid_features

    def _match_team_slots(self, frame, search_boxes, valid_features) -> list[tuple[int, str, float]]:
        """为每个槽位匹配最高分且未被其他槽位使用的角色模板。"""
        matches = []
        used_features: set[str] = set()
        for slot_idx, slot_box in enumerate(search_boxes[:4]):
            best_score = 0.0
            best_feature = None
            for feature_name in valid_features:
                if feature_name in used_features:
                    continue
                try:
                    result = self.find_one(feature_name, box=slot_box, frame=frame)
                except Exception:
                    continue
                if result is not None and result.confidence > best_score:
                    best_score = result.confidence
                    best_feature = feature_name
            if best_feature is not None:
                matches.append((slot_idx, best_feature, best_score))
                used_features.add(best_feature)

        return matches

    def _detect_team_core(self, frame=None) -> list[tuple[str, float, str]]:
        """编队识别核心逻辑：返回四个槽位的 (en_name, score, feature_name)。"""
        if frame is None:
            frame = self.frame

        slot_results: list[tuple[str, float, str]] = [("?", 0.0, "") for _ in range(4)]

        raw_boxes, valid_features = self._collect_team_candidate_boxes()
        if not raw_boxes:
            return slot_results

        fh, fw = frame.shape[:2]
        search_boxes = self._build_search_boxes(raw_boxes, frame_width=fw, frame_height=fh)
        search_boxes.sort(key=lambda b: b.x)

        if not search_boxes:
            return slot_results

        for slot_idx, feature_name, score in self._match_team_slots(frame, search_boxes, valid_features):
            en_name = _TEMPLATE_ALIASES.get(feature_name, feature_name)
            en_name = en_name.replace("battle_icon_", "")
            slot_results[slot_idx] = (en_name, score, feature_name)

        return slot_results

    def detect_team(self, frame=None) -> list[str]:
        """从战斗帧识别当前队伍角色名（中文）。

        槽位按 x 排序，未匹配到的槽位标记为 "?"。
        例: ["伊冯", "洁尔佩塔", "?", "余烬"] 表示第3位角色未识别。

        Args:
            frame: BGR numpy 帧（可选，默认使用 self.frame）

        Returns:
            角色名列表
        """
        slot_results = self._detect_team_core(frame)
        name_map = _load_char_name_map()

        return [name_map.get(en_name, en_name) for en_name, _, _ in slot_results]

    def detect_team_with_scores(self, frame=None) -> list[tuple[str, float]]:
        """同 detect_team，但同时返回匹配备分数（调试用）。"""
        slot_results = self._detect_team_core(frame)
        name_map = _load_char_name_map()

        return [(name_map.get(en, en), score) for en, score, _ in slot_results]

    # ── 切人图标：判定当前是第几个角色 ──────────────────────────────────────

    def get_switch_char_boxes(self) -> list[Box]:
        """切人图标（main_char 箭头）各槽位搜索框（像素坐标，按 x 升序）。

        以模块顶部 SWITCH_CHAR_* 归一化常量为基准生成：每个位置在
        归一化标注框基础上四周向外扩大 SWITCH_CHAR_EXPAND，最多
        SWITCH_CHAR_SLOTS 个，与 battle_icon 编队槽位一一平行。
        """
        dx = SWITCH_CHAR_W * SWITCH_CHAR_EXPAND
        dy = SWITCH_CHAR_H * SWITCH_CHAR_EXPAND
        boxes = []
        for slot in range(SWITCH_CHAR_SLOTS):
            nx = SWITCH_CHAR_BASE_X + slot * SWITCH_CHAR_X_PITCH
            boxes.append(
                self.box_of_screen(
                    nx - dx,
                    SWITCH_CHAR_Y - dy,
                    nx + SWITCH_CHAR_W + dx,
                    SWITCH_CHAR_Y + SWITCH_CHAR_H + dy,
                    name=f"switch_char_slot_{slot}",
                )
            )
        return boxes

    def detect_current_char_index(self, frame=None) -> int | None:
        """判定当前操作角色是编队第几个（0 起，未识别返回 None）。

        在各切人图标搜索框内查找 main_char 白色指向箭头，箭头落在
        第几个槽位即当前是第几个角色；取置信度最高的槽位。
        """
        best_index = None
        best_score = 0.0
        for index, box in enumerate(self.get_switch_char_boxes()):
            result = self.find_one(fL.main_char, box=box, frame=frame, threshold=0.7)
            if result is not None and result.confidence > best_score:
                best_index = index
                best_score = result.confidence
        if best_index is None:
            self.log_debug("切人图标未命中任何槽位，无法判定当前角色")
        else:
            self.log_debug(f"切人图标命中槽位 {best_index}（score={best_score:.3f}）→ 当前第 {best_index + 1} 个角色")
        return best_index

    def detect_team_stable(
        self,
        max_attempts: int = 6,
        interval: float = 0.2,
        confidence: int = 2,
        deadline: float | None = None,
    ) -> tuple[list[str], bool]:
        """多帧稳定识别：连续 confidence 次识别出相同队伍才视为稳定。

        用于战斗开始前的稳定检测，避免单帧误识别。

        Args:
            max_attempts:  最大采样次数
            interval:      每次采样间隔秒数
            confidence:    连续多少次相同结果视为稳定
            deadline:      可选截止时间戳（同 self.active_time() 单位），超时立即停止

        Returns:
            (team, stable) 二元组
        """
        last_result: list[str] = []
        streak = 0

        for i in range(max_attempts):
            if deadline is not None and self.active_time() >= deadline:
                break

            frame = self.next_frame()
            if frame is None or (hasattr(frame, "size") and frame.size == 0):
                self.sleep(min(interval, max(0, (deadline or self.active_time() + interval) - self.active_time())))
                continue

            current = self.detect_team(frame)

            # 无效识别：空结果，或者全部都是 ?
            if not current or all(x == "?" for x in current):
                last_result = []
                streak = 0
            else:
                if current == last_result:
                    streak += 1
                    if streak >= confidence:
                        return (current, True)
                else:
                    last_result = current
                    streak = 1
                    if streak >= confidence:
                        return (current, True)

            if i < max_attempts - 1:
                if deadline is not None:
                    remaining = deadline - self.active_time()
                    if remaining <= 0:
                        break
                    self.sleep(min(interval, remaining))
                else:
                    self.sleep(interval)

        return (last_result or ["?"], False)

    def _is_detected_team_frame_matched(self, team, battle_team, require_four_unknown):
        """判断当前帧的队伍识别结果是否满足等待条件。

        战斗中已经确认失效/阵亡的槽位不再要求头像恢复；其余槽位仍必须
        与开场队伍按位置完全一致。这样终结技动画结束后的 HUD 恢复不会
        因一个已经永久变成 "?" 的槽位每次都等到超时。
        """
        if require_four_unknown:
            return bool(team) and len(team) == 4 and all(member == "?" for member in team)
        if not team or not battle_team or len(team) != len(battle_team):
            return False
        disabled = set(getattr(self, "_battle_team_disabled_slots", set()) or ())
        return all(
            index in disabled or (current != "?" and current == expected)
            for index, (current, expected) in enumerate(zip(team, battle_team))
        )

    def _log_detected_team_member_result(self, team, battle_team, require_four_unknown, time_out, success):
        """记录队伍检测成功或超时的结果。"""
        if success:
            if require_four_unknown:
                self.log_info(f"队伍稳定为 4 个未知角色: {team}")
            else:
                self.log_info(f"队伍恢复确认: {team}")
        elif require_four_unknown:
            self.log_info(f"等待队伍稳定为 4 个 '?' 超时（{time_out:.1f}秒）")
        else:
            self.log_info(f"等待队伍恢复超时（{time_out:.1f}秒），目标队伍: {battle_team}")

    def _has_detected_team_member(self, time_out=3, require_four_unknown=False):
        """在指定时间内等待当前队伍恢复。

        require_four_unknown=False:
            等待队伍恢复为战斗开始时的完整队伍。

        require_four_unknown=True:
            等待队伍连续两帧识别为 4 个 '?'。

        超时返回 False。
        """
        battle_team = getattr(self, "_battle_team", None)

        if not require_four_unknown and not battle_team:
            return False

        start_time = self.active_time()
        matched_count = 0

        while self.active_time() - start_time < time_out:
            frame = self.next_frame()

            if frame is None or frame.size == 0:
                matched_count = 0
                continue

            team = self.detect_team(frame)
            self.log_info(f"当前队伍角色: {team}")

            matched = self._is_detected_team_frame_matched(team, battle_team, require_four_unknown)

            if matched:
                matched_count += 1

                if matched_count >= 2:
                    self._log_detected_team_member_result(
                        team, battle_team, require_four_unknown, time_out, success=True
                    )
                    return True
            else:
                matched_count = 0

        self._log_detected_team_member_result(
            team=None,
            battle_team=battle_team,
            require_four_unknown=require_four_unknown,
            time_out=time_out,
            success=False,
        )

        return False

    def probe_enemy_presence(self) -> EnemyPresence:
        """Hook for fixed-region enemy-presence detection.

        Placeholder only; current behavior stays unchanged by returning
        UNKNOWN. The concrete detector can later classify PRESENT when either
        of these fixed UI evidences is visible:

        - boss: the large red health bar near the top of the screen;
        - normal enemies: one or more small-enemy health bars / enemy markers.

        ABSENT must only be returned when the detector has positively checked
        those regions and found no enemy evidence while the outer combat HUD is
        still active. TimedCombatLogic will pause skill scheduling but keep
        normal attack and middle-button target acquisition running so a later
        enemy can be acquired without freezing elapsed time.
        """
        return EnemyPresence.UNKNOWN

    def probe_combat_action_block_reason(self) -> ActionBlockReason | None:
        """Detect the fixed white top-center failure band without OCR.

        The prompt is identified by its unique fixed position, near-full band
        width, white-pixel geometry, and two consecutive stable frames.
        """
        feedback_box = self.box_of_screen(
            *COMBAT_TOO_FAR_TEXT_REGION,
            name="combat_action_feedback",
        )
        signature = _measure_combat_too_far_text_band(feedback_box.crop_frame(self.frame))
        now = self.active_time()

        if signature is None:
            self._combat_too_far_band_streak = 0
            self._combat_too_far_band_signature = None
            self._combat_too_far_band_last_seen_at = None
            return None

        previous = getattr(self, "_combat_too_far_band_signature", None)
        last_seen_at = getattr(self, "_combat_too_far_band_last_seen_at", None)
        streak = getattr(self, "_combat_too_far_band_streak", 0)
        stable = (
            previous is not None
            and last_seen_at is not None
            and COMBAT_TOO_FAR_STABLE_MIN_GAP <= now - last_seen_at <= COMBAT_TOO_FAR_STABLE_MAX_GAP
            and all(
                abs(current - old) <= tolerance
                for current, old, tolerance in zip(
                    signature,
                    previous,
                    COMBAT_TOO_FAR_SIGNATURE_TOLERANCE,
                    strict=True,
                )
            )
        )

        self._combat_too_far_band_signature = signature
        self._combat_too_far_band_last_seen_at = now
        self._combat_too_far_band_streak = streak + 1 if stable else 1

        if self._combat_too_far_band_streak >= COMBAT_TOO_FAR_STABLE_FRAMES:
            return ActionBlockReason.TOO_FAR
        return None

    def recover_target_too_far(self) -> bool:
        """Hook for future distance recovery after a TOO_FAR prompt.

        Intentionally a no-op for now. The reserved flow is:
        target/lock so the camera turns toward the enemy -> allow a short settle
        delay -> perform a long forward rush. Return True once a concrete
        implementation has actually started that recovery action.
        """
        return False
    def is_link_skill_ready(self):
        """Shared link readiness monitor, without sending a combat key."""
        return bool(
            self.find_one(
                fL.default_link_skill,
                threshold=0.7,
                vertical_variance=0.005,
                horizontal_variance=0.005,
            )
        )

    def use_link_skill(self):
        """
        使用连携技能。
        """
        if self.is_link_skill_ready():
            self.press_combat_key("e")
            return True

        return False

    def use_recommend_skill(self):
        """检测推荐技能按钮的白色圆周脉冲，命中即按对应战技键（每周期按一次）。

        监测区域随队伍人数右锚定（复用 in_team() 检出的 _battle_member_count）：
        N 人取最右侧 N 个区域；按键编号为激活区域内从左到右的位次。
        例：3 人时批次2/3/4 激活，批次2 命中 → 按 1。

        Returns:
            bool: 本帧是否因推荐技能命中而按下了按键。
        """
        if not self.get_battle_config(KEY_RECOMMEND_SKILL, False) and not self.get_battle_config(
            KEY_SKILL_ALLOWLIST, False
        ):
            return False
        member_count = int(self._battle_member_count or 0)
        if not 1 <= member_count <= len(RECOMMEND_SKILL_REGIONS):
            return False
        active_regions = RECOMMEND_SKILL_REGIONS[-member_count:]
        frame = self.frame
        if frame is None or frame.size == 0:
            return False

        detector = get_recommend_skill_detector()
        # 本帧各激活区域白色占比只计算一次，诊断 / 上升沿检测 / 闪光过滤共用
        frame_ratios = {
            str(region["label"]): detector.white_ratio(
                frame, float(region["x"]), float(region["y"]), float(region["button_radius"])
            )
            for region in active_regions
        }
        # 节流诊断：每 2 秒输出一次各区域白色占比，便于实战核对检测状态
        now = self.active_time()
        if now - getattr(self, "_recommend_last_diag", 0.0) >= 2.0:
            self._recommend_last_diag = now
            ratios = " ".join(
                f"{region['label']}={frame_ratios[str(region['label'])]:.2f}" for region in active_regions
            )
            self.log_debug(f"推荐技能监测占比: {ratios}（人数 {member_count}）")

        # 先收集本帧全部上升沿，再做全屏闪光过滤
        confirmed = []
        for slot, region in enumerate(active_regions, start=1):
            label = str(region["label"])
            if detector.detect_ratio(frame_ratios[label], label):
                confirmed.append((slot, region, label))

        # 全部激活区域（≥3 个）当前均为白色 = 大招演出/爆炸等全屏白闪，
        # 而非单个按钮的推荐脉冲；整批忽略。用当帧白色状态而非上升沿集合
        # 判断：闪光前已 active 的标签不会产生上升沿，若只统计 confirmed
        # 会漏判闪光，导致其余区域误按技能键。
        if len(active_regions) >= 3 and all(
            frame_ratios[str(region["label"])] >= PULSE_ON_RATIO for region in active_regions
        ):
            labels = "、".join(str(region["label"]) for region in active_regions)
            self.log_info(f"推荐技能疑似全屏闪光，忽略本次命中: {labels}")
            # detect 已把本帧上升沿区域置为 active（闪光前已 active 的标签
            # 同样处于该状态）；复位全部激活区域标签，允许后续真实白圈
            # 重新产生上升沿并按出技能。
            for region in active_regions:
                detector.reset_label(str(region["label"]))
            return False

        pressed = False
        for slot, _, label in confirmed:
            key = str(slot)
            self.press_combat_key(key)
            self.log_info(f"推荐技能 {label} 命中, 按下按键 {key}（队伍 {member_count} 人）")
            pressed = True
        return pressed

    def probe_pulse(self):
        """独立脉冲探针：观测当前帧各技能按钮区域的白色脉冲并落盘。

        只记录不按键，不受「自动技能列表 / 推荐技能」等配置影响
        （自带独立开关 KEY_PULSE_PROBE，默认开）。由战斗循环每帧调用；
        探针内部自带节流与异常兜底，绝不干扰战斗行为。
        """
        from src.image.pulse_probe import get_pulse_probe

        get_pulse_probe().observe(self)

    def in_combat(self, required_yellow=1):
        """
        判断当前是否处于战斗中。

        条件：
            - 技能条数量 >= required_yellow
            - 在队伍状态
            - 非等级界面

        Returns:
            bool
        """

        return self.get_skill_bar_count() >= required_yellow and self.in_team() and not self.ocr_lv()

    def in_team(self):
        """
        判断当前是否处于队伍状态。
        """

        found_skills = 0
        sequence_valid = False
        skill_checks = []
        boxes = self._battle_feature_boxes("skill")
        for box_index, box in enumerate(boxes, start=1):
            result = self.find_one(fL.skill_1, box=box)
            match_position = f"({result.x},{result.y})" if result is not None else "-"
            match_score = f"{result.confidence:.3f}" if result is not None else "-"
            skill_checks.append(
                f"skill_1->框{box_index}({box.x},{box.y},{box.width},{box.height}) "
                f"{'命中' if result is not None else '未命中'}@{match_position}, score={match_score}"
            )
            if result is None:
                continue

            if box_index == len(boxes):
                # skill_1 位于最后一个框时，表示单人队伍。
                found_skills = 1
                sequence_valid = True
                break

            matched_skills = 1
            for skill_offset in range(1, len(boxes) - box_index + 1):
                skill_number = skill_offset + 1
                next_box = boxes[box_index + skill_offset - 1]
                next_result = self.find_one(f"skill_{skill_number}", box=next_box)
                next_position = f"({next_result.x},{next_result.y})" if next_result is not None else "-"
                next_score = f"{next_result.confidence:.3f}" if next_result is not None else "-"
                skill_checks.append(
                    f"skill_{skill_number}->框{box_index + skill_offset}"
                    f"({next_box.x},{next_box.y},{next_box.width},{next_box.height}) "
                    f"{'命中' if next_result is not None else '未命中'}@{next_position}, "
                    f"score={next_score}"
                )
                if next_result is not None:
                    matched_skills += 1
                    if matched_skills >= 2:
                        # 起始框决定队伍人数；第二个技能模板命中后即可确认队伍状态。
                        found_skills = len(boxes) - box_index + 1
                        sequence_valid = True
                        break

            if sequence_valid:
                break
        self._battle_member_count = found_skills
        self.log_debug(f"队伍人数检测: {found_skills} 人，检查结果: {'; '.join(skill_checks)}")
        return sequence_valid and found_skills >= 1

    def _battle_feature_boxes(self, prefix: str):
        """按模板初始位置的 x 坐标返回四个独立搜索框。"""
        initial_boxes = []
        for index in range(1, 5):
            try:
                initial_boxes.append(self.get_box_by_name(f"{prefix}_{index}"))
            except (ValueError, AttributeError):
                continue

        if not initial_boxes:
            return []

        max_width = max(box.width for box in initial_boxes)
        max_height = max(box.height for box in initial_boxes)
        for index in range(1, 5):
            try:
                template = self.get_feature_by_name(f"{prefix}_{index}")
            except (ValueError, AttributeError):
                continue
            if template is not None:
                max_width = max(max_width, template.width)
                max_height = max(max_height, template.height)

        if prefix == "skill":
            max_width = round(max_width * 1.25)

        boxes = []
        for initial_box in sorted(initial_boxes, key=lambda box: box.x):
            center_x = initial_box.x + initial_box.width // 2
            center_y = initial_box.y + initial_box.height // 2
            x = max(0, center_x - max_width // 2)
            y = max(0, center_y - max_height // 2)
            x = min(x, max(0, self.width - max_width))
            y = min(y, max(0, self.height - max_height))
            boxes.append(Box(x, y, width=max_width, height=max_height))
        return boxes

    def _find_battle_feature(self, feature: str):
        """在按 x 排序的独立模板框中查找技能，避免跨框误匹配。"""
        prefix, _, _ = feature.rpartition("_")
        if prefix not in ("skill", "ult"):
            return self.find_one(feature)
        for box in self._battle_feature_boxes(prefix):
            if result := self.find_one(feature, box=box):
                return result
        return None

    def are_all_battle_ults_ready(self) -> bool:
        """Return whether every current team member's ultimate is ready.

        in_combat() calls in_team() first, so _battle_member_count normally
        already reflects the visible team by the time combat logic starts.
        Keep this as a read-only readiness probe: it reuses the same per-slot
        template mapping as actual ultimate release and never presses a key.
        """
        member_count = int(getattr(self, "_battle_member_count", 0) or 0)
        if member_count < 1 or member_count > 4:
            return False
        return all(
            bool(self._find_battle_ult(f"ult_{index}"))
            for index in range(1, member_count + 1)
        )

    def _find_battle_ult(self, feature: str):
        """根据本次队伍人数，将终结技模板映射到实际技能框。"""
        boxes = self._battle_feature_boxes("ult")
        if len(boxes) != 4 or not self._battle_member_count:
            return self._find_battle_feature(feature)

        try:
            original_index = int(feature.rsplit("_", 1)[1])
        except (IndexError, ValueError):
            return self._find_battle_feature(feature)

        current_index = original_index + (4 - self._battle_member_count) - 1
        if current_index < 0 or current_index >= len(boxes):
            self.log_debug(
                f"终结技目标映射失败: {feature}, 队伍人数={self._battle_member_count}, 计算框索引={current_index + 1}"
            )
            return None
        self.log_debug(
            f"终结技目标映射: {feature}, 队伍人数={self._battle_member_count}, "
            f"检测第 {current_index + 1} 个框(x={boxes[current_index].x})"
        )
        result = self.find_one(feature, box=boxes[current_index])
        match_position = f"({result.x},{result.y})" if result is not None else "-"
        match_score = f"{result.confidence:.3f}" if result is not None else "-"
        self.log_debug(
            f"模板匹配结果: {feature}->框{current_index + 1}, "
            f"{'命中' if result is not None else '未命中'}@{match_position}, score={match_score}"
        )
        return result

    def is_combat_ended(self, exit_condition=None):
        """
        检查战斗是否结束。

        需要 **连续两次检测成功** 才判定结束。
        """

        if exit_condition is None:
            exit_condition = self._check_single_exit_condition()

        if exit_condition:
            self.exit_check_count += 1

            if self.exit_check_count >= 2:
                self.exit_check_count = 0
                return True
        else:
            self.exit_check_count = 0

        return False

    def _check_single_exit_condition(self):
        """
        单次战斗结束判定。
        """
        # 结算模板优先检查：检测到 fL.b 结算模板同样判定战斗结束
        if self.find_feature(feature=fL.b):
            self.log_info("退出检查通过: 检测到结算模板 fL.b")
            return True

        # 终结技释放后延迟退出检查：终结技动画期间 in_team 会返回 False，
        # 需要等待动画结束、技能图标重新出现后再做退出判定。
        last_ult_time = getattr(self, "_last_ult_release_time", 0)
        if last_ult_time > 0:
            elapsed = self.active_time() - last_ult_time
            if elapsed < self.ULT_EXIT_DELAY:
                self.log_debug(f"终结技释放后延迟退出检查（已过 {elapsed:.1f}s，需等待 {self.ULT_EXIT_DELAY:.1f}s）")
                return False

        # UI状态检测
        has_lv = self.ocr_lv()
        in_team = self.in_team()

        if not (has_lv or not in_team):
            return False

        self.log_info(f"退出检查通过: has_lv={has_lv}, in_team={in_team},")

        return True

    def ocr_lv(self):
        """
        检测是否出现 LV 或等级 UI。
        """

        return bool(
            self.find_one(
                fL.battle_lv,
                mask_function=self.make_hsv_isolator(hR.WHITE, invert=True),
                threshold=0.7,
                box=self.box_of_screen(0.02, 0.89, 0.23, 0.93),
            )
        )

    def wait_in_combat(self, time_out=3, click=False):
        """
        等待进入战斗状态。
        """

        start = self.active_time()

        while self.active_time() - start < time_out:
            if self.in_combat():
                return True

            elif click:
                self.perform_attack_weave()
            else:
                self.sleep(0.003)

        return False

    def approach_enemy(self):
        """战斗中周期触发操作（无伤害数字）"""
        interval = self.get_battle_config("无数字操作间隔", 6)
        interval = max(1.0, min(float(interval), 30.0))
        if self.active_time() - getattr(self, "last_no_number_action_time", 0) < interval:
            return
        self.log_info("战斗中周期触发：执行索敌+向前闪避（贴近敌人）")
        self.click(key="middle", down_time=0.002)
        self.dodge_forward(pre_hold=0.05, dodge_down_time=0.03, after_sleep=0.02)
        self.last_no_number_action_time = self.active_time()

    def _is_skill_bar_full(self, index):
        x1, x2 = SKILL_BAR_X_4K[index]
        y_start, y_end = SKILL_BAR_Y_4K
        return self.check_is_pure_color_in_4k(x1, y_start, x2, y_end, yellow_skill_color)

    def is_skill_bar_full_fast(self):
        """Cheap full-SP probe: check only the third skill bar.

        Timed combat uses this every scheduler frame only after a previous
        precise observation has already reached 2.x bars. It avoids repeating
        the full staged 3+1 scan while still reacting to 3 bars immediately.
        """
        return self._is_skill_bar_full(2)

    def _read_skill_bar_fill_ratio(self, index):
        x1, x2 = SKILL_BAR_X_4K[index]
        y1, y2 = SKILL_BAR_Y_4K

        # 收缩，避开边框
        box = self.box_of_screen_scaled(3840, 2160, x1 + 3, y1 + 2, x2 - 3, y2 - 2)
        bar = box.crop_frame(self.frame)
        if bar.size == 0:
            return None

        hsv = cv2.cvtColor(bar, cv2.COLOR_BGR2HSV)

        white_mask = cv2.inRange(hsv, (0, 0, 170), (180, 60, 255))
        yellow_mask = cv2.inRange(hsv, (20, 80, 140), (45, 255, 255))

        fill_mask = cv2.bitwise_or(white_mask, yellow_mask)

        h, w = fill_mask.shape
        filled_cols = 0
        gap = 0

        for x in range(w):
            col = fill_mask[:, x]
            ratio = np.count_nonzero(col) / h

            if ratio >= 0.55:
                filled_cols = x + 1
                gap = 0
            else:
                gap += 1
                # 允许少量抖动，不要一断就停
                if gap >= 2:
                    break

        ratio = filled_cols / w

        # 收尾修正
        if ratio < 0.02:
            return 0.0
        if ratio > 0.98:
            return 1.0
        return ratio

    def get_skill_bar_progress(self):
        """获取技力条精细进度，范围 0..3；-1 表示未检测到。

        优化顺序：
        - 先检测第 3 格是否完整，命中直接返回 3（1+0 次检测）；
        - 否则检测第 2 格，命中后只读取第 3 格部分填充；
        - 再检测第 1 格，命中后只读取第 2 格部分填充；
        - 三格都不完整时，只读取第 1 格部分填充（最坏 3+1 次检测）。
        """

        x1, y1, x2, y2 = SKILL_BAR_AREA_4K
        skill_area_box = self.box_of_screen_scaled(3840, 2160, x1, y1, x2, y2)
        skill_area = skill_area_box.crop_frame(self.frame)
        if not has_rectangles(skill_area):
            return -1.0

        progress = _resolve_skill_bar_progress(
            self._is_skill_bar_full,
            self._read_skill_bar_fill_ratio,
        )
        if progress is not None:
            return progress

        # 部分填充识别尚未接入时保留旧行为：白色左边界存在代表技力条有效，
        # 只是当前没有完整黄色格；否则视为未检测到。
        y_start, y_end = SKILL_BAR_Y_4K
        first_x1, _ = SKILL_BAR_X_4K[0]
        has_white_left = self.check_is_pure_color_in_4k(
            first_x1, y_start, first_x1 + 10, y_end, white_skill_color, threshold=0.1
        )
        return 0.0 if has_white_left else -1.0

    def get_skill_bar_sp(self):
        """获取近似技力值（0..300）；部分填充识别完成后可直接用于 25/50/75 SP 技能。"""
        progress = self.get_skill_bar_progress()
        return -1.0 if progress < 0 else progress * 100.0

    def get_skill_bar_count(self):
        """获取完整技能条数量；-1 表示未检测到。

        保持旧 API 语义，内部复用从右向左的快速扫描。
        """
        progress = self.get_skill_bar_progress()
        return -1 if progress < 0 else int(progress)

    def check_is_pure_color_in_4k(self, x1, y1, x2, y2, color_range=None, threshold=0.9):
        skill_area_box = self.box_of_screen_scaled(3840, 2160, x1, y1, x2, y2)
        bar = skill_area_box.crop_frame(self.frame)
        if bar.size == 0:
            return False

        height, width, _ = bar.shape
        consecutive_matches = 0

        for i in range(height):
            row_pixels = bar[i]
            unique_colors, counts = np.unique(row_pixels, axis=0, return_counts=True)
            most_frequent_index = np.argmax(counts)
            dominant_count = counts[most_frequent_index]
            dominant_color = unique_colors[most_frequent_index]

            is_valid_row = (dominant_count / width) >= threshold
            if is_valid_row and color_range:
                b, g, r = dominant_color
                if not (
                    color_range["r"][0] <= r <= color_range["r"][1]
                    and color_range["g"][0] <= g <= color_range["g"][1]
                    and color_range["b"][0] <= b <= color_range["b"][1]
                ):
                    is_valid_row = False

            if is_valid_row:
                consecutive_matches += 1
                if consecutive_matches >= 2:
                    return True
            else:
                consecutive_matches = 0
        return False

    def is_battle_settlement(self) -> bool:
        """
        判断是否进入战斗结算状态
        """

        return any(
            (
                self.find_feature(feature=fL.b),
                self.find_feature(feature=fL.battle_space_ok),
                self.find_feature(feature=fL.battle_gather_ok),
                self.find_feature(feature=fL.restart_battle),
                self.find_feature(feature=fL.restart_battle, box=self.box_of_screen(0.550, 0.896, 0.574, 0.943)),
            )
        )

    def auto_battle(self, no_battle: bool = False):
        """
        自动战斗主循环
        """

        start_time = self.active_time()
        deadline = start_time + 420
        last_battle_time = None
        sleep_time = self.get_battle_config(KEY_BATTLE_INITIAL_WAIT, 3)

        while True:
            # 全局超时保护
            if self.active_time() >= deadline:
                self.log_info("自动战斗超时")
                return False

            # 内层退出后继续走战斗检测：结算模板立即结束，
            # 未检测到战斗时最多等待 15 秒。
            if last_battle_time is not None:
                battle_elapsed = self.active_time() - last_battle_time
                self.next_frame()

                if self.is_battle_settlement():
                    self.log_info("检测到战斗结算状态，战斗完成")
                    return True

                if battle_elapsed >= 15:
                    self.log_info("战斗结束状态等待超时，视为战斗已结束")
                    return True

            # 检测战斗
            battle_detected = AutoCombatLogic(self).run(
                start_sleep=sleep_time,
                no_battle=no_battle,
                deadline=deadline,
            )

            if battle_detected:
                last_battle_time = self.active_time()
                sleep_time = 0.1
            else:
                self.sleep(0.1)


def has_rectangles(frame):
    if frame is None:
        return False

    original_h, original_w = frame.shape[:2]
    scale_factor = 4
    resized = cv2.resize(frame, None, fx=scale_factor, fy=scale_factor, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 100)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    closed_edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(closed_edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    min_width = (original_w * scale_factor) * 0.25
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        if w > min_width and w > h > 10:
            return True
    return False


lower_white_none_inclusive = np.array([222, 222, 222], dtype=np.uint8)
black = np.array([0, 0, 0], dtype=np.uint8)


def isolate_white_text_to_black(cv_image):
    match_mask = cv2.inRange(cv_image, black, lower_white_none_inclusive)
    output_image = cv2.cvtColor(match_mask, cv2.COLOR_GRAY2BGR)
    return output_image


yellow_skill_color = {"r": (230, 255), "g": (180, 255), "b": (0, 85)}

white_skill_color = {"r": (190, 255), "g": (190, 255), "b": (190, 255)}
