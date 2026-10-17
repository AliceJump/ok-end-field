"""按需拉起外部抓包后端（``endfield-capture-backend.exe``）。

:mod:`src.localization.capture_position_source` 只负责**读**后端；本模块负责让它**跑起来**。
两者一起构成「实验技术」直通定位：后端被动抓包、离线解出绝对坐标，本应用只消费结果。

为什么需要自动拉起：后端的抓包必须在**游戏建立主连接之前**就开始，否则拿不到登录握手，
整条流都推不出正确的解密位置（后端会明确报「缺少连接起点」）。让用户先手动开终端、再开
游戏、再开本应用，顺序很容易搞错；挂在这里由定位服务负责拉起，顺序就由应用保证了。

三条刻意的边界：

* **只拉起、不接管**。已经在跑的后端（用户自己起的）会被直接采用，绝不重复启动，
  也**绝不**去终止不是自己起的进程——那是别人的会话。
* **默认不在任务销毁时停掉后端**。后端的全部状态都绑在这一局游戏连接上，停掉再拉起
  就等于丢了登录握手，必须重启游戏才能恢复。要干净退出的用户可以把
  :data:`CONFIG_EXPERIMENTAL_STOP_ON_EXIT` 打开，但那样重新启用定位前要先重启游戏。
* **输出写文件、窗口不弹**。后端是控制台程序，直接拉起会多一个黑窗口；这里用
  ``CREATE_NO_WINDOW`` 藏掉，它的 stdout/stderr 落到 ``logs/capture_backend/`` 下，
  排查问题时照样看得到。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib import error, request

from src.core.PositionConfig import (
    CONFIG_EXPERIMENTAL_AUTOSTART,
    CONFIG_EXPERIMENTAL_EXE,
    CONFIG_EXPERIMENTAL_STOP_ON_EXIT,
)
from src.localization.capture_backend_installer import (
    ASSET_NAME,
    installed_executable,
    managed_runs_dir,
    prune_run_directories,
)

__all__ = [
    "CONFIG_EXPERIMENTAL_AUTOSTART",
    "CONFIG_EXPERIMENTAL_EXE",
    "CONFIG_EXPERIMENTAL_STOP_ON_EXIT",
    "EXECUTABLE_NAME",
    "BackendLauncher",
    "find_backend_executable",
    "probe_backend",
    "shared_launcher",
]

#: 后端可执行文件名。与安装器的 :data:`~src.localization.capture_backend_installer.ASSET_NAME`
#: 是同一个文件——托管下载下来的就是它，所以这里直接引用，避免两处各写一份而漂移。
EXECUTABLE_NAME = ASSET_NAME

#: 环境变量：直接指定后端 exe 路径（优先级只低于任务配置）
ENV_OVERRIDE = "EF_CAPTURE_BACKEND"

#: 拉起后**同步**等待后端开始应答的秒数。冷启动要先校验客户端哈希、读
#: ``global-metadata.dat`` 与两份预存数据，实测 2~3 秒；等这么久既能让多数冷启动在
#: 同一拍就绪，又能及时抓到「起来就退」（缺 Npcap 驱动时最常见的表现形式），同时不至于
#: 把定位服务的一次采样卡住太久。超过这个时间就交给后续探活，不再阻塞。
STARTUP_GRACE = 3.0

#: 确认后端在跑之后的免检时长：这段时间内不再重复探活，省掉每个采样周期的 HTTP 往返。
HEALTHY_TTL = 5.0

#: 拉起失败后的冷却时间，避免每个采样周期都去重试一次起进程。
RETRY_COOLDOWN = 15.0

#: 探活用的超时。后端是回环上的本机进程，超过这个数就是真的没起来。
PROBE_TIMEOUT = 1.0

#: 保留多少局运行产物（每局一个 ``runs/<时间戳>/``，约 20 MB）。超过的按时间从旧到新删。
#: 后端每局都写一份完整报文，不设上限能攒到几个 GB。
KEEP_RUNS = 5

#: 后端根目录（``src/localization/`` 的上一级再上一级）
REPO_ROOT = Path(__file__).resolve().parents[2]

#: 默认传给后端的子命令与参数。
#:
#: * ``serve`` —— 抓包 + 服务；
#: * ``--no-dashboard`` —— 本应用只读 ``/api/*``，不需要网页看板；
#: * ``--no-map-ws`` —— 官方地图 WS 对照要 ok-end-field 加 ok-script 的依赖，冻结出来的
#:   后端带不了它；而地图名本来就走后端包内的场景表（``levelId`` / ``mapId``），够用。
DEFAULT_SERVE_ARGS: tuple[str, ...] = ("serve", "--no-dashboard", "--no-map-ws")


def probe_backend(port: int, timeout: float = PROBE_TIMEOUT) -> bool:
    """后端在应答吗。**不抛异常、不写日志**，纯探活。"""

    url = f"http://127.0.0.1:{int(port)}/api/state"
    try:
        with request.urlopen(url, timeout=timeout) as response:
            response.read(1)  # 只要连通即可，内容由 capture_position_source 解析
    except (error.URLError, TimeoutError, OSError, ValueError):
        return False
    return True


def _from_candidate(value: str | None) -> Path | None:
    """把「用户给的路径」解释成 exe：可以是 exe 本身，也可以是放着它的目录。"""

    if not value:
        return None
    path = Path(str(value).strip().strip('"'))
    if path.is_dir():
        path = path / EXECUTABLE_NAME
    return path if path.is_file() else None


def find_backend_executable(configured: str = "") -> Path | None:
    """定位后端 exe；找不到返回 ``None``。

    顺序：任务配置 → 环境变量 → **托管下载目录** → 同级 ``endfield-capture-backend`` 仓库的
    ``dist/`` → 仓库根目录旁边 → ``PATH``。**不猜、不下载**：找不到就让上层明确报「后端没起来」，
    而不是随便拉起一个同名程序。

    托管目录排在同级仓库前面，是因为它是唯一被版本跟踪、会被自动更新的那一份；让陈旧的手工
    构建静默压过它，更新功能就形同虚设。要用自己的构建，把配置「实验技术后端程序路径」或环境
    变量 ``EF_CAPTURE_BACKEND`` 指过去即可——两者都在托管目录之前。
    """

    managed = installed_executable()
    for candidate in (
        _from_candidate(configured),
        _from_candidate(os.environ.get(ENV_OVERRIDE)),
        managed if managed.is_file() else None,
        _from_candidate(str(REPO_ROOT.parent / "endfield-capture-backend" / "dist")),
        _from_candidate(str(REPO_ROOT.parent / "endfield-capture-backend")),
        _from_candidate(str(REPO_ROOT)),
    ):
        if candidate is not None:
            return candidate
    found = shutil.which(EXECUTABLE_NAME)
    return Path(found) if found else None


class BackendLauncher:
    """让抓包后端处于「在跑」状态，必要时拉起它。

    只做三件事：探活、拉起、按冷却重试。**不解析坐标**（那是
    :class:`~src.localization.capture_position_source.CapturePositionSource` 的事）。
    """

    def __init__(
        self,
        port: int,
        *,
        executable: str = "",
        log_info=None,
        log_warning=None,
        serve_args: tuple[str, ...] = DEFAULT_SERVE_ARGS,
        log_directory: Path | None = None,
    ):
        self.port = int(port)
        self.executable = executable
        self.serve_args = tuple(serve_args)
        self._log_info = log_info
        self._log_warning = log_warning
        self._log_directory = Path(log_directory) if log_directory else Path("logs") / "capture_backend"

        self._process: subprocess.Popen | None = None
        #: 由本应用拉起的进程才会被 :meth:`stop` 终止
        self._started_by_us = False
        self._healthy_until = 0.0
        self._next_attempt = 0.0
        self._last_issue: str | None = None

    # ------------------------------------------------------------------ #
    # 日志（与 capture_position_source 同一套「同因只报一次」语义）
    # ------------------------------------------------------------------ #
    def _warn_once(self, issue: str, message: str) -> None:
        if issue == self._last_issue:
            return
        self._last_issue = issue
        if callable(self._log_warning):
            self._log_warning(message)

    def _info(self, message: str) -> None:
        if callable(self._log_info):
            self._log_info(message)

    # ------------------------------------------------------------------ #
    # 子进程
    # ------------------------------------------------------------------ #
    @property
    def running(self) -> bool:
        """由本应用拉起、且还活着的后端进程是否存在。"""

        return self._process is not None and self._process.poll() is None

    def _managed_runs_dir(self, executable: Path) -> Path | None:
        """这是**本应用下载的那份**时返回它的运行产物目录；用户自带的后端返回 ``None``。

        只管自己下载的那份：别人的后端该往哪儿写产物是他自己的事，替他改 ``--runs-dir``
        等于把他的产物挪到我们的目录里，那是越界。
        """

        try:
            if Path(executable).resolve() != installed_executable().resolve():
                return None
        except OSError:
            return None
        return managed_runs_dir()

    def _build_command(self, executable: Path) -> list[str]:
        command = [str(executable), *self.serve_args, "--port", str(self.port)]
        runs_dir = self._managed_runs_dir(executable)
        if runs_dir is not None:
            command += ["--runs-dir", str(runs_dir)]
        return command

    def _prune_managed_runs(self, executable: Path) -> None:
        """按 :data:`KEEP_RUNS` 清一次历史运行产物。

        调用时机是**拉起之后**：后端在开始监听之前就建好了这一局的 ``runs/<时间戳>/``，所以
        此刻最新的那个目录已经存在、会被保留，删掉的都是更早的。
        """

        runs_dir = self._managed_runs_dir(executable)
        if runs_dir is None:
            return
        removed = prune_run_directories(runs_dir, KEEP_RUNS)
        if removed:
            self._info(f"实验技术：已清理 {removed} 份历史运行产物（保留最近 {KEEP_RUNS} 局）")

    def _launch(self, executable: Path) -> None:
        """起后端进程；日志落到文件，不弹控制台窗口。"""

        self._log_directory.mkdir(parents=True, exist_ok=True)
        log_path = self._log_directory / f"backend-{self.port}.log"
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        # 每次拉起都续写而不是截断：后一轮的启动失败往往要对照上一轮的尾巴看。
        # 句柄交给 Popen 之后立刻关掉本进程这一份——子进程持有的是自己的副本，
        # 留着只会每次拉起漏一个句柄。
        with log_path.open("ab", buffering=0) as handle:
            self._process = subprocess.Popen(
                self._build_command(executable),
                cwd=str(executable.parent),
                stdout=handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        self._started_by_us = True
        self._info(f"实验技术：已拉起抓包后端 {executable.name}（端口 {self.port}，日志 {log_path}）")

    def _wait_until_ready(self, budget: float) -> bool:
        """在 ``budget`` 秒内等后端开始应答；进程提前退出就直接判失败。"""

        deadline = time.monotonic() + budget
        while True:
            if probe_backend(self.port):
                return True
            if self._process is not None and self._process.poll() is not None:
                self._warn_once(
                    "exited",
                    f"实验技术：抓包后端启动后立刻退出（返回码 {self._process.returncode}）。"
                    f"常见原因是缺少 Npcap 驱动或 --game-dir 指向的客户端目录不对，"
                    f"详见 {self._log_directory} 下的日志。",
                )
                return False
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.25)

    # ------------------------------------------------------------------ #
    # 对外
    # ------------------------------------------------------------------ #
    def _mark_healthy(self) -> None:
        self._healthy_until = time.monotonic() + HEALTHY_TTL
        # 探通即清空这条，好让真正断开时能重新告警
        if self._last_issue == "unreachable":
            self._last_issue = None

    def ensure_running(self) -> bool:
        """后端可用吗；不可用且还没起过时拉起它。

        已经在跑的后端（不管是谁起的）直接采用。**只在拉起那一拍做限时同步等待**
        （:data:`STARTUP_GRACE`），之后靠每次探活推进，不会把采样周期拖住。
        拉起失败会进入冷却，:data:`RETRY_COOLDOWN` 秒内不再重试，避免反复起进程。
        """

        now = time.monotonic()
        if now < self._healthy_until:
            return True
        if probe_backend(self.port):
            self._mark_healthy()
            return True
        if now < self._next_attempt:
            return False
        if self.running:
            # 已经起过了，只是还在冷启动：这一拍不等，交给下一次探活
            return False

        executable = find_backend_executable(self.executable)
        if executable is None:
            self._next_attempt = time.monotonic() + RETRY_COOLDOWN
            self._warn_once(
                "not_found",
                "实验技术：找不到抓包后端程序。请把 "
                f"{EXECUTABLE_NAME} 放到 ok-end-field 同级目录，"
                f"或用配置「{CONFIG_EXPERIMENTAL_EXE}」/ 环境变量 {ENV_OVERRIDE} 指定它的路径。",
            )
            return False

        self._launch(executable)
        ready = self._wait_until_ready(STARTUP_GRACE)
        # **必须等到探活有结果之后再清**：后端是先 mkdir 这一局的 runs/<时间戳>/、再起 HTTP
        # 服务的，所以端口一旦应答，新目录必然已经存在。放在 _launch 之后立刻清会早于它建目录，
        # 于是"保留 5 份"实际会留成 6 份。
        self._prune_managed_runs(executable)
        if ready:
            self._mark_healthy()
            self._info(f"实验技术：抓包后端就绪（端口 {self.port}）")
            return True
        # 起来又立刻退：冷却后再试，别每个采样周期都起一次进程
        if self._process is not None and self._process.poll() is not None:
            self._next_attempt = time.monotonic() + RETRY_COOLDOWN
        return False

    def _stop_process(self, process: subprocess.Popen, timeout: float) -> None:
        """结束进程**树**，不只是我们手里那个 PID。

        打包成 onefile 的 exe 是**两个**进程：外层 PyInstaller bootloader，里层才真正跑
        Python。只对 bootloader 调 ``terminate()`` 会留下里层继续跑——端口照占、抓包照抓，
        而且它已经不再是我们 ``_process`` 指向的对象，之后连探活都发现不了它。
        Windows 上用 ``taskkill /T`` 连子进程一起收掉；换平台只杀自己那个。
        """

        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            process.terminate()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=timeout)

    def stop(self, timeout: float = 5.0) -> None:
        """停掉**由本应用拉起**的后端；不是自己起的（或本来就没起）时什么都不做。"""

        if not self._started_by_us or self._process is None:
            return
        if self._process.poll() is None:
            self._stop_process(self._process, timeout)
        self._process = None
        self._started_by_us = False
        self._healthy_until = 0.0
        self._info("实验技术：已停止本应用拉起的抓包后端")

    def stats(self) -> dict:
        """给状态展示用的一行摘要。"""

        return {
            "port": self.port,
            "running": self.running,
            "startedByApp": self._started_by_us,
            "pid": self._process.pid if self.running else None,
        }


#: 进程内共享的拉起器，键是 ``(端口, exe 路径)``。
#:
#: **必须共享**：拉起后端的时机是「游戏启动前」（见
#: ``src/patches/capture_backend_autostart_patch.py``），而使用它的是定位服务——两处各建一个
#: 实例的话，探活会重复、``stop()`` 还会因为"不是我起的"而拒绝收尾。
_SHARED: dict[tuple[int, str], BackendLauncher] = {}


def shared_launcher(
    port: int,
    *,
    executable: str = "",
    log_info=None,
    log_warning=None,
) -> BackendLauncher:
    """取（必要时建）该端口对应的进程内共享拉起器。"""

    key = (int(port), str(executable or ""))
    launcher = _SHARED.get(key)
    if launcher is None:
        launcher = BackendLauncher(
            port,
            executable=executable,
            log_info=log_info,
            log_warning=log_warning,
        )
        _SHARED[key] = launcher
    elif log_info is not None or log_warning is not None:
        # 后到的调用方可能带上了任务自己的日志接口，接过去免得拉起日志掉进黑洞
        launcher._log_info = log_info or launcher._log_info
        launcher._log_warning = log_warning or launcher._log_warning
    return launcher
