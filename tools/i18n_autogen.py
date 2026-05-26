"""
自动扫描 src 中的字面 OCR match（区分裸字符串和 re.compile），为每个源文件生成模块化 lang/<module>/zh_CN.json，且只写入对应字段：
- 来自 match="..." 的只生成 {"string": "..."}
- 来自 match=re.compile("...") 的只生成 {"pattern": "..."}

随后仅在源文件中替换匹配为 match=self.lang.<module>.<key>
仅生成简中文件（zh_CN）。
"""
import re
import json
from pathlib import Path
import hashlib

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'

# patterns to find: match="..." and match=re.compile("...")
pat_str = re.compile(r'match\s*=\s*"([^"]+)"')
pat_re = re.compile(r'match\s*=\s*re\.compile\(r?"([^"]+)"\)')

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
        s = m.group(1)
        if is_simple_text(s):
            strs.add(s)
    for m in pat_re.finditer(txt):
        s = m.group(1)
        if is_simple_text(s):
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
        s = m.group(1)
        key = mapping.get(('str', s))
        if key:
            return f'match=self.lang.{module}.{key}'
        return m.group(0)

    def repl_re(m):
        s = m.group(1)
        key = mapping.get(('re', s))
        if key:
            return f'match=self.lang.{module}.{key}'
        return m.group(0)

    new = pat_str.sub(repl_str, new)
    new = pat_re.sub(repl_re, new)

    if new != txt:
        f.write_text(new, encoding='utf-8')
        modified.append(str(f.relative_to(ROOT)))

print('Modified files:')
for m in modified:
    print(m)
print('\nAdded keys:')
for module, key, lit, kind in added_keys:
    print(f'{module} -> {key} ({kind}) => {lit}')
