#!/usr/bin/env python3
"""明日方舟：终末地 VFS 解密核心模块

协议来源：endGuaGua/EndfieldUnpacker (MIT)
- ChaCha20 (pycryptodome), 12-byte nonce
- BLC: nonce=blob[0:12], skip64 via decrypt(b'\\x00'*64), decrypt blob[12:], strip CRC32
- CHK (per-file): nonce=pack('<i',3)+pack('<q',iv_seed), skip64, decrypt
"""
import hashlib
import re
import struct
import zlib
from pathlib import Path
from typing import Optional

from Crypto.Cipher import ChaCha20

KEY = bytes.fromhex('E95B317AC4F828569D23A86BF271DCB53E846FA75C924D671DBA8E38F4CA52E1')
VFS_PROTO_VERSION = 3
BLOCK_HEAD_LEN = 12

GROUP_NAMES = {
    "42A8FCA6": "Table",
    "775A31D1": "JsonData",
    "19E3AE45": "Lua",
}


def decrypt_blc(blc_path: Path) -> bytes:
    """解密 BLC 文件，返回明文（去除 CRC32 尾部）"""
    data = blc_path.read_bytes()
    nonce = data[:BLOCK_HEAD_LEN]
    c = ChaCha20.new(key=KEY, nonce=nonce)
    c.decrypt(b'\x00' * 64)  # 跳过 64 字节（pycryptodome 无 seek）
    plain = c.decrypt(data[BLOCK_HEAD_LEN:])
    # CRC32 校验（最后 4 字节）
    if len(plain) >= 4:
        expected_crc = struct.unpack('<i', plain[-4:])[0]
        actual_crc = zlib.crc32(plain[:-4]) & 0xFFFFFFFF
        if expected_crc != actual_crc:
            print(f'  WARNING: CRC mismatch: expected {expected_crc:#x}, got {actual_crc:#x}')
        plain = plain[:-4]
    return plain


def per_file_decrypt(data: bytes, iv_seed: int) -> bytes:
    """单文件 ChaCha20 解密"""
    nonce = struct.pack('<i', VFS_PROTO_VERSION) + struct.pack('<q', iv_seed)
    c = ChaCha20.new(key=KEY, nonce=nonce)
    c.decrypt(b'\x00' * 64)  # 跳过 64 字节
    return c.decrypt(data)


def r32(plain, off):
    return struct.unpack_from('<i', plain, off)[0], off + 4

def r64(plain, off):
    return struct.unpack_from('<q', plain, off)[0], off + 8

def r16(plain, off):
    return struct.unpack_from('<H', plain, off)[0], off + 2

def r128(plain, off):
    return plain[off:off + 16], off + 16

def r8(plain, off):
    return plain[off], off + 1

def rstr(plain, off, length):
    return plain[off:off + length].decode('ascii', errors='replace'), off + length


def parse_blc_entries(blc_path: Path) -> list:
    """解析 BLC 文件，返回所有文件条目"""
    plain = decrypt_blc(blc_path)

    off = 0
    raw_version, off = r32(plain, off)
    if raw_version < 11:
        code_version = raw_version
        version, off = r32(plain, off)
    else:
        code_version = 3
    name_len, off = r16(plain, off)
    name, off = rstr(plain, off, name_len)
    dir_hash, off = r64(plain, off)
    file_cnt, off = r32(plain, off)
    chunks_len, off = r64(plain, off)
    block_type_val, off = r8(plain, off)
    chunk_count, off = r32(plain, off)

    entries = []
    for ci in range(chunk_count):
        chunk_md5, off = r128(plain, off)
        content_md5, off = r128(plain, off)
        length, off = r64(plain, off)
        chunk_bt_val, off = r8(plain, off)
        if code_version > 3:
            main_tag, off = r32(plain, off)
        file_count, off = r32(plain, off)
        for fi in range(file_count):
            fn_len, off = r16(plain, off)
            raw_fn, off = rstr(plain, off, fn_len)
            fn_hash, off = r64(plain, off)
            file_chunk_md5, off = r128(plain, off)
            file_data_md5, off = r128(plain, off)
            file_off, off = r64(plain, off)
            file_len, off = r64(plain, off)
            file_bt_val, off = r8(plain, off)
            use_encrypt_val, off = r8(plain, off)
            iv_seed = 0
            if use_encrypt_val:
                iv_seed, off = r64(plain, off)
            if code_version > 3:
                file_tag, off = r32(plain, off)

            # 清理文件名
            fn_clean = re.sub(r'[^\x20-\x7e/\\]', '', raw_fn)
            m = re.search(r'(Data/|Assets/)[A-Za-z0-9_./-]+', fn_clean)
            if m:
                fn_clean = m.group(0)
            for prefix in ['Assets/StreamingAssets/', 'Assets/', 'Data/']:
                if fn_clean.startswith(prefix):
                    fn_clean = fn_clean[len(prefix):]

            entries.append({
                'raw_name': raw_fn,
                'name': fn_clean,
                'chunk_md5': chunk_md5.hex().upper(),
                'file_data_md5': file_data_md5.hex().upper(),
                'file_off': file_off,
                'file_len': file_len,
                'encrypted': use_encrypt_val != 0,
                'iv_seed': iv_seed,
            })
    return entries


def decrypt_chk_file(chk_path: Path, file_off: int, file_len: int, iv_seed: int, encrypted: bool) -> bytes:
    """从 CHK 文件中解密单个文件"""
    file_data = chk_path.read_bytes()
    if encrypted:
        nonce = struct.pack('<i', VFS_PROTO_VERSION) + struct.pack('<q', iv_seed)
        c = ChaCha20.new(key=KEY, nonce=nonce)
        c.decrypt(b'\x00' * 64)
        return c.decrypt(file_data[file_off:file_off + file_len])
    return file_data[file_off:file_off + file_len]


def extract_file(vfs_root: Path, group_name: str, entry: dict, output_dir: Path) -> Optional[Path]:
    """从 VFS 中提取单个文件"""
    chk_path = vfs_root / group_name / f"{entry['chunk_md5']}.chk"
    if not chk_path.exists():
        return None

    try:
        data = decrypt_chk_file(
            chk_path, entry['file_off'], entry['file_len'],
            entry['iv_seed'], entry['encrypted']
        )
        # 验证 MD5
        actual_md5 = hashlib.md5(data).hexdigest().upper()
        if actual_md5 != entry['file_data_md5']:
            print(f"  MD5 mismatch for {entry['name']}")
            return None

        # 写入文件
        out_path = output_dir / entry['name']
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(data)
        return out_path
    except Exception as e:
        print(f"  Error extracting {entry['name']}: {e}")
        return None


def process_group(vfs_root: Path, group_name: str, output_dir: Path,
                  file_filter: str = '', max_entries: int = 0, dry_run: bool = False) -> dict:
    """处理一个 VFS group，提取文件"""
    blc_path = vfs_root / group_name / f"{group_name}.blc"
    if not blc_path.exists():
        return {"group": group_name, "status": "no_blc"}

    display = GROUP_NAMES.get(group_name, group_name)
    print(f"\n{'=' * 60}")
    print(f"Group: {display} ({group_name})")

    entries = parse_blc_entries(blc_path)
    print(f"  Total files: {len(entries)}")

    # 过滤
    if file_filter:
        entries = [e for e in entries if file_filter.lower() in e['name'].lower()]
        print(f"  After filter: {len(entries)}")

    if max_entries > 0:
        entries = entries[:max_entries]

    results = {"group": display, "total": len(entries), "extracted": 0, "failed": 0, "skipped": 0}

    for entry in entries:
        if dry_run:
            enc_tag = "[ENC]" if entry['encrypted'] else ""
            print(f"  {entry['name']}  len={entry['file_len']} {enc_tag}")
            results["extracted"] += 1
            continue

        out = extract_file(vfs_root, group_name, entry, output_dir)
        if out:
            results["extracted"] += 1
            if results["extracted"] % 100 == 0:
                print(f"  Extracted {results['extracted']}...")
        else:
            results["failed"] += 1

    print(f"  Extracted: {results['extracted']}, Failed: {results['failed']}")
    return results
