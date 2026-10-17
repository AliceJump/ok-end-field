"""下载并更新外部抓包后端（``endfield-capture-backend.exe``），并托管它的运行产物目录。

:mod:`src.localization.capture_backend_launcher` 负责让后端**跑起来**，本模块负责让它**存在**
——从 GitHub Release 取回可执行文件、在有新版时静默替换，以及管理它写出来的运行产物。

三条边界：

* **下载是开关驱动的**：只有「使用实验技术」打开后才会走到这里（调用点在定位服务的
  :meth:`~src.localization.minimap_position_mixin.MinimapPositionMixin.ensure_capture_backend`）。
  关着的时候一次网络请求都不会发。
* **安装位置避开 ``configs/``**：18 MB 的 exe 放进配置目录会让配置导出包超过导入侧的
  16 MB 单文件上限（``config_transfer_patch._MAX_IMPORT_MEMBER_BYTES``），导出的包再也导不回来。
  所以落在应用目录下的 ``downloads/capture_backend/``（已被 ``.gitignore`` 覆盖）。
* **失败绝不阻断游戏**：拿不到新版就用手上这个旧的；一个都没有就明确报「后端不可用」，
  由上层决定提示，而不是抛异常把启动打断。

关于取版本的方式：GitHub 的 REST API 对**未认证**请求按 IP 限流（每小时 60 次，共享出口很容易
撞上，实测就撞到过 403）。所以主路径先用 API（它额外给出 sha256 摘要，可以校验完整性），
一旦不可用就退回**不加 API 的** ``/releases/latest`` 302 重定向拿 tag，再按约定拼资产 URL——
这条路不消耗配额，代价是拿不到官方摘要，只能校验大小。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

__all__ = [
    "ASSET_NAME",
    "CHECK_COOLDOWN_SECONDS",
    "CHECK_TTL_SECONDS",
    "GITHUB_OWNER",
    "GITHUB_REPO",
    "CaptureBackendInstaller",
    "InstalledInfo",
    "ReleaseInfo",
    "fetch_latest_release",
    "installed_executable",
    "managed_install_dir",
    "managed_runs_dir",
    "prune_run_directories",
]

#: 后端所在仓库（发布页由它派生）
GITHUB_OWNER = "luqing233"
GITHUB_REPO = "endfield-capture-backend"

#: 发布资产名。与 :data:`~src.localization.capture_backend_launcher.EXECUTABLE_NAME` 同一个文件，
#: 这里单独写一份是为了不让本模块反向依赖拉起器（拉起器要 import 本模块取安装路径）。
ASSET_NAME = "endfield-capture-backend.exe"

#: 安装台账文件名，与 exe 并排存放
METADATA_NAME = "installed.json"

#: 距上次**成功**检查这么久之后才再查一次。检查发生在每次采样前，没有这个就会把
#: 未认证的 GitHub 配额打光。
CHECK_TTL_SECONDS = 6 * 3600.0

#: 检查**失败**后的冷却。比 TTL 短得多，好让临时网络故障能较快自愈，又不至于每拍重试。
CHECK_COOLDOWN_SECONDS = 120.0

#: 下载超时（秒）。18 MB 在慢网上也要给足时间。
DOWNLOAD_TIMEOUT = 300.0

#: 元数据请求超时
REQUEST_TIMEOUT = 20.0

_API_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
_REDIRECT_URL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
_DOWNLOAD_TMPL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases/download/{{tag}}/{ASSET_NAME}"

_USER_AGENT = "ok-end-field-capture-backend-installer"


# --------------------------------------------------------------------------- #
# 安装位置
# --------------------------------------------------------------------------- #
def managed_install_dir() -> Path:
    """托管安装目录：``<应用目录>/downloads/capture_backend``。

    用 ``__file__`` 反推而不是 ``os.getcwd()``：打包运行时工作目录未必是应用目录，而这个位置
    必须稳定——它同时是 exe 的家和后端 ``runs/`` 产物的家。
    """

    return Path(__file__).resolve().parents[2] / "downloads" / "capture_backend"


def installed_executable() -> Path:
    """托管 exe 的完整路径（**不保证存在**）。"""

    return managed_install_dir() / ASSET_NAME


def managed_runs_dir() -> Path:
    """托管后端写运行产物的目录：``<托管目录>/runs``。

    我们拉起托管的那份 exe 时会用 ``--runs-dir`` 明确指到这里。**不指的话它按 ``./runs``
    相对当前工作目录写**，而那取决于我们给的 cwd——显式指定才不依赖这个耦合。

    一个运行产物目录约 20 MB（整局报文），攒下去能到几个 GB，所以配了保留策略，
    见 :func:`prune_run_directories`。
    """

    return managed_install_dir() / "runs"


#: 后端给每局建的目录名形状：``datetime.now().strftime("%Y%m%d-%H%M%S")``
_RUN_DIR_PATTERN = re.compile(r"^\d{8}-\d{6}$")


def prune_run_directories(directory: Path, keep: int) -> int:
    """只保留最近 ``keep`` 个运行产物目录，返回删掉了几个。

    **只动名字像时间戳的目录**。这个函数会删东西，所以判据刻意收得很紧：名字不符合
    ``YYYYMMDD-HHMMSS`` 的一律不碰——认不出来就说明它不是后端按惯例建的那批，宁可漏删
    也不能误删用户放在那儿的东西。

    时间戳是定宽的，所以**字典序就是时间序**，不需要解析成 datetime。正在跑的那一局总是
    最新的一个，天然落在保留范围内，不会被自己删掉。
    """

    if keep < 0:
        return 0
    try:
        candidates = [path for path in Path(directory).iterdir() if path.is_dir() and _RUN_DIR_PATTERN.match(path.name)]
    except OSError:
        return 0
    stale = sorted(candidates, key=lambda path: path.name)[: max(0, len(candidates) - keep)]
    removed = 0
    for path in stale:
        try:
            shutil.rmtree(path)
        except OSError:
            # 删不掉（还是别人的句柄、权限不够）不该让调用方失败，下次再来。
            continue
        removed += 1
    return removed


# --------------------------------------------------------------------------- #
# 台账
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class InstalledInfo:
    """已安装版本。"""

    tag: str
    size: int
    sha256: str
    #: 安装时是否用官方摘要校验过完整性。走重定向回退路径时为 False。
    verified: bool
    installed_at: float


@dataclass(frozen=True)
class ReleaseInfo:
    """远端最新版本。"""

    tag: str
    asset_url: str
    size: int | None
    #: 官方 sha256 摘要；空串表示拿不到（API 不可用时的回退路径）
    sha256: str
    #: 来自哪条路：``api`` 或 ``redirect``
    source: str


def _sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


# --------------------------------------------------------------------------- #
# 远端版本发现
# --------------------------------------------------------------------------- #
def _open(url: str, *, timeout: float, headers: dict | None = None):
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, **(headers or {})})
    return urllib.request.urlopen(request, timeout=timeout)


def _fetch_via_api(timeout: float) -> ReleaseInfo | None:
    """走 GitHub REST API；顺带拿到官方 sha256 摘要。任何异常都返回 ``None``。"""

    try:
        with _open(_API_URL, timeout=timeout, headers={"Accept": "application/vnd.github+json"}) as response:
            payload = json.loads(response.read().decode("utf-8", errors="ignore"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    tag = str(payload.get("tag_name") or "").strip()
    assets = payload.get("assets")
    if not tag or not isinstance(assets, list):
        return None
    for asset in assets:
        if not isinstance(asset, dict) or str(asset.get("name") or "") != ASSET_NAME:
            continue
        digest = str(asset.get("digest") or "").strip()
        if digest.startswith("sha256:"):
            digest = digest[len("sha256:") :]
        elif digest:
            # 将来 GitHub 换摘要算法（比如 sha512:）时不要当成 sha256 用。
            digest = ""
        size = asset.get("size")
        return ReleaseInfo(
            tag=tag,
            asset_url=str(asset.get("browser_download_url") or _DOWNLOAD_TMPL.format(tag=tag)),
            size=int(size) if isinstance(size, (int, float)) and not isinstance(size, bool) else None,
            sha256=digest.lower(),
            source="api",
        )
    return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """拦住重定向，好从 302 的 ``Location`` 里读 tag，而不下载整个发布页。"""

    def redirect_request(self, *args, **kwargs):
        return None


def _fetch_via_redirect(timeout: float) -> ReleaseInfo | None:
    """不依赖 API 的版本发现：``/releases/latest`` 会 302 到 ``/releases/tag/<tag>``。

    这条路不消耗 API 配额。代价是拿不到官方摘要，只能事后按 ``Content-Length`` 校验大小。
    """

    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(_REDIRECT_URL, headers={"User-Agent": _USER_AGENT})
    tag = ""
    try:
        with opener.open(request, timeout=timeout) as response:
            location = response.headers.get("Location") or ""
    except urllib.error.HTTPError as exc:
        if exc.code != 302:
            return None
        location = exc.headers.get("Location") or ""
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    marker = "/releases/tag/"
    if marker in location:
        tag = location.split(marker, 1)[1].split("?", 1)[0].strip("/")
    if not tag:
        return None
    return ReleaseInfo(
        tag=tag,
        asset_url=_DOWNLOAD_TMPL.format(tag=tag),
        size=None,
        sha256="",
        source="redirect",
    )


def fetch_latest_release(*, timeout: float = REQUEST_TIMEOUT) -> ReleaseInfo | None:
    """取远端最新版本；两条路都不通时返回 ``None``。"""

    return _fetch_via_api(timeout) or _fetch_via_redirect(timeout)


def _probe_asset_size(url: str, timeout: float) -> int | None:
    """只取第一个字节，从 ``Content-Range`` 里读资产总大小。"""

    try:
        with _open(url, timeout=timeout, headers={"Range": "bytes=0-0"}) as response:
            content_range = response.headers.get("Content-Range") or ""
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    if "/" not in content_range:
        return None
    total = content_range.rsplit("/", 1)[1].strip()
    return int(total) if total.isdigit() else None


# --------------------------------------------------------------------------- #
# 下载
# --------------------------------------------------------------------------- #
def _download(
    url: str,
    staging: Path,
    *,
    expected_size: int | None,
    on_progress: Callable[[int, int | None], None] | None,
    timeout: float,
) -> None:
    """流式下载到 ``staging``；不在此处校验，由调用方校验后再原子替换。"""

    staging.parent.mkdir(parents=True, exist_ok=True)
    with _open(url, timeout=timeout) as response:
        total = response.headers.get("Content-Length")
        total_size = int(total) if total and total.isdigit() else expected_size
        received = 0
        with open(staging, "wb") as handle:
            while True:
                block = response.read(1 << 16)
                if not block:
                    break
                handle.write(block)
                received += len(block)
                if on_progress is not None:
                    on_progress(received, total_size)
    if expected_size is not None and received != expected_size:
        raise ValueError(f"后端下载不完整：期望 {expected_size} 字节，实际 {received} 字节")


class CaptureBackendInstaller:
    """确保托管 exe 存在且为最新。

    线程安全：预下载线程与「启动游戏」前的兜底可能同时进来，用一把锁串起来，后到的等前一个
    结束——在启动前那条路上，等是正确的（必须有 exe 才能拉起后端）。
    """

    def __init__(
        self,
        *,
        log_info: Callable[[str], None] | None = None,
        log_warning: Callable[[str], None] | None = None,
        install_dir: Path | None = None,
        request_timeout: float = REQUEST_TIMEOUT,
        download_timeout: float = DOWNLOAD_TIMEOUT,
        ttl: float = CHECK_TTL_SECONDS,
        cooldown: float = CHECK_COOLDOWN_SECONDS,
    ):
        self._log_info = log_info
        self._log_warning = log_warning
        self._directory = Path(install_dir) if install_dir else managed_install_dir()
        self._request_timeout = float(request_timeout)
        self._download_timeout = float(download_timeout)
        self._ttl = float(ttl)
        self._cooldown = float(cooldown)

        self._lock = threading.Lock()
        self._checked_at = 0.0
        self._next_attempt_at = 0.0
        self._warned: set[str] = set()
        #: 已确认可用的 exe 与其大小，稳态下只做一次 stat 就返回
        self._ready: Path | None = None
        self._ready_size = -1

    # ------------------------------------------------------------------ #
    # 日志
    # ------------------------------------------------------------------ #
    def _info(self, message: str) -> None:
        if callable(self._log_info):
            self._log_info(message)

    def _warn_once(self, key: str, message: str) -> None:
        if key in self._warned:
            return
        self._warned.add(key)
        if callable(self._log_warning):
            self._log_warning(message)

    def _clear_warning(self, key: str) -> None:
        self._warned.discard(key)

    # ------------------------------------------------------------------ #
    # 路径
    # ------------------------------------------------------------------ #
    def executable_path(self) -> Path:
        return self._directory / ASSET_NAME

    def _metadata_path(self) -> Path:
        return self._directory / METADATA_NAME

    def _read_installed(self) -> InstalledInfo | None:
        try:
            raw = json.loads(self._metadata_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(raw, dict):
            return None
        tag = str(raw.get("tag") or "").strip()
        try:
            size = int(raw.get("size") or 0)
        except (TypeError, ValueError):
            return None
        if not tag or size <= 0:
            return None
        return InstalledInfo(
            tag=tag,
            size=size,
            sha256=str(raw.get("sha256") or ""),
            verified=bool(raw.get("verified")),
            installed_at=float(raw.get("installed_at") or 0.0),
        )

    def _write_installed(self, info: InstalledInfo) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        target = self._metadata_path()
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(asdict(info), ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, target)

    def _mark_ready(self, path: Path) -> Path:
        self._ready = path
        try:
            self._ready_size = path.stat().st_size
        except OSError:
            self._ready_size = -1
        return path

    # ------------------------------------------------------------------ #
    # 对外
    # ------------------------------------------------------------------ #
    def ensure_installed(self, *, allow_network: bool = True) -> Path | None:
        """返回可用的 exe 路径；没有且拿不到时返回 ``None``。

        ``allow_network=False`` 时只认本地已有文件（关掉自动启动后不该偷偷联网）。
        """

        exe = self.executable_path()
        # 稳态快路径：一次 stat，不联网、不哈希。
        if self._ready is not None and exe.is_file() and exe.stat().st_size == self._ready_size:
            return exe

        if not allow_network:
            return exe if exe.is_file() else None

        with self._lock:
            now = time.monotonic()
            installed = self._read_installed()
            have_local = exe.is_file() and installed is not None and exe.stat().st_size == installed.size

            if now < self._next_attempt_at:
                return self._mark_ready(exe) if have_local else (exe if exe.is_file() else None)
            if have_local and now - self._checked_at < self._ttl:
                self._checked_at = self._checked_at or now
                return self._mark_ready(exe)

            release = fetch_latest_release(timeout=self._request_timeout)
            if release is None:
                self._next_attempt_at = now + self._cooldown
                self._warn_once(
                    "no_release",
                    "实验技术：拿不到抓包后端的发布信息（网络不可用，或 GitHub 接口被限流）。"
                    + ("将继续使用已下载的版本。" if have_local else "当前没有可用的后端程序。"),
                )
                return self._mark_ready(exe) if have_local else (exe if exe.is_file() else None)

            self._clear_warning("no_release")
            self._checked_at = now
            if have_local and installed is not None and installed.tag == release.tag:
                return self._mark_ready(exe)

            try:
                self._install(release, exe, installed)
            except Exception as exc:  # 下载失败不该阻断游戏，用旧的继续
                self._next_attempt_at = now + self._cooldown
                self._warn_once(
                    f"install_failed:{type(exc).__name__}",
                    f"实验技术：下载抓包后端 {release.tag} 失败（{type(exc).__name__}）"
                    + ("，继续使用已下载的版本。" if have_local else "。请检查网络后重试。"),
                )
                return self._mark_ready(exe) if have_local else (exe if exe.is_file() else None)
            return self._mark_ready(exe)

    def _install(self, release: ReleaseInfo, exe: Path, installed: InstalledInfo | None) -> None:
        """下载 → 校验 → 原子替换 → 记台账。任何一步失败都抛异常，由调用方兜底。"""

        previous = installed.tag if installed else ""
        if previous:
            self._info(f"实验技术：发现抓包后端新版本 {release.tag}（当前 {previous}），开始更新")
        else:
            self._info(f"实验技术：开始下载抓包后端 {release.tag}（{ASSET_NAME}）")

        size = release.size or _probe_asset_size(release.asset_url, self._request_timeout)
        staging = exe.with_name(exe.name + ".download")
        staging.unlink(missing_ok=True)

        reported = -1

        def on_progress(received: int, total: int | None) -> None:
            nonlocal reported
            if not total:
                return
            percent = received * 100 // total
            step = percent // 25
            if step > reported:
                reported = step
                self._info(f"实验技术：抓包后端下载中 {percent}%")

        try:
            _download(
                release.asset_url,
                staging,
                expected_size=size,
                on_progress=on_progress,
                timeout=self._download_timeout,
            )

            actual_size = staging.stat().st_size
            if size is not None and actual_size != size:
                raise ValueError(f"大小不符：期望 {size}，实际 {actual_size}")

            actual_sha = _sha256_file(staging)
            verified = bool(release.sha256)
            if verified and actual_sha != release.sha256:
                raise ValueError("sha256 校验失败，拒绝安装")
            if not verified:
                self._warn_once(
                    "unverified",
                    "实验技术：这次下载没拿到官方 sha256 摘要（GitHub 接口不可用），"
                    "只校验了文件大小。如需完整校验请稍后点「检查并更新后端」重试。",
                )

            # 先落盘校验过的文件再替换正式 exe：替换是原子的，不会出现半截 exe 被拉起的窗口。
            os.replace(staging, exe)
        finally:
            staging.unlink(missing_ok=True)

        self._write_installed(
            InstalledInfo(
                tag=release.tag,
                size=actual_size,
                sha256=actual_sha,
                verified=verified,
                installed_at=time.time(),
            )
        )
        suffix = "" if verified else "（未校验摘要）"
        self._info(f"实验技术：抓包后端 {release.tag} 已就绪{suffix}")

    def check_now(self) -> Path | None:
        """用户手动触发的一次检查；忽略 TTL，但仍受并发锁保护。"""

        with self._lock:
            self._checked_at = 0.0
            self._next_attempt_at = 0.0
        self._warned.clear()
        return self.ensure_installed(allow_network=True)
