"""网格覆盖与抓包时间对齐的纯计算工具，不发送游戏输入。"""  # 支持离线单元测试

import math  # 计算误差与有限值校验
from bisect import bisect_left, bisect_right  # 按采集时刻查找相邻参考点
from collections import deque  # 使用广度优先遍历连通网格

import cv2  # 对可走网格标记连通区域
import numpy as np  # 批量计算覆盖率和误差分位数

from src.nav.grid_io import CELL_BLOCKED, CELL_FREE, DenseGrid  # 复用网格格式与临时副本


def duplicate_observation(row):
    """Use consumer-local identity when available, retaining compatibility with older logs."""
    return row.get("validation_duplicate", row["pose"].get("visual_duplicate_frame", False))


def navigation_pose_ready(pose):  # 与正式导航保持同样的楼层确认前置条件
    return bool(
        pose and pose.get("position_trusted") and (not pose.get("tier_evidence_required") or pose.get("tier_trusted"))
    )  # 临时楼层扫描不应误报道路受阻


class NavigationSourcePolicy:  # 只决定控制来源，不修改视觉定位器
    def __init__(self, enabled):  # 默认保持纯视觉验证
        self.enabled, self.capture, self.good, self.bad = enabled, False, 0, 0  # 分开统计连续恢复和失败
        self._last_frame = None

    def choose(self, pose):  # 连续三帧确认避免来回切换
        stamp = (pose or {}).get("frame_t")
        if isinstance(stamp, (int, float)) and math.isfinite(stamp) and stamp > 0:
            if self._last_frame is not None and stamp <= self._last_frame:
                return "capture" if self.capture else "visual_map"
            self._last_frame = stamp
        ready = navigation_pose_ready(pose)  # 复用与导航一致的可用性条件
        self.good = self.good + 1 if ready else 0  # 连续恢复计数
        self.bad = self.bad + 1 if not ready else 0  # 连续失败计数
        if self.enabled and self.bad >= 3:  # 用户显式启用后才允许抓包接管
            self.capture = True  # 保留视觉观测但更换导航来源
        if self.good >= 3:  # 原有视觉门槛连续通过后恢复
            self.capture = False  # 不降低任何匹配门槛
        return "capture" if self.capture else "visual_map"  # 控制来源由日志明确标记


def temporary_obstacle_grid(grid, failures):  # 只限制本次测试中的已失败接近方向
    cells = grid.cells.copy()  # 不改原始网格或其缓存
    for failure in failures:  # 失败记录保留推断来源
        position, waypoint = failure.get("position"), failure.get("waypoint") or failure.get("goal")  # 使用实际失败航点
        if not position or not waypoint or not failure.get("obstructed", False):  # 普通超时不能当障碍
            continue  # 只跳过目标，不改变通路
        distance = math.dist(position, waypoint)  # 沿实际受阻方向设置有限避让区域
        if distance < 0.5:  # 不能封住当前位置
            continue  # 缺少可信方向时不推断
        center = tuple(
            a + (b - a) * min(2.5, distance) / distance for a, b in zip(position, waypoint, strict=True)
        )  # 只推断近处
        row, col = grid.index_of_world(*center)  # 复用网格坐标变换
        radius = math.ceil(1.5 / grid.meta.cell_size)  # 临时避让半径一米半
        for i in range(max(0, row - radius), min(cells.shape[0], row + radius + 1)):  # 有界扫描
            for j in range(max(0, col - radius), min(cells.shape[1], col + radius + 1)):  # 保持原网格边界
                point = grid.world_of_index(i, j)  # 使用单元中心
                if math.dist(point, center) <= 1.5 and math.dist(point, position) > 1.0:  # 保留站立和退出空间
                    cells[i, j] = CELL_BLOCKED  # 仅在本次副本阻挡
    return DenseGrid(cells, grid.meta)  # 项目规划器原样读取副本


def connected_region(grid, position):  # 提取当前位置实际所属的区域
    cell = grid.index_of_world(*position)  # 将世界坐标转换为网格
    if not grid.is_free(*cell):  # 不静默吸附到另一块区域
        raise ValueError(f"start_not_on_free_grid: cell={cell}")  # 交由现场诊断起点问题
    _, labels = cv2.connectedComponents((grid.cells == CELL_FREE).astype(np.uint8), connectivity=4)  # 不跨角连接
    region = labels == labels[cell]  # 仅保留起点所在分量
    return region, int(np.count_nonzero(grid.cells == CELL_FREE) - region.sum())  # 同时返回排除格数


def nearby_cells(region, start, steps):  # 以可走路网距离限制覆盖范围
    if not (0 <= start[0] < region.shape[0] and 0 <= start[1] < region.shape[1]) or not region[start]:  # 检查边界
        return set()  # 越界或离开区域不计入覆盖
    seen = {start}  # 当前格的距离为零
    queue = deque([(start, 0)])  # 保存广度优先遍历深度
    while queue:  # 有限网格与距离保证终止
        (i, j), depth = queue.popleft()  # 取最近未展开格
        if depth >= steps:  # 到达最大路网距离
            continue  # 不越过本次采样范围
        for point in ((i - 1, j), (i + 1, j), (i, j - 1), (i, j + 1)):  # 与四连通分量规则一致
            y, x = point  # 分离行列便于检查
            if (
                0 <= y < region.shape[0] and 0 <= x < region.shape[1] and region[point] and point not in seen
            ):  # 已知可走
                seen.add(point)  # 标记已经入队
                queue.append((point, depth + 1))  # 继续覆盖下一层
    return seen  # 返回真实可达的覆盖格


def coverage_targets(grid, region, spacing=5.0):  # 在每个局部通路生成约五米验证点
    if not math.isfinite(spacing) or spacing < grid.meta.cell_size:  # 拒绝无效采样间距
        raise ValueError("spacing must be finite and at least one grid cell")  # 明确参数约束
    stride = max(1, round(spacing / grid.meta.cell_size))  # 将世界距离换算为网格数量
    rows, columns = np.where(region)  # 提取当前分量的包围盒
    targets = []  # 各验证点均为可走格中心
    clearance = cv2.distanceTransform(region.astype(np.uint8), cv2.DIST_L2, 3)  # 优先选择通道中央
    for i in range(int(rows.min()), int(rows.max()) + 1, stride):  # 按固定间隔扫描行
        for j in range(int(columns.min()), int(columns.max()) + 1, stride):  # 扫描列
            tile = region[i : i + stride, j : j + stride]  # 仅查看当前采样块
            count, labels = cv2.connectedComponents(tile.astype(np.uint8), connectivity=4)  # 保留同块内的平行通路
            for label in range(1, count):  # 每条局部连通通路都取一点
                yy, xx = np.where(labels == label)  # 候选必须落在通路上
                rank = clearance[yy + i, xx + j] - 0.01 * ((yy - stride / 2) ** 2 + (xx - stride / 2) ** 2)  # 离墙优先
                chosen = int(np.argmax(rank))  # 固定排序使计划可复现
                targets.append((int(yy[chosen] + i), int(xx[chosen] + j)))  # 保存网格行列
    covered = set()  # 验证所有格都有附近的验证点
    steps = math.floor(spacing / grid.meta.cell_size)  # 覆盖半径不超过声明间距
    for point in targets:  # 通过可走路径扩展每个点的覆盖
        covered.update(nearby_cells(region, point, steps))  # 不跨墙统计覆盖
    for y, x in zip(rows, columns, strict=True):  # 补齐蛇形或狭窄区域中的遗漏
        point = (int(y), int(x))  # 标准化网格索引
        if point not in covered:  # 仅添加现有计划不能覆盖的格
            targets.append(point)  # 添加必要验证点
            covered.update(nearby_cells(region, point, steps))  # 更新覆盖证明
    return targets  # 路线顺序由运行时按当前视觉位置选择


def valid_reference(record):  # 过滤无效、重播或未标时的数据
    try:  # 外部接口可能返回不完整字段
        values = [float(record[key]) for key in ("time", "x", "z")]  # 需要同一墙钟时间轴
        return all(math.isfinite(v) for v in values) and values[0] > 0 and not record.get("replayed")  # 拒绝旧回放
    except (KeyError, TypeError, ValueError):  # 记录异常而不伪造坐标
        return False  # 不作为精度真值


class ReferenceTimeline:  # 长时间遍历只排序一次参考轨迹
    def __init__(self, records):  # 建立可重复查询的时间索引
        self.samples, self.times = [], []
        self.extend(records)

    def extend(self, records):
        """Validate only new records; retain stable ordering for late arrivals."""
        incoming = sorted((r for r in records if valid_reference(r)), key=lambda r: float(r["time"]))
        if not incoming:
            return
        if not self.times or float(incoming[0]["time"]) >= self.times[-1]:
            self.samples.extend(incoming)
            self.times.extend(float(row["time"]) for row in incoming)
            return
        for row in incoming:
            stamp = float(row["time"])
            index = bisect_right(self.times, stamp)
            self.times.insert(index, stamp)
            self.samples.insert(index, row)


def align_reference(records, stamp, map_id, *, flow=None, max_gap=0.6, stationary=False):  # 对齐到图像呈现时刻
    timeline = records if isinstance(records, ReferenceTimeline) else ReferenceTimeline(records)  # 批量报告复用索引
    samples, times = timeline.samples, timeline.times  # 不为每帧重新排序全部抓包历史
    if not samples:  # 后端尚未解出坐标
        return None  # 缺失参考不能算作零误差
    index = bisect_left(times, stamp)  # 查找图像时刻右边的抓包点
    left = samples[max(0, index - 1)]  # 图像前一条数据
    right = samples[min(index, len(samples) - 1)]  # 图像后一条数据
    if index < len(samples) and abs(times[index] - stamp) < 1e-6:  # 恰好同一时间则无需插值
        left = right  # 使用原始抓包坐标
    pair = (left, right)  # 两端必须来自同一地图与连接
    if any(r.get("mapId") != map_id or (flow is not None and r.get("flow") != flow) for r in pair):  # 拒绝跨地图
        return None  # 不插值传送或换号后的点
    if left.get("flow") != right.get("flow") or left.get("scene") != right.get("scene"):  # 不跨连接与场景
        return None  # 保持坐标身份完整
    begin, end = float(left["time"]), float(right["time"])  # 原始抓包墙钟
    if begin <= stamp <= end and end - begin <= max_gap:  # 两侧确有有效时间包围
        if math.dist((left["x"], left["z"]), (right["x"], right["z"])) > max(1.0, (end - begin) * 30):  # 过滤瞬移
            return None  # 不把跳变平滑成运动
        weight = (stamp - begin) / (end - begin) if end > begin else 0.0  # 线性插值比例
        return {
            "x": left["x"] + weight * (right["x"] - left["x"]),
            "z": left["z"] + weight * (right["z"] - left["z"]),
            "mode": "interpolated",
            "gap_s": end - begin,
            "scene": left.get("scene"),
            "mapId": left.get("mapId"),
            "flow": left.get("flow"),
        }  # 输出未做空间校正的真值
    if (
        stationary
        and float(left.get("speed") if left.get("speed") is not None else math.inf) <= 0.1
        and stamp >= begin
        and (index == len(samples) or end - stamp > max_gap)
    ):  # 静止停发坐标
        return {
            "x": left["x"],
            "z": left["z"],
            "mode": "stationary_hold",
            "gap_s": stamp - begin,
            "scene": left.get("scene"),
            "mapId": left.get("mapId"),
            "flow": left.get("flow"),
        }  # 单列静止保留值
    return None  # 移动时绝不以旧点外推代替准确坐标


def error_statistics(pairs):  # 分别汇总原始或时间对齐后的误差
    if not pairs:  # 没有有效配对就明确返回空结果
        return {"count": 0}  # 不报告伪造的零均方误差
    delta = np.asarray(pairs, dtype=float)  # 每行是视觉减抓包的坐标偏差
    distance = np.linalg.norm(delta, axis=1)  # 平面欧氏误差
    return {
        "count": len(pairs),
        "median_m": float(np.median(distance)),
        "p95_m": float(np.quantile(distance, 0.95)),
        "max_m": float(distance.max()),
        "rmse_m": float(np.sqrt(np.mean(distance**2))),
        "bias_x_m": float(np.median(delta[:, 0])),
        "bias_z_m": float(np.median(delta[:, 1])),
    }  # 偏置仅报告不回灌定位
