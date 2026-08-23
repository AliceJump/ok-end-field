# -*- coding: utf-8 -*-
# ruff: noqa: UP009
"""终末地地图接口使用的数美(SMSdk)设备ID管理。

实测结论（2026-08，基于真实浏览器 CDP 抓包 + 离线复现实验）：

1. zonai 接口的 ``dId`` 必须是数美已签发的设备指纹：纯随机串在
   ``generate_cred_by_code`` / ``auth/refresh`` 上会返回 10001 设备信息无效，
   且在真实浏览器内用原生 fetch 发随机串同样被拒（排除 TLS 指纹因素）。

2. 官方注册链路（SMSdk 3.0.0，脚本来自 bbs.hycdn.cn 的 skland-bbs 资源）：
   页面初始化 -> 收集指纹 -> gzip -> AES-128-CBC(ZeroPadding,
   key=md5(uid)[:16], iv='0102030405060708') -> RSA 加密 uid 作为信封 ->
   ``POST https://fp-it.portal101.cn/deviceprofile/v4`` ->
   服务端返回 ``{"code":1100,"detail":{"deviceId":"..."}}`` ->
   SDK 以 ``"B" + deviceId`` 写入 cookie 与 localStorage。

3. 关键性质（均有实验支撑）：
   - dId 由服务端签发，与账号无关、与浏览器环境无绑定，可跨进程长期复用；
   - 注册载荷（ep/data 加密信封）有使用次数上限（实测约 6~8 次后该载荷被
     服务端持续拒绝，返回 code=1902），但浏览器每次访问都会生成全新载荷；
   - 官方页面自身也只在会话内缓存 dId，重启浏览器即重新注册。

因此本模块采用混合策略（按优先级）：
- 已持久化的 dId 直接复用（与账号无关，可长期使用）；
- 「本地保存的注册载荷」走纯 HTTP 重放铸造（无需浏览器/Node）；
- 「Node 运行官方 SMSdk 本体」（``smsdk_runner.mjs`` + 官方 SDK 脚本，
  用浏览器环境垫片让 SDK 自己生成并提交注册请求），无需浏览器、
  无载荷次数上限；SDK 脚本不随仓库分发，首次使用时从官方 CDN
  下载并做 SHA256 校验，缓存在 ``configs/``（已 gitignore）；
- 以上都不可用时回退到「临时浏览器匿名访问官方地图页」完成注册，
  并顺带捕获全新注册载荷存入本地，供后续 HTTP 复用。
- 铸造成功的 dId 持久化到 ``configs/map_device_id.json`` 长期使用，
  仅在被风控拒绝时才需要重新铸造。

内置兜底载荷来自本项目调查期间一次性匿名临时 Profile 的抓包，
不含任何用户个人信息。
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from ok.util.file import ensure_dir_for_file, get_relative_path, write_json_file
from websockets.sync.client import connect as ws_connect

MAP_PAGE_URL = "https://game.skland.com/map/endfield"
_DEVICEPROFILE_URL = "https://fp-it.portal101.cn/deviceprofile/v4"

_SHUMEI_LS_KEY = "SK_SHUMEI_DEVICE_ID_KEY"
_STORE_PATH = get_relative_path("configs", "map_device_id.json")
_PAYLOAD_STORE_PATH = get_relative_path("configs", "map_registration_payload.json")
_DID_MIN_LENGTH = 16
_MINT_TIMEOUT_SECONDS = 90.0

_BROWSER_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)
_SHUMEI_VALUE_RE = re.compile(r'\{"id":"([^"]+)"(?:,"timestamp":(\d{10,16}))?\}')

# 数美 /deviceprofile/v4 注册载荷样本（匿名临时 Profile 抓包）。
# 注意：单个载荷有铸造次数上限（约 6~8 次），耗尽后返回 code=1902；
# 正常情况下本文件只作为首次安装的兜底，之后以浏览器捕获的新鲜载荷为主。
_REGISTRATION_PAYLOAD = {
    "appId": "default",
    "organization": "UWXspnCCJN4sfYlNfqps",
    "ep": 'WmWXdJCDSoNrEbXAPAkHHzngRtqBQ0CWvnhmVbFCEiMJKQa6wFpA/FcCFqnHpBX6/lfPhOdfNzu118yEjc7I6pHkHDsXqt6l0v/pUo+VtMhmNxOT0SgkNmTP6Fht1nYXegfBOl2q9sExG4uvMod4B1V+3tItBx9o+BpQpr1H7Ug=',
    "data": 'eeb41ebb429dddb3343cdbb67d2e10baf8f0f79d8b59e797d28861512751bbc381ecc28dbffb6619e04388da23e08e7e329bc82c349c131e2fd865041a112dacb64c265eb3dd964928dd21ca263f0c03efa235c621632b612f6af3fc8564294d4e427e648e81f1738ec5e43bf1654908afaf67005c7f7a9b4414afd321f8b6da2b3ec6f125b7cac821b959ec93c289ae613bb3c546e44599a80e94ed4dfb0e4b4ed579fd6b606add2d3c775b64ff499625737c8419a5be8f25dfe6a2c246e170099b7cfd50b18d7236ab26a0c7c60cca783c5c7736537bb11a8ebc4f5f4aabdc744860f0e8f2728e1b8321438403ecf224c9b9a83c6e177fcae220ec0dfc94fac5297a31b0bbba7eed3067f9e5dafa54b979cbecde1e632fddc4a6a333bb366d7227d21ea1b71798abae8b1d7714f7992c2c6f304b328bfa97b5ac28933aa8c6d171bab86a80b5bcff89dde7793e46daa1a16fa7a40cdd6044dfbc8506116440380ccecb2c4e0077b531c1207bd4767c146a489e9ccbf3b3675c86bd53c723373ea070a7e725adca6e7983bc6ab9fcc3600eb0884dc39d703cbe4e5325baff3e4204cc385783c14332bf6549582d4d6f6b964d0e67f2ab20968e0441a1bc4abc28ee108b3339c357a0b5540fc311785530cea1a218bd1947bfdc256d30c779a8001194d1ffea479092b4513ed0093969c43ff6c73d1ddc7078e46cf045f862f142f5d919f9e7788279df78d35f7b9a8726fe235c3b15797297446f382a40b92208e9a1895c4af37c321045e85d4271c19f9fa3bb8867f79739d569d7a6ba20d2f89d7850acd7343a00e9d9eacc79ef40fd0be331d139d30e09285d1a77c7bc89549ca8bbc5b28c6a371304b7b0f5ab2ccd946348bf1654c206d2293ac6b399cacdcbd3e53c6d961aae282275f82a44f2930312601fff0c8efdc124fabc7afaefaa61574e7c79753dbe6889eff16306f63f03f7a292429441dc206fa8656940a73debbdf46ccddd8f09b88d1b1396f752db5399a543701f20e3ccb4a0f650915728d3638b73e7350cccdee5ad6ac5b6d41a8f1afc513111296990905c93d342eb9ca22e22e75c13ec85f8602aabb2679478493270f0311be7797e6e26e38ec5fdfc7ed0630009ca6466e1170c271877d0678216f132b0e6b6a8ec414ee49f2f823bb167b71a28f4f6c384b95206f901707c0fb394f619abe49e22096a866a1d0c27408c2530e60e7316399ac6a33b8b9e54da67f172f7bbd4a96ba173f5b520912fbb13fcbb6ee51a89c388604996ce337996562c06fc63d0d47b9493c412c99469b9bcc65c48fc5f3436112dfef830aaca481e8a3021dc242d075f2d4cdd825b5e8feb67b3cb5b13b66eda1f2004c3cb256a41873b545c9dca3aa8592042590cb91',
    "os": "web",
    "encode": "5",
    "compress": "2",
}

_cached_device_id = ""
_failed_at = 0.0
_RETRY_INTERVAL_SECONDS = 30.0


def load_stored_device_id() -> str:
    """读取已持久化的自铸设备ID；不存在或格式非法时返回空串。"""
    try:
        with open(_STORE_PATH, encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, ValueError):
        return ""
    did = str((data or {}).get("id") or "").strip()
    return did if len(did) >= _DID_MIN_LENGTH else ""


def save_stored_device_id(device_id: str) -> None:
    """持久化自铸设备ID。"""
    ensure_dir_for_file(_STORE_PATH)
    write_json_file(_STORE_PATH, {
        "id": device_id,
        "minted_at": int(time.time() * 1000),
    })


def clear_stored_device_id() -> None:
    """删除已保存的设备ID，下次调用 ``ensure_map_device_id`` 时重新铸造。"""
    global _cached_device_id, _failed_at
    _cached_device_id = ""
    _failed_at = 0.0
    try:
        Path(_STORE_PATH).unlink()
    except OSError as exc:
        del exc


def load_captured_payload() -> dict:
    """读取浏览器铸造时顺带捕获的新鲜注册载荷；无则返回空 dict。"""
    try:
        with open(_PAYLOAD_STORE_PATH, encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, ValueError):
        return {}
    if isinstance(data, dict) and data.get("ep") and data.get("data"):
        return data
    return {}


def save_captured_payload(payload: dict) -> None:
    """保存浏览器捕获的注册载荷，供后续纯 HTTP 铸造复用。"""
    if not (isinstance(payload, dict) and payload.get("ep") and payload.get("data")):
        return
    ensure_dir_for_file(_PAYLOAD_STORE_PATH)
    write_json_file(_PAYLOAD_STORE_PATH, {
        **payload,
        "captured_at": int(time.time() * 1000),
    })


def _post_registration_payload(payload: dict, timeout: float) -> str:
    """提交一份注册载荷并返回 ``B + deviceId``；服务端拒绝时抛出异常。"""
    req = urllib.request.Request(
        _DEVICEPROFILE_URL,
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
    device_id = str(((result.get("detail") or {}).get("deviceId")) or "").strip()
    if len(device_id) < _DID_MIN_LENGTH:
        raise RuntimeError(f"数美注册接口未签发 deviceId: {result}")
    return "B" + device_id


def mint_device_id_http(timeout: float = 15.0) -> str:
    """主铸造路径：重放本地保存/内置的注册载荷，直接从数美接口获取新 dId。

    依次尝试「浏览器捕获的新鲜载荷」与「内置兜底载荷」；
    单个载荷有铸造次数上限，耗尽后服务端持续返回 code=1902。
    """
    errors: list[str] = []
    candidates = []
    captured = load_captured_payload()
    if captured:
        candidates.append(("本地捕获载荷", captured))
    candidates.append(("内置兜底载荷", _REGISTRATION_PAYLOAD))

    tried: list[str] = []
    for label, payload in candidates:
        key = str(payload.get("ep"))
        if key in tried:
            continue
        tried.append(key)
        try:
            return _post_registration_payload(payload, timeout)
        except Exception as e:  # noqa: BLE001 - 记录后尝试下一份载荷
            errors.append(f"{label}: {e}")
    raise RuntimeError("；".join(errors) or "没有可用的注册载荷")


def _find_node() -> str:
    return shutil.which("node") or ""


_RUNNER_PATH = Path(__file__).with_name("smsdk_runner.mjs")
# 官方 SMSdk 脚本（skland-bbs 前端引用的公开静态资源），不随仓库分发，
# 首次使用时下载到 configs/ 缓存（该目录已被 gitignore）。
_SDK_JS_URL = ("https://bbs.hycdn.cn/public/skland/others/skland-bbs/"
               "60e9c30fb0b1d1ca574c4522ca06fc7b.js")
_SDK_JS_NAME = "smsdk_60e9c30fb0b1d1ca574c4522ca06fc7b.js"
_SDK_JS_CACHE = get_relative_path("configs", _SDK_JS_NAME)
_SDK_JS_SHA256 = "2dbd8228c80c13e05c05e2e3093fd1c5935fd62937a1b0add7c6fe28a1905f9f"
_SDK_JS_MIN_SIZE = 200_000
_DID_OUTPUT_RE = re.compile(r"^DID=(.+)$", re.MULTILINE)


def _sdk_js_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fp:
        for chunk in iter(lambda: fp.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_sdk_js(dest: str) -> None:
    req = urllib.request.Request(_SDK_JS_URL, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        content = resp.read()
    if len(content) < _SDK_JS_MIN_SIZE or \
            hashlib.sha256(content).hexdigest() != _SDK_JS_SHA256:
        raise RuntimeError(
            f"官方 SDK 脚本校验失败(len={len(content)})，拒绝缓存使用")
    ensure_dir_for_file(dest)
    tmp = dest + ".part"
    with open(tmp, "wb") as fp:
        fp.write(content)
    os.replace(tmp, dest)


def _ensure_sdk_js() -> str:
    if Path(_SDK_JS_CACHE).is_file():
        try:
            if _sdk_js_sha256(_SDK_JS_CACHE) == _SDK_JS_SHA256:
                return _SDK_JS_CACHE
        except OSError:
            pass
    _download_sdk_js(_SDK_JS_CACHE)
    return _SDK_JS_CACHE


def mint_device_id_node(timeout: float = _MINT_TIMEOUT_SECONDS) -> str:
    """Node 铸造路径：运行官方 SMSdk 本体（浏览器环境垫片）完成注册。

    ``smsdk_runner.mjs`` 在 Node vm 中垫片 document/navigator/localStorage/
    XHR/canvas 等，让官方 SDK 自己采集指纹、加密并提交注册请求；
    XHR 垫片把请求转发给真实服务端。全程无浏览器，且每次铸造都是
    全新载荷，不受「载荷次数上限」限制。
    """
    node = _find_node()
    if not node:
        raise RuntimeError("未找到 node.exe")
    try:
        sdk_js = _ensure_sdk_js()
    except Exception as exc:
        raise RuntimeError(f"获取官方 SDK 脚本失败: {exc}") from exc
    if not _RUNNER_PATH.is_file():
        raise RuntimeError(f"缺少 SDK 运行文件: {_RUNNER_PATH}")
    # SDK 源码经 stdin 传给 runner，避免 CLI 传递文件路径
    sdk_source = Path(sdk_js).read_text(encoding="utf-8")

    env_base = os.environ.copy()
    attempts = [
        # Node >= 17 (OpenSSL 3) 需要 legacy provider 才启用 DES
        {**env_base, "NODE_OPTIONS": "--openssl-legacy-provider"},
        # 旧版 Node 不认识该选项时去掉重试
        env_base,
    ]
    errors: list[str] = []
    for env in attempts:
        try:
            proc = subprocess.run(
                [node, str(_RUNNER_PATH)],
                input=sdk_source,
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=timeout, env=env, check=False,
            )
        except subprocess.TimeoutExpired:
            errors.append(f"超时({timeout:.0f}s)")
            break
        except OSError as exc:
            raise RuntimeError(f"无法启动 node: {exc}") from exc
        match = _DID_OUTPUT_RE.search(proc.stdout or "")
        if match and len(match.group(1).strip()) >= _DID_MIN_LENGTH:
            return match.group(1).strip()
        stderr_tail = (proc.stderr or "").strip().splitlines()[-3:]
        errors.append("；".join(stderr_tail) or f"exit={proc.returncode}")
    raise RuntimeError("；".join(errors))


def _find_browser() -> str:
    for candidate in _BROWSER_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return ""


def _wait_devtools_port(profile_dir: str, deadline: float) -> int:
    ws_info = Path(profile_dir) / "DevToolsActivePort"
    while time.time() < deadline:
        try:
            port = int(ws_info.read_text(encoding="utf-8").splitlines()[0])
            if port > 0:
                return port
        except (OSError, ValueError, IndexError):
            pass
        time.sleep(0.2)
    raise TimeoutError("浏览器调试端口未就绪")


def _create_blank_target(port: int) -> dict:
    """在调试浏览器中新建一个 about:blank 标签页并返回 target 信息。"""
    last_error: Exception | None = None
    for method in ("PUT", "GET"):
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/json/new?"
                + urllib.request.quote("about:blank"),
                method=method,
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001 - 不同浏览器版本接受的 HTTP 方法不同
            last_error = e
    raise RuntimeError(f"无法在新浏览器中打开空白标签页: {last_error}")


def _launch_debug_browser() -> tuple[subprocess.Popen, str]:
    """启动带远程调试端口的临时浏览器，返回 (进程对象, 临时Profile目录)。"""
    browser = _find_browser()
    if not browser:
        raise RuntimeError(
            "未找到 Edge/Chrome，无法通过浏览器铸造地图设备ID(dId)；"
            "请安装 Edge 或 Chrome 后重试"
        )
    profile_dir = tempfile.mkdtemp(prefix="ok_ef_map_did_")
    try:
        proc = subprocess.Popen([
            browser,
            f"--user-data-dir={profile_dir}",
            "--remote-debugging-port=0",
            "--no-first-run",
            "--no-default-browser-check",
            "--window-size=900,700",
            "about:blank",
        ])
    except OSError:
        shutil.rmtree(profile_dir, ignore_errors=True)
        raise
    return proc, profile_dir


def _extract_profile_payload(msg: dict) -> dict | None:
    """从 Network.requestWillBeSent 事件提取数美注册载荷；无则返回 None。"""
    request = (msg.get("params") or {}).get("request") or {}
    body = request.get("postData")
    if "deviceprofile" not in request.get("url", "") or not body:
        return None
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


class _LsPoller:
    """管理 localStorage 轮询：跟踪未决请求，空闲时发起新的查询。"""

    def __init__(self, conn):
        self.conn = conn
        self.next_id = 10
        self.pending: set[int] = set()

    def maybe_send(self) -> bool:
        """无未决请求时发起新的 localStorage 查询；返回是否真的发送。"""
        if self.pending:
            return False
        self.next_id += 1
        self.pending.add(self.next_id)
        self.conn.send(json.dumps({
            "id": self.next_id,
            "method": "Runtime.evaluate",
            "params": {
                "expression":
                    f"localStorage.getItem('{_SHUMEI_LS_KEY}')",
                "returnByValue": True,
            },
        }))
        return True

    def consume_response(self, msg: dict) -> str | None:
        """消息是未决查询的响应时消费它并解析值；否则返回 None。"""
        eval_id = msg.get("id")
        if eval_id not in self.pending:
            return None
        self.pending.discard(eval_id)
        return _parse_ls_value(msg)


def _recv_cdp_message(conn) -> dict | None:
    """接收一条 CDP 消息；超时或空载返回 None。"""
    try:
        raw = conn.recv(2.0)
    except TimeoutError:
        return None
    return json.loads(raw) if raw else None


def _parse_ls_value(msg: dict) -> str | None:
    """解析 Runtime.evaluate 响应中的 localStorage 值；过短视为无效。"""
    result = ((msg.get("result") or {}).get("result") or {})
    value = result.get("value")
    if isinstance(value, str) and len(value) > 20:
        return value
    return None


def _collect_registration_via_cdp(
        conn, deadline: float) -> tuple[str | None, dict | None]:
    """循环读取 CDP 消息直到拿到 dId 或超时。

    返回 ``(ls_value, captured_payload)``：localStorage 中读到的数美串
    （未读到为 ``None``）与捕获到的注册载荷（未捕获为 ``None``）。
    """
    captured_payload: dict | None = None
    ls_value: str | None = None
    poller = _LsPoller(conn)
    while time.time() < deadline and ls_value is None:
        msg = _recv_cdp_message(conn)
        if not msg:
            continue
        if msg.get("method") == "Network.requestWillBeSent":
            payload = _extract_profile_payload(msg)
            if payload is not None:
                captured_payload = payload
            continue
        ls_value = poller.consume_response(msg)
        if ls_value is not None:
            break
        # 周期性轮询 localStorage（无未决请求时才发起新的查询）
        if poller.maybe_send():
            time.sleep(3)
    return ls_value, captured_payload


def mint_device_id_browser(timeout: float = _MINT_TIMEOUT_SECONDS) -> str:
    """备用铸造路径：临时浏览器配置匿名打开官方地图页，等待 SMSdk 完成注册。

    这一次访问会同时带回两样东西：
    1. 全新的 dId（本次返回值）；
    2. 该次注册使用的加密载荷（从网络层捕获后写入本地，
       之后 ``mint_device_id_http`` 即可用它进行免浏览器铸造）。
    """
    proc, profile_dir = _launch_debug_browser()
    conn = None
    try:
        deadline = time.time() + timeout
        port = _wait_devtools_port(profile_dir, min(deadline, time.time() + 15))
        target = _create_blank_target(port)
        conn = ws_connect(target["webSocketDebuggerUrl"], timeout=2)
        # 先启用网络监听再导航到地图页：若先导航后启用，
        # 加载早期发出的 deviceprofile 注册请求可能被漏掉
        conn.send(json.dumps({"id": 1, "method": "Network.enable"}))
        conn.send(json.dumps({
            "id": 2,
            "method": "Page.navigate",
            "params": {"url": MAP_PAGE_URL},
        }))

        ls_value, captured_payload = _collect_registration_via_cdp(
            conn, deadline)

        match = _SHUMEI_VALUE_RE.search(ls_value or "")
        if not match or len(match.group(1)) < _DID_MIN_LENGTH:
            raise TimeoutError(f"等待数美设备ID注册超时（{timeout:.0f} 秒）")

        if captured_payload:
            save_captured_payload(captured_payload)
        return match.group(1)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception as exc:  # noqa: BLE001 - 关闭失败不影响主流程
                del exc
        if proc.poll() is None:
            proc.kill()
        proc.wait()  # 等待浏览器退出：Windows 下进程未结束时 Profile 文件仍被锁定
        shutil.rmtree(profile_dir, ignore_errors=True)


def mint_device_id() -> str:
    """铸造全新 dId：HTTP 重放 -> Node 运行官方 SDK -> 临时浏览器。"""
    errors: list[str] = []
    for label, mint in (
        ("HTTP 重放", mint_device_id_http),
        ("Node+官方SDK", mint_device_id_node),
        ("临时浏览器", mint_device_id_browser),
    ):
        try:
            return mint()
        except Exception as exc:  # noqa: BLE001 - 依次回退到下一条路径
            errors.append(f"{label}({exc})")
    raise RuntimeError("地图设备ID铸造失败：" + "；".join(errors))


def ensure_map_device_id(explicit: str = "", allow_mint: bool = True) -> str:
    """返回可用的 dId：优先显式指定，其次已持久化/已缓存的值，最后现场铸造。"""
    global _cached_device_id, _failed_at

    explicit = str(explicit or "").strip()
    if explicit:
        _cached_device_id = explicit
        return explicit

    if _cached_device_id:
        return _cached_device_id

    now = time.time()
    if now - _failed_at < _RETRY_INTERVAL_SECONDS:
        return ""

    stored = load_stored_device_id()
    if stored:
        _cached_device_id = stored
        return stored

    if not allow_mint:
        return ""

    try:
        minted = mint_device_id()
    except Exception:
        _failed_at = time.time()
        raise

    save_stored_device_id(minted)
    _cached_device_id = minted
    return minted


def main() -> int:
    """命令行入口：查看当前设备ID；``--refresh`` 强制重新铸造。"""
    import argparse

    parser = argparse.ArgumentParser(description="管理终末地地图接口的自铸设备ID(dId)")
    parser.add_argument("--refresh", action="store_true", help="忽略已存值，重新铸造")
    args = parser.parse_args()

    if args.refresh:
        clear_stored_device_id()
    current = ensure_map_device_id()
    print(current)
    print(f"dId 存储位置: {_STORE_PATH}")
    print(f"载荷存储位置: {_PAYLOAD_STORE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
