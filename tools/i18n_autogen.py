"""
自动扫描 src 中的 OCR 字面量，并为每个源文件生成模块化 lang/<module>/zh_CN.json。

规则：
- 来自裸字符串匹配的，只生成 {"string": "..."}
- 来自 re.compile("...") 的，只生成 {"pattern": "..."}

替换规则：
- match=...
- target_ocr_pattern=...
- success_match=...
- OCR 调用里静态列表/元组中的 re.compile("...")

不替换动态变量，例如 re.compile(area)、re.compile(str(i))。
仅生成简中文件（zh_CN）。
"""
import re
import json
from pathlib import Path
import hashlib

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'

# patterns to find: match="..." and match=re.compile("...")
pat_str = re.compile(r'\b(match|target_ocr_pattern|success_match)\s*=\s*"([^"]+)"')
pat_re = re.compile(r'\b(match|target_ocr_pattern|success_match)\s*=\s*re\.compile\(r?"([^"]+)"\)')
pat_compile = re.compile(r're\.compile\(r?"([^"]+)"\)')

OCR_HINTS = (
    'wait_ocr(',
    'wait_click_ocr(',
    'click_text(',
    'navigate_until_target(',
    'safe_back(',
    'target_ocr_pattern=',
    'success_match=',
    'match=',
)


def line_has_ocr_hint(text: str, pos: int) -> bool:
    start = text.rfind('\n', 0, pos) + 1
    end = text.find('\n', pos)
    if end == -1:
        end = len(text)
    window = text[start:end]
    if any(h in window for h in OCR_HINTS):
        return True
    # 看前后两行，覆盖多行参数列表
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

# skip if pattern contains regex metacharacters that indicate complex regex
def is_simple_text(s: str) -> bool:
    if re.search(r'\\|\(|\)|\[|\]|\?|\+|\*|\^|\$|\.|\||\{|\}', s):
        return False
    if s.strip() == '' or re.fullmatch(r'\d+', s.strip()):
        return False
    return True

# build key generator
def make_key(s: str) -> str:
    slug = re.sub(r"[^0-9a-zA-Z_]+", "_", s)
    slug = slug.strip('_')
    if slug:
        key = slug.lower()
        if key[0].isdigit():
            key = 'k_' + key
        return key
    h = hashlib.sha1(s.encode('utf-8')).hexdigest()[:8]
    return 'k_' + h

# collect literals per source file
files = list(SRC.rglob('*.py'))
per_file_literals = {}  # path -> {'str': set(), 're': set()}
for f in files:
    try:
        txt = f.read_text(encoding='utf-8')
    except Exception:
        continue
    strs = set()
    res = set()
    for m in pat_str.finditer(txt):
        s = m.group(2)
        if is_simple_text(s):
            strs.add(s)
    for m in pat_re.finditer(txt):
        s = m.group(2)
        if is_simple_text(s):
            res.add(s)
    for m in pat_compile.finditer(txt):
        s = m.group(1)
        if is_simple_text(s) and line_has_ocr_hint(txt, m.start()):
            res.add(s)
    if strs or res:
        per_file_literals[f] = {'str': strs, 're': res}

modified = []
added_keys = []
for f, groups in per_file_literals.items():
    module = f.stem
    lang_dir = ROOT / 'lang' / module
    lang_dir.mkdir(parents=True, exist_ok=True)
    zhf = lang_dir / 'zh_CN.json'
    zh = {}
    if zhf.exists():
        try:
            zh = json.load(zhf.open(encoding='utf-8'))
        except Exception:
            zh = {}

    mapping = {}
    used_keys = set(zh.keys())

    # process string literals
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

    # process regex literals
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

    # write zh file
    zhf.write_text(json.dumps(zh, ensure_ascii=False, indent=2), encoding='utf-8')

    # replace occurrences in that source file only
    txt = f.read_text(encoding='utf-8')
    new = txt

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
            return f'self.lang.{module}.{key}'
        return m.group(0)

    new = pat_str.sub(repl_str, new)
    new = pat_re.sub(repl_re, new)
    new = pat_compile.sub(repl_compile, new)

    if new != txt:
        f.write_text(new, encoding='utf-8')
        modified.append(str(f.relative_to(ROOT)))

print('Modified files:')
for m in modified:
    print(m)
print('\nAdded keys:')
for module, key, lit, kind in added_keys:
    print(f'{module} -> {key} ({kind}) => {lit}')
