"""角色静态能力注册表：从技能快照解析「能给队伍提供什么」。

供队伍感知口径选择消费（skill_rotation.load_damage_baseline_for_team）：
- ``attach_elements``：该角色可施加的元素附着（灼热/寒冷/电磁/自然），
  由技能文案中的「施加/附加/赋予 X 附着」句式解析；
- ``combo_applier``：该角色可施加连击（队伍共享层），由「获得/赋予/附加 连击」
  句式解析（当前 32 角色中仅黎风；秋栗经天赋给连击，快照未覆盖天赋，
  数据落地后自动识别）。

解析口径说明：
- 只认施加动词，不认消耗——提弗洛斯「消耗自然附着」不会误标为施加者；
- 「不再施加」「无法重复施加」等条件句仍视为可施加（条件只影响频率，
  不影响「队伍能否提供该附着」的判定）；
- 「当有敌人被施加 X 附着」类触发文本会命中正则，当前 32 角色中命中者
  （黎智研/汤汤）本身也是真实施加者，属可接受的宽口径。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_SKILLS_DIR = _ROOT / "assets" / "data" / "character_skills"

_ATTACH_RE = re.compile(r"(施加|附加|赋予)[^。]{0,12}?(灼热|寒冷|电磁|自然)附着")
_COMBO_RE = re.compile(r"(获得|赋予|附加)[^。]{0,8}连击")


@dataclass(frozen=True)
class CharacterCapabilities:
    """单个角色的队伍供给能力。"""

    key: str                      # 快照文件名（拼音 key，如 tifuluosi）
    name: str                     # 中文角色名（与队伍识别/baseline 键一致）
    attach_elements: tuple[str, ...]  # 可施加的元素附着
    combo_applier: bool           # 可施加连击（队伍共享层）


_cached_caps: dict[str, CharacterCapabilities] | None = None


def _parse_capabilities(key: str, payload: dict, raw_text: str) -> CharacterCapabilities:
    attach = tuple(dict.fromkeys(m.group(2) for m in _ATTACH_RE.finditer(raw_text)))
    return CharacterCapabilities(
        key=key,
        name=str(payload.get("name") or key),
        attach_elements=attach,
        combo_applier=bool(_COMBO_RE.search(raw_text)),
    )


def load_character_capabilities() -> dict[str, CharacterCapabilities]:
    """加载全部角色能力，返回 {中文角色名: CharacterCapabilities}。

    键与队伍识别名、damage_baseline.json 的 character 字段一致。
    """
    global _cached_caps
    if _cached_caps is not None:
        return _cached_caps

    result: dict[str, CharacterCapabilities] = {}
    if _SKILLS_DIR.is_dir():
        for path in sorted(_SKILLS_DIR.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            caps = _parse_capabilities(path.stem, payload, path.read_text(encoding="utf-8"))
            result[caps.name] = caps
    _cached_caps = result
    return result


def clear_capabilities_cache() -> None:
    """清除缓存（测试用 / 快照更新后调用）。"""
    global _cached_caps
    _cached_caps = None
