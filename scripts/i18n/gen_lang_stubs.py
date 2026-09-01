"""生成 src/data/lang/_lang_typed.py 类型提示文件。

扫描 assets/lang/*.json，为 self.lang.<模块>.<key> 生成静态类型定义，
让 Pylance / VS Code 提供：
  - 自动补全：输入 self.lang.<模块>. 时列出该模块全部 key
  - 悬浮提示：hover key 时显示它在基准语言（zh_CN）下对应的值

节点类型映射：
  - string 节点 -> str                （运行时按当前 UI 语言取值，故用 str，docstring 显示基准值）
  - pattern 节点 -> re.Pattern[str]   （运行时是 re.Pattern，值显示在 docstring）

用法:
    python scripts/i18n/gen_lang_stubs.py

生成的 src/data/lang/_lang_typed.py 由本脚本自动维护，请勿手改。
本脚本也会幂等地把 src/data/lang/__init__.py 里 LangAccessor 改为继承
_LangAccessorTyped（仅类型提示，不改变运行时行为）。
"""

from __future__ import annotations

import json
import keyword
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LANG_ROOT = REPO_ROOT / "assets" / "lang"
TYPED_OUT = REPO_ROOT / "src" / "data" / "lang" / "_lang_typed.py"
INIT_FILE = REPO_ROOT / "src" / "data" / "lang" / "__init__.py"

BASE_LOCALE = "zh_CN"
LOCALE_ORDER = ["zh_CN", "zh_TW", "en_US", "ja_JP", "ko_KR", "es_ES"]
DATA_ONLY_MODULES = {
    "effect_names",
    "yingtuo_stages",
}


def pascal_case(name: str) -> str:
    parts = re.split(r"[_\-]+", name)
    return "".join(p[:1].upper() + p[1:] for p in parts if p)


def _pick_value(locale_dict: dict) -> tuple[str, str] | None:
    """返回 (节点类型, 值)。优先 BASE_LOCALE，缺失时取第一个可用节点。"""

    def _from_node(node) -> tuple[str, str] | None:
        if not isinstance(node, dict):
            return None
        for node_type in ("string", "pattern"):
            value = node.get(node_type)
            if isinstance(value, str):
                return node_type, value
        return None

    ordered = dict.fromkeys([BASE_LOCALE, *LOCALE_ORDER])
    for loc in ordered:
        if picked := _from_node(locale_dict.get(loc)):
            return picked
    for node in locale_dict.values():
        if picked := _from_node(node):
            return picked
    return None


def _is_valid_attr(name: str) -> bool:
    """校验 name 是否可作 Python 属性名（合法标识符且非关键字）。"""
    return bool(name) and name.isidentifier() and not keyword.iskeyword(name)


def _doc(s: str) -> str:
    """转义 docstring 文本，避免破坏三引号。"""
    return s.replace("\\", "\\\\").replace('"""', '\\"\\"\\"').replace("\r", " ").replace("\n", " ")


def build_module_class(module_name: str, data: dict) -> tuple[str, int]:
    """生成单个模块类，返回 (类代码, 跳过的非法 key 数)。"""
    cls = pascal_case(module_name) + "Module"
    lines = [f"class {cls}(_LangModuleBaseT):"]
    lines.append(f'    """{module_name} — OCR 语言节点（值取自 {BASE_LOCALE}）"""')
    skipped = 0
    for key, locale_dict in data.items():
        if not isinstance(locale_dict, dict):
            continue
        if not _is_valid_attr(key):
            print(f"[warn] 跳过非法 key: {module_name}.{key}")
            skipped += 1
            continue
        picked = _pick_value(locale_dict)
        if picked is None:
            continue
        node_type, value = picked
        if node_type == "pattern":
            ann = "re.Pattern[str]"
        else:
            ann = "str"
        lines.append("")
        lines.append(f"    {key}: {ann}")
        lines.append(f'    """{_doc(value)}"""')
    lines.append("")
    return "\n".join(lines) + "\n", skipped


def main() -> int:
    modules: list[tuple[str, dict]] = []
    failed = False
    for f in sorted(LANG_ROOT.glob("*.json")):
        if f.stem in DATA_ONLY_MODULES:
            continue
        try:
            with f.open(encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:
            print(f"[warn] 跳过 {f.name}: {exc}")
            continue
        if not isinstance(data, dict) or not data:
            continue
        if not _is_valid_attr(f.stem):
            print(f"[warn] 跳过非法模块名: {f.name}")
            failed = True
            continue
        modules.append((f.stem, data))

    if not modules:
        print("[error] 未找到任何 lang JSON 模块")
        return 1

    parts = [
        '"""由 scripts/i18n/gen_lang_stubs.py 自动生成，请勿手改。',
        "",
        "为 self.lang.<模块>.<key> 提供静态类型提示：",
        "  - 自动补全：输入 self.lang.<模块>. 时列出全部 key",
        "  - 悬浮提示：hover 显示该 key 在基准语言下的对应值",
        "",
        "string 节点 -> str（运行时按当前 UI 语言取值，docstring 显示基准值）；",
        "pattern 节点 -> re.Pattern[str]（docstring 显示文本）。",
        '"""',
        "",
        "import re",
        "from typing import TYPE_CHECKING",
        "",
        "if TYPE_CHECKING:",
        "    from . import LangModule as _LangModuleBase",
        "",
        "    _LangModuleBaseT = _LangModuleBase",
        "else:",
        "    _LangModuleBaseT = object",
        "",
        "",
    ]

    for module_name, data in modules:
        block, skipped = build_module_class(module_name, data)
        parts.append(block.rstrip())
        parts.extend(("", ""))
        if skipped:
            failed = True

    parts.append("class _LangAccessorTyped:")
    parts.append('    """self.lang 的类型化声明（仅类型提示，运行时由 __getattr__ 动态加载）"""')
    parts.append("")
    for module_name, _ in modules:
        cls = pascal_case(module_name) + "Module"
        parts.append(f"    {module_name}: {cls}")
    parts.append("")

    TYPED_OUT.write_text("\n".join(parts).rstrip() + "\n", encoding="utf-8")
    print(f"[ok] 已生成 {TYPED_OUT.relative_to(REPO_ROOT)}（{len(modules)} 个模块）")

    # 幂等更新 __init__.py：LangAccessor 继承 _LangAccessorTyped
    init_text = INIT_FILE.read_text(encoding="utf-8")
    changed = False
    if "from ._lang_typed import _LangAccessorTyped" not in init_text:
        marker = "from typing import Any\n"
        if marker in init_text:
            init_text = init_text.replace(
                marker,
                marker + "\nfrom ._lang_typed import _LangAccessorTyped\n",
                1,
            )
            changed = True
        else:
            print("[warn] 未在 __init__.py 中找到插入点，请手动添加 import")
            failed = True
    if "class LangAccessor(_LangAccessorTyped):" not in init_text:
        if "class LangAccessor:" in init_text:
            init_text = init_text.replace(
                "class LangAccessor:",
                "class LangAccessor(_LangAccessorTyped):",
                1,
            )
            changed = True
        else:
            print("[warn] 未在 __init__.py 中找到 class LangAccessor，请手动修改")
            failed = True
    if changed:
        INIT_FILE.write_text(init_text, encoding="utf-8")
        print(f"[ok] 已更新 {INIT_FILE.relative_to(REPO_ROOT)}（LangAccessor 继承类型化基类）")
    else:
        print(f"[ok] {INIT_FILE.relative_to(REPO_ROOT)} 无需更新")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
