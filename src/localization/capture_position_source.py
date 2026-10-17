"""实验性的抓包定位数据源（应用侧客户端）。

游戏主 TCP 连接里周期性上报主控角色的世界逻辑坐标。一个**独立的**外部程序被动抓包并
解码后，通过本地 HTTP 暴露**同一个世界坐标系**的坐标——与官方地图 WS 逐位一致，并且额外
自带真实高度、朝向与速度。

那个后端是单独的项目：``endfield-capture-backend``（与 ok-end-field 同级目录）。它需要
本模块不需要的依赖（scapy / unicorn / capstone / pycryptodome / lz4 / protobuf），
用户还得自行安装 Npcap 驱动，所以**不适合并进本应用**——本应用是打包发布的，
把它拉进来会让每个用户都背上这些体积与安装负担。

本模块只做两件事：读后端、把它翻译成定位状态契约。**不含**任何抓包或解密代码，
因此应用侧不需要新增依赖；代价是后端必须单独运行。

约定：

- 后端不可达时返回 ``None``，且同一原因只告警一次，避免刷屏；
- **坐标是绑在一条 TCP 连接上的**，而那条连接会随着玩家登出、换账号、重启游戏而结束。
  后端把"最后一次解出的坐标"一直留着不清理（它的 ``state.last`` 没有清空点），所以**登出
  之后到新会话报出第一条坐标之前，``/api/state`` 给的仍是上一局的坐标**。消费方必须按
  :attr:`CaptureSample.flow_alive` 判断这条坐标所属的连接还在不在，否则会把上一局（乃至
  另一个账号）的坐标当真值喂给导航；
- **地图名以游戏自己的地图服务为准**：后端同时跑着官方地图 WS，它的 ``mapId`` 是权威来源。
  ``scene_num_id`` **不是**地图的一一对应键——实测 ``scene=102`` 与 ``scene=232`` 都落在
  ``map02``，一个地图有多个场景号。因此 :data:`SCENE_MAP_NAMES` 只作 WS 不可用时的兜底；
  两条路都拿不到才返回空串，让导航以"没有网格"明确失败，而不是拿错网格静默规划。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib import error, request

__all__ = [
    "CAPTURE_HEALTH_BACKEND_DOWN",
    "CAPTURE_HEALTH_DECODING",
    "CAPTURE_HEALTH_HANDSHAKE_PENDING",
    "CAPTURE_HEALTH_MISSED_HANDSHAKE",
    "CAPTURE_HEALTH_NO_CONNECTION",
    "CAPTURE_HEALTH_OK",
    "CAPTURE_SOURCE_NAME",
    "FLOW_IDLE_SECONDS",
    "REST_SPEED_THRESHOLD",
    "SCENE_MAP_NAMES",
    "CaptureHealth",
    "CapturePositionSource",
    "CaptureSample",
    "infer_map_id_from_coordinates",
    "map_id_for_scene",
]

#: 定位来源名，写进 ``build_world_pose(source=...)``，导航据此判断是否走直通
CAPTURE_SOURCE_NAME = "capture"

#: 低于该速度（世界单位/秒）算静止。抓包自带速度向量，不需要从位移反推。
REST_SPEED_THRESHOLD = 0.1

#: 产出坐标的连接这么久没再收到**任何**报文，就认为这一局已经结束。
#:
#: 这条是给"连接的终态没被捕捉到"兜底的（丢包，或系统复用了同一个客户端端口导致新连接的
#: 字节被当作旧段丢掉）：那时连接状态会一直停在「正在解码」，坐标永远停在旧值，只看终态
#: 是发现不了的。取 30 秒而不是紧贴采样间隔，是因为**误判为"还活着"只是让陈旧窗口变长，
#: 误判为"已死"却会把一条正常连接上的导航停掉**；而活着的游戏连接不会这么久完全静默
#: （移动中约 0.27 秒一条，静止时也还有保活与其他对象的报文）。
FLOW_IDLE_SECONDS = 30.0

#: 连接终态的**确切**取值。
TERMINAL_FLOW_STATUSES = frozenset({"连接已结束"})

#: 后端在「抓包晚于该连接」时给的终态前缀（完整取值是 ``缺少连接起点，请重新启动游戏``）。
_NO_CONNECTION_START = "缺少连接起点"

#: 连接终态的**前缀**（后面还拼了具体原因，只能按前缀判）。
TERMINAL_FLOW_STATUS_PREFIXES = ("解码停止", _NO_CONNECTION_START)

# --------------------------------------------------------------------------- #
# 抓包会话的健康状态
# --------------------------------------------------------------------------- #
#: 有可用坐标
CAPTURE_HEALTH_OK = "ok"
#: 后端不可达
CAPTURE_HEALTH_BACKEND_DOWN = "backend_down"
#: 后端在跑，但还没看到任何游戏连接（多半是刚启动、游戏还没起）
CAPTURE_HEALTH_NO_CONNECTION = "no_connection"
#: 连接已发现，握手还没完成
CAPTURE_HEALTH_HANDSHAKE_PENDING = "handshake_pending"
#: 握手成功，正在解码（等第一条坐标）
CAPTURE_HEALTH_DECODING = "decoding"
#: **抓包晚于连接建立** —— 这条连接救不回来，只能换新连接（``no_connection`` 同理）
CAPTURE_HEALTH_MISSED_HANDSHAKE = "missed_handshake"

#: ``scene_num_id`` → 网格名。**只是近似，别当权威**。
#:
#: ``scene_num_id`` 比地图更细：实测 102 / 228 / 232 都落在 ``map02``，而且场景会在
#: 行走途中变化（实测导航到一半从 102 变成 228）。所以地图名的权威来源是游戏地图服务
#: 下发的 ``mapId``；本表只在拿不到它时用，再拿不到就按坐标范围推断。
#:
#: **不要为了消警告就随手往里加条目**：这里填错会让导航加载错的网格，比报"没有网格"更糟。
#: 只填用 WS 对照核对过的。
SCENE_MAP_NAMES: dict[int, str] = {
    102: "map02",
    232: "map02",
}

#: 后端不可达时的告警键前缀。这类告警在连接恢复后允许再次告警，语义类告警不允许。
_CONNECTION_ISSUE = "unreachable:"

#: 后端接口路径
_STATE_PATH = "/api/state"

#: 网格目录。坐标兜底推断地图名时要读它的范围。
_GRID_DIR = Path(__file__).resolve().parents[2] / "assets" / "nav-grid-data"


def map_id_for_scene(scene_num_id: object) -> str:
    """把 ``scene_num_id`` 翻成网格名；查不到返回空串。"""

    try:
        key = int(scene_num_id)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    return SCENE_MAP_NAMES.get(key, "")


def _flow_status_is_terminal(status: str) -> bool:
    """这条连接的解码状态是不是终态（不会再喂数据了）。

    后端的 ``flows[].status`` 有两类取值：进行中的（``等待登录初始化`` / ``正在解码`` /
    ``等待 TCP 补包``）和终态的（``连接已结束`` 与 ``解码停止：<原因>``）。终态一旦进入就
    不会退出，所以按它扣信任是**精确**的判据，不依赖任何时间阈值。
    """

    text = str(status or "").strip()
    return text in TERMINAL_FLOW_STATUSES or text.startswith(TERMINAL_FLOW_STATUS_PREFIXES)


def _flow_status_is_missed_handshake(status: str) -> bool:
    """这条连接是不是「抓包晚于它建立」。

    这条连接**救不回来、只能换新连接**：后端没看到 SYN，所以推不出密钥流，但它自己并没有坏
    ——换一条连接、让它从头抓握手就行。``连接已结束``（人家正常结束）与 ``解码停止``（这条流
    真出问题了）都不属于这一类，重建连接对它们没有意义。判定"该不该重进"的口径还包含
    ``no_connection``，见 ``MinimapPositionMixin.ensure_capture_session``。
    """

    return str(status or "").strip().startswith(_NO_CONNECTION_START)


@lru_cache(maxsize=4)
def _grid_extents(grid_dir: str) -> tuple[tuple[str, float, float, float, float], ...]:
    """读各张**基础**地图的范围 ``(名字, x0, z0, x1, z1)``。

    只认基础网格（``map02_4`` 这种），跳过 ``map02__scene_...`` 那种分层子网格——
    后者是同一张地图的局部图层，不是独立地图。只读元数据，不加载格点数组。
    """

    import numpy as np

    rows: list[tuple[str, float, float, float, float]] = []
    for path in sorted(Path(grid_dir).glob("*.grid.npz")):
        if "__" in path.name:
            continue
        try:
            with np.load(path, allow_pickle=True) as data:
                meta = data["meta"]
                meta = meta.item() if getattr(meta, "ndim", 0) == 0 else meta
                if isinstance(meta, (bytes, str)):
                    meta = json.loads(meta)
                if not isinstance(meta, dict):
                    continue
                name = str(meta.get("map_name") or "").strip()
                origin = meta.get("origin") or (0.0, 0.0, 0.0)
                cell = float(meta.get("cell_size") or 1.0)
                cells = data["cells"]
                shape = tuple(int(v) for v in cells.shape)
        except (OSError, ValueError, KeyError, ImportError):
            continue
        if not name or len(shape) != 2:
            continue
        x0, z0 = float(origin[0]), float(origin[2])
        rows.append((name, x0, z0, x0 + shape[1] * cell, z0 + shape[0] * cell))
    return tuple(rows)


def infer_map_id_from_coordinates(x: float, z: float, grid_dir: Path | None = None) -> str:
    """按坐标落在哪张基础地图的范围里来兜底推断地图名。

    只在「游戏地图服务没给 mapId、场景表也查不到」时才用。它够可靠是因为各张地图的
    范围基本不重叠：实测某点只落在一张基础地图里。**命中 0 张或多张都返回空串——不猜**，
    宁可让导航报"没有网格"也不要加载错的网格去规划。
    """

    directory = _GRID_DIR if grid_dir is None else Path(grid_dir)
    hits = [
        name for name, x0, z0, x1, z1 in _grid_extents(str(directory)) if x0 <= float(x) < x1 and z0 <= float(z) < z1
    ]
    return hits[0] if len(hits) == 1 else ""


@dataclass(frozen=True)
class CaptureSample:
    """后端解出的一条角色坐标。"""

    x: float
    y: float
    z: float
    #: 朝向（度，正北 0、正东 90、顺时针）。与 ``src/nav/route_follower.py`` 的
    #: ``bearing_to_point`` 同一套约定，可直接使用，无需换算。
    heading: float | None
    #: 速度模长（世界单位/秒）
    speed: float | None
    #: 网格名；空串表示没认出来
    map_id: str
    map_id_source: str
    #: 抓包时间戳（后端时钟）
    capture_t: float
    #: 该采样距今多少秒（本机时钟，仅用于显示）
    age_seconds: float
    #: ``sceneNumId``。同一局内会变（实测一次会话里走过 463 / 228 / 232 / 234 / 361），
    #: 所以它只是**诊断用**的现场信息，不是地图的键。
    scene: object = None
    #: 解出这条坐标的 TCP 连接号（后端的 ``lastPosition.flow``）。重登/换账号/重启游戏都会
    #: 换成新的一条；无法识别时为 ``None``。
    flow: int | None = None
    #: 那条连接当前的解码状态，原样带出供诊断。
    flow_status: str = ""
    #: 那条连接还在喂数据吗。``False`` 表示这条坐标来自**已经结束的一局**——坐标本身是
    #: 真的，但它描述的是过去的位置，不能拿来导航。见模块 docstring 的约定。
    flow_alive: bool = True


@dataclass(frozen=True)
class CaptureHealth:
    """抓包会话的整体健康状态。

    与 :class:`CaptureSample` 的分工：样本回答"现在有没有坐标"，健康状态回答"**为什么**没有"
    ——后者才是决定要不要干预的依据。两者的判定都读同一份 ``/api/state``，不额外发请求。
    """

    #: :data:`CAPTURE_HEALTH_*` 之一
    state: str
    #: 人类可读的说明，用于日志
    detail: str = ""
    #: 相关连接的 id / 状态，便于排查
    flow_id: int | None = None
    flow_status: str = ""

    @property
    def needs_new_connection(self) -> bool:
        """这条连接是否属于「握手已经救不回来」的情形（看到过连接，但没抓到 SYN）。

        只回答"这条连接还能不能解码"，**不回答"该不该重建"**：调用方
        （``MinimapPositionMixin.ensure_capture_session``）把 ``no_connection``（连一条游戏连接
        都没看到）也算作"需要重进"的一种。
        """

        return self.state == CAPTURE_HEALTH_MISSED_HANDSHAKE

    @property
    def settled(self) -> bool:
        """是否已经到了"不必再等"的状态（好或坏都算）。

        用来区分"还需要等一会儿"与"已经能下结论"。等一会儿的那几种不该触发恢复，也不该被
        当成失败上报。
        """

        return self.state in (
            CAPTURE_HEALTH_OK,
            CAPTURE_HEALTH_MISSED_HANDSHAKE,
            CAPTURE_HEALTH_BACKEND_DOWN,
        )


class CapturePositionSource:
    """读取外部抓包后端的位置，并翻译成定位契约所需字段。"""

    def __init__(self, port: int, *, log_info=None, log_warning=None, timeout: float = 1.0):
        self.port = int(port)
        self.timeout = float(timeout)
        self._log_info = log_info
        self._log_warning = log_warning
        #: 已经报过的告警原因。**必须是集合而不是"最后一条"**：同一次采样里可能同时命中
        #: 两个不同原因（例如"认不出场景"与"状态里没有 flows"），单槽位会让它们交替把对方
        #: 顶掉，于是每个采样周期各报一次——实测 5 次采样刷出 10 条。
        self._warned: set[str] = set()

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #
    def _warn_once(self, issue: str, message: str) -> None:
        """同一原因只告警一次；原因变化时重新告警。"""

        if issue in self._warned:
            return
        self._warned.add(issue)
        if callable(self._log_warning):
            self._log_warning(message)

    def _info(self, message: str) -> None:
        if callable(self._log_info):
            self._log_info(message)

    def _resolve_map_id(self, record: dict, state: dict, x: float, z: float) -> tuple[str, str]:
        """定地图名。**顺序与"不猜"的边界都很重要**。

        后端会带上场景表查出的 ``levelId`` / ``mapId``（``sceneNumId`` 就是客户端
        ``LevelBasicInfoTable.idNum``）。关键在于 ``levelId`` 非空 ⟺ 这个场景**在表里**，
        于是 ``mapId`` 为空有确切含义：**它不属于任何大地图**（试炼场 ``blackbox_*``、
        独立场景 ``indie_*`` 等自己就是一个地图项），表里 216 个场景有 84 个是这样。

        所以：

        1. 场景在表里且有大地图 → 用它。这是客户端表给的答案，不依赖地图 WS，随时可用。
        2. 场景在表里但**没有**大地图 → 先看地图 WS 的实时观测；没有就**返回空串**。
           **绝不按坐标范围去猜**——试炼场有自己的一套坐标，万一落在某张大地图的外接矩形里，
           猜出来的地图会让导航加载错的网格、照着错的坐标规划路线。宁可明确报"没有网格"。
        3. 场景不在表里（``levelId`` 为空，多半是客户端更新后新增的场景）→ 地图 WS →
           :data:`SCENE_MAP_NAMES` 手写表 → 按坐标范围推断（各图范围基本不重叠）。
        """

        known_scene = bool(str(record.get("levelId") or "").strip())
        declared = str(record.get("mapId") or "").strip()
        ws_map_id = ""
        ws = state.get("ws")
        if isinstance(ws, dict):
            last = ws.get("lastPosition")
            if isinstance(last, dict):
                ws_map_id = str(last.get("mapId") or "").strip()

        if known_scene:
            if declared:
                if ws_map_id and ws_map_id != declared:
                    # 两边不一致值得知道（场景表是静态快照，WS 是实时观测），但不因此改判。
                    self._warn_once(
                        f"map_conflict:{record.get('scene')}:{declared}:{ws_map_id}",
                        f"实验技术：场景 {record.get('scene')} 场景表说是 {declared}，"
                        f"地图 WS 说是 {ws_map_id}；按场景表处理。",
                    )
                return declared, "scene_table"
            if ws_map_id:
                return ws_map_id, "backend_ws"
            # 表里明确"不属于任何大地图"：不猜坐标
            return "", "no_parent_map"

        if ws_map_id:
            return ws_map_id, "backend_ws"
        mapped = map_id_for_scene(record.get("scene"))
        if mapped:
            return mapped, "manual_table"
        inferred = infer_map_id_from_coordinates(x, z)
        if inferred:
            return inferred, "coordinates"
        return "", "unknown"

    # ------------------------------------------------------------------ #
    # 对外
    # ------------------------------------------------------------------ #
    def fetch_state(self) -> dict | None:
        """读后端状态；失败返回 ``None``。"""

        url = f"http://127.0.0.1:{self.port}{_STATE_PATH}"
        try:
            with request.urlopen(url, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8", errors="ignore"))
        except (error.URLError, TimeoutError, OSError, ValueError) as exc:
            self._warn_once(
                f"unreachable:{type(exc).__name__}",
                f"实验技术：连接不到抓包后端 127.0.0.1:{self.port}（{type(exc).__name__}）。"
                "请先单独启动后端；在此之前定位会保持不可用。",
            )
            return None
        # 连上了就清掉"连不上"这条，好让它下次再断时能重新告警。
        # **只清连接类**：把整个集合清空会把"认不出场景"之类语义告警的去重一起抹掉，
        # 于是每次采样都刷一条（实测每 200ms 一条，把日志冲爆）。
        if any(issue.startswith(_CONNECTION_ISSUE) for issue in self._warned):
            self._warned = {issue for issue in self._warned if not issue.startswith(_CONNECTION_ISSUE)}
        if not isinstance(payload, dict):
            self._warn_once("bad_payload", "实验技术：抓包后端返回的不是状态对象。")
            return None
        return payload

    def health(self, *, now: float | None = None) -> CaptureHealth:
        """这条抓包链现在处于什么状态。

        **只看连接，不看坐标**——坐标缺失有好几种成因，混在一起就没法决定该做什么。关键区分：

        * 后端没看到任何连接 → 这一局已经没有位置源，逼一条新连接（见
          ``MinimapPositionMixin.ensure_capture_session``）；
        * 有连接但握手没完成 / 握手完成还没走到坐标 → 等；
        * 有连接停在「缺少连接起点」→ 这次抓包**已经失败了**，只有新连接能救；
        * 有任何一条连接走到了解码 → 正常。

        「有条死连接 + 有条活连接」要判成正常：后端把历史连接一直留着，拿旧的死连接去判断会
        让每一次正常抓包都被误判成需要重建。
        """

        state = self.fetch_state()
        if state is None:
            return CaptureHealth(CAPTURE_HEALTH_BACKEND_DOWN, "连接不到抓包后端")

        flows = state.get("flows")
        if not isinstance(flows, list) or not flows:
            return CaptureHealth(CAPTURE_HEALTH_NO_CONNECTION, "后端还没看到游戏连接")

        current = time.time() if now is None else float(now)
        pending: CaptureHealth | None = None
        missed: CaptureHealth | None = None
        for item in flows:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "")
            raw_id = item.get("id")
            flow_id = int(raw_id) if isinstance(raw_id, (int, float)) and not isinstance(raw_id, bool) else None
            if _flow_status_is_missed_handshake(status):
                missed = missed or CaptureHealth(CAPTURE_HEALTH_MISSED_HANDSHAKE, status, flow_id, status)
                continue
            if _flow_status_is_terminal(status):
                # 正常结束或解码失败：都是这条流的终局，但不代表需要重建连接
                continue
            last_packet = item.get("lastPacket")
            if isinstance(last_packet, (int, float)) and current - float(last_packet) > FLOW_IDLE_SECONDS:
                continue
            if status.strip() == "等待登录初始化":
                pending = pending or CaptureHealth(CAPTURE_HEALTH_HANDSHAKE_PENDING, status, flow_id, status)
                continue
            # 其余进行中的状态（正在解码 / 等待 TCP 补包 覆盖值）都算已经握上手了
            return CaptureHealth(CAPTURE_HEALTH_DECODING, status, flow_id, status)

        if missed is not None:
            return missed
        if pending is not None:
            return pending
        return CaptureHealth(CAPTURE_HEALTH_NO_CONNECTION, "没有仍在继续的连接")

    def _flow_liveness(self, state: dict, record: dict, now: float) -> tuple[int | None, str, bool]:
        """这条坐标是哪条连接解出来的，那条连接还在不在。

        返回 ``(flow_id, status, alive)``。**认不出来时一律当"活着"**：``flows`` 缺失
        （老版本后端）或 ``lastPosition`` 里没有 ``flow`` 时，缺信息不等于会话已死——
        与 :meth:`_resolve_map_id` 同一条原则，**没有证据就不扣信任**，否则会把一条正常的
        定位链整段停掉。``flows`` 整个缺失时会告警一次，好让这种"门控静默失效"看得见。

        三种判据，任中一条即认为该连接不会再喂数据：

        1. ``flows`` 里查不到这个 id——后端只留最近 12 条（``snapshot`` 的 ``[-12:]``），
           查不到说明它已经老到被挤出去；
        2. 状态是终态（``连接已结束`` / ``解码停止：…``）——由 FIN/RST 或解码异常写死；
        3. 这条连接已经 :data:`FLOW_IDLE_SECONDS` 没收到任何报文。

        **为什么需要第 3 条**：后端的 ``snapshot`` 会把状态**计算覆盖**成 ``等待 TCP 补包``
        （``status = "等待 TCP 补包" if any(gaps.values()) and not flow.failed else flow.status``）。
        而 ``连接已结束`` 的 flow 并不是 ``failed``，所以**连接结束时如果还有没补齐的 TCP 空洞，
        它的终态就被这个覆盖遮住了**，第 2 条看不见它。反过来 ``解码停止：…`` 只可能在
        ``failed`` 时出现，覆盖不生效，所以它永远遮不住——两类终态各自被不同的判据接住。
        （``failed`` 的 flow 其 ``lastPacket`` 仍在更新，第 3 条对它无效，也用不着。）
        """

        flows = state.get("flows")
        if not isinstance(flows, list):
            self._warn_once(
                "no_flows",
                "实验技术：抓包后端的状态里没有 flows，无法判断坐标所属的连接是否还在——"
                "这一项门控暂时失效（坐标仍照常使用）。后端版本可能过旧。",
            )
            return None, "", True
        flow_id = record.get("flow")
        if not isinstance(flow_id, (int, float)) or isinstance(flow_id, bool):
            return None, "", True
        entry = next(
            (item for item in flows if isinstance(item, dict) and item.get("id") == flow_id),
            None,
        )
        if entry is None:
            return int(flow_id), "", False
        status = str(entry.get("status") or "")
        if _flow_status_is_terminal(status):
            return int(flow_id), status, False
        last_packet = entry.get("lastPacket")
        if isinstance(last_packet, (int, float)) and now - float(last_packet) > FLOW_IDLE_SECONDS:
            return int(flow_id), status, False
        return int(flow_id), status, True

    def _no_position_hint(self, state: dict) -> tuple[str, str]:
        """没有坐标时给出**可操作**的提示；返回 ``(文案, 分类)``。

        默认文案是"确认后端在本局连接建立之前启动"——正确但泛泛，用户看了不知道该做什么。
        后端侧明确过一条：``flows[]`` 里一条连接停在「缺少连接起点」的典型成因就是**后端被
        重启过**（或启动晚于登录），而那种情况下**重启后端只会更糟**——新后端看到的仍是一条
        没有 SYN 的连接，永远拿不到握手。真正的解法是重开游戏逼出一条新连接。所以这里先扫一眼
        ``flows[]``，把这种情况挑出来直接说清该做什么。

        分类参与告警去重键：两种情况之间来回切换时应当重新告警一次。
        """

        flows = state.get("flows")
        if isinstance(flows, list):
            for item in flows:
                if isinstance(item, dict) and str(item.get("status") or "").startswith(_NO_CONNECTION_START):
                    return (
                        "实验技术：后端在抓包，但本局拿不到连接起点——登录握手发生在抓包开始之前"
                        "（最常见的原因是后端被重启过）。**请重开游戏**来逼出一条新连接；"
                        "重启后端没有用，它看到的仍是一条没有 SYN 的连接。",
                        "no_connection_start",
                    )
        return (
            "实验技术：后端已连上但还没有解出角色坐标。"
            "请确认后端是在本局连接建立之前启动的（登录握手必须在抓包范围内）。",
            "generic",
        )

    def poll(self, *, now: float | None = None) -> CaptureSample | None:
        """取最新一条坐标；没有可用坐标时返回 ``None``。"""

        state = self.fetch_state()
        if state is None:
            return None
        last = state.get("lastPosition")
        if not isinstance(last, dict):
            message, category = self._no_position_hint(state)
            self._warn_once(f"no_position:{category}", message)
            return None
        try:
            x = float(last["x"])
            y = float(last["y"])
            z = float(last["z"])
        except (KeyError, TypeError, ValueError):
            self._warn_once("bad_coordinates", "实验技术：后端坐标字段无法解析。")
            return None

        heading = last.get("yaw")
        speed = last.get("speed")
        map_id, map_id_source = self._resolve_map_id(last, state, x, z)
        if not map_id:
            if map_id_source == "no_parent_map":
                # 这是**正确**结果而不是故障：该场景本就不属于任何大地图（试炼场等），
                # nav-grid-data 里也没有它的网格。说清楚，别让人以为是定位坏了。
                self._warn_once(
                    f"no_parent_map:{last.get('scene')}",
                    f"实验技术：场景 {last.get('scene')}（{last.get('levelName') or last.get('levelId')}）"
                    "不属于任何大地图，没有对应的导航网格——该场景下不能做网格导航，这是预期行为。",
                )
            else:
                self._warn_once(
                    f"unknown_map:{last.get('scene')}",
                    f"实验技术：场景 {last.get('scene')!r} 不在场景表里，坐标 {x:.0f},{z:.0f} 也不在任何"
                    "基础地图范围内。请启用地图 WS 对照，或等后端场景表跟上客户端版本。",
                )

        # capture_t 是后端记录的**墙钟**（Unix 时间戳）。这里必须用同一个时钟去减——
        # 之前用了任务侧的 time.monotonic() 时钟，相减恒为负、被夹成 0，
        # 于是陈旧度永远显示 0.0s，等于这个安全提示完全失效。连接活性同理：``lastPacket``
        # 也是抓包墙钟，所以下面传给它的必须是同一个时钟。
        capture_t = float(last.get("time") or 0.0)
        current = time.time() if now is None else float(now)
        flow_id, flow_status, flow_alive = self._flow_liveness(state, last, current)
        return CaptureSample(
            x=x,
            y=y,
            z=z,
            heading=None if heading is None else float(heading),
            speed=None if speed is None else float(speed),
            map_id=map_id,
            map_id_source=map_id_source,
            capture_t=capture_t,
            age_seconds=max(0.0, current - capture_t) if capture_t else 0.0,
            scene=last.get("scene"),
            flow=flow_id,
            flow_status=flow_status,
            flow_alive=flow_alive,
        )
