# -*- coding: utf-8 -*-
# ruff: noqa: UP009
"""验证合成数美指纹模块：载荷结构、字段改名加密、失败路径。"""

import base64
import json
import unittest
from io import BytesIO
from unittest import mock

from src.core import map_device_fingerprint as fp


class TestMapDeviceFingerprint(unittest.TestCase):
    def test_des_renames_and_passthrough(self):
        """规则表内字段改名为两字母键；box 改名不加密；未知键原样保留。"""
        result = fp._des({"appId": "default", "box": "", "protocol": 102})
        self.assertIn("xx", result)  # appId -> xx
        self.assertNotIn("appId", result)
        self.assertEqual(result["jf"], "")  # box -> jf（不改值）
        self.assertEqual(result["protocol"], 102)  # 不在表中，原样保留

    def test_des_deterministic(self):
        """同输入两次加密结果一致（ECB 确定性）。"""
        a = fp._des({"platform": "Win32"})
        b = fp._des({"platform": "Win32"})
        self.assertEqual(a["gm"], b["gm"])

    def test_build_registration_payload_structure(self):
        """注册载荷包含必需键，ep 为合法 base64，data 为 hex 密文。"""
        payload, uid = fp.build_registration_payload()
        for key in ("appId", "compress", "data", "encode", "ep", "organization", "os"):
            self.assertIn(key, payload)
        self.assertEqual(payload["compress"], 2)
        self.assertEqual(payload["encode"], 5)
        self.assertEqual(payload["organization"], fp._SM_CONFIG["organization"])
        base64.b64decode(payload["ep"])  # ep 可解码
        self.assertGreater(len(uid), 0)

    def test_mint_rejects_non_1100(self):
        """服务端返回非 1100 时抛出 RuntimeError。"""

        class _FakeResp:
            def read(self):
                return json.dumps({"code": 1902, "detail": {}}).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with mock.patch.object(fp.urllib.request, "urlopen", return_value=_FakeResp()):
            with self.assertRaises(RuntimeError) as ctx:
                fp.mint_device_id_synthetic()
            self.assertIn("1902", str(ctx.exception))

    def test_mint_returns_prefixed_device_id(self):
        """code=1100 时返回 B 前缀的 deviceId。"""

        class _FakeResp:
            def read(self):
                stream = BytesIO(
                    json.dumps(
                        {
                            "code": 1100,
                            "detail": {"deviceId": "x" * 32},
                        }
                    ).encode("utf-8")
                )
                return stream.read()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with mock.patch.object(fp.urllib.request, "urlopen", return_value=_FakeResp()):
            did = fp.mint_device_id_synthetic()
        self.assertEqual(did, "B" + "x" * 32)


if __name__ == "__main__":
    unittest.main()
