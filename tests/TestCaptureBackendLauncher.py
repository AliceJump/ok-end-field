"""抓包后端拉起器的测试（headless，不需要 Qt、游戏或 Npcap）。

拉起相关的用例都拿 ``sys.executable -c <脚本>`` 当"后端"进程，所以跑的是**真实的
Popen 路径**（参数拼装、cwd、日志落盘、就看即退的判定），而不是打桩出来的假象。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from src.localization import capture_backend_launcher as launcher_mod
from src.localization import minimap_position_mixin
from src.localization.capture_backend_launcher import (
    EXECUTABLE_NAME,
    BackendLauncher,
    find_backend_executable,
    probe_backend,
    shared_launcher,
)
from src.localization.minimap_position_mixin import MinimapPositionMixin

#: 假后端：起一个最小 HTTP 服务，端口从命令行 ``--port`` 取。
#: 参数顺序与真实后端的 ``serve --port N`` 一致，好让拉起路径原样跑通。
_FAKE_BACKEND = """
import http.server, sys

port = int(sys.argv[sys.argv.index("--port") + 1])


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"lastPosition": null}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


http.server.HTTPServer(("127.0.0.1", port), Handler).serve_forever()
"""

#: 立刻就退的"后端"，用来验证"起来又马上退"能被识别（缺 Npcap 驱动时的表现形式）
_DIES_IMMEDIATELY = "import sys; sys.exit(3)"

#: 带"孙进程"的假后端，用来验证 stop() 收的是整棵进程树。
#: 打包成 onefile 的真实后端就是两层（bootloader + 里层 Python），只杀外层会留下里层
#: 继续占着端口——这正是 ``stop()`` 曾经漏掉的情形。这里用一个真正的孙进程复现该结构。
_WITH_GRANDCHILD = """
import http.server, subprocess, sys, time

pid_file = sys.argv[sys.argv.index("--pid-file") + 1]
port = int(sys.argv[sys.argv.index("--port") + 1])

grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
with open(pid_file, "w", encoding="utf-8") as handle:
    handle.write(str(grandchild.pid))


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"{}"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


http.server.HTTPServer(("127.0.0.1", port), Handler).serve_forever()
"""

_FAKE_ARGS = ("-c", _FAKE_BACKEND)
_DEAD_ARGS = ("-c", _DIES_IMMEDIATELY)


def _free_port() -> int:
    """要一个当前空闲的端口；子进程随后去 bind 它。"""

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_port(port: int, timeout: float = 10.0) -> bool:
    """等端口开始应答（子进程冷启动要一点时间）。"""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if probe_backend(port, timeout=0.3):
            return True
        time.sleep(0.1)
    return False


def _pid_alive(pid: int) -> bool:
    """PID 还在吗。``tasklist`` 是按 PID 过滤的，比 OpenProcess 少一层权限麻烦。"""

    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
        capture_output=True,
        text=True,
        check=False,
    )
    return str(pid) in result.stdout


def _wait_for_file(path: Path, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file() and path.read_text(encoding="utf-8").strip():
            return True
        time.sleep(0.1)
    return False


class ProbeTest(unittest.TestCase):
    def test_closed_port_is_not_alive(self):
        self.assertFalse(probe_backend(_free_port(), timeout=0.3))

    def test_fake_backend_is_alive(self):
        port = _free_port()
        launcher = BackendLauncher(
            port, executable=sys.executable, serve_args=_FAKE_ARGS, log_directory=Path(tempfile.mkdtemp())
        )
        try:
            self.assertTrue(launcher.ensure_running())
            self.assertTrue(probe_backend(port))
        finally:
            launcher.stop()


class FindExecutableTest(unittest.TestCase):
    """定位 exe 的搜索顺序。所有用例都把仓库根指到空目录、把 PATH 关掉、把托管安装目录也指到
    空目录，免得在本机上搜到真实的 ``endfield-capture-backend/dist`` 或已下载的托管副本——
    那两样都是一装就有，会让"找不到"这类断言随机器状态飘。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name) / "ok-end-field"
        self.root.mkdir()
        self._patches = [
            mock.patch.object(launcher_mod, "REPO_ROOT", self.root),
            mock.patch.object(launcher_mod.shutil, "which", return_value=None),
            mock.patch.object(
                launcher_mod, "installed_executable", return_value=self.root / "downloads" / EXECUTABLE_NAME
            ),
            mock.patch.dict(os.environ, {launcher_mod.ENV_OVERRIDE: ""}),
        ]
        for patch in self._patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self._patches):
            patch.stop()
        self._tmp.cleanup()

    def test_returns_none_when_nothing_is_found(self):
        self.assertIsNone(find_backend_executable())

    def test_configured_file_wins(self):
        exe = self.root / "somewhere" / EXECUTABLE_NAME
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"")
        self.assertEqual(find_backend_executable(str(exe)), exe)

    def test_configured_directory_is_expanded(self):
        exe = self.root / "backend" / EXECUTABLE_NAME
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"")
        self.assertEqual(find_backend_executable(str(exe.parent)), exe)

    def test_configured_path_that_does_not_exist_is_ignored(self):
        """配置写错了要退回自动探测，而不是拿一个不存在的路径去 Popen。"""

        self.assertIsNone(find_backend_executable(str(self.root / "nope" / EXECUTABLE_NAME)))

    def test_environment_override_is_used(self):
        exe = self.root / "env" / EXECUTABLE_NAME
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"")
        with mock.patch.dict(os.environ, {launcher_mod.ENV_OVERRIDE: str(exe)}):
            self.assertEqual(find_backend_executable(), exe)

    def test_sibling_repo_dist_is_found(self):
        exe = self.root.parent / "endfield-capture-backend" / "dist" / EXECUTABLE_NAME
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"")
        self.assertEqual(find_backend_executable(), exe)

    def test_configured_beats_sibling_repo(self):
        """显式配置必须压过自动探测——否则用户指定的版本永远用不上。"""

        configured = self.root / "mine" / EXECUTABLE_NAME
        configured.parent.mkdir(parents=True)
        configured.write_bytes(b"")
        sibling = self.root.parent / "endfield-capture-backend" / "dist" / EXECUTABLE_NAME
        sibling.parent.mkdir(parents=True)
        sibling.write_bytes(b"")
        self.assertEqual(find_backend_executable(str(configured)), configured)


class LaunchTest(unittest.TestCase):
    def setUp(self):
        # ignore_cleanup_errors：子进程刚被终止时 Windows 还会短暂占着它的日志文件，
        # 清理临时目录会随机撞上 WinError 32。断言都已经跑完，这里不该因此判失败。
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.log_dir = Path(self._tmp.name) / "logs"

    def tearDown(self):
        self._tmp.cleanup()

    def _launcher(self, port: int, *, args=_FAKE_ARGS, logs: list[str] | None = None):
        return BackendLauncher(
            port,
            executable=sys.executable,
            serve_args=args,
            log_info=(logs.append if logs is not None else None),
            log_warning=(logs.append if logs is not None else None),
            log_directory=self.log_dir,
        )

    def test_spawns_and_waits_until_ready(self):
        port = _free_port()
        launcher = self._launcher(port)
        try:
            self.assertTrue(launcher.ensure_running())
            self.assertTrue(launcher.running)
            stats = launcher.stats()
            self.assertTrue(stats["running"])
            self.assertTrue(stats["startedByApp"])
            self.assertEqual(stats["port"], port)
        finally:
            launcher.stop()

    def test_writes_backend_output_to_a_log_file(self):
        """后端是控制台程序，我们没有窗口可看；输出必须落到文件里。"""

        port = _free_port()
        launcher = self._launcher(port)
        try:
            self.assertTrue(launcher.ensure_running())
            self.assertTrue(_wait_for_port(port))
        finally:
            launcher.stop()
        self.assertTrue((self.log_dir / f"backend-{port}.log").is_file(), "应当留下日志文件")

    def test_adopts_a_backend_it_did_not_start(self):
        """已经在跑的后端（用户自己起的）直接采用，**绝不**重复启动。"""

        port = _free_port()
        external = self._launcher(port)
        self.assertTrue(external.ensure_running())
        try:
            adopter = self._launcher(port)
            self.assertTrue(adopter.ensure_running())
            self.assertFalse(adopter.running, "不应另起一个进程")
            self.assertFalse(adopter.stats()["startedByApp"])
            # 关键：stop() 不能把别人的会话杀掉
            adopter.stop()
            self.assertTrue(probe_backend(port), "不是自己起的后端不该被终止")
        finally:
            external.stop()

    def test_stop_terminates_the_process_it_started(self):
        port = _free_port()
        launcher = self._launcher(port)
        self.assertTrue(launcher.ensure_running())
        launcher.stop()
        self.assertFalse(launcher.running)
        self.assertFalse(probe_backend(port, timeout=0.3))

    def test_stop_kills_the_whole_process_tree(self):
        """**这条是回归测试**：onefile 后端是两层进程，只杀外层会留下里层继续占端口。

        假后端故意再生一个"孙进程"来复现这个结构；``stop()`` 之后它也必须没了。
        """

        port = _free_port()
        pid_file = Path(self._tmp.name) / "grandchild.pid"
        launcher = self._launcher(port, args=("-c", _WITH_GRANDCHILD, "--pid-file", str(pid_file)))
        try:
            self.assertTrue(launcher.ensure_running())
            self.assertTrue(_wait_for_file(pid_file), "假后端没能记下孙进程 PID")
            grandchild = int(pid_file.read_text(encoding="utf-8").strip())
            self.assertTrue(_pid_alive(grandchild), "孙进程本就该活着")
            launcher.stop()
            deadline = time.monotonic() + 10.0
            while _pid_alive(grandchild) and time.monotonic() < deadline:
                time.sleep(0.2)
            self.assertFalse(_pid_alive(grandchild), "stop() 必须连子进程一起收掉")
        finally:
            launcher.stop()

    def test_healthy_ttl_skips_repeated_probing(self):
        """确认健康后短时间内不再探活，省掉每个采样周期的 HTTP 往返。"""

        port = _free_port()
        launcher = self._launcher(port)
        try:
            self.assertTrue(launcher.ensure_running())
            with mock.patch.object(launcher_mod, "probe_backend") as probe:
                self.assertTrue(launcher.ensure_running())
            probe.assert_not_called()
        finally:
            launcher.stop()

    def test_warns_and_backs_off_when_the_backend_instantly_exits(self):
        """起来就退必须被识别（缺 Npcap 驱动时最常见），且不能每个采样周期都重试一次。"""

        port = _free_port()
        logs: list[str] = []
        launcher = self._launcher(port, args=_DEAD_ARGS, logs=logs)
        self.assertFalse(launcher.ensure_running())
        self.assertTrue(any("立刻退出" in text for text in logs), f"应当报告立刻退出：{logs}")
        started = launcher._process
        # 冷却期内再问一次：不能又起一个新进程
        self.assertFalse(launcher.ensure_running())
        self.assertIs(launcher._process, started, "冷却期内不应重新拉起")

    def test_warns_when_no_executable_can_be_found(self):
        logs: list[str] = []
        empty_root = Path(self._tmp.name) / "empty"
        empty_root.mkdir()
        launcher = BackendLauncher(
            _free_port(),
            executable="",
            log_warning=logs.append,
            log_directory=self.log_dir,
        )
        with (
            mock.patch.object(launcher_mod, "REPO_ROOT", empty_root),
            mock.patch.object(launcher_mod.shutil, "which", return_value=None),
            mock.patch.object(
                launcher_mod, "installed_executable", return_value=empty_root / "downloads" / EXECUTABLE_NAME
            ),
            mock.patch.dict(os.environ, {launcher_mod.ENV_OVERRIDE: ""}),
        ):
            self.assertFalse(launcher.ensure_running())
        self.assertTrue(any("找不到抓包后端程序" in text for text in logs), f"应当提示找不到程序：{logs}")


class SharedLauncherTest(unittest.TestCase):
    """进程内共享：游戏启动前的自动拉起与定位任务必须用**同一个**实例。

    否则探活会重复，而 ``stop()`` 会因为"不是我起的"拒绝收尾——端口就此泄漏。
    """

    def setUp(self):
        launcher_mod._SHARED.clear()

    def tearDown(self):
        for launcher in launcher_mod._SHARED.values():
            launcher.stop()
        launcher_mod._SHARED.clear()

    def test_same_key_returns_the_same_instance(self):
        first = shared_launcher(8765, executable="D:/a.exe")
        second = shared_launcher(8765, executable="D:/a.exe")
        self.assertIs(first, second)

    def test_different_port_or_path_is_a_different_instance(self):
        base = shared_launcher(8765, executable="D:/a.exe")
        self.assertIsNot(base, shared_launcher(8766, executable="D:/a.exe"))
        self.assertIsNot(base, shared_launcher(8765, executable="D:/b.exe"))

    def test_later_loggers_are_adopted(self):
        """后到的调用方（定位任务）带上了自己的日志接口，接过去免得拉起日志掉进黑洞。"""

        shared_launcher(8765, executable="D:/a.exe")
        collected: list[str] = []
        launcher = shared_launcher(8765, executable="D:/a.exe", log_warning=collected.append)
        launcher._warn_once("x", "hello")
        self.assertEqual(collected, ["hello"])

    def test_task_and_patch_share_one_launcher(self):
        owner = _FakeOwner({"使用实验技术": True, "实验技术后端程序路径": "D:/ef.exe"})
        from src.localization.capture_backend_launcher import shared_launcher as again

        self.assertIs(owner.capture_backend_launcher(), again(8765, executable="D:/ef.exe"))


class AutostartPatchTest(unittest.TestCase):
    """游戏启动前拉起后端——**这才是唯一来得及的时机**。

    触发任务要等游戏窗口能截屏才会被执行（``can_capture()``），所以把拉起放在定位任务里
    永远晚于登录握手。补丁挂在 ``StartController.do_start`` 上。
    """

    def setUp(self):
        launcher_mod._SHARED.clear()
        self.installed = []

    def tearDown(self):
        for launcher in launcher_mod._SHARED.values():
            launcher.stop()
        launcher_mod._SHARED.clear()

    class _Task:
        """只实现被补丁用到的两个方法，省掉整条任务依赖链。"""

        def __init__(self, enabled: bool, ready: bool = True):
            self.enabled = enabled
            self.ready = ready
            self.calls = 0

        def experimental_capture_enabled(self) -> bool:
            return self.enabled

        def ensure_capture_backend(self) -> bool:
            self.calls += 1
            return self.ready

    def test_does_nothing_when_the_switch_is_off(self):
        from src.patches import capture_backend_autostart_patch as patch_mod

        task = self._Task(enabled=False)
        with mock.patch.object(patch_mod, "_position_task", return_value=task):
            self.assertFalse(patch_mod.ensure_capture_backend_before_game())
        self.assertEqual(task.calls, 0, "开关关着就不该去碰后端")

    def test_ensures_the_backend_when_the_switch_is_on(self):
        from src.patches import capture_backend_autostart_patch as patch_mod

        task = self._Task(enabled=True)
        with mock.patch.object(patch_mod, "_position_task", return_value=task):
            self.assertTrue(patch_mod.ensure_capture_backend_before_game())
        self.assertEqual(task.calls, 1)

    def test_returns_false_when_the_backend_cannot_be_readied(self):
        from src.patches import capture_backend_autostart_patch as patch_mod

        task = self._Task(enabled=True, ready=False)
        with mock.patch.object(patch_mod, "_position_task", return_value=task):
            self.assertFalse(patch_mod.ensure_capture_backend_before_game())

    def test_returns_false_when_no_task_can_be_found(self):
        from src.patches import capture_backend_autostart_patch as patch_mod

        with mock.patch.object(patch_mod, "_position_task", return_value=None):
            self.assertFalse(patch_mod.ensure_capture_backend_before_game())

    def test_wrapped_do_start_runs_the_game_even_if_ensuring_fails(self):
        """拉起后端是锦上添花：起不来也不该让用户连游戏都开不了。"""

        from src.patches import capture_backend_autostart_patch as patch_mod

        ran: list[str] = []

        class _Controller:
            logger = None

            def do_start(self, task=None, exit_after=False):
                ran.append("game")
                return "started"

        original = _Controller.do_start
        with (
            mock.patch.object(patch_mod, "_PATCH_INSTALLED", False),
            mock.patch.object(patch_mod, "ensure_capture_backend_before_game", side_effect=RuntimeError("boom")),
            mock.patch("ok.core.start_controller.StartController", _Controller),
        ):
            patch_mod.install_capture_backend_autostart_patch()
            self.assertEqual(_Controller().do_start(), "started")
        _Controller.do_start = original
        self.assertEqual(ran, ["game"], "游戏必须照常启动")

    def test_install_is_idempotent(self):
        from src.patches import capture_backend_autostart_patch as patch_mod

        class _Controller:
            def do_start(self, task=None, exit_after=False):
                return None

        with (
            mock.patch.object(patch_mod, "_PATCH_INSTALLED", False),
            mock.patch("ok.core.start_controller.StartController", _Controller),
        ):
            patch_mod.install_capture_backend_autostart_patch()
            first = _Controller.do_start
            patch_mod.install_capture_backend_autostart_patch()
        self.assertIs(_Controller.do_start, first, "重复安装不该层层套娃")


class _FakeOwner(MinimapPositionMixin):
    """只保留定位 mixin 的最小宿主：补齐日志、时钟与配置读取。

    ``MinimapPositionMixin`` 需要 ``self.config``；``_cfg_bool`` / ``_cfg_str`` 来自
    :class:`MinimapHeadingMixin`，随继承链一起具备。
    """

    def __init__(self, config: dict):
        self.config = dict(config)
        self._clock = 1000.0
        self._init_minimap_position_mixin()

    def active_time(self) -> float:
        return self._clock

    def log_info(self, *args, **kwargs):
        pass

    def log_warning(self, *args, **kwargs):
        pass


class MixinWiringTest(unittest.TestCase):
    """配置到行为的接线：自动启动开关、路径配置、端口变化时重建。

    **必须屏蔽联网那一步**：``ensure_capture_backend`` 现在会先确保本地有 exe，而缺了它就会
    真去 GitHub 下载 18 MB 并把它拉起。这个测试类只关心接线，安装/更新由
    ``TestCaptureBackendInstaller`` 负责。
    """

    def setUp(self):
        self._patches = [
            mock.patch.object(MinimapPositionMixin, "ensure_capture_backend_installed", return_value=True),
            # 托管目录也指空：免得本机已下载的副本被 find_backend_executable 找到并真跑起来
            mock.patch.object(
                launcher_mod, "installed_executable", return_value=Path(tempfile.mkdtemp()) / EXECUTABLE_NAME
            ),
        ]
        for patch in self._patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self._patches):
            patch.stop()

    def test_autostart_disabled_only_probes(self):
        owner = _FakeOwner({"使用实验技术": True, "实验技术自动启动后端": False, "实验技术后端端口": _free_port()})
        # 打桩要打在 mixin 的命名空间上——它是 `from ... import probe_backend` 进来的
        with mock.patch.object(minimap_position_mixin, "probe_backend", return_value=False) as probe:
            self.assertFalse(owner.ensure_capture_backend())
        probe.assert_called_once()
        self.assertFalse(owner._minimap_capture_launcher.running, "关掉自动启动就不该起进程")

    def test_autostart_enabled_reports_a_running_backend(self):
        port = _free_port()
        external = BackendLauncher(
            port, executable=sys.executable, serve_args=_FAKE_ARGS, log_directory=Path(tempfile.mkdtemp())
        )
        self.assertTrue(external.ensure_running())
        try:
            owner = _FakeOwner({"使用实验技术": True, "实验技术后端端口": port})
            self.assertTrue(owner.ensure_capture_backend())
            self.assertFalse(owner._minimap_capture_launcher.running, "已经在跑就不该另起进程")
        finally:
            external.stop()

    def test_launcher_is_rebuilt_when_path_config_changes(self):
        owner = _FakeOwner({"使用实验技术": True, "实验技术后端程序路径": "D:/one/backend.exe"})
        first = owner.capture_backend_launcher()
        owner.config["实验技术后端程序路径"] = "D:/two/backend.exe"
        second = owner.capture_backend_launcher()
        self.assertIsNot(first, second)
        self.assertEqual(second.executable, "D:/two/backend.exe")

    def test_launcher_is_reused_when_config_is_stable(self):
        owner = _FakeOwner({"使用实验技术": True, "实验技术后端程序路径": "D:/one/backend.exe"})
        self.assertIs(owner.capture_backend_launcher(), owner.capture_backend_launcher())

    def test_stop_on_exit_is_off_by_default(self):
        """默认不停后端：它的状态绑在当前这一局连接上，关掉就等于丢登录握手。"""

        owner = _FakeOwner({"使用实验技术": True})
        launcher = owner.capture_backend_launcher()
        with mock.patch.object(launcher, "stop") as stop:
            owner.stop_capture_backend()
        stop.assert_not_called()

    def test_stop_on_exit_terminates_when_enabled(self):
        owner = _FakeOwner({"使用实验技术": True, "实验技术停止定位时关闭后端": True})
        launcher = owner.capture_backend_launcher()
        with mock.patch.object(launcher, "stop") as stop:
            owner.stop_capture_backend()
        stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
