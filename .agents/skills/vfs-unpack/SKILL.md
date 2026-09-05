# VFS Unpack Skill

解密和提取明日方舟：终末地 VFS 游戏资源，包括 BLC 索引解密、CHK 数据解密、SparkBuffer 格式解析、I18nTextTable 多语言文本提取与对比。

## 适用场景

- 游戏更新后需要重新提取 i18n_texts 多语言文本
- 验证本地 JSON 文件与当前游戏版本的一致性
- 解包 Table/JsonData/Lua 等 VFS 资源
- 调试 VFS 加密协议或 SparkBuffer 格式
- 对比新旧版本翻译差异

## 前置条件

- Python 环境：`.venv`（通过 `uv` 管理）
- 依赖：`pycryptodome`（已在 `requirements.txt` 中）
- 游戏安装目录：`{游戏安装目录}\Endfield_Data\Persistent\VFS`
- 外部依赖：`decode_sparkbuffer.py`（来自 [endGuaGua/EndfieldUnpacker](https://github.com/endGuaGua/EndfieldUnpacker) MIT）

## 目录结构

```
scripts/vfs_unpack/
├── vfs_decrypt.py           # VFS 解密核心模块（BLC/CHK 解密、条目解析）
├── extract_i18n.py          # I18nTextTable 提取与对比脚本
├── decode_sparkbuffer.py    # SparkBuffer 二进制格式解析器（外部依赖）
└── README.md                # 本文档
```

## 快速开始

### 1. 提取所有语言的 i18n_texts

```bash
uv run --locked python scripts/vfs_unpack/extract_i18n.py
```

输出：`assets/data/i18n_texts/{BR,CN,DE,EN,FR,ID,IT,JP,KR,MX,RU,TC,TH,VN}.json`

### 2. 仅提取指定语言

```bash
uv run --locked python scripts/vfs_unpack/extract_i18n.py CN EN JP
```

### 3. 对比 VFS 与现有 JSON

```bash
uv run --locked python scripts/vfs_unpack/extract_i18n.py --compare
```

输出示例：
```
CN:
  VFS: 147,603  JSON: 147,602  Common: 147,593
  Value differences: 0
  Only in VFS: 10  Only in JSON: 9
```

### 4. 仅列出不提取（dry-run）

```bash
uv run --locked python scripts/vfs_unpack/extract_i18n.py --dry-run
```

## 完整操作流程

### 第一步：解包游戏资源

```bash
# 进入项目目录
cd /path/to/ok-end-field

# 试运行：查看将要提取的文件列表
uv run --locked python scripts/vfs_unpack/extract_i18n.py --dry-run

# 正式提取所有语言
uv run --locked python scripts/vfs_unpack/extract_i18n.py
```

### 第二步：验证提取结果

```bash
# 检查输出目录
Get-ChildItem assets/data/i18n_texts/*.json | Select-Object Name, @{N='Size(MB)';E={[math]::Round($_.Length/1MB, 2)}}

# 检查条目数（以CN为例）
python -c "import json; d=json.load(open('assets/data/i18n_texts/CN.json','r',encoding='utf-8')); print(f'CN条目数: {len(d)}')"
```

### 第三步：对比新旧版本

```bash
# 对比VFS与现有JSON
uv run --locked python scripts/vfs_unpack/extract_i18n.py --compare
```

### 第四步：对比翻译内容差异

```python
# 对比新旧版本的翻译文本
import json

# 读取旧版本（如果有merged_by_cn.json）
with open('assets/data/i18n_texts/merged_by_cn.json', 'r', encoding='utf-8') as f:
    old_data = json.load(f)

# 读取新版本
with open('assets/data/i18n_texts/CN.json', 'r', encoding='utf-8') as f:
    new_data = json.load(f)

# 提取旧版本的CN翻译文本
old_cn_texts = set()
for k, v in old_data.items():
    if isinstance(v, dict) and 'CN' in v:
        old_cn_texts.add(v['CN'])

# 提取新版本的CN翻译文本
new_cn_texts = set(new_data.values())

# 对比差异
only_old = old_cn_texts - new_cn_texts
only_new = new_cn_texts - old_cn_texts

print(f'仅旧版本有（被删除）: {len(only_old)}')
print(f'仅新版本有（新增）: {len(only_new)}')
```

### 第五步：提交更改

```bash
git add assets/data/i18n_texts/
git commit -m "chore: update i18n_texts from game v{版本号}"
```

## 数据格式说明

### 输出文件格式

| 文件 | 键格式 | 值格式 | 条目数 |
|------|--------|--------|--------|
| `{LANG}.json` | 16位十六进制ID | 单语言字符串 | 147,603 |
| `merged_by_cn.json` | 中文文本 | 多语言字典 | 81,348 |

### 键的转换

```
SparkBuffer 内部: signed i64 (如 -9223254181335467105)
JSON 文件: unsigned i64 的十六进制 (如 80006b30605cfb9f)
转换公式: unsigned = signed % 2^64
```

### 条目数差异解释

新版本（147,603条目）比旧版本（81,348条目）多，原因：
- 新版本用十六进制ID作为键，每个游戏元素有独立ID
- 旧版本用中文文本作为键，相同文本合并为一个条目
- 同一翻译文本可能被多个游戏元素引用（如"数百"被2691个ID引用）

## 游戏更新后操作流程

1. **更新游戏**：通过官方启动器更新到最新版本
2. **验证一致性**：
   ```bash
   uv run --locked python scripts/vfs_unpack/extract_i18n.py --compare
   ```
3. **如有差异，重新提取**：
   ```bash
   uv run --locked python scripts/vfs_unpack/extract_i18n.py
   ```
4. **提交更改**：
   ```bash
   git add assets/data/i18n_texts/
   git commit -m "chore: update i18n_texts from game v{版本号}"
   ```

## VFS 加密协议

### 密钥

```
Hex: E95B317AC4F828569D23A86BF271DCB53E846FA75C924D671DBA8E38F4CA52E1
Base64: 6VsxesT4KFad6Ihr8nHctT6Eb6dckk1nHbqOOPTKUuE=
```

### ChaCha20 参数

- 算法：ChaCha20 (IETF, 12-byte nonce)
- 库：pycryptodome (`Crypto.Cipher.ChaCha20`)
- Nonce 长度：12 bytes
- 前缀跳过：64 bytes（通过 `c.decrypt(b'\x00' * 64)` 实现）

### BLC 解密

```
nonce = blob[0:12]
encrypted = blob[12:]
cipher.decrypt(b'\x00' * 64)  # 跳过 64 字节
plain = cipher.decrypt(encrypted)
plain = plain[:-4]  # 去除 CRC32
```

### CHK 单文件解密

```
nonce = pack('<i', 3) + pack('<q', iv_seed)
cipher.decrypt(b'\x00' * 64)
plain = cipher.decrypt(file_data)
```

## BLC 二进制格式

### 头部

| 字段 | 类型 | 说明 |
|------|------|------|
| raw_version | i32 | 格式版本 (当前=4) |
| version | i32 | 仅 raw_version < 11 时存在 |
| name_len | u16 | Block 名称长度 |
| name | str | Block 名称 (如 "Table", "JsonData", "Lua") |
| dir_hash | i64 | 目录哈希 |
| file_cnt | i32 | 文件总数 |
| chunks_len | i64 | chunks 数据总长 |
| block_type | u8 | 17=Lua, 18=Table, 19=JsonData |
| chunk_count | i32 | chunk 数量 |

### Chunk 条目

| 字段 | 类型 | 说明 |
|------|------|------|
| chunk_md5 | 16B | 对应 CHK 文件名 (MD5 hex) |
| content_md5 | 16B | 内容 MD5 |
| length | i64 | 数据长度 |
| block_type | u8 | Block 类型 |
| main_tag | i32 | 仅 code_version > 3 |
| file_count | i32 | 该 chunk 内的文件数 |

### File 条目

| 字段 | 类型 | 说明 |
|------|------|------|
| fn_len | u16 | 文件名长度 |
| fn | str | 文件名 |
| fn_hash | i64 | 文件名哈希 |
| file_chunk_md5 | 16B | 所属 chunk MD5 |
| file_data_md5 | 16B | 文件内容 MD5 |
| file_off | i64 | 在 CHK 中的偏移量 |
| file_len | i64 | 文件数据长度 |
| block_type | u8 | Block 类型 |
| use_encrypt | u8 | 是否加密 (0/1) |
| iv_seed | i64 | 仅 use_encrypt=1 时存在 |
| file_tag | i32 | 仅 code_version > 3 |

## SparkBuffer 格式

I18nTextTable 使用 SparkBuffer 二进制格式：

1. 头部：3 个 i32 偏移（type_def, root_def, data）
2. 类型定义区：Bean/Enum 类型描述
3. 根定义区：根类型（Map）+ 键值类型
4. 数据区：实际键值对

键的表示：
- SparkBuffer 内部：signed i64（如 `-9223254181335467105`）
- JSON 文件：unsigned i64 的十六进制（如 `80006b30605cfb9f`）
- 转换：`unsigned = signed % 2^64`

## VFS 目录结构

```
Endfield_Data/Persistent/VFS/
├── 42A8FCA6/          ← Table group
│   ├── 42A8FCA6.blc   ← BLC 索引 (85KB, 724 files, 59 chunks)
│   └── *.chk          ← CHK 数据块 (26 files)
├── 775A31D1/          ← JsonData group
│   ├── 775A31D1.blc   ← BLC 索引 (12MB, 95249 files, 112 chunks)
│   └── *.chk          ← CHK 数据块 (12 files)
└── 19E3AE45/          ← Lua group
    ├── 19E3AE45.blc   ← BLC 索引 (178KB, 1339 files, 1 chunk)
    └── *.chk          ← CHK 数据块 (1 file)
```

## 验证结果 (2026-09)

| Group | 总文件 | 验证通过 | 缺失CHK | 说明 |
|-------|--------|---------|---------|------|
| **Lua** | 1,339 | **1,339 (100%)** | 0 | 完整 |
| **Table** | 724 | **396 (55%)** | 328 | 缺失 CHK 在基础安装包中 |
| **JsonData** | 95,249 | **2,006 (2%)** | 93,243 | 缺失 CHK 在基础安装包中 |

- 所有已解密文件 MD5 校验 **100% 通过**，零失败
- `missing_chk` 文件的 CHK 数据块在基础安装中，Persistent/VFS 仅包含热更新差异

## 探索过程记录

本目录包含两个会话的完整探索过程记录，详细描述了如何一步步发现和解决解包问题：

| 文件 | 内容 | 时长 |
|------|------|------|
| `session_20260805.md` | 8月5日会话原始记录（GitHub Copilot） | ~6小时 |
| `session_20260902.md` | 9月2日会话原始记录（mimo-v2.5-free） | ~90分钟 |
| `exploration_detailed.md` | **详细探索过程整理**（推荐） | - |

### 8月5日会话关键突破

1. **Bug修复**: Lua提取0条目 → 精确诊断tail位置（dot+ext_len）
2. **文档错误**: base64密钥抄写错误 → 对比字节形式修正
3. **深入探索**: DungeonTable结构 → 全局搜索I18nText key
4. **Streaming数据**: VFSAES加密 → 逆向解密算法
5. **地形数据**: TRET压缩格式 → 分析块格式

### 9月2日会话关键突破

1. **索引位置**: 假设在尾部失败 → 阅读文档发现"在中部"
2. **编码问题**: PowerShell乱码 → 写入文件用Python读取
3. **格式转换**: signed→unsigned hex → 计算公式

### 核心经验

1. **文档是关键**: 完全基于文档，没有读外部网络
2. **验证很重要**: 每一步都要验证（文件大小、内容格式、校验和）
3. **Bug是常态**: 第一次几乎都会失败，需要精确诊断
4. **逆向思维**: 当解析失败时，重新阅读文档，找关键句
5. **工具选择**: PowerShell有编码问题，Python更可靠

## 参考

- [endGuaGua/EndfieldUnpacker](https://github.com/endGuaGua/EndfieldUnpacker) — 主要参考实现 (MIT)
- [fluffield/fluffy-dumper](https://git.nekolab.app/fluffield/fluffy-dumper) — Rust 原版实现
- [EIHRTeam/EndfieldStudio](https://github.com/EIHRTeam/EndfieldStudio) — C# 实现
