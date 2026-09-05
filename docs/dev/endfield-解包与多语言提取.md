# 终末地 VFS 解包与完整性验证

> 本文档记录如何解密和验证明日方舟：终末地的 VFS (Virtual File System) 游戏资源。

## 概述

终末地使用自定义 VFS 存储游戏资源，包含两层加密：

| 层级 | 文件 | 加密方式 | 说明 |
|------|------|---------|------|
| BLC 索引 | `*.blc` | ChaCha20 | 文件目录索引，包含文件名、偏移量、MD5 校验 |
| CHK 数据 | `*.chk` | ChaCha20 (per-file) | 实际文件数据块，按需解密 |

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

## 加密协议

### 密钥

```
Hex: E95B317AC4F828569D23A86BF271DCB53E846FA75C924D671DBA8E38F4CA52E1
Base64: 6VsxesT4KFad6Ihr8nHctT6Eb6dckk1nHbqOOPTKUuE=
```

### ChaCha20 参数

- **算法**: ChaCha20 (IETF, 12-byte nonce)
- **库**: pycryptodome (`Crypto.Cipher.ChaCha20`)
- **Nonce 长度**: 12 bytes
- **前缀跳过**: 64 bytes

> ⚠️ **pycryptodome 没有 `ChaCha20.seek()` 方法**。跳过前 64 字节的方式是解密 64 字节的零数据：
> ```python
> c = ChaCha20.new(key=KEY, nonce=nonce)
> c.decrypt(b'\x00' * 64)  # 跳过 64 字节
> plain = c.decrypt(data)    # 解密实际内容
> ```

### BLC 解密

```
nonce = blob[0:12]
encrypted = blob[12:]
cipher.seek(64)  →  cipher.decrypt(b'\x00' * 64)
plain = cipher.decrypt(encrypted)
# 去除尾部 CRC32 (4 bytes)
plain = plain[:-4]
```

### CHK 单文件解密

```
nonce = pack('<i', 3) + pack('<q', iv_seed)   # version=3 固定
cipher.seek(64)  →  cipher.decrypt(b'\x00' * 64)
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

## 验证结果 (2026-09)

| Group | 总文件 | 验证通过 | 缺失CHK | 说明 |
|-------|--------|---------|---------|------|
| **Lua** | 1,339 | **1,339 (100%)** | 0 | 完整 |
| **Table** | 724 | **396 (55%)** | 328 | 缺失 CHK 在基础安装包中 |
| **JsonData** | 95,249 | **2,006 (2%)** | 93,243 | 缺失 CHK 在基础安装包中 |

- 所有已解密文件 MD5 校验 **100% 通过**，零失败
- `missing_chk` 文件的 CHK 数据块在基础安装中，Persistent/VFS 仅包含热更新差异

## 使用方式

```bash
# 试运行：列出所有文件
uv run --locked python tmp/unpack_vfs.py 0

# 限制条目数（调试用）
uv run --locked python tmp/unpack_vfs.py 10
```

## 参考

- [endGuaGua/EndfieldUnpacker](https://github.com/endGuaGua/EndfieldUnpacker) — 主要参考实现 (MIT)
- [fluffield/fluffy-dumper](https://git.nekolab.app/fluffield/fluffy-dumper) — Rust 原版实现
- [EIHRTeam/EndfieldStudio](https://github.com/EIHRTeam/EndfieldStudio) — C# 实现
