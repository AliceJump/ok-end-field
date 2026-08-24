# -*- coding: utf-8 -*-
# ruff: noqa: UP009
"""合成数美(SMSdk)设备指纹并注册，纯 Python 铸造地图 dId。

算法移植自 NoelZong/skland-auto-sign 的 SecuritySm 实现（经本地
auto-checkin 工具实测可用，与本项目同 organization/公钥/端点）。
独立成文件的原因：字段映射表与指纹模板可能随数美前端版本变化，
后续调整只改这里，不影响 ``map_device_id`` 的调用方。

流程（与官方 SDK 等价）：
1. 构造伪浏览器环境档案（UA/canvas 指纹/分辨率/时区等固定模板）；
2. 已知字段按 :data:`_DES_RULE` 逐项 3DES-ECB 加密并改名为两字母键；
3. 整体 gzip+b64 后用 AES-128-CBC 加密
   （key=md5(uid)[:16]，iv=``0102030405060708``，ZeroPadding）；
4. uid 用官方 RSA 公钥加密作为 ep 信封；
5. POST ``https://fp-it.portal101.cn/deviceprofile/v4``，
   返回 code=1100 表示签发成功，取 ``B + detail.deviceId``。

每次铸造的 uid/smid/vpw 均随机生成，无注册载荷次数上限。
"""
import base64
import gzip
import hashlib
import json
import time
import urllib.request
import uuid
import warnings

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
from cryptography.hazmat.primitives.ciphers.algorithms import AES

try:  # cryptography 48+ 将 TripleDES 移入 decrepit
    from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
except ImportError:  # pragma: no cover - 兼容旧版本
    from cryptography.hazmat.primitives.ciphers.algorithms import TripleDES
from cryptography.hazmat.primitives.ciphers.base import Cipher
from cryptography.hazmat.primitives.ciphers.modes import CBC, ECB

DEVICEPROFILE_URL = "https://fp-it.portal101.cn/deviceprofile/v4"
MAP_PAGE_URL = "https://game.skland.com/map/endfield"

_SM_CONFIG = {
    "organization": "UWXspnCCJN4sfYlNfqps",
    "appId": "default",
    # 官方地图页 _smConf 内嵌的 RSA 公钥（公开静态资源）
    "publicKey": (
        "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCmxMNr7n8ZeT0tE1R9j/mPixoinPkeM+k4VGIn"
        "/s0k7N5rJAfnZ0eMER+QhwFvshzo0LNmeUkpR8uIlU/GEVr8mN28sKmwd2gpygqj0ePnBmOW4v0Z"
        "VwbSYK+izkhVFk2V/doLoMbWy6b+UnA8mkjvg0iYWRByfRsK2gdl7llqCwIDAQAB"
    ),
}

_PK = serialization.load_der_public_key(base64.b64decode(_SM_CONFIG["publicKey"]))

# 已知字段的 3DES 加密键与改名映射，逆向自官方 SDK。
# 注意：这些 8 字节键是公开的协议常量（与上游开源实现
# NoelZong/skland-auto-sign 等完全一致），仅用于指纹报文的混淆格式，
# 不用于保护任何数据，因此不属于"密钥/凭据"类敏感信息。
# 注意：这些键值不是敏感凭据，而是数美 SDK 公开前端脚本中的固定
# 协议常量，与下方 RSA 公钥同性质，任何人均可从公开静态资源反编译
# 得到；仅用于构造合法报文格式，不提供任何保密性，故随源码提交。
_DES_RULE = {
    "appId": {"key": "uy7mzc4h", "name": "xx"},
    "canvas": {"key": "snrn887t", "name": "yk"},
    "clientSize": {"key": "cpmjjgsu", "name": "zx"},
    "organization": {"key": "78moqjfc", "name": "dp"},
    "os": {"key": "je6vk6t4", "name": "pj"},
    "platform": {"key": "pakxhcd2", "name": "gm"},
    "plugins": {"key": "v51m3pzl", "name": "kq"},
    "pmf": {"key": "2mdeslu3", "name": "vw"},
    "referer": {"key": "y7bmrjlc", "name": "ab"},
    "res": {"key": "whxqm2a7", "name": "hf"},
    "rtype": {"key": "x8o2h2bl", "name": "lo"},
    "sdkver": {"key": "9q3dcxp2", "name": "sc"},
    "status": {"key": "2jbrxxw4", "name": "an"},
    "subVersion": {"key": "eo3i2puh", "name": "ns"},
    "svm": {"key": "fzj3kaeh", "name": "qr"},
    "time": {"key": "q2t3odsk", "name": "nb"},
    "timezone": {"key": "1uv05lj5", "name": "as"},
    "tn": {"key": "x9nzj1bp", "name": "py"},
    "trees": {"key": "acfs0xo4", "name": "pi"},
    "ua": {"key": "k92crp1t", "name": "bj"},
    "url": {"key": "y95hjkoo", "name": "cf"},
    "vpw": {"key": "r9924ab5", "name": "ca"},
}
# 只改名不加密的字段
_DES_RENAME = {"box": "jf"}

# 固定的伪浏览器环境模板（大量实现共用该模板；被批量风控时调整这里）
_BROWSER_ENV = {
    "plugins": (
        "MicrosoftEdgePDFPluginPortableDocumentFormatinternal-pdf-viewer1,"
        "MicrosoftEdgePDFViewermhjfbmdgcfjbbpaeojofohoefgiehjai1"
    ),
    "ua": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0"
    ),
    "canvas": "259ffe69",
    "timezone": -480,
    "platform": "Win32",
    "url": "https://www.skland.com/",
    "referer": "",
    "res": "1920_1080_24_1.25",
    "clientSize": "0_0_1080_1920_1920_1080_1920_1080",
    "status": "0011",
}


def _des(o: dict) -> dict:
    """按规则表逐项 3DES-ECB 加密并改名；不在表内的字段原样保留。"""
    result: dict = {}
    for key, value in o.items():
        rule = _DES_RULE.get(key)
        if rule:
            # 单键 8 字节 3DES 是官方 SDK 的既有行为，忽略 cryptography 弃用告警
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                cipher = Cipher(TripleDES(rule["key"].encode("utf-8")), ECB())
            data = str(value).encode("utf-8") + b"\x00" * 8
            result[rule["name"]] = base64.b64encode(
                cipher.encryptor().update(data)
            ).decode("utf-8")
        elif key in _DES_RENAME:
            result[_DES_RENAME[key]] = value
        else:
            result[key] = value
    return result


def _aes(v: bytes, k: bytes) -> str:
    """AES-128-CBC(ZeroPadding, iv=0102030405060708)，输出 hex。"""
    cipher = Cipher(AES(k), CBC(b"0102030405060708"))
    v += b"\x00"
    while len(v) % 16 != 0:
        v += b"\x00"
    return cipher.encryptor().update(v).hex()


def _gzip_b64(o: dict) -> bytes:
    stream = gzip.compress(json.dumps(o, ensure_ascii=False).encode("utf-8"), 2, mtime=0)
    return base64.b64encode(stream)


def _get_tn(o: dict) -> str:
    """完整性校验串：按键排序拼接所有值（数值乘 10000，dict 递归）。"""
    parts: list[str] = []
    for key in sorted(o.keys()):
        value = o[key]
        if isinstance(value, (int, float)):
            value = str(value * 10000)
        elif isinstance(value, dict):
            value = _get_tn(value)
        parts.append(value)
    return "".join(parts)


def _get_smid() -> str:
    """生成长度合规的 smid：时间戳 + 随机 md5 + 校验尾。"""
    t = time.localtime()
    stamp = f"{t.tm_year:04d}{t.tm_mon:02d}{t.tm_mday:02d}{t.tm_hour:02d}{t.tm_min:02d}{t.tm_sec:02d}"
    # md5 在此仅用于生成随机标识与校验尾，非安全哈希用途，协议规定算法
    v = stamp + hashlib.md5(str(uuid.uuid4()).encode()).hexdigest() + "00"  # NOSONAR
    tail = hashlib.md5(("smsk_web_" + v).encode()).hexdigest()[:14]  # NOSONAR
    return v + tail + "0"


def build_registration_payload() -> tuple[dict, bytes]:
    """构造一份全新注册载荷。

    Returns:
        ``(payload_json_dict, uid_bytes)``：uid 是被 RSA 信封加密的原始值，
        仅用于测试断言与调试，正常铸造流程不需要保存。
    """
    uid = str(uuid.uuid4()).encode("utf-8")
    # md5 派生 AES 密钥是数美协议的规定算法，非安全哈希用途
    pri_id = hashlib.md5(uid).hexdigest()[:16].encode("utf-8")  # NOSONAR
    ep = base64.b64encode(_PK.encrypt(uid, rsa_padding.PKCS1v15())).decode("utf-8")

    now_ms = int(time.time() * 1000)
    profile = {
        **_BROWSER_ENV,
        "vpw": str(uuid.uuid4()),
        "svm": now_ms,
        "trees": str(uuid.uuid4()),
        "pmf": now_ms,
        "protocol": 102,
        "organization": _SM_CONFIG["organization"],
        "appId": _SM_CONFIG["appId"],
        "os": "web",
        "version": "3.0.0",
        "sdkver": "3.0.0",
        "box": "",
        "rtype": "all",
        "smid": _get_smid(),
        "subVersion": "1.0.0",
        "time": 0,
    }
    # tn 是数美协议要求的完整性校验串，算法固定为 MD5，非安全用途
    profile["tn"] = hashlib.md5(_get_tn(profile).encode()).hexdigest()  # NOSONAR

    payload = {
        "appId": "default",
        "compress": 2,
        "data": _aes(_gzip_b64(_des(profile)), pri_id),
        "encode": 5,
        "ep": ep,
        "organization": _SM_CONFIG["organization"],
        "os": "web",
    }
    return payload, uid


def mint_device_id_synthetic(timeout: float = 15.0) -> str:
    """合成设备指纹并向数美注册，返回形如 ``B<deviceId>`` 的 dId。

    失败（网络异常/非 1100 响应/未签发）抛出异常，由调用方回退到下一条路径。
    """
    payload, _uid = build_registration_payload()
    req = urllib.request.Request(
        DEVICEPROFILE_URL,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json;charset=UTF-8",
            "User-Agent": "Mozilla/5.0 ok-ef map websocket client",
            "Origin": MAP_PAGE_URL.rsplit("/", 2)[0],
            "Referer": MAP_PAGE_URL,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    if result.get("code") != 1100:
        raise RuntimeError(f"数美合成指纹注册失败: {result}")
    device_id = str((result.get("detail") or {}).get("deviceId") or "").strip()
    if len(device_id) < 16:
        raise RuntimeError(f"数美合成指纹未签发有效 deviceId: {result}")
    return "B" + device_id
