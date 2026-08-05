# -*- coding: utf-8 -*-
"""生成 src/data/lang/_lang_typed.py 类型提示文件。

扫描 assets/lang/*.json，为 self.lang.<模块>.<key> 生成静态类型定义，
让 Pylance / VS Code 提供：
  - 自动补全：输入 self.lang.<模块>. 时列出该模块全部 key
  - 悬浮提示：hover key 时显示它在基准语言（zh_CN）下对应的值

节点类型映射：
  - string 节点 -> Literal["值"]          （运行时就是 str，值直接显示在类型里）
  - pattern 节点 -> re.Pattern[str]       （运行时是 re.Pattern，值显示在 docstring）

用法:
    python tools/gen_lang_stubs.py

生成的 src/data/lang/_lang_typed.py 由本脚本自动维护，请勿手改。
本脚本也会幂等地把 src/data/lang/__init__.py 里 LangAccessor 改为继承
_LangAccessorTyped（仅类型提示，不改变运行时行为）。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LANG_ROOT = REPO_ROOT / "assets" / "lang"
TYPED_OUT = REPO_ROOT / "src" / "data" / "lang" / "_lang_typed.py"
INIT_FILE = REPO_ROOT / "src" / "data" / "lang" / "__init__.py"

BASE_LOCALE = "zh_CN"
LOCALE_ORDER = ["zh_CN", "zh_TW", "en_US", "ja_JP", "ko_KR", "es_ES"]


def pascal_case(name: str) -> str:
    parts = re.split(r"[_\-]+", name)
    return "".join(p[:1].upper() + p[1:] for p in parts if p)


def _pick_value(locale_dict: dict) -> tuple[str, str] | None:
    """返回 (节点类型, 值)。优先 BASE_LOCALE，缺失时取第一个可用节点。"""
    for loc in [BASE_LOCALE, *LOCALE_ORDER]:
        node = locale_dict.get(loc)
        if isinstance(node, dict):
            if isinstance(node.get("string"), str):
                return "string", node["string"]
            if isinstance(node.get("pattern"), str):
                return "pattern", node["pattern"]
    for node in locale_dict.values():
        if isinstance(node, dict):
            if isinstance(node.get("string"), str):
                return "string", node["string"]
            if isinstance(node.get("pattern"), str):
                return "pattern", node["pattern"]
    return None


def _quote(s: str) -> str:
    """生成适合 Literal["..."] 的双引号字符串字面量。"""
    return json.dumps(s, ensure_ascii=False)


def _doc(s: str) -> str:
    """转义 docstring 文本，避免破坏三引号。"""
    return (
        s.replace("\\", "\\\\")
        .replace('"""', '\\"\\"\\"')
        .replace("\r", " ")
        .replace("\n", " ")
    )


def build_module_class(module_name: str, data: dict) -> str:
    cls = pascal_case(module_name) + "Module"
    lines = [f"class {cls}(_LangModuleBaseT):"]
    lines.append(f'    """{module_name} — OCR 语言节点（值取自 {BASE_LOCALE}）"""')
    for key, locale_dict in data.items():
        if not isinstance(locale_dict, dict):
            continue
        picked = _pick_value(locale_dict)
        if picked is None:
            continue
        node_type, value = picked
        if node_type == "pattern":
            ann = "re.Pattern[str]"
        else:
            ann = f"Literal[{_quote(value)}]"
        lines.append("")
        lines.append(f"    {key}: {ann}")
        lines.append(f'    """{_doc(value)}"""')
    return "\n".join(lines) + "\n"


def main() -> int:
    modules: list[tuple[str, dict]] = []
    for f in sorted(LANG_ROOT.glob("*.json")):
        try:
            data = json.load(f.open(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] 跳过 {f.name}: {exc}")
            continue
        if not isinstance(data, dict) or not data:
            continue
        modules.append((f.stem, data))

    if not modules:
        print("[error] 未找到任何 lang JSON 模块")
        return 1

    parts = [
        "# -*- coding: utf-8 -*-",
        '"""由 tools/gen_lang_stubs.py 自动生成，请勿手改。',
        "",
        "为 self.lang.<模块>.<key> 提供静态类型提示：",
        "  - 自动补全：输入 self.lang.<模块>. 时列出全部 key",
        "  - 悬浮提示：hover 显示该 key 在基准语言下的对应值",
        "",
        "string 节点 -> Literal[值]；pattern 节点 -> re.Pattern[str]（docstring 显示文本）。",
        '"""',
        "import re",
        "from typing import TYPE_CHECKING, Literal",
        "",
        "if TYPE_CHECKING:",
        "    from . import LangModule as _LangModuleBase",
        "    _LangModuleBaseT = _LangModuleBase",
        "else:",
        "    _LangModuleBaseT = object",
        "",
    ]

    for module_name, data in modules:
        parts.append(build_module_class(module_name, data))
        parts.append("")

    parts.append("")
    parts.append("class _LangAccessorTyped:")
    parts.append('    """self.lang 的类型化声明（仅类型提示，运行时由 __getattr__ 动态加载）"""')
    for module_name, _ in modules:
        cls = pascal_case(module_name) + "Module"
        parts.append(f"    {module_name}: {cls}")
    parts.append("")

    TYPED_OUT.write_text("\n".join(parts), encoding="utf-8")
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
    if changed:
        INIT_FILE.write_text(init_text, encoding="utf-8")
        print(f"[ok] 已更新 {INIT_FILE.relative_to(REPO_ROOT)}（LangAccessor 继承类型化基类）")
    else:
        print(f"[ok] {INIT_FILE.relative_to(REPO_ROOT)} 无需更新")

    return 0


if __name__ == "__main__":
    sys.exit(main())
