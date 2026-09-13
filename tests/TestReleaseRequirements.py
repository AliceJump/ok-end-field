"""发布用 requirements.txt 必须与 uv.lock 的解析结果一致。

requirements.txt 由 `scripts/release/gen_release_requirements.py` 从 uv.lock 导出
（会裁剪 pyside6 / pyside6-addons）。改了依赖却忘记重新生成时，发布包会装到与
开发环境不一致的版本——PR #371 就踩过一次。

CI 有 `--check` 步骤，这里再补一道，让它在普通测试里也能被抓到。
重新生成：
    uv run --locked --with packaging python scripts/release/gen_release_requirements.py
"""

import re
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.release.gen_release_requirements import marker_applies

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "requirements.txt"
LOCK = ROOT / "uv.lock"

_PIN_RE = re.compile(r"^([A-Za-z0-9._-]+)==([^\s;]+)")


def _normalize(name: str) -> str:
    return name.lower().replace("_", "-")


def _pinned_versions(text: str) -> dict[str, str]:
    """从 requirements.txt 取出 pkg==version（跳过注释与 via 行）。"""
    result = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#") or line.startswith(" "):
            continue
        match = _PIN_RE.match(line)
        if match:
            result[_normalize(match.group(1))] = match.group(2)
    return result


def _locked_versions(text: str) -> dict[str, str]:
    """从 uv.lock 取出 Windows 发布环境的每个包解析版本。"""
    packages = tomllib.loads(text)["package"]
    return {
        _normalize(package["name"]): package["version"]
        for package in packages
        if any(
            marker_applies(marker)
            for marker in package.get("resolution-markers", [None])
        )
    }


class ReleaseRequirementsMatchLockTestCase(unittest.TestCase):
    def test_locked_versions_uses_windows_resolution_markers(self):
        lock = """\
[[package]]
name = "platform-package"
version = "2.0.0"
resolution-markers = ["sys_platform == 'win32'"]

[[package]]
name = "platform-package"
version = "1.0.0"
resolution-markers = ["sys_platform == 'darwin'"]
"""

        with patch(
            f"{__name__}.marker_applies",
            side_effect=lambda marker: marker == "sys_platform == 'win32'",
        ):
            self.assertEqual(_locked_versions(lock), {"platform-package": "2.0.0"})

    def test_pinned_versions_match_uv_lock(self):
        self.assertTrue(REQUIREMENTS.exists(), f"缺少文件: {REQUIREMENTS}")
        self.assertTrue(LOCK.exists(), f"缺少文件: {LOCK}")

        pinned = _pinned_versions(REQUIREMENTS.read_text(encoding="utf-8"))
        locked = _locked_versions(LOCK.read_text(encoding="utf-8"))
        self.assertTrue(pinned, "requirements.txt 里没有解析到任何钉版条目")

        missing = sorted(pkg for pkg in pinned if pkg not in locked)
        self.assertEqual(missing, [], f"uv.lock 中找不到这些包: {missing}")

        mismatched = [
            f"  {pkg}: requirements.txt={ver} / uv.lock={locked[pkg]}"
            for pkg, ver in sorted(pinned.items())
            if locked[pkg] != ver
        ]
        self.assertEqual(
            mismatched,
            [],
            "requirements.txt 与 uv.lock 版本不一致，请重新生成：\n"
            "  uv run --locked --with packaging python "
            "scripts/release/gen_release_requirements.py\n"
            + "\n".join(mismatched),
        )


if __name__ == "__main__":
    unittest.main()
