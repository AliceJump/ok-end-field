"""Packet-position lifecycle, independent from WS and visual localization."""

from __future__ import annotations

import os
import threading

from src.core.PositionConfig import (
    CONFIG_EXPERIMENTAL_AUTOSTART,
    CONFIG_EXPERIMENTAL_EXE,
    CONFIG_EXPERIMENTAL_PORT,
    CONFIG_EXPERIMENTAL_RELOGIN,
    CONFIG_EXPERIMENTAL_STOP_ON_EXIT,
    CONFIG_USE_EXPERIMENTAL,
)
from src.data.FeatureList import FeatureList as fL
from src.localization.capture_backend_installer import CaptureBackendInstaller
from src.localization.capture_backend_launcher import (
    BackendLauncher,
    probe_backend,
    shared_launcher,
)
from src.localization.capture_position_source import (
    CAPTURE_HEALTH_DECODING,
    CAPTURE_HEALTH_MISSED_HANDSHAKE,
    CAPTURE_HEALTH_NO_CONNECTION,
    CAPTURE_HEALTH_OK,
    CAPTURE_SOURCE_NAME,
    REST_SPEED_THRESHOLD,
    CaptureHealth,
    CapturePositionSource,
)
from src.localization.capture_travel_poles import CaptureTravelPoleLoad, CaptureTravelPoleSource

CAPTURE_RELOGIN_ATTEMPTS = 3
CAPTURE_RELOGIN_WAIT_SECONDS = 90.0
CAPTURE_RELOGIN_INTERVAL_SECONDS = 20.0


class CapturePoseMixin:
    def _init_capture_pose_mixin(self):
        self._minimap_capture_source: CapturePositionSource | None = None
        self._minimap_capture_travel_pole_source: CaptureTravelPoleSource | None = None
        self._minimap_capture_launcher: BackendLauncher | None = None
        self._minimap_capture_installer: CaptureBackendInstaller | None = None
        #: 预下载线程句柄；非 None 表示已经起过一次，不再重复起
        self._minimap_capture_prefetch: threading.Thread | None = None
        #: 已尝试重建连接的次数；抓包恢复正常即清零
        self._minimap_capture_relogin_attempts = 0
        #: 重建相关的告警只报一次，避免每拍刷屏
        self._minimap_capture_relogin_warned = False
        #: 下一次允许重建的时间点（active_time 基准）
        self._minimap_capture_relogin_next_at = 0.0
        self._minimap_capture_seq = 0
        #: 上一条坐标所属的连接号；只有它变化才推进 ``_minimap_capture_seq``
        self._minimap_capture_last_flow: int | None = None

    def experimental_capture_enabled(self) -> bool:
        """是否启用外部抓包直通定位。"""

        return self._cfg_bool(CONFIG_USE_EXPERIMENTAL, False)

    def capture_position_source(self) -> CapturePositionSource:
        """按当前配置端口返回抓包数据源（端口变化时重建）。"""

        port = int(self._cfg_int(CONFIG_EXPERIMENTAL_PORT, 8765))
        source = self._minimap_capture_source
        if source is None or source.port != port:
            source = CapturePositionSource(
                port,
                log_info=self.log_info,
                log_warning=self.log_warning,
            )
            self._minimap_capture_source = source
        return source

    def capture_travel_pole_source(self) -> CaptureTravelPoleSource:
        """按当前配置端口返回按需拉取的滑索快照数据源。"""

        port = int(self._cfg_int(CONFIG_EXPERIMENTAL_PORT, 8765))
        source = self._minimap_capture_travel_pole_source
        if source is None or source.port != port:
            source = CaptureTravelPoleSource(port, log_warning=self.log_warning)
            self._minimap_capture_travel_pole_source = source
        return source

    def capture_travel_pole_graph(self, *, map_id: str, include_shared: bool) -> CaptureTravelPoleLoad:
        """读取一次抓包滑索快照；调用方负责仅在实验抓包模式下调用。"""

        return self.capture_travel_pole_source().load(map_id=map_id, include_shared=include_shared)

    def capture_backend_launcher(self) -> BackendLauncher:
        """按当前配置返回后端拉起器（与游戏启动前的自动拉起共用同一个实例）。"""

        port = int(self._cfg_int(CONFIG_EXPERIMENTAL_PORT, 8765))
        executable = self._cfg_str(CONFIG_EXPERIMENTAL_EXE, "")
        launcher = shared_launcher(
            port,
            executable=executable,
            log_info=self.log_info,
            log_warning=self.log_warning,
        )
        self._minimap_capture_launcher = launcher
        return launcher

    def capture_backend_installer(self) -> CaptureBackendInstaller:
        """按当前配置返回托管安装器（进程内复用同一个实例）。"""

        installer = self._minimap_capture_installer
        if installer is None:
            installer = CaptureBackendInstaller(
                log_info=self.log_info,
                log_warning=self.log_warning,
            )
            self._minimap_capture_installer = installer
        return installer

    def ensure_capture_backend_installed(self) -> bool:
        """确保托管目录里有一份可用的后端 exe；返回它是否可用。

        只有「使用实验技术」打开后才会走到这里，所以关着的时候一次网络请求都不会发。检查受
        :data:`~src.localization.capture_backend_installer.CHECK_TTL_SECONDS` 节流——本方法会被
        定位任务**每一拍**调用，没有节流会把未认证的 GitHub 配额打光。

        指定了「实验技术后端程序路径」（或环境变量）时完全跳过：用户自带后端就不该再联网。
        """

        if self._cfg_str(CONFIG_EXPERIMENTAL_EXE, "").strip() or os.environ.get("EF_CAPTURE_BACKEND"):
            return True
        path = self.capture_backend_installer().ensure_installed(allow_network=True)
        return path is not None and path.is_file()

    def prefetch_capture_backend(self) -> None:
        """后台预下载后端，不阻塞界面。

        时机是用户**刚勾上**风险开关的那一刻：那时他还没启动游戏，趁这段时间把 18 MB 拿下来，
        等点「启动游戏」时就不必干等。整个过程失败都不影响什么——启动前那次兜底会再试一次。
        """

        installer = self.capture_backend_installer()
        if installer.ensure_installed(allow_network=False) is not None:
            return

        def _run() -> None:
            try:
                installer.ensure_installed(allow_network=True)
            except Exception as exc:  # 后台线程里的异常没人接，必须自己吞掉
                self.log_warning(f"实验技术：后端预下载失败（{type(exc).__name__}）")

        if getattr(self, "_minimap_capture_prefetch", None) is not None:
            return
        self._minimap_capture_prefetch = threading.Thread(target=_run, name="CaptureBackendPrefetch", daemon=True)
        self._minimap_capture_prefetch.start()

    def ensure_capture_backend(self) -> bool:
        """确保抓包后端在跑；返回它当前是否可用。

        关掉自动启动时只探活、不拉起——留给"我自己管后端进程"的用法。

        **必须在游戏建立主连接之前调用**：后端要抓到登录握手才推得出正确的解密流。所以
        定位任务在启用直通的第一拍就会走到这里，而不是等到第一次要坐标时。
        """

        launcher = self.capture_backend_launcher()
        if not self._cfg_bool(CONFIG_EXPERIMENTAL_AUTOSTART, True):
            return probe_backend(launcher.port)
        # 先保证 exe 在本地（缺了就下载/更新），再拉起。这一步是**阻塞**的：宁可让启动多等
        # 十几秒，也不能让游戏先连上——那样这一局就抓不到登录握手了。
        if not self.ensure_capture_backend_installed():
            return False
        return launcher.ensure_running()

    def capture_session_health(self) -> CaptureHealth | None:
        """读一次抓包会话的健康状态；开关关着时返回 ``None``。"""

        if not self.experimental_capture_enabled():
            return None
        return self.capture_position_source().health()

    def force_new_capture_connection(self) -> bool:
        """走**完整的重进流程**（打开登出入口 → 确认 → 点一下继续），逼一条新的 TCP 连接。

        只对"抓包错过了本次握手"这种情形有意义：后端没看到 SYN 就推不出密钥流，而它本身没坏
        ——换一条连接让它从头抓就行。

        复用 :meth:`LoginMixin.relogin_same_account`：按用户口径**不重新选择账号**，确认登出后
        在登录界面（出现 ``fL.logout``、"点击任意位置继续"）点一下屏幕中央继续进入。用鸭子类型
        取方法而不是 import，定位层不反向依赖任务层；拿不到可用的重进实现就干净失败——宁可不做，
        也不要乱点。
        """

        relogin = getattr(self, "relogin_same_account", None)
        if not callable(relogin):
            self.log_warning(
                "实验技术：当前任务没有可用的重进实现，无法自动重建连接——请手动退回登录界面再进入一次",
                notify=True,
            )
            return False
        try:
            return bool(relogin())
        except Exception as exc:
            self.log_warning(f"实验技术：自动重新登录失败（{type(exc).__name__}: {exc}）", notify=True)
            return False

    def wait_for_capture_connection(self, *, timeout: float, tick: float = 2.0) -> CaptureHealth:
        """等新连接被接管：一直轮询到"握手成功"或"又错过握手"。

        游戏重新登录要读条，这期间后端会先看到「没有连接」、再到「等待登录初始化」、最后才
        「正在解码」，所以不能只查一次。返回值是最后一次读到的健康状态。
        """

        deadline = self.active_time() + max(1.0, float(timeout))
        health = self.capture_position_source().health()
        while self.active_time() < deadline:
            # 读到"正在解码"就够：握手已经拿到，坐标迟早会来。
            if health.state in (CAPTURE_HEALTH_OK, CAPTURE_HEALTH_DECODING):
                return health
            # 又错过了一次：再等下去也不会自己好，交给调用方决定要不要再试
            if health.needs_new_connection:
                return health
            self.sleep(max(0.2, float(tick)))
            health = self.capture_position_source().health()
        return health

    def ensure_capture_session(self, *, reason: str) -> bool:
        """校验抓包是否**真的**接上了；后端在跑但当前没有坐标时，回登录界面重进一次。

        这是「启用后第一拍 / 每次导航前 / 每次切号后」三个时机共用的入口。返回抓包当前是否可用。

        "可用" = 已经有坐标，或握手已成、第一条坐标在路上（``ok`` / ``decoding``）。

        会触发重建的只有**后端在跑、但当前没有坐标且不会自己恢复**这一种：``no_connection``
        （后端一条游戏连接都没看到）与 ``missed_handshake``（看到连接却没过握手）。前者多半是
        后端晚于游戏连接才开始抓，后者是错过了 SYN——两种都靠"退到登录界面再进入"逼一条新连接，
        让后端从连接开头抓握手。

        ``handshake_pending``（握手正在进行）与 ``backend_down``（后端不可达）都不动手：前者再等
        一会就好，后者重建也没用——对它们做"退出重进"只会白白打断玩家。

        最多尝试 :data:`CAPTURE_RELOGIN_ATTEMPTS` 次，**成功即清零**：重登后能正常抓包，下一次
        再出问题理应还能修。次数用尽就停手并报错，绝不在无人值守时反复登出。
        """

        health = self.capture_session_health()
        if health is None:
            return False
        if health.state in (CAPTURE_HEALTH_OK, CAPTURE_HEALTH_DECODING):
            self._minimap_capture_relogin_attempts = 0
            self._minimap_capture_relogin_warned = False
            return True
        if health.state not in (CAPTURE_HEALTH_NO_CONNECTION, CAPTURE_HEALTH_MISSED_HANDSHAKE):
            return False
        # A trigger may run on the login screen. Leave login to its owner;
        # recoverable menus still use relogin_same_account's ensure_main path.
        find_one = getattr(self, "find_one", None)
        if not callable(find_one) or find_one(fL.logout):
            return False
        if not self._cfg_bool(CONFIG_EXPERIMENTAL_RELOGIN, True):
            self._warn_capture_relogin_disabled()
            return False
        if self._minimap_capture_relogin_attempts >= CAPTURE_RELOGIN_ATTEMPTS:
            self._warn_capture_relogin_exhausted(health)
            return False
        # 本方法每拍都会被调（"没坐标"时），没有这个间隔的话三次尝试会在一分钟内打光：
        # 实测两次之间只隔了 14 秒，而每次都要把玩家从游戏里踢回登录界面——太激进了。
        now = self.active_time()
        if now < self._minimap_capture_relogin_next_at:
            return False
        self._minimap_capture_relogin_next_at = now + CAPTURE_RELOGIN_INTERVAL_SECONDS

        self._minimap_capture_relogin_attempts += 1
        attempt = self._minimap_capture_relogin_attempts
        self.log_warning(
            f"实验技术：{reason}——本次抓包没有可用坐标（{health.detail or health.state}），"
            f"正在返回登录界面重新进入以建立新连接（第 {attempt}/{CAPTURE_RELOGIN_ATTEMPTS} 次）",
            notify=True,
        )
        if not self.force_new_capture_connection():
            return False

        after = self.wait_for_capture_connection(timeout=CAPTURE_RELOGIN_WAIT_SECONDS)
        if after.state in (CAPTURE_HEALTH_OK, CAPTURE_HEALTH_DECODING):
            self._minimap_capture_relogin_attempts = 0
            self.log_info("实验技术：新连接已被后端接管，抓包恢复")
            return True
        self.log_warning(
            f"实验技术：重新进入后仍没抓到握手（{after.state}）"
            + ("，稍后会自动再试" if attempt < CAPTURE_RELOGIN_ATTEMPTS else ""),
            notify=True,
        )
        return False

    def _warn_capture_relogin_disabled(self) -> None:
        if self._minimap_capture_relogin_warned:
            return
        self._minimap_capture_relogin_warned = True
        self.log_warning(
            "实验技术：本次抓包没有可用坐标，而「抓包失败时自动重新登录」是关着的——"
            "请手动退回登录界面再进入一次，或打开该开关",
            notify=True,
        )

    def _warn_capture_relogin_exhausted(self, health: CaptureHealth) -> None:
        if self._minimap_capture_relogin_warned:
            return
        self._minimap_capture_relogin_warned = True
        self.log_warning(
            f"实验技术：已尝试 {CAPTURE_RELOGIN_ATTEMPTS} 次重建连接仍未抓到握手（{health.detail or health.state}）。"
            "请手动重启游戏后再启用抓包；本条不再自动重试",
            notify=True,
        )

    def stop_capture_backend(self) -> None:
        """按配置停掉后端。默认不停——见 :data:`CONFIG_EXPERIMENTAL_STOP_ON_EXIT` 的说明。"""

        launcher = self._minimap_capture_launcher
        if launcher is None:
            return
        if self._cfg_bool(CONFIG_EXPERIMENTAL_STOP_ON_EXIT, False):
            launcher.stop()
        self._minimap_capture_launcher = None

    def _capture_world_pose(self) -> dict:
        """把抓包样本组装成定位状态契约（直通：抓包给什么就用什么）。

        与融合链的关键差别是整个校准体系都不参与：坐标本来就是绝对值，没有"锚点"可设、
        也没有漂移可修。因此只要坐标可用，``sync_needed`` 就恒为假——否则导航的
        ``CALIBRATE`` 状态会把车停下来等一个永远不会到来的静止校准。

        但"绝对"不等于"当前"：坐标是**某一条 TCP 连接**解出来的，而那条连接会随着登出、
        换账号、重启游戏而结束。后端把最后一次解出的坐标一直留着（它的 ``state.last`` 没有
        清空点），所以重登之后、新会话报出第一条坐标之前，``/api/state`` 给的仍是**上一局**
        的坐标——那可能是另一张地图、乃至另一个账号的位置。这种坐标对导航比"没有坐标"更糟，
        所以这里按下面两条扣掉信任，交给 :meth:`_capture_trust` 判：

        * 产出它的连接已经不再喂数据（``WAIT_TRUST``，带 ``capture_session_ended``）；
        * 认不出地图（``WAIT_TRUST``，带 ``capture_no_parent_map`` / ``capture_unknown_map``）
          ——**决不回退到调用方请求的 map_id**：实测场景 463 是自带独立坐标空间的可走区域，
          拿它当 map02 会在错的网格上从错的起点规划路线。

        两条都只是扣信任、不清空坐标（``x``/``z`` 照给），这样导航停在 ``WAIT_TRUST`` 时能把
        真正的原因打出来，而不是含糊地报"未锚定"。
        """

        # 不传本任务时钟：抓包源要用**墙钟**去减后端记录的 Unix 时间戳算陈旧度，
        # 传 active_time()（time.monotonic）进去会让差值恒为负、被夹成 0。
        sample = self.capture_position_source().poll()
        if sample is None:
            # 后端没数据：不发布伪造坐标。沿用上一帧只会让导航拿着旧位置继续走，
            # 所以这里显式把坐标置空，让消费者看到"位置不可用"。
            # 刻意不清空 ``_minimap_capture_last_flow``：数据中断后接上的如果还是同一条连接，
            # 就不该被当成新的一局而让导航作废"走过的已知格"。
            return {
                "x": None,
                "y": None,
                "z": None,
                "heading": None,
                "heading_score": None,
                "map_id": None,
                "anchor_set": False,
                "position_trusted": False,
                "trust_reason": "capture_unavailable",
                "rest": False,
                "odom_ok": False,
                "odom_reason": "capture_unavailable",
                "sync_needed": False,
                "sync_checked": True,
                # 校准不成立是**数据源的性质**，与此刻有没有坐标无关，所以这条分支也给。
                "sync_supported": False,
                "sync_seq": self._minimap_capture_seq,
                "distance_since_sync": 0.0,
                "ws": None,
                "ws_xyz": None,
            }

        # ``sync_seq`` 只在**换了连接**（换了一局）时推进：导航把它解释成"真重锚 → 世界坐标
        # 整体平移 → 已走过的已知格作废"，并在 ``NavSession.note_sync`` 里清空
        # ``visited_cells``。抓包源给的是绝对坐标，移动时每拍都在变；若按"采样变了"就推进，
        # 移动本身就会每拍清空导航的历史格，偏航回退（网格导航文档 §7.5）会退化成
        # "没有已走过的已知格可返回"。
        #
        # 判据必须用连接号而不是坐标：换连接时坐标可能逐位相同（"登出后在原地重新登录"），
        # 那种情况只有 ``flow`` 变了。连接号识别不出来（``None``）时按"同一局"处理，宁可少
        # 失效一次，也不要把正常移动误判成重锚。
        if sample.flow != self._minimap_capture_last_flow:
            self._minimap_capture_last_flow = sample.flow
            self._minimap_capture_seq += 1

        rest = sample.speed is not None and abs(sample.speed) < REST_SPEED_THRESHOLD
        trusted, trust_reason = self._capture_trust(sample)
        return {
            "position_t": sample.capture_t,
            "x": sample.x,
            "y": sample.y,
            "z": sample.z,
            # 朝向与 src/nav/route_follower.bearing_to_point 同一套约定（正北 0、顺时针），
            # 是游戏上报的真值而非图像推断，因此置信度给满。
            "heading": sample.heading,
            "heading_score": None if sample.heading is None else 1.0,
            "map_id": sample.map_id or None,
            "anchor_set": trusted,
            "position_trusted": trusted,
            "trust_reason": trust_reason,
            "rest": rest,
            "odom_ok": True,
            "odom_reason": "capture",
            "sync_needed": False,
            "sync_checked": True,
            # 契约字段：直通源上"静止校准"这个概念不成立，导航据此不必停车等校准。
            # 直接置为 False，而不是让导航去比对 source 名称（导航层不该知道本模块的命名）。
            "sync_supported": False,
            "just_synced": False,
            "sync_seq": self._minimap_capture_seq,
            "distance_since_sync": 0.0,
            # Compatibility aliases for older consumers; no WS transport is used.
            "ws": (sample.x, sample.z) if trusted else None,
            "ws_xyz": (sample.x, sample.y, sample.z) if trusted else None,
            # 以下字段只供状态展示，不进发布契约（build_world_pose 会略过未知键）。
            "capture_age": sample.age_seconds,
            "capture_speed": sample.speed,
            "capture_map_id_source": sample.map_id_source,
            "capture_scene": sample.scene,
            "capture_flow": sample.flow,
            "capture_flow_status": sample.flow_status,
            "capture_flow_alive": sample.flow_alive,
        }

    @staticmethod
    def _capture_trust(sample) -> tuple[bool, str]:
        """这条抓包坐标能不能被导航当真值；返回 ``(可信, 原因)``。

        只做两件事，都遵循"没有把握就不给导航用"：先看产出它的连接是否还在，再看地图是否
        认得出来。坐标数值本身**不**在这里判——数值是不是合理是后端的事，这里只判"这份坐标
        描述的是不是当下的位置"。
        """

        if not sample.flow_alive:
            return False, "capture_session_ended"
        if not sample.map_id:
            return False, (
                "capture_no_parent_map" if sample.map_id_source == "no_parent_map" else "capture_unknown_map"
            )
        return True, CAPTURE_SOURCE_NAME
