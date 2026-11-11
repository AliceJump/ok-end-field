"""按连通网格遍历并记录视觉定位与独立抓包坐标；默认仅规划。"""  # 运行模式必须显式选择
# ruff: noqa: E402

from __future__ import annotations  # 延后类型注解解析

import argparse  # 解析规划、执行与输出参数
import ctypes  # 设置与生产截图一致的 DPI 上下文
import hashlib  # 固定被测网格内容摘要
import json  # 保存可复核的原始记录
import math  # 计算位置和路线距离
import sys  # 将仓库加入模块路径
import threading  # 独立采集抓包真值与监视紧急停止
import time  # 同时使用墙钟配对与单调时钟超时
from pathlib import Path  # 统一使用仓库绝对路径

ROOT = Path(__file__).resolve().parents[2]  # 根据脚本位置固定仓库根
sys.path.insert(0, str(ROOT))  # 支持从命令行直接运行


from scripts.nav.path_safety import resolve_under  # 禁止离线工具逃出仓库根目录
from scripts.nav.visual_validation import (  # 复用可离线验证算法
    ReferenceTimeline,
    align_reference,
    connected_region,
    coverage_targets,
    duplicate_observation,
    error_statistics,
)
from src.localization.capture_position_source import CapturePositionSource  # 只读取已开启的抓包后端
from src.nav.grid_io import load_grid  # 使用现有网格格式


def write_json(path, value):  # 原子写入进度与最终报告
    pending = path.with_suffix(path.suffix + ".tmp")  # 避免中断留下半截 JSON
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")  # 保留中文诊断
    pending.replace(path)  # 同目录内替换结果


class ReferenceRecorder:  # 抓包真值与视觉计算相互独立
    def __init__(self, port, directory):  # 初始化只读 HTTP 客户端
        self.source = CapturePositionSource(port, timeout=0.5)  # 不启停后端或改变登录状态
        self.records = {}  # 以连接和采集时刻去重
        self.lock = threading.Lock()  # 保护后台更新快照
        self.stop = threading.Event()  # 控制后台退出
        self.latest = None  # 保存最近原始坐标与连接健康状态
        self.checked_at = 0.0  # 单独记录本次 HTTP 健康检查时间
        self.handle = (directory / "reference.jsonl").open("w", encoding="utf-8")  # 保留精简原始真值
        try:
            self.thread = threading.Thread(target=self._run, daemon=True)  # 不阻塞进程退出
            self.thread.start()  # 开始观察现有后端
        except Exception:
            self.handle.close()
            raise

    def _run(self):  # 持续拉取后端已保存的轨迹
        fields = (
            "time",
            "captureAt",
            "sampleId",
            "flow",
            "scene",
            "levelId",
            "mapId",
            "x",
            "y",
            "z",
            "yaw",
            "speed",
            "replayed",
        )  # 不记录账户与对象标识
        while not self.stop.is_set():  # 有显式退出事件
            state = self.source.fetch_state()  # 仅读取本地接口
            if state is not None:  # 后端暂时不可达时不续期旧健康状态
                last = state.get("lastPosition") or {}  # 获取最近坐标所属连接
                _, _, alive = self.source._flow_liveness(state, last, time.time())  # 复用生产连接活性判定
                with self.lock:  # 原子更新真值与健康信息
                    self.latest = dict(last) if alive else None  # 已结束连接不能作为真值
                    self.checked_at = time.time()  # 保留健康检查墙钟
                    for item in [*(state.get("trajectory") or []), last]:  # 获取两次轮询间的全部历史点
                        if not isinstance(item, dict) or not item.get("time"):  # 忽略缺失时刻的记录
                            continue  # 不伪造时间戳
                        row = {key: item.get(key) for key in fields}  # 保存必要验证字段
                        key = (row["flow"], row["time"], row["sampleId"])  # 不重复计数同一抓包点
                        if key not in self.records:  # 只追加新点
                            self.records[key] = row  # 保存完整测试轨迹
                            self.handle.write(json.dumps(row) + "\n")  # 每行一条便于中断后恢复
                    self.handle.flush()  # 及时落盘
            self.stop.wait(0.1)  # 十赫兹拉取，不忙等

    def snapshot(self):  # 返回一致的只读快照
        with self.lock:  # 避免读到一半更新的连接状态
            return self.latest, self.checked_at, list(self.records.values())  # 调用者不能改动内部字典

    def close(self):  # 等待后台安全结束
        self.stop.set()  # 唤醒后台轮询
        self.thread.join(timeout=2)  # HTTP 请求本身有短超时
        self.handle.close()  # 释放原始记录文件


def report_accuracy(samples, references):  # 结束后用完整轨迹重新对齐，避免实时参考点尚未到达
    references = ReferenceTimeline(references)  # 一次排序，避免长距离测试后处理平方级开销
    groups = {
        "absolute_time_aligned": [],
        "fused_time_aligned": [],
        "absolute_stationary": [],
        "fused_stationary": [],
    }  # 区分原始匹配、融合与静止保留
    eligible = missing = 0  # 记录有效样本及缺失配对
    for row in samples:  # 不将无可信坐标的帧算成成功
        pose = row["pose"]  # 当前帧的视觉状态
        if (
            pose.get("source") == "capture" or not pose.get("position_trusted") or duplicate_observation(row)
        ):  # 抓包自身不能当作视觉精度样本
            continue  # 仍在原始日志中保留失败原因
        eligible += 1  # 本帧满足视觉信任门槛
        reference = align_reference(
            references,
            row["frame_t"],
            pose.get("map_id"),
            flow=row.get("flow"),
            stationary=row.get("stationary_reference", False),
        )  # 依据原始时刻对齐
        if reference is None:  # 没有可靠包围时间就不比较
            missing += 1  # 将不可配对数量公开
            continue  # 不以最近点冒充同步真值
        suffix = (
            "stationary" if reference["mode"] == "stationary_hold" else "time_aligned"
        )  # 时间插值组不假定每个点都在运动
        groups[f"fused_{suffix}"].append(
            (pose["x"] - reference["x"], pose["z"] - reference["z"])
        )  # 未做空间补偿的融合误差
        absolute = row.get("absolute")  # 绝对匹配与融合分开核验
        if absolute is not None:  # 有新绝对匹配才计入绝对精度
            groups[f"absolute_{suffix}"].append(
                (absolute["world_x"] - reference["x"], absolute["world_z"] - reference["z"])
            )  # 保留标定偏差
    return {
        "eligible_frames": eligible,
        "unpaired_frames": missing,
        **{key: error_statistics(values) for key, values in groups.items()},
    }  # 输出可解释统计口径


def execute(args, grid, region, targets, directory, plan):  # 使用项目原有任务运行时和导航入口
    from src.patches.pre_config_patch import install_pre_config_patch  # 与正式入口保持相同兼容初始化

    install_pre_config_patch()  # 在导入任务和配置之前安装框架兼容层
    from scripts.nav.validation_task import run_validation_task  # 延后导入完整项目运行时

    return run_validation_task(args, grid, region, targets, directory, plan)  # 不维护第二套键鼠控制循环


def main():  # 提供可复用的命令行入口
    parser = argparse.ArgumentParser(  # 参数默认不移动游戏
        description=__doc__,
        epilog=(  # 让复跑方式与证据位置在帮助中可见
            "Run from an Administrator terminal with the game and capture backend open. "
            "Press F8 or create OUTPUT/STOP to stop. Each run writes plan.json, progress.json, "
            "visual.jsonl, reference.jsonl and report.json to a new output directory. "
            "Use --resume DIR --skip-blocked to continue coverage. "
            "--capture-fallback explicitly allows packet navigation while visual localization fails; "
            "packet traversal and visual validation are reported separately. "
            "--activate-game brings the game to the foreground at startup."
        ),
    )
    parser.add_argument("--mode", choices=("plan", "record", "run"), default="plan")  # 需要显式启用运行模式
    parser.add_argument("--grid", type=Path, default=ROOT / "assets/nav-grid-data/map02_4.grid.npz")  # 默认当前主图网格
    parser.add_argument("--port", type=int, default=8765)  # 使用用户已打开的抓包服务
    parser.add_argument("--spacing", type=float, default=5.0)  # 使用用户选择的约五米间距
    parser.add_argument("--seconds", type=float, default=14400)  # 最多四小时，停止时保留部分结果
    parser.add_argument("--max-error", type=float, default=5.0)  # 只作停止保护，不改变视觉置信度
    parser.add_argument("--output", type=Path)  # 支持指定证据目录
    parser.add_argument("--resume", type=Path)  # 从前次相同网格的覆盖证据继续
    parser.add_argument("--skip-blocked", action="store_true")  # 允许跳过正式导航恢复失败的目标
    parser.add_argument("--capture-fallback", action="store_true")  # 显式授权视觉失败时用项目抓包定位导航
    parser.add_argument("--activate-game", action="store_true")  # 用户授权后在启动时调用项目窗口激活接口
    args = parser.parse_args()  # 读取本次运行参数
    args.grid = resolve_under(ROOT, args.grid, label="grid", must_exist=True)
    if args.output is not None:
        args.output = resolve_under(ROOT, args.output, label="output")
    if args.resume is not None:
        args.resume = resolve_under(ROOT, args.resume, label="resume", must_exist=True)
    if args.mode == "run" and not ctypes.windll.shell32.IsUserAnAdmin():  # 用户要求以管理员权限发送游戏输入
        parser.error("run mode requires an Administrator terminal")  # 不静默启动低权限移动
    if (
        not math.isfinite(args.seconds) or args.seconds <= 0 or not math.isfinite(args.max_error) or args.max_error <= 0
    ):  # 拒绝无界时限与无效误差配置
        parser.error("seconds and max-error must be finite and positive")  # 输入错误不启动游戏控制
    directory = args.output or ROOT / "logs" / time.strftime("visual_grid_validation_%Y%m%d_%H%M%S")  # 每次运行独立保存
    sample = CapturePositionSource(args.port).poll()  # 只用当前真值确认起始区域
    if sample is None or not sample.flow_alive:  # 前置条件不满足则不移动
        raise RuntimeError("reference_backend_has_no_live_position")  # 明确指出后端状态
    grid = load_grid(args.grid)  # 严格读取现有网格
    if grid.meta.map_name != sample.map_id or grid.meta.tier:  # 本脚本只遍历当前主图平面连通分量
        raise RuntimeError("grid_does_not_match_current_main_map")  # 不以高度或名字猜层
    start_cell = grid.index_of_world(sample.x, sample.z)  # 使用网格已有的起点换算
    start_free = grid.nearest_free(*start_cell, max_radius=2)  # 复用生产网格的有界起点吸附
    if start_free is None or grid.is_blocked(*start_cell):  # 阻挡起点不允许跨墙吸附
        raise RuntimeError("start_not_near_free_grid")  # 明确停止而不猜测区域
    start_center = grid.world_of_index(*start_free)  # 仅用于确定连通分量，不替换视觉坐标
    start_snap_m = math.dist((sample.x, sample.z), start_center)  # 在报告中公开吸附距离
    if start_snap_m > 2.0:  # 不能从远处未知区域接入路线
        raise RuntimeError(f"start_snap_too_far: {start_snap_m:.2f}m")  # 保持有界接入
    region, excluded = connected_region(grid, start_center)  # 正式路线仍只走该已知分量
    targets = coverage_targets(grid, region, args.spacing)  # 生成覆盖所有通路的验证计划
    directory.mkdir(parents=True, exist_ok=False)  # 校验通过后再创建，不覆盖既有实验数据
    plan = {
        "grid": str(args.grid.resolve()),
        "grid_sha256": hashlib.sha256(args.grid.read_bytes()).hexdigest(),
        "map_id": sample.map_id,
        "tier_id": "__main__",
        "spacing_m": args.spacing,
        "coverage_definition": "observed visual position within spacing_m along free four-connected cells; not every cell physically visited",
        "component_cells": int(region.sum()),
        "excluded_disconnected_cells": excluded,
        "planned_targets": len(targets),
        "reference_role": "independent validation only; never passed to visual localization or steering",
        "start_reference": [sample.x, sample.z],
        "start_grid_center": start_center,  # 记录现有网格吸附结果
        "start_snap_m": start_snap_m,  # 不隐去边缘起点偏移
        "administrator": bool(ctypes.windll.shell32.IsUserAnAdmin()),  # 在报告中证明实际运行权限
    }  # 固定实验范围与统计定义
    args.resume_covered, args.resume_failures, args.resume_skipped = set(), [], set()  # 默认从头验证
    args.resume_traversed = set()  # 总遍历与视觉验证分开继承
    if args.resume:  # 只继承匹配的实验范围，不覆盖原证据
        previous = json.loads((args.resume / "plan.json").read_text(encoding="utf-8"))  # 固定原始网格身份
        for key in ("grid_sha256", "map_id", "tier_id", "spacing_m", "component_cells"):  # 避免跨图续跑
            if previous[key] != plan[key]:  # 不接受不兼容的进度
                raise ValueError(f"resume_mismatch: {key}")  # 明确不一致字段
        args.resume_covered = set(
            map(tuple, json.loads((args.resume / "covered_cells.json").read_text(encoding="utf-8")))
        )  # 继承真实覆盖
        traversed_file = args.resume / "traversed_cells.json"  # 新版保留抓包接管经过的范围
        args.resume_traversed = (
            set(map(tuple, json.loads(traversed_file.read_text(encoding="utf-8"))))
            if traversed_file.exists()
            else set(args.resume_covered)
        )  # 兼容旧纯视觉记录
        if any(not grid.in_bounds(*p) or not region[p] for p in args.resume_covered):  # 校验当前仍在同一分量
            raise ValueError("resume_component_mismatch")  # 禁止将其他分量算入覆盖
        if any(not grid.in_bounds(*p) or not region[p] for p in args.resume_traversed):  # 总遍历同样不能跨图继承
            raise ValueError("resume_traversal_component_mismatch")  # 不接受错误范围
        failure_file = args.resume / "blocked_points.json"  # 新版结构化失败记录
        if failure_file.exists():  # 支持连续多次续跑
            args.resume_failures = json.loads(failure_file.read_text(encoding="utf-8"))  # 原样保留来源
        elif (args.resume / "failure_analysis.json").exists():  # 导入首次实测的明确卡住证据
            failure = json.loads(
                (args.resume / "failure_analysis.json").read_text(encoding="utf-8")
            )  # 不从成功记录猜测
            args.resume_failures = [
                {
                    "position": failure["last_visual_position"],
                    "goal": failure["last_goal"],
                    "reason": failure["navigation_failure"],
                    "obstructed": True,
                    "source_run": str(args.resume.resolve()),
                }
            ]  # 公开历史来源
        unconfirmed = [
            item for item in args.resume_failures if not item.get("obstructed")
        ]  # 旧版本可能包含暂时楼层失败
        args.resume_failures = [
            item for item in args.resume_failures if item.get("obstructed")
        ]  # 仅确认卡住的点继续跳过
        if unconfirmed:  # 保留重分类证据，不静默删除历史错误
            write_json(directory / "inherited_unconfirmed_failures.json", unconfirmed)  # 待重试目标不再黑名单化
        plan.update(
            resumed_from=str(args.resume.resolve()), inherited_covered_cells=len(args.resume_covered)
        )  # 报告累积口径
    plan["skip_blocked"] = args.skip_blocked  # 明确本次失败策略
    plan["capture_fallback"] = args.capture_fallback  # 明确本次来源切换权限
    plan["activate_game"] = args.activate_game  # 记录是否请求了自动切前台
    if args.capture_fallback:  # 用户已明确允许抓包继续行走
        plan["reference_role"] = (
            "accuracy reference and explicit fallback navigation; never fed into visual matching"  # 不再宣称所有行走独立于参考源
        )
    write_json(
        directory / "plan.json", {**plan, "targets": [grid.world_of_index(*point) for point in targets]}
    )  # 保存可审查路线点
    print(json.dumps(plan, ensure_ascii=False), flush=True)  # 先输出实际规模
    return (
        0 if args.mode == "plan" else execute(args, grid, region, targets, directory, plan)
    )  # 默认只规划，无任何键鼠输入


if __name__ == "__main__":  # 允许作为模块导入进行离线测试
    raise SystemExit(main())  # 向外部监控返回明确完成状态
