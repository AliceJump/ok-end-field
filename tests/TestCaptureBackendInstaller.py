"""抓包后端下载/更新器的测试（headless，不需要 Qt、游戏或真实 GitHub）。

下载路径用**本机 HTTP 服务**喂假数据，所以走的是真实的流式下载、校验、原子替换，而不是
把 `_download` 打桩掉——那样最容易漏掉的恰恰是"下到一半失败会不会留下半个 exe"。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

from src.localization import capture_backend_installer as installer_mod
from src.localization.capture_backend_installer import (
    ASSET_NAME,
    CaptureBackendInstaller,
    fetch_latest_release,
    installed_executable,
    managed_install_dir,
)

#: 假 exe：以 MZ 开头，内容随便，大小够触发分块读取
FAKE_EXE = b"MZ" + bytes(range(256)) * 40


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _Handler(BaseHTTPRequestHandler):
    #: 由测试逐个用例注入
    state: ClassVar[dict] = {}

    def log_message(self, *args):
        pass

    def do_GET(self):
        state = type(self).state
        path = self.path.split("?")[0]
        if path == "/api":
            if state.get("api_fails"):
                self.send_response(403)
                self.end_headers()
                return
            body = json.dumps(state["api_payload"]).encode("utf-8")
            self._send(200, body)
            return
        if path == "/redirect":
            self.send_response(302)
            # 形状照抄 GitHub：真实的重定向目标是 .../releases/tag/<tag>，解析就依赖这个前缀
            location = (
                f"http://127.0.0.1:{state['port']}/luqing233/endfield-capture-backend"
                f"/releases/tag/{state['redirect_tag']}"
            )
            self.send_header("Location", location)
            self.end_headers()
            return
        if path == "/asset":
            self._send(200, state.get("asset_bytes", FAKE_EXE))
            return
        self.send_response(404)
        self.end_headers()

    def _send(self, code: int, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class FakeReleaseHost:
    """最小发布服务：/api、/redirect、/asset。"""

    def __init__(
        self,
        *,
        asset_bytes: bytes = FAKE_EXE,
        tag: str = "v1.2.3",
        api_fails: bool = False,
        redirect_tag: str = "v9.9.9",
    ):
        host = type("_H", (_Handler,), {"state": {}})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), host)
        self.port = self.server.server_address[1]
        host.state = {
            "port": self.port,
            "asset_bytes": asset_bytes,
            "api_fails": api_fails,
            "redirect_tag": redirect_tag,
            "api_payload": {
                "tag_name": tag,
                "assets": [
                    {
                        "name": ASSET_NAME,
                        "size": len(asset_bytes),
                        "digest": f"sha256:{_sha256(asset_bytes)}",
                        "browser_download_url": f"http://127.0.0.1:{self.port}/asset",
                    }
                ],
            },
        }
        self.handler = host
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def set_api_fails(self, value: bool) -> None:
        self.handler.state["api_fails"] = value

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def _point_at(host: FakeReleaseHost):
    """把模块里的两个远端地址指到本机服务上。"""

    return patch.multiple(
        installer_mod,
        _API_URL=f"http://127.0.0.1:{host.port}/api",
        _REDIRECT_URL=f"http://127.0.0.1:{host.port}/redirect",
    )


class LayoutTest(unittest.TestCase):
    """安装位置必须待在 configs/ 外面 —— 否则会撑爆配置导出/导入。"""

    def test_install_dir_is_not_under_configs(self):
        """**回归**：exe 有 18 MB，而配置导入对单个成员硬性限制 16 MB

        （``config_transfer_patch._MAX_IMPORT_MEMBER_BYTES``）。一旦落进 ``configs/``，
        导出的配置包会带着它，然后再也导不回来。
        """

        directory = managed_install_dir()
        self.assertNotIn("configs", directory.parts, f"安装目录不能落在 configs 下：{directory}")
        self.assertEqual(directory.parts[-2:], ("downloads", "capture_backend"))

    def test_installed_executable_uses_the_shared_asset_name(self):
        self.assertEqual(installed_executable().name, ASSET_NAME)
        self.assertEqual(installed_executable().parent, managed_install_dir())

    def test_launcher_and_installer_agree_on_the_file_name(self):
        from src.localization.capture_backend_launcher import EXECUTABLE_NAME

        self.assertEqual(EXECUTABLE_NAME, ASSET_NAME)


class DiscoveryTest(unittest.TestCase):
    """托管目录要参与 exe 探测，且排在手工构建之前。"""

    def test_managed_copy_wins_over_a_sibling_checkout(self):
        from src.localization import capture_backend_launcher as launcher_mod

        with tempfile.TemporaryDirectory() as directory:
            managed = Path(directory) / ASSET_NAME
            managed.write_bytes(FAKE_EXE)
            with patch.object(launcher_mod, "installed_executable", return_value=managed):
                found = launcher_mod.find_backend_executable()
        self.assertEqual(found, managed)

    def test_configured_path_still_beats_the_managed_copy(self):
        from src.localization import capture_backend_launcher as launcher_mod

        with tempfile.TemporaryDirectory() as directory:
            managed = Path(directory) / "managed" / ASSET_NAME
            managed.parent.mkdir(parents=True)
            managed.write_bytes(FAKE_EXE)
            configured = Path(directory) / ASSET_NAME
            configured.write_bytes(FAKE_EXE)
            with patch.object(launcher_mod, "installed_executable", return_value=managed):
                found = launcher_mod.find_backend_executable(str(configured))
        self.assertEqual(found, configured)


class ReleaseDiscoveryTest(unittest.TestCase):
    def test_api_gives_tag_size_and_digest(self):
        with FakeReleaseHost() as host, _point_at(host):
            release = fetch_latest_release()
        self.assertEqual(release.tag, "v1.2.3")
        self.assertEqual(release.size, len(FAKE_EXE))
        self.assertEqual(release.sha256, _sha256(FAKE_EXE))
        self.assertEqual(release.source, "api")

    def test_redirect_fallback_yields_tag_without_a_digest(self):
        """API 被限流（实测撞到过 403）时退回 302 重定向那条路。"""

        with FakeReleaseHost(api_fails=True) as host, _point_at(host):
            release = fetch_latest_release()
        self.assertEqual(release.tag, "v9.9.9")
        self.assertEqual(release.sha256, "", "回退路径拿不到官方摘要")
        self.assertEqual(release.source, "redirect")
        self.assertIn("v9.9.9", release.asset_url)

    def test_unknown_digest_algorithm_is_not_used_as_sha256(self):
        payload = {
            "tag_name": "v1.0.0",
            "assets": [{"name": ASSET_NAME, "size": 10, "digest": "sha512:deadbeef"}],
        }
        with patch.object(installer_mod, "_open") as fake_open:
            fake_open.return_value.__enter__.return_value.read.return_value = json.dumps(payload).encode()
            release = installer_mod._fetch_via_api(1.0)
        self.assertEqual(release.sha256, "")

    def test_no_release_when_both_paths_fail(self):
        # 指向一个没人监听的端口，两条路都连不上
        with patch.multiple(
            installer_mod,
            _API_URL="http://127.0.0.1:9/api",
            _REDIRECT_URL="http://127.0.0.1:9/redirect",
        ):
            self.assertIsNone(fetch_latest_release(timeout=0.5))


class InstallTest(unittest.TestCase):
    def _installer(self, directory: str, **kwargs) -> CaptureBackendInstaller:
        return CaptureBackendInstaller(
            install_dir=Path(directory),
            request_timeout=5.0,
            download_timeout=10.0,
            ttl=kwargs.pop("ttl", 0.0),
            cooldown=kwargs.pop("cooldown", 0.0),
            **kwargs,
        )

    def test_downloads_verifies_and_records(self):
        with tempfile.TemporaryDirectory() as directory:
            with FakeReleaseHost() as host, _point_at(host):
                path = self._installer(directory).ensure_installed()
            self.assertIsNotNone(path)
            self.assertEqual(path.read_bytes(), FAKE_EXE)
            meta = json.loads((Path(directory) / "installed.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["tag"], "v1.2.3")
            self.assertEqual(meta["size"], len(FAKE_EXE))
            self.assertTrue(meta["verified"])

    def test_digest_mismatch_is_rejected_and_nothing_is_left_behind(self):
        """校验不过就必须什么都不留：既不能留下 exe，也不能留下半截下载文件。"""

        tampered = b"MZ" + b"evil" * 100
        with tempfile.TemporaryDirectory() as directory:
            # 服务端给的是 A，摘要声明的是 A，但资产内容被换成 B
            with FakeReleaseHost(asset_bytes=FAKE_EXE) as host, _point_at(host):
                host.handler.state["asset_bytes"] = tampered
                path = self._installer(directory, cooldown=3600.0).ensure_installed()
            self.assertIsNone(path)
            self.assertFalse((Path(directory) / ASSET_NAME).exists(), "校验失败不能留下 exe")
            self.assertEqual(list(Path(directory).glob("*.download")), [], "不能留下半截下载")
            self.assertFalse((Path(directory) / "installed.json").exists())

    def test_size_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with FakeReleaseHost() as host, _point_at(host):
                # 声明一个比实际更大的大小
                host.handler.state["api_payload"]["assets"][0]["size"] = len(FAKE_EXE) + 999
                path = self._installer(directory, cooldown=3600.0).ensure_installed()
            self.assertIsNone(path)
            self.assertFalse((Path(directory) / ASSET_NAME).exists())

    def test_up_to_date_copy_is_not_downloaded_again(self):
        with tempfile.TemporaryDirectory() as directory:
            with FakeReleaseHost() as host, _point_at(host):
                installer = self._installer(directory)
                self.assertIsNotNone(installer.ensure_installed())
                # 把资产撤掉：若还去下载就会失败
                host.handler.state["asset_bytes"] = b""
                again = self._installer(directory).ensure_installed()
            self.assertIsNotNone(again)
            self.assertEqual(again.read_bytes(), FAKE_EXE, "已是最新就不该再下")

    def test_check_now_bypasses_the_ttl(self):
        with tempfile.TemporaryDirectory() as directory, FakeReleaseHost() as host, _point_at(host):
            installer = self._installer(directory, ttl=3600.0)
            installer.ensure_installed()
            installer._checked_at = 0.0  # 模拟 TTL 到期前的状态
            self.assertIsNotNone(installer.check_now())

    def test_network_failure_without_a_local_copy_reports_unavailable(self):
        warnings: list[str] = []
        with tempfile.TemporaryDirectory() as directory, patch.multiple(
            installer_mod,
            _API_URL="http://127.0.0.1:9/api",
            _REDIRECT_URL="http://127.0.0.1:9/redirect",
        ):
            installer = self._installer(directory, cooldown=3600.0, log_warning=warnings.append)
            self.assertIsNone(installer.ensure_installed())
            installer.ensure_installed()
        self.assertEqual(len(warnings), 1, "同一原因只告警一次")

    def test_network_failure_keeps_an_existing_copy_working(self):
        """网络断了也必须继续用手上这个——不然一次断网就把功能整个停掉。"""

        with tempfile.TemporaryDirectory() as directory:
            with FakeReleaseHost() as host, _point_at(host):
                self.assertIsNotNone(self._installer(directory).ensure_installed())
            with patch.multiple(
                installer_mod,
                _API_URL="http://127.0.0.1:9/api",
                _REDIRECT_URL="http://127.0.0.1:9/redirect",
            ):
                path = self._installer(directory, ttl=0.0, cooldown=0.0).ensure_installed()
            self.assertIsNotNone(path)
            self.assertEqual(path.read_bytes(), FAKE_EXE)

    def test_allow_network_false_never_touches_the_network(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(installer_mod, "fetch_latest_release") as fake:
                self.assertIsNone(self._installer(directory).ensure_installed(allow_network=False))
            fake.assert_not_called()

    def test_truncated_local_copy_is_repaired(self):
        """本地文件被截断（磁盘满、杀进程）时不能当成好的用。"""

        with tempfile.TemporaryDirectory() as directory:
            with FakeReleaseHost() as host, _point_at(host):
                self.assertIsNotNone(self._installer(directory).ensure_installed())
                (Path(directory) / ASSET_NAME).write_bytes(b"MZ")  # 截断
                path = self._installer(directory, ttl=3600.0).ensure_installed()
            self.assertIsNotNone(path)
            self.assertEqual(path.read_bytes(), FAKE_EXE)

    def test_new_tag_replaces_the_old_build(self):
        with tempfile.TemporaryDirectory() as directory:
            with FakeReleaseHost(tag="v1.0.0") as host, _point_at(host):
                self._installer(directory).ensure_installed()
            newer = b"MZ" + b"new" * 200
            with FakeReleaseHost(tag="v2.0.0", asset_bytes=newer) as host, _point_at(host):
                path = self._installer(directory, ttl=0.0).ensure_installed()
            self.assertEqual(path.read_bytes(), newer)
            meta = json.loads((Path(directory) / "installed.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["tag"], "v2.0.0")

    def test_failed_download_does_not_replace_a_working_build(self):
        with tempfile.TemporaryDirectory() as directory:
            with FakeReleaseHost(tag="v1.0.0") as host, _point_at(host):
                self._installer(directory).ensure_installed()
            with FakeReleaseHost(tag="v2.0.0") as host, _point_at(host):
                host.handler.state["asset_bytes"] = b"MZ" + b"bad" * 50  # 与摘要不符
                path = self._installer(directory, ttl=0.0, cooldown=3600.0).ensure_installed()
            self.assertEqual(path.read_bytes(), FAKE_EXE, "新版装不上时必须留着旧版继续用")


class MixedInTest(unittest.TestCase):
    """定位 mixin 侧的接线：只有在开关打开、且没配自备后端时才联网。"""

    def _owner(self, config: dict):
        from src.localization.minimap_position_mixin import MinimapPositionMixin

        class _Owner(MinimapPositionMixin):
            def __init__(self):
                self.config = dict(config)
                self._init_minimap_position_mixin()

            def active_time(self) -> float:
                return 0.0

            def log_info(self, *args, **kwargs):
                pass

            def log_warning(self, *args, **kwargs):
                pass

        return _Owner()

    def test_configured_executable_skips_the_download_entirely(self):
        owner = self._owner({"实验技术后端程序路径": r"D:\my\backend.exe"})
        with patch.object(installer_mod, "fetch_latest_release") as fake:
            self.assertTrue(owner.ensure_capture_backend_installed())
        fake.assert_not_called()

    def test_environment_override_also_skips_the_download(self):
        owner = self._owner({})
        with (
            patch.dict(os.environ, {"EF_CAPTURE_BACKEND": r"D:\my\backend.exe"}),
            patch.object(installer_mod, "fetch_latest_release") as fake,
        ):
            self.assertTrue(owner.ensure_capture_backend_installed())
        fake.assert_not_called()

    def test_installer_is_reused_across_calls(self):
        owner = self._owner({})
        self.assertIs(owner.capture_backend_installer(), owner.capture_backend_installer())

    def test_launch_waits_for_the_download_first(self):
        """**顺序**：必须先确保 exe 在本地，再拉起。反了就会让游戏先连上，这一局抓不到握手。

        这条覆盖的是 ``ensure_capture_backend`` 里新加的接线——``MixinWiringTest`` 为了不联网
        把它打桩掉了，所以这里补上。
        """

        owner = self._owner({})
        events: list[str] = []
        with (
            patch.object(owner, "ensure_capture_backend_installed", side_effect=lambda: events.append("install") or True),
            patch.object(owner, "capture_backend_launcher") as launcher,
        ):
            launcher.return_value.ensure_running.side_effect = lambda: events.append("launch") or True
            self.assertTrue(owner.ensure_capture_backend())
        self.assertEqual(events, ["install", "launch"])

    def test_no_launch_when_the_download_did_not_land(self):
        """拿不到 exe 就不要去拉起了——那样只会得到一个必然失败的 Popen 和一条误导的报错。"""

        owner = self._owner({})
        with (
            patch.object(owner, "ensure_capture_backend_installed", return_value=False),
            patch.object(owner, "capture_backend_launcher") as launcher,
        ):
            self.assertFalse(owner.ensure_capture_backend())
        launcher.return_value.ensure_running.assert_not_called()

    def test_autostart_off_does_not_download(self):
        """关掉自动启动=我自己管后端，这种用法下不该偷偷联网。"""

        owner = self._owner({"实验技术自动启动后端": False})
        with (
            patch.object(owner, "ensure_capture_backend_installed") as install,
            patch.object(owner, "capture_backend_launcher"),
        ):
            owner.ensure_capture_backend()
        install.assert_not_called()

    def test_prefetch_skips_the_network_when_a_copy_already_exists(self):
        owner = self._owner({})
        with tempfile.TemporaryDirectory() as directory:
            exe = Path(directory) / ASSET_NAME
            exe.write_bytes(FAKE_EXE)
            with (
                patch.object(owner.capture_backend_installer(), "_directory", Path(directory)),
                patch.object(installer_mod, "fetch_latest_release") as fake,
            ):
                owner.prefetch_capture_backend()
        fake.assert_not_called()


if __name__ == "__main__":
    unittest.main()
