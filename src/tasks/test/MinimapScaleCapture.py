# -*- coding: utf-8 -*-
"""小地图比例尺采集：静止时成对保存「整帧截图 + 该拍 WS 精确坐标 + 画面分辨率」。

为什么需要这个任务
------------------
「小地图 1 像素 = 多少米」这个量**随画面分辨率变化**——小地图圆盘按画面宽缩放。
只记 ``scale_m_per_px`` 而不记分辨率，数据既不能跨分辨率复用，也没法和别的测量对齐
（``logs/minimap_calibration/`` 的历史标定就缺分辨率）。所以本任务把三样东西绑在一起落盘：

- **整帧截图**，不做任何改动（不叠字、不裁切、不压黑），供北向上模板匹配做像素级配准；
- **同一拍的 WS 绝对坐标**。WS 有传输延迟，只有静止时它才是无延迟真值；所以要求连续
  ``静止稳定拍数`` 拍同时满足「小地图与 WS 都没动」才采，避免采到刹车的残余；
- **画面分辨率**，以及由 :func:`region_geometry` 算出的圆心与内/外半径。

有这三样就能用「拼接总图配准 + WS 真值」做绝对配准，去掉「手抄坐标」与「总图标定」
两个近似，把 m/px 与圆盘几何一起钉死。

``rest`` 取自融合层的静止判定；本任务还会额外要求存在 WS 真值，二者都满足才采样，
不是简单的“没按键”。

输出
----
``保存目录``（默认 ``logs/minimap_scale_capture/``）下按 **分辨率 / 地图** 两级分组：

- ``<宽>x<高>/<地图id>/<时间戳>_<序号>.png``：整帧截图，按分组归档
- ``<同目录>/<同主名>.json``：**逐图 sidecar**，内容与该图在总索引里的记录逐字段相同。
  单拿一张图走也带着坐标；写失败只告警，不影响已落盘的图片
- ``index.json``：全部样本的单一索引，每条记录的 ``file`` 是相对保存目录的路径
  （分组已经体现在路径里），另带 ``group`` 字段便于过滤。**跨运行合并**（按 ``file``
  去重，``created`` 保留最早一次、``updated`` 是最后写入），所以为了换位置跑很多次任务
  也不会把前面的坐标覆盖掉

坐标跟着图片走（sidecar）而不只躺在索引里，索引被谁覆盖都不影响单张图；反过来说
索引只是"一次读全量"的便捷入口，不是唯一真相源。

分组维度就是下游做批处理时要分开跑的两个量：**分辨率**决定比例尺（圆盘按画面宽缩放），
**地图**决定用哪张拼接总图去配准。混在一起会得到对不上的结果。

**不要填 ``screenshots/``**：该目录每次启动会被清空（见 ``src/config.py`` 的注释）。

定位能力由 ``MinimapPositionTask`` 统一维护，本任务只负责等待静止、采样与落盘。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import cv2
from qfluentwidgets import FluentIcon

from src.core.BaseEfTask import BaseEfTask
from src.tasks.mixin.minimap_odometry import region_geometry
from src.tasks.trigger.MinimapPositionTask import MinimapPositionTask

CONFIG_INTERVAL = "采样间隔(秒)"
CONFIG_SAMPLES = "采集张数"
CONFIG_REST_TICKS = "静止稳定拍数"
CONFIG_TIMEOUT = "采集超时(秒)"
CONFIG_SAVE_DIR = "保存目录"


class MinimapScaleCapture(BaseEfTask):
    """静止时成对采集整帧截图与 WS 精确坐标，用于标定小地图比例尺（工具与调试分组）。"""

    requires_foreground = True  # 需要读取游戏画面/小地图

    #: screenshots/ 每次启动会被清空，样本默认写到不会被清的 logs/ 下
    SAVE_DIR_DEFAULT = "logs/minimap_scale_capture"
    INDEX_NAME = "index.json"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "小地图比例尺采集"
        self.group_name = "工具与调试"
        self.group_icon = FluentIcon.DEVELOPER_TOOLS
        self.description = "静止时成对保存整帧截图与 WS 精确坐标，用于标定小地图比例尺(米/像素)"
        self.visible = self.debug

        self.default_config = {
            CONFIG_INTERVAL: 0.5,
            CONFIG_SAMPLES: 8,
            CONFIG_REST_TICKS: 3,
            CONFIG_TIMEOUT: 180.0,
            CONFIG_SAVE_DIR: self.SAVE_DIR_DEFAULT,
        }
        self.config_description = {
            CONFIG_INTERVAL: "每拍采样的间隔（秒）；越小越不容易错过静止窗口",
            CONFIG_SAMPLES: "采集多少张后自动结束（一次性任务）",
            CONFIG_REST_TICKS: "要求连续多少拍同时满足「小地图与 WS 都没动」才落盘。"
                               "WS 有传输延迟，只有静止时它才是无延迟真值；拍数太少会采到刹车的残余",
            CONFIG_TIMEOUT: "最长运行时间（秒）。一直等不到静止/WS 真值时到点结束，并说明卡在哪一步",
            CONFIG_SAVE_DIR: "样本保存目录（screenshots/ 每次启动会清空，勿填那里）",
        }

        self._index = []
        self._created = ""

    # ------------------------------------------------------------------ #
    # 小工具
    # ------------------------------------------------------------------ #
    def _cfg_float(self, key, default):
        try:
            return float(self.config.get(key, default))
        except (TypeError, ValueError):
            return float(default)

    def _cfg_int(self, key, default):
        try:
            return int(self.config.get(key, default))
        except (TypeError, ValueError):
            return int(default)

    # ------------------------------------------------------------------ #
    # 主流程
    # ------------------------------------------------------------------ #
    def run(self):
        self.log_info("=== 小地图比例尺采集 ===", notify=True)
        if not self.in_world():
            self.log_info("当前不在大世界画面，无法读取小地图。请先进入大世界。", notify=True)
            return

        position_task = self.get_task_by_class(MinimapPositionTask)
        if position_task is None:
            self.log_warning("未注册「小地图定位」触发任务，无法读取 WS 真值", notify=True)
            return
        if not getattr(position_task, "enabled", True):
            self.log_warning(
                "「小地图定位」触发任务未启用。它同时负责消费 WS 真值，"
                "请先在触发任务里启用后再运行本任务。",
                notify=True,
            )
            return
        position_task.start_minimap_position(wait_stable=False)
        if not getattr(position_task, "minimap_position_ready", True):
            self.log_warning("小地图定位器未完成初始化，请检查全局「Nav Config」和游戏窗口", notify=True)
            return

        interval = max(0.05, self._cfg_float(CONFIG_INTERVAL, 0.5))
        target = max(1, self._cfg_int(CONFIG_SAMPLES, 8))
        rest_ticks = max(1, self._cfg_int(CONFIG_REST_TICKS, 3))
        timeout = max(1.0, self._cfg_float(CONFIG_TIMEOUT, 180.0))
        save_dir = Path(
            str(self.config.get(CONFIG_SAVE_DIR, self.SAVE_DIR_DEFAULT) or "").strip()
            or self.SAVE_DIR_DEFAULT
        )

        self._index = []
        self._created = datetime.now().isoformat(timespec="seconds")
        still = 0
        # 结束诊断用：分别数出"卡在哪一步"，避免失败时只剩一句"没采到"
        stats = {"ticks": 0, "no_frame": 0, "no_rest": 0, "no_ws": 0, "no_position": 0}

        self.log_info(
            f"开始采集：共 {target} 张，采样间隔 {interval:.2f}s，"
            f"需连续 {rest_ticks} 拍静止（约 {rest_ticks * interval:.1f}s）才落盘；"
            f"保存至 {save_dir}（按 分辨率/地图 分组归档）。"
            f"请在采样之间走动换位置，然后站定等落盘。",
            notify=True,
        )

        started = self.active_time()
        next_at = started
        timeout_hit = False
        while len(self._index) < target:
            # 固定采样节拍：睡到下一个采样点，而不是「sleep(interval) 后再干活」，
            # 否则实际周期 = 间隔 + 单拍耗时，静止窗口的判定会跟着漂。
            next_at += interval
            now = self.active_time()
            if next_at > now:
                self.sleep(next_at - now)
            elif now - next_at > interval:
                next_at = now
            now = self.active_time()
            if now - started >= timeout:
                timeout_hit = True
                break

            try:
                frame = self.next_frame()
            except Exception as e:
                self.log_warning(f"next_frame 失败: {e}")
                stats["no_frame"] += 1
                continue
            if frame is None:
                stats["no_frame"] += 1
                continue

            stats["ticks"] += 1
            st = position_task.minimap_position(frame=frame, now=now)

            # 三个条件必须同时成立才允许落盘：静止、有 WS 真值、已锚定出坐标。
            # 缺任何一个都当作"这一拍不算静止"并把连续计数清零——否则"连续 N 拍静止"
            # 会被中间那些没有 WS 的拍悄悄跨过去，恰好违背了这个判据的本意。
            if not st.get("rest"):
                stats["no_rest"] += 1
                still = 0
            elif not st.get("ws"):
                stats["no_ws"] += 1
                still = 0
            elif st.get("x") is None or st.get("z") is None:
                stats["no_position"] += 1
                still = 0
            else:
                still += 1
                if still >= rest_ticks:
                    still = 0
                    self._capture(frame, st, save_dir)
                    self.info_set("比例尺采集", f"{len(self._index)}/{target}")

        self._report(timeout_hit, target, rest_ticks, stats, save_dir)

    @staticmethod
    def _safe_component(text, fallback: str) -> str:
        """把外部字符串（如 WS 的 ``map_id``）变成安全的单层目录名。

        ``map_id`` 来自 WS 报文而不是本地常量，直接拼进路径的话，一个 ``../../x``
        就能把样本写到保存目录之外。只保留字母数字与 ``-_``，其余替换成 ``_``，
        并把 "."/"_" 打头尾的结果判空回退到 ``fallback``。
        """
        cleaned = re.sub(r"[^0-9A-Za-z_-]", "_", str(text if text is not None else "").strip())
        return cleaned.strip("._") or fallback

    # ------------------------------------------------------------------ #
    # 落盘
    # ------------------------------------------------------------------ #
    def _capture(self, frame, st: dict, save_dir: Path) -> None:
        """保存整帧并把这一拍的元数据追加进 index.json。"""
        h, w = frame.shape[:2]
        # 按「分辨率 / 地图」分组：分辨率决定比例尺（圆盘按画面宽缩放），地图决定用
        # 哪张拼接总图配准——下游批处理本来就要按这两个维度分开跑。
        group = f"{int(w)}x{int(h)}/{self._safe_component(st.get('map_id'), 'unknown')}"
        name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{len(self._index):03d}.png"
        # 索引里存相对保存目录的路径：分组已经体现在路径里，带上 group 便于直接过滤
        rel_path = f"{group}/{name}"
        try:
            (save_dir / group).mkdir(parents=True, exist_ok=True)
            # 存原帧：不叠字、不裁切、不压黑。任何改动都会影响后续的像素级模板匹配。
            written = cv2.imwrite(str(save_dir / rel_path), frame)
        except OSError as e:
            self.log_warning(f"截图保存失败: {e}")
            return
        if not written:
            self.log_warning(f"截图写入失败: {save_dir / rel_path}")
            return

        # region_geometry 是圆心/半径的单一事实来源（与里程计建掩膜同一个函数），
        # 这里记录默认几何下的结果，便于把"总图标定残差"反推成"圆心偏了几像素"。
        cx, cy, r_inner, r_outer = region_geometry(w, h)
        task_w = int(getattr(self, "width", 0) or 0)
        task_h = int(getattr(self, "height", 0) or 0)
        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "file": rel_path,
            "group": group,
            "width": int(w),
            "height": int(h),
            # 里程计是按任务分辨率建掩膜再把帧缩放到该尺寸的，两者不一致时圆心会错位；
            # 分辨率本身就是本次采集要解开的问题，所以两个都记下来。
            "task_width": task_w,
            "task_height": task_h,
            "x": round(float(st["x"]), 3),
            "z": round(float(st["z"]), 3),
            "map_id": st.get("map_id"),
            "heading": None if st.get("heading") is None else round(float(st["heading"]), 3),
            "region": [round(float(cx), 3), round(float(cy), 3),
                       round(float(r_inner), 3), round(float(r_outer), 3)],
        }
        # **同一份 dict** 既进总索引、又落同名 sidecar：两份记录各写各的迟早会漂，
        # 而 region / x / z 都是要拿去算数的字段。
        self._write_sidecar(save_dir, rel_path, record)
        self._index.append(record)
        self._write_index(save_dir)

        self.log_info(
            f"[{len(self._index)}/{self._cfg_int(CONFIG_SAMPLES, 8)}] {rel_path} "
            f"WS=({record['x']:.2f}, {record['z']:.2f}) "
            f"map={record['map_id'] or '-'} 朝向={record['heading']}"
        )
        if task_w and task_h and (task_w, task_h) != (w, h):
            self.log_warning(
                f"任务分辨率 {task_w}x{task_h} 与实际帧 {w}x{h} 不一致：里程计按任务分辨率"
                f"建掩膜再缩放帧，比例相同但宽高比不同会导致圆心错位，配准时需留意"
            )

    def _write_sidecar(self, save_dir: Path, rel_path: str, record: dict) -> None:
        """给图片写一份同名 ``.json``（只换扩展名，同目录）。

        单拿一张 PNG 出来也能对上坐标。写失败不影响已落盘的图片，但必须报出来——
        否则用户会以为这张图的坐标存下来了。
        """
        path = (save_dir / rel_path).with_suffix(".json")
        try:
            path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as e:
            self.log_warning(
                f"坐标 sidecar 写入失败（图片已保存，但这张没有同名 .json）: {path}: {e}"
            )

    def _write_index(self, save_dir: Path) -> None:
        """把本次样本并进盘上已有的 ``index.json``（按 ``file`` 去重），再原子替换。

        两个"不能丢数据"的点：

        - **跨运行合并**：一次运行采多张，但用户为了换位置必然会跑多次任务。若每次都从
          空列表整份覆盖，前几次的 PNG 还在盘上、坐标却被抹掉，就成了对不上号的孤儿图。
        - **先写临时文件再替换**：直接 ``write_text`` 是先清空再写，中途失败会把**全部**
          已有样本一起丢掉——那比原来的 bug 更糟。
        """
        path = save_dir / self.INDEX_NAME
        merged: dict[str, dict] = {}
        created = self._created
        if path.is_file():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                self.log_warning(f"已有 {self.INDEX_NAME} 读取失败，本次会覆盖它: {e}")
                existing = None
            if isinstance(existing, dict):
                for sample in existing.get("samples") or []:
                    if isinstance(sample, dict) and sample.get("file"):
                        merged[sample["file"]] = sample
                previous = str(existing.get("created") or "")
                # created 是"这个索引最早建于何时"，跑多次要保留最早那次而不是最后一次
                if previous and (not created or previous < created):
                    created = previous
        for sample in self._index:
            merged[sample["file"]] = sample

        payload = {
            "created": created,
            "updated": datetime.now().isoformat(timespec="seconds"),
            "samples": [merged[key] for key in sorted(merged)],
        }
        tmp_path = path.parent / (path.name + ".tmp")
        try:
            tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                encoding="utf-8")
            tmp_path.replace(path)
        except OSError as e:
            self.log_warning(f"index.json 写入失败: {e}")

    # ------------------------------------------------------------------ #
    # 结束报告
    # ------------------------------------------------------------------ #
    def _report(self, timeout_hit: bool, target: int, rest_ticks: int,
                stats: dict, save_dir: Path) -> None:
        count = len(self._index)
        if count >= target:
            self.log_info(
                f"采集完成：{count}/{target} 张。index → {save_dir / self.INDEX_NAME}",
                notify=True,
            )
            return

        # 没采满就要说清卡在哪一步，否则只有一句"没采到"没法排查
        hints = []
        if stats["ticks"] == 0:
            hints.append("一拍都没取到画面（窗口是否在前台 / 是否在大世界）")
        else:
            if stats["no_ws"]:
                hints.append(f"{stats['no_ws']} 拍没有 WS 真值"
                             "（触发任务未启用 / 未连上 / 没选地图账号）")
            if stats["no_position"]:
                hints.append(f"{stats['no_position']} 拍还没锚定出坐标（启动后需先静止校准一次）")
            if stats["no_rest"]:
                hints.append(f"{stats['no_rest']} 拍判为移动中（要连续 {rest_ticks} 拍静止才落盘）")
            if stats["no_frame"]:
                hints.append(f"{stats['no_frame']} 拍取不到画面")
        self.log_warning(
            f"只采到 {count}/{target} 张（{'超时' if timeout_hit else '未达张数'} 结束）。"
            f"未落盘原因分布：{'；'.join(hints) or '未知'}。",
            notify=True,
        )
