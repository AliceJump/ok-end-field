"""抓包直通定位数据源的契约测试（headless，不需要 Qt、游戏或真实后端）。"""

from __future__ import annotations

import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

from src.localization.capture_position_source import (
    CAPTURE_HEALTH_BACKEND_DOWN,
    CAPTURE_HEALTH_DECODING,
    CAPTURE_HEALTH_HANDSHAKE_PENDING,
    CAPTURE_HEALTH_MISSED_HANDSHAKE,
    CAPTURE_HEALTH_NO_CONNECTION,
    CAPTURE_SOURCE_NAME,
    FLOW_IDLE_SECONDS,
    SCENE_MAP_NAMES,
    CaptureHealth,
    CapturePositionSource,
    map_id_for_scene,
)
from src.localization.minimap_position_mixin import (
    CAPTURE_RELOGIN_ATTEMPTS,
    MinimapPositionMixin,
)


def _state_payload(
    *, scene=232, x=-243.2, y=259.47, z=-228.39, yaw=250.586, speed=0.0, ws_map_id="map02",
    level_id="map02_lv005", level_name="试验园区", map_id="map02", map_name="武陵", dungeon_name="",
    captured_at=None, flow=None, flows=None,
):
    """构造后端 /api/state 的响应。

    ``level_id`` / ``map_id`` 是后端场景表查出来的字段（``sceneNumId`` 即客户端
    ``LevelBasicInfoTable.idNum``）。老版本后端没有这几个字段，传 ``level_id=None`` 来模拟。

    ``flow`` / ``flows`` 是连接信息：``flow`` 写进 ``lastPosition``（这条坐标由哪条连接解出），
    ``flows`` 是后端的状态表。两者都不给就等价于老版本后端。
    """

    last = {
        "time": time.time() if captured_at is None else captured_at,
        "scene": scene,
        "x": x,
        "y": y,
        "z": z,
        "yaw": yaw,
        "speed": speed,
    }
    if level_id is not None:
        last.update(levelId=level_id, levelName=level_name, mapId=map_id, mapName=map_name,
                    dungeonName=dungeon_name)
    if flow is not None:
        last["flow"] = flow
    payload = {"lastPosition": last, "ws": {"lastPosition": {"mapId": ws_map_id}}}
    if flows is not None:
        payload["flows"] = flows
    return payload


def _flows_entry(flow_id=1, *, status="正在解码", last_packet=None):
    """构造后端 ``/api/state`` 里的一条 ``flows[]`` 记录。"""

    return {
        "id": flow_id,
        "client": "127.0.0.1:50000",
        "server": "203.0.113.7:30000",
        "status": status,
        "frames": 10,
        "positions": 3,
        "gaps": {},
        "lastPacket": time.time() if last_packet is None else last_packet,
    }


class _Handler(BaseHTTPRequestHandler):
    payload: ClassVar[dict] = {}

    def log_message(self, *args):
        pass

    def do_GET(self):
        body = json.dumps(self.payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class FakeBackend:
    """最小 HTTP 后端，用来喂固定的状态对象。"""

    def __init__(self, payload: dict):
        handler = type("_H", (_Handler,), {"payload": payload})
        self.handler = handler
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def set_payload(self, payload: dict) -> None:
        self.handler.payload = payload

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


class SceneTableTest(unittest.TestCase):
    def test_known_and_unknown_scene(self):
        self.assertEqual(map_id_for_scene(232), "map02")
        self.assertEqual(map_id_for_scene("102"), "map02")
        self.assertEqual(map_id_for_scene(999999), "")
        self.assertEqual(map_id_for_scene(None), "")
        self.assertEqual(map_id_for_scene("不是数字"), "")

    def test_scene_table_is_not_one_to_one(self):
        """同一个地图可以有多个 scene_num_id —— 表里必须都能命中，且不能反向唯一化。"""

        maps = set(SCENE_MAP_NAMES.values())
        self.assertEqual(maps, {"map02"})


class PollTest(unittest.TestCase):
    def test_reads_coordinates_and_heading(self):
        # 两边都固定：payload 给一个已知的抓包时刻，poll 也传同一基准的"现在"
        with FakeBackend(_state_payload(captured_at=1791802263.33)) as backend:
            source = CapturePositionSource(backend.port)
            sample = source.poll(now=1791802263.33 + 0.2)
        self.assertIsNotNone(sample)
        self.assertAlmostEqual(sample.x, -243.2)
        self.assertAlmostEqual(sample.y, 259.47)
        self.assertAlmostEqual(sample.z, -228.39)
        self.assertAlmostEqual(sample.heading, 250.586)
        self.assertAlmostEqual(sample.speed, 0.0)
        self.assertAlmostEqual(sample.age_seconds, 0.2, places=3)
        self.assertEqual(sample.map_id, "map02")

    def test_scene_table_wins_over_ws_for_a_known_scene(self):
        """场景在客户端表里时以它为准。

        这不会把陈旧的表固化成错答案：后端有客户端哈希闸门，表一旦与客户端版本不符，
        后端根本起不来，所以"表是旧的但仍在用"这个情形不存在。
        """

        with FakeBackend(_state_payload(scene=232, ws_map_id="map01")) as backend:
            sample = CapturePositionSource(backend.port).poll()
        self.assertEqual(sample.map_id, "map02")
        self.assertEqual(sample.map_id_source, "scene_table")

    def test_old_backend_without_scene_fields_still_resolves(self):
        """老版本后端不带 levelId/mapId：应退回手写表 + 坐标推断，仍能定出地图。"""

        with FakeBackend(_state_payload(scene=102, ws_map_id="", level_id=None)) as backend:
            sample = CapturePositionSource(backend.port).poll()
        self.assertEqual(sample.map_id, "map02")
        self.assertEqual(sample.map_id_source, "manual_table")

    def test_unknown_scene_and_no_ws_yields_empty_map_id(self):
        warnings: list[str] = []
        with FakeBackend(_state_payload(scene=777, ws_map_id="", level_id="", map_id="")) as backend:
            source = CapturePositionSource(backend.port, log_warning=warnings.append)
            sample = source.poll()
        self.assertIsNotNone(sample)
        self.assertEqual(sample.map_id, "")
        self.assertEqual(sample.map_id_source, "unknown")
        self.assertTrue(warnings, "认不出场景时应当留下告警")

    def test_missing_heading_field_is_tolerated(self):
        """后端没升级、没有 yaw/speed 字段时不能崩。"""

        payload = _state_payload()
        payload["lastPosition"].pop("yaw")
        payload["lastPosition"].pop("speed")
        with FakeBackend(payload) as backend:
            sample = CapturePositionSource(backend.port).poll()
        self.assertIsNone(sample.heading)
        self.assertIsNone(sample.speed)

    def test_no_position_yet(self):
        warnings: list[str] = []
        with FakeBackend({"lastPosition": None, "ws": {}}) as backend:
            source = CapturePositionSource(backend.port, log_warning=warnings.append)
            self.assertIsNone(source.poll())
        self.assertTrue(any("还没有解出角色坐标" in text for text in warnings))

    def test_no_position_points_at_restarting_the_game_when_the_handshake_was_missed(self):
        """**这是那条最容易被误处理的故障**：后端被重启过 → 本局拿不到登录握手。

        泛泛地报"还没有解出角色坐标"会让用户去重启后端，而那**只会更糟**——新后端看到的仍是
        一条没有 SYN 的连接，永远拿不到握手。所以这里必须直接说清该做什么。
        """

        warnings: list[str] = []
        payload = {
            "lastPosition": None,
            "ws": {},
            "flows": [_flows_entry(1, status="缺少连接起点，请重新启动游戏")],
        }
        with FakeBackend(payload) as backend:
            source = CapturePositionSource(backend.port, log_warning=warnings.append)
            source.poll()
        self.assertEqual(len(warnings), 1)
        self.assertIn("重开游戏", warnings[0])
        self.assertIn("重启后端没有用", warnings[0])

    def test_no_position_without_that_flow_keeps_the_generic_hint(self):
        """没有那条终态时不能乱指路：说法要停在"确认启动顺序"。"""

        warnings: list[str] = []
        with FakeBackend({"lastPosition": None, "ws": {}, "flows": [_flows_entry(1)]}) as backend:
            source = CapturePositionSource(backend.port, log_warning=warnings.append)
            source.poll()
        self.assertEqual(len(warnings), 1)
        self.assertIn("本局连接建立之前启动", warnings[0])
        self.assertNotIn("重开游戏", warnings[0])

    def test_missing_handshake_hint_does_not_repeat_and_rewarns_on_change(self):
        """分类进了去重键：同一分类只报一次，但分类变化时应当重新报一次。"""

        warnings: list[str] = []
        payload = {
            "lastPosition": None,
            "ws": {},
            "flows": [_flows_entry(1, status="缺少连接起点，请重新启动游戏")],
        }
        with FakeBackend(payload) as backend:
            source = CapturePositionSource(backend.port, log_warning=warnings.append)
            source.poll()
            source.poll()
            self.assertEqual(len(warnings), 1, "同一分类不应重复告警")
            backend.set_payload({"lastPosition": None, "ws": {}, "flows": [_flows_entry(1)]})
            source.poll()
        self.assertEqual(len(warnings), 2, "分类变化后应重新告警")

    def test_unreachable_warns_once(self):
        warnings: list[str] = []
        source = CapturePositionSource(19999, log_warning=warnings.append, timeout=0.2)
        for _ in range(3):
            self.assertIsNone(source.poll())
        self.assertEqual(len(warnings), 1, "同一原因只应告警一次")

    def test_unreachable_reason_change_rewarns(self):
        warnings: list[str] = []
        source = CapturePositionSource(19999, log_warning=warnings.append, timeout=0.2)
        source.poll()
        with FakeBackend({"lastPosition": None, "ws": {}}) as backend:
            source.port = backend.port
            source.poll()
        self.assertEqual(len(warnings), 2, "原因变了应重新告警")


class _FakeOwner(MinimapPositionMixin):
    """只保留定位 mixin 的最小宿主：补齐日志、时钟与游戏交互接口。

    游戏交互那几个（``wait_ocr`` / ``ensure_main`` / …）在真实任务上来自 ``BaseEfTask``，这里
    给一组可被逐个覆盖的桩，好让"重建连接"那条路能在无 GUI 环境下被完整走一遍。
    """

    def __init__(self, config: dict):
        self.config = dict(config)
        self._clock = 1000.0
        self.clicks: list = []
        self._init_minimap_position_mixin()

    def active_time(self) -> float:
        return self._clock

    def sleep(self, seconds=0.0):
        self._clock += max(0.0, float(seconds or 0.0))

    def log_info(self, *args, **kwargs):
        pass

    def log_warning(self, *args, **kwargs):
        pass

    # --- 游戏交互桩（默认"什么都找不到"，由用例按需覆盖）--- #
    box = type("_Box", (), {"center": (0.0, 0.0, 1.0, 1.0)})()

    def wait_ocr(self, *args, **kwargs):
        return None

    def wait_feature(self, *args, **kwargs):
        return None

    def click(self, *args, **kwargs):
        self.clicks.append(args)

    def ensure_main(self, *args, **kwargs):
        pass

    def back(self, *args, **kwargs):
        pass

    def click_confirm(self, *args, **kwargs):
        pass


class PoseContractTest(unittest.TestCase):
    """直通模式下组装的 pose 必须让导航既不停车、也不去等校准。"""

    def _pose_for(self, payload: dict):
        with FakeBackend(payload) as backend:
            owner = _FakeOwner({"使用实验技术": True, "实验技术后端端口": backend.port})
            return owner._capture_world_pose(now=1791802263.33)

    def test_passthrough_never_parks_navigation(self):
        pose = self._pose_for(_state_payload())
        self.assertTrue(pose["position_trusted"], "否则导航会停在 WAIT_TRUST")
        self.assertTrue(pose["anchor_set"], "否则导航会停在 WAIT_ANCHOR")
        self.assertFalse(pose["sync_needed"], "直通模式不应请求校准")
        self.assertEqual(pose["distance_since_sync"], 0.0)
        self.assertEqual(pose["trust_reason"], CAPTURE_SOURCE_NAME)
        self.assertEqual(pose["map_id"], "map02")
        self.assertAlmostEqual(pose["y"], 259.47, places=3)
        self.assertAlmostEqual(pose["heading"], 250.586)
        self.assertEqual(pose["heading_score"], 1.0, "朝向是上报真值，置信度给满")

    def test_rest_comes_from_reported_speed(self):
        self.assertTrue(self._pose_for(_state_payload(speed=0.0))["rest"])
        self.assertFalse(self._pose_for(_state_payload(speed=6.85))["rest"])

    def test_source_name_is_capture(self):
        self.assertEqual(_FakeOwner({"使用实验技术": True})._minimap_position_source_name(), CAPTURE_SOURCE_NAME)

    def test_source_name_is_minimap_when_disabled(self):
        self.assertEqual(_FakeOwner({"使用实验技术": False})._minimap_position_source_name(), "minimap")

    def test_sync_seq_advances_only_on_new_coordinate(self):
        with FakeBackend(_state_payload()) as backend:
            owner = _FakeOwner({"使用实验技术": True, "实验技术后端端口": backend.port})
            first = owner._capture_world_pose()
            again = owner._capture_world_pose()
        self.assertEqual(first["sync_seq"], 1)
        self.assertEqual(again["sync_seq"], 1, "同一坐标重复到达不应推进序号")

    def test_sync_seq_advances_when_coordinate_changes(self):
        with FakeBackend(_state_payload()) as backend:
            owner = _FakeOwner({"使用实验技术": True, "实验技术后端端口": backend.port})
            first = owner._capture_world_pose()
            backend.set_payload(_state_payload(x=-240.0))
            second = owner._capture_world_pose()
        self.assertEqual(second["sync_seq"], first["sync_seq"] + 1)

    def test_unavailable_backend_reports_no_position(self):
        """后端不可用时不能伪造坐标：必须显式给 None 让消费者看到"位置不可用"。"""

        owner = _FakeOwner({"使用实验技术": True, "实验技术后端端口": 19999})
        pose = owner._capture_world_pose()
        self.assertIsNone(pose["x"])
        self.assertFalse(pose["position_trusted"])
        self.assertEqual(pose["trust_reason"], "capture_unavailable")
        self.assertFalse(pose["sync_needed"])


class SessionLivenessTest(unittest.TestCase):
    """坐标属于哪条连接、那条连接还在不在。

    **这是退出→重新登录的核心缺口**：后端把最后一次解出的坐标一直留着（它的 ``state.last``
    没有清空点），所以登出之后、新会话报出第一条坐标之前，``/api/state`` 给的仍是**上一局**的
    坐标——可能是另一张地图、乃至另一个账号的位置。这些用例钉住应用侧不会把它当真值喂给导航。
    """

    def _pose_for(self, payload: dict):
        with FakeBackend(payload) as backend:
            owner = _FakeOwner({"使用实验技术": True, "实验技术后端端口": backend.port})
            return owner._capture_world_pose()

    def test_live_connection_is_trusted(self):
        pose = self._pose_for(_state_payload(flow=1, flows=[_flows_entry(1)]))
        self.assertTrue(pose["position_trusted"])
        self.assertEqual(pose["trust_reason"], CAPTURE_SOURCE_NAME)
        self.assertEqual(pose["capture_flow"], 1)
        self.assertTrue(pose["capture_flow_alive"])

    def test_ended_connection_is_not_trusted(self):
        """登出：FIN 已到，坐标还在，但它描述的是上一局的位置。"""

        pose = self._pose_for(_state_payload(flow=1, flows=[_flows_entry(1, status="连接已结束")]))
        self.assertFalse(pose["position_trusted"], "上一局的坐标不能喂给导航")
        self.assertEqual(pose["trust_reason"], "capture_session_ended")
        self.assertEqual(pose["capture_flow_status"], "连接已结束")
        self.assertFalse(pose["anchor_set"])
        # 坐标照给：让导航停在 WAIT_TRUST 并把原因打出来，而不是含糊地报"未锚定"。
        self.assertAlmostEqual(pose["x"], -243.2, places=3)
        self.assertFalse(pose["sync_needed"], "直通模式仍不该请求校准")

    def test_decoder_failure_is_not_trusted(self):
        """``解码停止：<原因>`` 是终态，后面拼了原因，只能按前缀判。

        它的 ``lastPacket`` 是**新鲜的**（实测这条判据拿不到它：``receive`` 在 ``if self.failed:
        return`` **之前**就写了 ``last_time``，所以失败的连接 ``lastPacket`` 照常更新）。同时
        ``解码停止`` 只可能出现在 ``failed`` 时，而 snapshot 的「等待 TCP 补包」覆盖要求
        ``not flow.failed``，所以覆盖**永远**遮不住它。于是这类终态只由状态判据接住——
        与下面那条「已结束但被覆盖遮住」正好互为补集，两条都需要。
        """

        pose = self._pose_for(
            _state_payload(flow=2, flows=[_flows_entry(2, status="解码停止：加密流位置无效", last_packet=time.time())])
        )
        self.assertFalse(pose["position_trusted"])
        self.assertEqual(pose["trust_reason"], "capture_session_ended")

    def test_a_newer_connection_makes_the_old_position_stale(self):
        """重登最典型的形态：新连接已经建好并在解码，但还没报过任何坐标。"""

        pose = self._pose_for(
            _state_payload(flow=1, flows=[_flows_entry(1, status="连接已结束"), _flows_entry(2)])
        )
        self.assertFalse(pose["position_trusted"])
        self.assertEqual(pose["capture_flow"], 1, "要报出产出坐标的那条连接，不是最新的那条")

    def test_connection_missing_from_the_reported_list_is_not_trusted(self):
        """``flows`` 只留最近 12 条：查不到说明这条连接已经老到被挤出去了。"""

        pose = self._pose_for(_state_payload(flow=1, flows=[_flows_entry(7)]))
        self.assertFalse(pose["position_trusted"])
        self.assertEqual(pose["capture_flow"], 1)

    def test_silent_connection_is_not_trusted(self):
        """终态没被捕捉到（丢包，或端口复用让新连接的字节被当旧段丢掉）时只剩这一条判据。

        那种情形下连接状态会一直停在「正在解码」，坐标永远停在旧值。
        """

        silent = time.time() - (FLOW_IDLE_SECONDS + 5.0)
        pose = self._pose_for(_state_payload(flow=1, flows=[_flows_entry(1, last_packet=silent)]))
        self.assertFalse(pose["position_trusted"])
        self.assertEqual(pose["trust_reason"], "capture_session_ended")

    def test_gapped_connection_is_still_trusted(self):
        """「等待 TCP 补包」是**进行中**的状态（乱序缓存还没补齐），不是终态。"""

        pose = self._pose_for(_state_payload(flow=1, flows=[_flows_entry(1, status="等待 TCP 补包")]))
        self.assertTrue(pose["position_trusted"])

    def test_ended_but_gapped_connection_is_not_trusted(self):
        """**回归**：后端会把状态计算覆盖成「等待 TCP 补包」，而它能遮住「连接已结束」。

        覆盖率见 ``State.snapshot``：``status = "等待 TCP 补包" if any(gaps.values()) and
        not flow.failed else flow.status``，而收到 FIN 的 flow 并不是 ``failed``。所以**连接
        结束时如果还有没补齐的 TCP 空洞，终态就被遮住了**，只看状态字符串会漏判——这一条
        由 :data:`FLOW_IDLE_SECONDS` 的静默判据接住。
        """

        silent = time.time() - (FLOW_IDLE_SECONDS + 5.0)
        pose = self._pose_for(
            _state_payload(flow=1, flows=[_flows_entry(1, status="等待 TCP 补包", last_packet=silent)])
        )
        self.assertFalse(pose["position_trusted"])
        self.assertEqual(pose["trust_reason"], "capture_session_ended")

    def test_missing_flows_is_trusted_but_warns_once(self):
        """契约里没有版本字段：后端换了键名时，这项门控会**静默失效**。至少要说一声。

        只告警一次——它每个采样周期都会命中，刷屏没用；同时仍然放行，因为"缺信息"和
        "会话已死"是两件事。
        """

        warnings: list[str] = []
        payload = _state_payload(flow=1)
        with FakeBackend(payload) as backend:
            owner = _FakeOwner({"使用实验技术": True, "实验技术后端端口": backend.port})
            owner.log_warning = warnings.append
            first = owner._capture_world_pose()
            second = owner._capture_world_pose()
        self.assertTrue(first["position_trusted"])
        self.assertTrue(second["position_trusted"])
        self.assertEqual(len(warnings), 1, "同一原因只告警一次")
        self.assertIn("flows", warnings[0])

    def test_older_backend_without_flow_reporting_is_still_trusted(self):
        """不报 ``flows`` 的老版本后端：缺信息不等于会话已死，不能把整条定位链停掉。"""

        self.assertTrue(self._pose_for(_state_payload())["position_trusted"])

    def test_position_without_a_flow_id_is_still_trusted(self):
        self.assertTrue(self._pose_for(_state_payload(flows=[_flows_entry(1)]))["position_trusted"])

    def test_sync_seq_advances_when_only_the_connection_changed(self):
        """原地重新登录：坐标逐位相同，但换了一局，导航"已走过的已知格"必须失效。"""

        with FakeBackend(_state_payload(flow=1, flows=[_flows_entry(1)])) as backend:
            owner = _FakeOwner({"使用实验技术": True, "实验技术后端端口": backend.port})
            first = owner._capture_world_pose()
            backend.set_payload(_state_payload(flow=2, flows=[_flows_entry(2)]))
            second = owner._capture_world_pose()
        self.assertEqual(second["sync_seq"], first["sync_seq"] + 1)

    def test_identical_sample_on_the_same_connection_does_not_advance_sync_seq(self):
        """同一连接上的重复坐标仍不推进序号——这条是原先的行为，不能被上面的改动带坏。"""

        with FakeBackend(_state_payload(flow=1, flows=[_flows_entry(1)])) as backend:
            owner = _FakeOwner({"使用实验技术": True, "实验技术后端端口": backend.port})
            first = owner._capture_world_pose()
            second = owner._capture_world_pose()
        self.assertEqual(second["sync_seq"], first["sync_seq"])


class MapTrustTest(unittest.TestCase):
    """认不出地图时不能把坐标交给导航 —— 导航侧会**静默**回退到调用方请求的 map_id。"""

    def _pose_for(self, payload: dict):
        with FakeBackend(payload) as backend:
            owner = _FakeOwner({"使用实验技术": True, "实验技术后端端口": backend.port})
            return owner._capture_world_pose()

    def test_scene_outside_the_table_and_every_grid_is_not_trusted(self):
        """实测场景 463 是**自带独立坐标空间的可走区域**：那 11 秒里角色以 5.0 的速度正常行走，

        坐标在 (16, 107, -41) 附近（y=107），而 map02 的区域 y≈320、相距约 1400 个世界单位。
        拿它当 map02 会在错的网格上、从错的起点规划路线。
        """

        pose = self._pose_for(
            _state_payload(scene=463, x=16.3, y=107.3, z=-41.2, level_id="", map_id="", ws_map_id="")
        )
        self.assertIsNone(pose["map_id"])
        self.assertFalse(pose["position_trusted"])
        self.assertEqual(pose["trust_reason"], "capture_unknown_map")

    def test_scene_without_a_parent_map_is_not_trusted(self):
        """试炼场这类场景本就不属于任何大地图：没有网格可导航，这是正确结果而不是故障。"""

        pose = self._pose_for(
            _state_payload(scene=999, level_id="blackbox_power_1", map_id="", ws_map_id="")
        )
        self.assertIsNone(pose["map_id"])
        self.assertFalse(pose["position_trusted"])
        self.assertEqual(pose["trust_reason"], "capture_no_parent_map")

    def test_mapped_scene_stays_trusted(self):
        pose = self._pose_for(_state_payload(scene=232, level_id="map02_lv005", map_id="map02"))
        self.assertTrue(pose["position_trusted"])
        self.assertEqual(pose["map_id"], "map02")


class CapturePoseGatesNavigationTest(unittest.TestCase):
    """跨模块契约：抓包源说"这份坐标不能用于导航"时，导航必须真的停车。

    导航侧算的是 ``map_id = str(pose.get("map_id") or session.request.map_id or "")`` —— 会
    **静默**回退到调用方请求的地图。所以"不猜"只做在定位源里是不够的：必须由
    ``position_trusted`` 在规划之前把这一拍拦下来。实测场景 463（独立坐标空间的可走区域）
    走的正是这条路。
    """

    class _Owner:
        """只用来跑定位闸门的空宿主：闸门一旦穿过信任判定就会要求朝向阈值。"""

        def _grid_heading_min_score(self):
            return 0.5

    def _classify(self, pose: dict, *, request_map_id: str = "map02"):
        from src.nav.nav_state import GridRouteRequest, NavSession, NavTick
        from src.tasks.navigation.mixin.grid_navigation_mixin import GridNavigationMixin

        session = NavSession(
            request=GridRouteRequest(goal=(0.0, 0.0), map_id=request_map_id),
            deadline=0.0,
            limit=0.0,
            tick=0.1,
        )
        ctx = NavTick(
            session=session,
            frame=None,
            pose=pose,
            x=pose.get("x"),
            z=pose.get("z"),
            map_id=str(pose.get("map_id") or request_map_id or ""),
            sync_seq=int(pose.get("sync_seq") or 0),
            heading=pose.get("heading"),
            heading_score=pose.get("heading_score"),
        )
        owner = self._Owner()
        owner._grid_nav_classify = GridNavigationMixin.__dict__["_grid_nav_classify"].__get__(owner)
        return owner._grid_nav_classify(ctx)

    def _pose_for(self, payload: dict):
        with FakeBackend(payload) as backend:
            owner = _FakeOwner({"使用实验技术": True, "实验技术后端端口": backend.port})
            return owner._capture_world_pose()

    def test_unknown_map_pose_stops_at_wait_trust(self):
        from src.nav.nav_state import NavState

        pose = self._pose_for(
            _state_payload(scene=463, x=16.3, y=107.3, z=-41.2, level_id="", map_id="", ws_map_id="")
        )
        self.assertEqual(self._classify(pose), NavState.WAIT_TRUST)

    def test_ended_session_pose_stops_at_wait_trust(self):
        from src.nav.nav_state import NavState

        pose = self._pose_for(_state_payload(flow=1, flows=[_flows_entry(1, status="连接已结束")]))
        self.assertEqual(self._classify(pose), NavState.WAIT_TRUST)

    def test_a_healthy_pose_passes_the_gate(self):
        """反向钉子：上面的拦截必须来自坐标本身不可用，而不是把正常帧也一起拦掉。"""

        pose = self._pose_for(_state_payload(flow=1, flows=[_flows_entry(1)]))
        self.assertIsNone(self._classify(pose), "健康帧应当放行到规划阶段")


class CaptureSkipsMinimapChainTest(unittest.TestCase):
    """抓包直通模式下不该启动小地图定位链。

    导航入口 ``navigate_grid_to`` 会**无条件**调 ``ensure_runtime_position_service``，而后者无条件
    调 ``start_minimap_position``。抓包模式下那条路根本不需要比例尺/里程计/融合/地图 WS，所以要
    在 mixin 里短路——否则不只是白建对象、白启 WS，还会在拿不到比例尺时让导航**立刻失败**并报
    一条指向全局导航配置的错（而该查的是抓包后端）。
    """

    def _owner(self, config: dict):
        return _FakeOwner(config)

    def test_start_is_a_noop_and_never_reads_the_nav_profile(self):
        owner = self._owner({"使用实验技术": True})
        with patch.object(
            MinimapPositionMixin, "_nav_profile", side_effect=AssertionError("抓包模式不该读比例尺")
        ):
            self.assertTrue(owner.start_minimap_position(wait_stable=False))
        self.assertIsNone(owner._minimap_od, "不该建里程计")
        self.assertIsNone(owner._minimap_fusion, "不该建融合")

    def test_position_is_ready_without_odometry_objects(self):
        owner = self._owner({"使用实验技术": True})
        self.assertIsNone(owner._minimap_od)
        self.assertTrue(owner.minimap_position_ready)

    def test_ensure_position_service_succeeds_with_an_unusable_nav_config(self):
        """**这是导航真的会撞上的那条**：拿不到比例尺时，抓包模式也必须能过这道闸。"""

        owner = self._owner({"使用实验技术": True})
        owner.enabled = True
        owner._minimap_position_service = owner  # 让它把自己当成共享定位服务
        with patch.object(MinimapPositionMixin, "_nav_profile", return_value=None):
            service = owner.ensure_runtime_position_service(force_start=True)
        self.assertIs(service, owner, "抓包模式不该因为拿不到比例尺而返回 None")

    def test_fusion_path_still_requires_the_profile(self):
        """反向钉子：非抓包模式行为不变，仍然要靠比例尺建链。"""

        owner = self._owner({"使用实验技术": False})
        with patch.object(MinimapPositionMixin, "_nav_profile", return_value=None):
            self.assertFalse(owner.start_minimap_position(wait_stable=False))
        self.assertFalse(owner.minimap_position_ready)


class WaitReasonTest(unittest.TestCase):
    """导航等待态的文案要带上真实原因——抓包模式下没有"融合"可言。"""

    def test_reason_is_appended_when_present(self):
        from src.tasks.navigation.mixin.grid_navigation_mixin import GridNavigationMixin

        self.assertEqual(
            GridNavigationMixin._grid_wait_reason({"trust_reason": "capture_unavailable"}, "等待坐标"),
            "等待坐标: capture_unavailable",
        )

    def test_odom_reason_is_used_as_the_fallback(self):
        from src.tasks.navigation.mixin.grid_navigation_mixin import GridNavigationMixin

        self.assertEqual(
            GridNavigationMixin._grid_wait_reason({"odom_reason": "shift_too_small"}, "等待坐标"),
            "等待坐标: shift_too_small",
        )

    def test_prefix_alone_when_there_is_no_reason(self):
        from src.tasks.navigation.mixin.grid_navigation_mixin import GridNavigationMixin

        self.assertEqual(GridNavigationMixin._grid_wait_reason(None, "等待坐标"), "等待坐标")
        self.assertEqual(GridNavigationMixin._grid_wait_reason({}, "等待坐标"), "等待坐标")

    def test_the_unavailable_capture_pose_reports_capture_unavailable(self):
        """端到端：后端连不上时，导航等待文案里出现的正是 ``capture_unavailable``。"""

        from src.tasks.navigation.mixin.grid_navigation_mixin import GridNavigationMixin

        owner = _FakeOwner({"使用实验技术": True, "实验技术后端端口": 19999})
        pose = owner._capture_world_pose()
        self.assertEqual(
            GridNavigationMixin._grid_wait_reason(pose, "等待坐标"),
            "等待坐标: capture_unavailable",
        )


class NavigationSyncBypassTest(unittest.TestCase):
    """抓包直通时导航不应停车等静止校准（八个调用点共用的那一处判断）。

    判据是 pose 契约上的 ``sync_supported``，**不是** ``source`` 名称：导航层依赖 pose 的
    接口，不该知道 "capture" 是定位模块的内部命名（``tests/TestRuntimeStateArchitecture``
    钉着这条边界）。
    """

    _Ctx: ClassVar[type]

    def test_capture_source_bypasses_sync_wait(self):
        from src.nav.nav_state import SyncReason
        from src.tasks.navigation.mixin.grid_navigation_mixin import GridNavigationMixin

        class _Ctx:
            pose: ClassVar[dict] = {"sync_supported": False}
            sync_seq = 0
            deadline = 0.0
            tick = 0.1
            limit = 0.0

        class _Owner(GridNavigationMixin):
            def _wait_for_minimap_sync(self, *args, **kwargs):
                raise AssertionError("抓包直通时不应进入等待静止校准")

            def _grid_navigation_timed_out(self, deadline, limit):
                return False

        wait = _Owner()._grid_nav_sync(_Ctx(), SyncReason.TRUST)
        self.assertTrue(wait.synced)
        self.assertFalse(wait.timed_out)

    def test_source_without_the_field_still_waits(self):
        """字段缺省为 True：老的定位源（以及还没跟上契约的源）行为不变。"""

        from src.nav.nav_state import SyncReason
        from src.tasks.navigation.mixin.grid_navigation_mixin import GridNavigationMixin

        called = []

        class _Ctx:
            pose: ClassVar[dict] = {"source": "minimap"}
            sync_seq = 0
            deadline = 0.0
            tick = 0.1
            limit = 0.0

        class _Owner(GridNavigationMixin):
            def _wait_for_minimap_sync(self, *args, **kwargs):
                called.append(True)
                return True

            def _grid_navigation_timed_out(self, deadline, limit):
                return False

        self.assertTrue(_Owner()._grid_nav_sync(_Ctx(), SyncReason.TRUST).synced)
        self.assertEqual(called, [True], "没有该字段时必须照常走等待校准")

    def test_capture_pose_reaches_navigation_through_the_real_contract(self):
        """端到端：抓包源的 pose 发布后，导航读到的确实是 ``sync_supported=False``。

        上面两条直接构造 dict，这条走 ``build_world_pose``，防止"源给了但契约没透传"。
        """

        from src.runtime_state.world_pose import build_world_pose

        pose = build_world_pose({"x": 1.0, "z": 2.0, "sync_supported": False}, source=CAPTURE_SOURCE_NAME)
        self.assertFalse(pose["sync_supported"])
        legacy = build_world_pose({"x": 1.0, "z": 2.0}, source="minimap")
        self.assertTrue(legacy["sync_supported"], "缺省必须为 True")


class CoordinateInferenceTest(unittest.TestCase):
    """场景号认不出来时，按坐标落在哪张基础地图的范围里兜底。"""

    def test_infers_map02_for_the_live_position(self):
        """实测位置 (-1100.3, -251.9)：只落在 map02 范围里（map01/base01 都不包含）。"""

        from src.localization.capture_position_source import infer_map_id_from_coordinates

        self.assertEqual(infer_map_id_from_coordinates(-1100.3, -251.9), "map02")

    def test_returns_empty_outside_every_grid(self):
        from src.localization.capture_position_source import infer_map_id_from_coordinates

        self.assertEqual(infer_map_id_from_coordinates(99999.0, 99999.0), "")

    def test_returns_empty_when_grid_dir_is_missing(self):
        """网格目录不存在时必须返回空串，而不是抛异常。"""

        from src.localization.capture_position_source import infer_map_id_from_coordinates

        self.assertEqual(infer_map_id_from_coordinates(0.0, 0.0, Path("no/such/dir")), "")

    def test_scene_table_beats_ws_when_scene_is_known(self):
        """场景在表里时以场景表为准——它是客户端表给的答案，不依赖地图 WS。"""

        source = CapturePositionSource(1)
        record = {"scene": 228, "levelId": "map02_lv002", "mapId": "map02"}
        state = {"ws": {"lastPosition": {"mapId": "map01"}}}
        self.assertEqual(source._resolve_map_id(record, state, -1100.3, -251.9), ("map02", "scene_table"))

    def test_known_scene_without_parent_map_never_guesses_by_coordinates(self):
        """**这条是重点**：试炼场那类场景本就不属于任何大地图，而它们的坐标可能落在
        某张大地图的外接矩形内。此时必须返回空串，让导航明确说"没有网格"——
        按坐标猜出 map02 会让导航加载错网格、照着错坐标规划路线。"""

        source = CapturePositionSource(1)
        # 坐标故意放在 map02 范围内：如果实现去猜，这里就会得到 map02
        record = {"scene": 89, "levelId": "blackbox_basic_1", "mapId": ""}
        self.assertEqual(source._resolve_map_id(record, {}, -1100.3, -251.9), ("", "no_parent_map"))

    def test_known_scene_without_parent_map_accepts_live_ws_observation(self):
        source = CapturePositionSource(1)
        record = {"scene": 89, "levelId": "blackbox_basic_1", "mapId": ""}
        state = {"ws": {"lastPosition": {"mapId": "map02"}}}
        self.assertEqual(source._resolve_map_id(record, state, 0.0, 0.0), ("map02", "backend_ws"))

    def test_unknown_scene_falls_back_to_ws_then_table_then_coordinates(self):
        source = CapturePositionSource(1)
        unknown = {"scene": 99999, "levelId": "", "mapId": ""}
        state = {"ws": {"lastPosition": {"mapId": "map01"}}}
        self.assertEqual(source._resolve_map_id(unknown, state, 0.0, 0.0), ("map01", "backend_ws"))
        # 没有 WS：手写表认不出 99999，落到坐标推断
        self.assertEqual(source._resolve_map_id(unknown, {}, -1100.3, -251.9), ("map02", "coordinates"))
        self.assertEqual(source._resolve_map_id(unknown, {}, 99999.0, 99999.0), ("", "unknown"))

    def test_map_conflict_is_reported_but_scene_table_wins(self):
        warnings: list[str] = []
        source = CapturePositionSource(1, log_warning=warnings.append)
        record = {"scene": 228, "levelId": "map02_lv002", "mapId": "map02"}
        state = {"ws": {"lastPosition": {"mapId": "map01"}}}
        self.assertEqual(source._resolve_map_id(record, state, 0.0, 0.0)[0], "map02")
        self.assertTrue(any("按场景表处理" in w for w in warnings), "两边不一致应当留痕")


class StalenessClockTest(unittest.TestCase):
    """陈旧度必须用墙钟算——它曾经用任务单调钟去减 Unix 时间戳，恒为 0。"""

    def test_age_uses_wall_clock_not_monotonic(self):
        # 必须用"现在"往后推 3.5 秒：poll 内部用 time.time() 当当前时间。
        # 修复前这里会得到 0.0（单调钟减 Unix 时间戳被夹成 0），所以这条断言本身
        # 就是那个 bug 的回归测试。
        payload = _state_payload()
        payload = _state_payload(captured_at=time.time() - 3.5)
        with FakeBackend(payload) as backend:
            sample = CapturePositionSource(backend.port).poll()
        self.assertAlmostEqual(sample.age_seconds, 3.5, delta=1.0)

    def test_age_never_negative(self):
        payload = _state_payload()
        payload["lastPosition"]["time"] = time.time() + 100  # 时钟偏差
        with FakeBackend(payload) as backend:
            sample = CapturePositionSource(backend.port).poll()
        self.assertGreaterEqual(sample.age_seconds, 0.0)


class WarnDedupTest(unittest.TestCase):
    """语义类告警只报一次；连接类告警在恢复后可以再报。

    去重必须是**按原因记集合**：同一次采样里会同时命中多个原因（"认不出场景" + "状态里没有
    flows"），只记"最后一条"会让它们交替把对方顶掉、每个采样周期各报一次。所以这里的 payload
    都带上 ``flows``，否则数出来的就不只是本条要验的那个告警了。
    """

    def test_unknown_map_warns_once_while_position_repeats(self):
        warnings: list[str] = []
        payload = _state_payload(
            scene=999999, ws_map_id="", level_id="", map_id="", flow=1, flows=[_flows_entry(1)]
        )
        payload["lastPosition"]["x"] = 99999.0
        payload["lastPosition"]["z"] = 99999.0
        with FakeBackend(payload) as backend:
            source = CapturePositionSource(backend.port, log_warning=warnings.append)
            for _ in range(5):
                source.poll()
        self.assertEqual(len(warnings), 1, "同一原因连续 5 次采样只应告警一次")

    def test_two_different_reasons_in_one_sample_do_not_thrash(self):
        """**回归**：单槽位去重曾让两个并发原因互相顶掉，5 次采样刷出 10 条。"""

        warnings: list[str] = []
        payload = _state_payload(scene=999999, ws_map_id="", level_id="", map_id="")
        payload["lastPosition"]["x"] = 99999.0
        payload["lastPosition"]["z"] = 99999.0
        with FakeBackend(payload) as backend:
            source = CapturePositionSource(backend.port, log_warning=warnings.append)
            for _ in range(5):
                source.poll()
        # 两个原因各报一次，而不是各报五次
        self.assertEqual(len(warnings), 2, f"应收敛到每个原因一条，实际 {len(warnings)} 条：{warnings}")

    def test_connection_warning_repeats_after_recovery(self):
        warnings: list[str] = []
        source = CapturePositionSource(19999, log_warning=warnings.append, timeout=0.2)
        source.poll()
        with FakeBackend(_state_payload(flow=1, flows=[_flows_entry(1)])) as backend:
            source.port = backend.port
            source.poll()
            source.port = 19999
            source.poll()
        self.assertEqual(len(warnings), 2, "连接恢复后再次断开应重新告警")


if __name__ == "__main__":
    unittest.main()


class CaptureHealthTest(unittest.TestCase):
    """健康状态要把"还没好"和"已经废了"分开——只有后者才值得动玩家的游戏。"""

    def _health(self, payload: dict):
        with FakeBackend(payload) as backend:
            return CapturePositionSource(backend.port).health()

    def test_no_connection_when_the_backend_has_seen_nothing(self):
        self.assertEqual(self._health({"lastPosition": None, "flows": []}).state, CAPTURE_HEALTH_NO_CONNECTION)

    def test_backend_down(self):
        self.assertEqual(CapturePositionSource(19999, timeout=0.2).health().state, CAPTURE_HEALTH_BACKEND_DOWN)

    def test_handshake_pending_is_not_a_failure(self):
        h = self._health(_state_payload(flow=1, flows=[_flows_entry(1, status="等待登录初始化")]))
        self.assertEqual(h.state, CAPTURE_HEALTH_HANDSHAKE_PENDING)
        self.assertFalse(h.needs_new_connection, "握手进行中不该触发重建")
        self.assertFalse(h.settled, "还没到下结论的时候")

    def test_decoding_is_healthy(self):
        h = self._health(_state_payload(flow=1, flows=[_flows_entry(1, status="正在解码")]))
        self.assertEqual(h.state, CAPTURE_HEALTH_DECODING)

    def test_missed_handshake_is_the_only_recoverable_state(self):
        """**这是本次要修的那个**：后端没看到 SYN，只有新连接能救。"""

        h = self._health(_state_payload(flow=1, flows=[_flows_entry(1, status="缺少连接起点，请重新启动游戏")]))
        self.assertEqual(h.state, CAPTURE_HEALTH_MISSED_HANDSHAKE)
        self.assertTrue(h.needs_new_connection)
        self.assertTrue(h.settled)

    def test_normal_connection_end_does_not_ask_for_a_rebuild(self):
        """``连接已结束`` 是人家正常结束，重建连接没有意义。"""

        h = self._health(_state_payload(flow=1, flows=[_flows_entry(1, status="连接已结束")]))
        self.assertFalse(h.needs_new_connection)

    def test_a_live_connection_wins_over_an_old_dead_one(self):
        """**回归**：后端把历史连接一直留着；拿旧的死连接去判会让每次正常抓包都被误判。"""

        h = self._health(
            _state_payload(
                flow=1,
                flows=[
                    _flows_entry(1, status="缺少连接起点，请重新启动游戏"),
                    _flows_entry(2, status="正在解码"),
                ],
            )
        )
        self.assertEqual(h.state, CAPTURE_HEALTH_DECODING)
        self.assertFalse(h.needs_new_connection)


class CaptureRecoveryTest(unittest.TestCase):
    """重建连接：只在真需要时做，最多 3 次，成功即清零。"""

    def _owner(self, payload: dict | None, config: dict | None = None):
        base = {"使用实验技术": True}
        base.update(config or {})
        owner = _FakeOwner(base)
        owner.warnings = []
        owner.log_warning = lambda message, **kwargs: owner.warnings.append(message)
        if payload is not None:
            backend = FakeBackend(payload)
            backend.__enter__()
            owner._backend = backend
            owner.config["实验技术后端端口"] = backend.port
        return owner

    def test_does_nothing_when_the_capture_is_healthy(self):
        owner = self._owner(_state_payload(flow=1, flows=[_flows_entry(1, status="正在解码")]))
        try:
            with patch.object(owner, "force_new_capture_connection") as relogin:
                self.assertTrue(owner.ensure_capture_session(reason="测试"))
            relogin.assert_not_called()
        finally:
            owner._backend.__exit__(None, None, None)

    def test_does_nothing_while_the_handshake_is_still_in_progress(self):
        owner = self._owner(_state_payload(flow=1, flows=[_flows_entry(1, status="等待登录初始化")]))
        try:
            with patch.object(owner, "force_new_capture_connection") as relogin:
                self.assertFalse(owner.ensure_capture_session(reason="测试"))
            relogin.assert_not_called()
        finally:
            owner._backend.__exit__(None, None, None)

    def test_does_nothing_when_the_backend_is_down(self):
        """后端根本没起来时重建连接毫无用处，只会白打断玩家。"""

        owner = _FakeOwner({"使用实验技术": True, "实验技术后端端口": 19999})
        owner.warnings = []
        with patch.object(owner, "force_new_capture_connection") as relogin:
            self.assertFalse(owner.ensure_capture_session(reason="测试"))
        relogin.assert_not_called()

    def test_recovers_when_the_handshake_was_missed(self):
        missed = _state_payload(flow=1, flows=[_flows_entry(1, status="缺少连接起点，请重新启动游戏")])
        owner = self._owner(missed)
        try:
            with (
                patch.object(owner, "force_new_capture_connection", return_value=True),
                patch.object(
                    owner, "wait_for_capture_connection",
                    return_value=CaptureHealth(CAPTURE_HEALTH_DECODING),
                ),
            ):
                self.assertTrue(owner.ensure_capture_session(reason="测试"))
            self.assertEqual(owner._minimap_capture_relogin_attempts, 0, "成功就要清零")
        finally:
            owner._backend.__exit__(None, None, None)

    def test_gives_up_after_three_attempts(self):
        """**关键安全约束**：无人值守时绝不能反复登出登录。"""

        missed = _state_payload(flow=1, flows=[_flows_entry(1, status="缺少连接起点，请重新启动游戏")])
        owner = self._owner(missed)
        try:
            still_bad = CaptureHealth(CAPTURE_HEALTH_MISSED_HANDSHAKE, "缺少连接起点")
            with (
                patch.object(owner, "force_new_capture_connection", return_value=True) as relogin,
                patch.object(owner, "wait_for_capture_connection", return_value=still_bad),
            ):
                for _ in range(6):
                    self.assertFalse(owner.ensure_capture_session(reason="测试"))
            self.assertEqual(
                relogin.call_count, CAPTURE_RELOGIN_ATTEMPTS, f"最多试 {CAPTURE_RELOGIN_ATTEMPTS} 次"
            )
        finally:
            owner._backend.__exit__(None, None, None)

    def test_disabled_by_config(self):
        missed = _state_payload(flow=1, flows=[_flows_entry(1, status="缺少连接起点，请重新启动游戏")])
        owner = self._owner(missed, {"抓包失败时自动重新登录": False})
        try:
            with patch.object(owner, "force_new_capture_connection") as relogin:
                self.assertFalse(owner.ensure_capture_session(reason="测试"))
            relogin.assert_not_called()
            self.assertTrue(any("自动重新登录" in w for w in owner.warnings), owner.warnings)
        finally:
            owner._backend.__exit__(None, None, None)

    def test_aborts_cleanly_when_the_login_button_is_not_found(self):
        """**这条钉住"绝不盲点"**：界面上找不到按钮时要干净失败，不能继续点下去。"""

        owner = _FakeOwner({"使用实验技术": True})
        owner.warnings = []
        owner.log_warning = lambda message, **kwargs: owner.warnings.append(message)
        with (
            patch.object(owner, "_capture_leave_to_login", return_value=True),
            patch.object(owner, "wait_ocr", return_value=None),
            patch.object(owner, "click") as click,
        ):
            self.assertFalse(owner.force_new_capture_connection())
        click.assert_not_called()
        self.assertTrue(any("请手动进入" in w for w in owner.warnings), owner.warnings)

    def test_aborts_when_the_main_screen_exit_is_missing(self):
        owner = _FakeOwner({"使用实验技术": True})
        owner.warnings = []
        owner.log_warning = lambda message, **kwargs: owner.warnings.append(message)
        with (
            patch.object(owner, "ensure_main"),
            patch.object(owner, "back"),
            patch.object(owner, "wait_feature", return_value=None),
            patch.object(owner, "click") as click,
        ):
            self.assertFalse(owner.force_new_capture_connection())
        click.assert_not_called()
        self.assertTrue(any("没找到退出入口" in w for w in owner.warnings), owner.warnings)
