"""
自动扫描 src 中的 OCR 字面量，并为每个源文件生成模块化 assets/lang/<module>/zh_CN.json。

已支持：
- match="xxx"
- target_ocr_pattern="xxx"
- success_match="xxx"
- re.compile("xxx")
- self.to_model_area(area, "xxx")

已排除：
- login_mixin.py（黑名单）

规则：
- string → {"string": "..."}
- regex → {"pattern": "..."}
"""

import re
import json
from pathlib import Path
import hashlib

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'

# -----------------------------
# 黑名单（关键）
# -----------------------------
FILE_BLACKLIST = {
    "login_mixin.py",
}

# -----------------------------
# regex patterns
# -----------------------------
pat_str = re.compile(r'\b(match|target_ocr_pattern|success_match)\s*=\s*"([^"]+)"')
pat_re = re.compile(r'\b(match|target_ocr_pattern|success_match)\s*=\s*re\.compile\(r?"([^"]+)"\)')
pat_compile = re.compile(r're\.compile\(r?"([^"]+)"\)')

# 新增：函数参数 OCR
pat_to_model_area = re.compile(
    r'self\.to_model_area\(\s*[^,]+,\s*"([^"]+)"\s*\)'
)

OCR_HINTS = (
    'wait_ocr(',
    'wait_click_ocr(',
    'click_text(',
    'navigate_until_target(',
    'safe_back(',
    'target_ocr_pattern=',
    'success_match=',
    'match=',
    'to_model_area(',
)

# -----------------------------
# utils
# -----------------------------
def line_has_ocr_hint(text: str, pos: int) -> bool:
    start = text.rfind('\n', 0, pos) + 1
    end = text.find('\n', pos)
    if end == -1:
        end = len(text)

    window = text[start:end]
    if any(h in window for h in OCR_HINTS):
        return True

    prev_start = text.rfind('\n', 0, start - 2)
    if prev_start == -1:
        prev_start = 0
    else:
        prev_start += 1

    next_end = text.find('\n', end + 1)
    if next_end == -1:
        next_end = len(text)

    surrounding = text[prev_start:next_end]
    return any(h in surrounding for h in OCR_HINTS)


def is_simple_text(s: str) -> bool:
    if not s or s.strip() == '':
        return False
    if re.fullmatch(r'\d+', s.strip()):
        return False

    # 过滤复杂 regex
    if re.search(r'[\\\(\)\[\]\?\+\*\^\$\.\|\{\}]', s):
        return False

    return True


def make_key(s: str) -> str:
    slug = re.sub(r"[^0-9a-zA-Z_]+", "_", s).strip('_')
    if slug:
        key = slug.lower()
        if key[0].isdigit():
            key = 'k_' + key
        return key

    h = hashlib.sha1(s.encode('utf-8')).hexdigest()[:8]
    return 'k_' + h


# -----------------------------
# scan files
# -----------------------------
files = [
    f for f in SRC.rglob('*.py')
    if f.name not in FILE_BLACKLIST
]

per_file_literals = {}

for f in files:
    try:
        txt = f.read_text(encoding='utf-8')
    except Exception:
        continue

    strs = set()
    res = set()

    # match string
    for m in pat_str.finditer(txt):
        s = m.group(2)
        if is_simple_text(s):
            strs.add(s)

    # re.compile match
    for m in pat_re.finditer(txt):
        s = m.group(2)
        if is_simple_text(s):
            res.add(s)

    # compile OCR
    for m in pat_compile.finditer(txt):
        s = m.group(1)
        if is_simple_text(s) and line_has_ocr_hint(txt, m.start()):
            res.add(s)

    # function OCR
    for m in pat_to_model_area.finditer(txt):
        s = m.group(1)
        if is_simple_text(s):
            strs.add(s)

    if strs or res:
        per_file_literals[f] = {'str': strs, 're': res}


# -----------------------------
# replace phase
# -----------------------------
modified = []
added_keys = []

for f, groups in per_file_literals.items():
    module = f.stem
    lang_dir = ROOT / 'assets' / 'lang' / module
    lang_dir.mkdir(parents=True, exist_ok=True)

    zhf = lang_dir / 'zh_CN.json'

    try:
        zh = json.load(zhf.open(encoding='utf-8')) if zhf.exists() else {}
    except Exception:
        zh = {}

    mapping = {}
    used_keys = set(zh.keys())

    # string keys
    for s in sorted(groups['str']):
        key = make_key(s)
        base = key
        i = 1
        while key in used_keys:
            key = f"{base}_{i}"
            i += 1

        zh[key] = {"string": s}
        mapping[('str', s)] = key
        used_keys.add(key)
        added_keys.append((module, key, s, 'string'))

    # regex keys
    for s in sorted(groups['re']):
        key = make_key(s)
        base = key
        i = 1
        while key in used_keys:
            key = f"{base}_{i}"
            i += 1

        zh[key] = {"pattern": s}
        mapping[('re', s)] = key
        used_keys.add(key)
        added_keys.append((module, key, s, 'pattern'))

    zhf.write_text(json.dumps(zh, ensure_ascii=False, indent=2), encoding='utf-8')

    txt = f.read_text(encoding='utf-8')
    new = txt

    # -----------------------------
    # replacers
    # -----------------------------
    def repl_str(m):
        s = m.group(2)
        key = mapping.get(('str', s))
        if key:
            return f'{m.group(1)}=self.lang.{module}.{key}'
        return m.group(0)

    def repl_re(m):
        s = m.group(2)
        key = mapping.get(('re', s))
        if key:
            return f'{m.group(1)}=self.lang.{module}.{key}'
        return m.group(0)

    def repl_compile(m):
        s = m.group(1)
        if not is_simple_text(s):
            return m.group(0)
        if not line_has_ocr_hint(new, m.start()):
            return m.group(0)

        key = mapping.get(('re', s)) or mapping.get(('str', s))
        if key:
            return f're.compile(self.lang.{module}.{key})'
        return m.group(0)

    def repl_to_model_area(m):
        s = m.group(1)
        key = mapping.get(('str', s))
        if key:
            return f'self.to_model_area(area, self.lang.{module}.{key})'
        return m.group(0)

    # apply
    new = pat_str.sub(repl_str, new)
    new = pat_re.sub(repl_re, new)
    new = pat_compile.sub(repl_compile, new)
    new = pat_to_model_area.sub(repl_to_model_area, new)

    if new != txt:
        f.write_text(new, encoding='utf-8')
        modified.append(str(f.relative_to(ROOT)))

# -----------------------------
# report
# -----------------------------
print("Modified files:")
for m in modified:
    print(m)

print("\nAdded keys:")
for module, key, lit, kind in added_keys:
    print(f"{module} -> {key} ({kind}) => {lit}")