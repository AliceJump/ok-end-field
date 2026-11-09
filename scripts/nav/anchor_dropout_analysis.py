"""失锚试验的独立抓包评分，不向导航返回位置。"""  # 仅做事后分析

import csv  # 导出可绘图的距离表
import hashlib  # 标记独立评分算法版本
import json  # 重新读取原始证据
import math  # 计算路程和二维误差
from itertools import pairwise  # 遍历相邻独立参考点
from pathlib import Path  # 使用明确的证据路径

from scripts.nav.visual_validation import ReferenceTimeline, align_reference, error_statistics  # 复用时间对齐与统计


def reference_path(timeline, start, end, map_id, flow, *, allow_gaps=False):  # 严格路程与有空档折线下界分开
    first = align_reference(timeline, start, map_id, flow=flow)  # 起点必须有时间包围
    last = align_reference(timeline, end, map_id, flow=flow)  # 终点同样不外推
    if first is None or last is None:  # 无法评分时保留缺失原因
        return None  # 不把缺失当作零路程
    middle = [r for r in timeline.samples if start < r["time"] < end]  # 保留所有中间转弯
    points = [{**first, "time": start}, *middle, {**last, "time": end}]  # 裁切到试验区间
    distance = 0.0  # 累计折线路程
    for left, right in pairwise(points):  # 不只比较起终点直线距离
        gap = right["time"] - left["time"]  # 独立包的采集间隔
        step = math.dist((left["x"], left["z"]), (right["x"], right["z"]))  # 本段平面距离
        if (gap > 0.6 and not allow_gaps) or step > max(1.0, gap * 30):  # 折线下界可跨时间空档但不跨异常跳变
            return None  # 有缺口的路程不能当作已验证上限
        if any(r.get("mapId", map_id) != map_id or r.get("flow", flow) != flow for r in (left, right)):  # 不跨地图会话
            return None  # 保持独立真值身份
        if left.get("scene", right.get("scene")) != right.get("scene", left.get("scene")):  # 不跨场景跳变
            return None  # 避免过渡时刻假精度
        distance += step  # 使用原始坐标，不拟合比例尺
    return distance  # 只是观测到的折线长度


def summarize(  # 完成当前观测或统计步骤
    samples,
    references,
    trials,
    bins=(0, 1, 2, 3, 5, 10, 15, 20, 30, 40, 60, 80, 120, 200, 300, 400, 600, 800),  # 完成当前观测或统计步骤
):  # 汇总各距离段
    timeline = ReferenceTimeline(references)  # 共用排序索引
    results, scored = [], []  # 分试验与分距离输出
    for trial in trials:  # 每次试验使用自己的强锚起点
        anchor = trial.get("anchor")  # 最后一帧正常强绝对观测
        output = {**trial, "paired_frames": 0, "unpaired_frames": 0}  # 原始终态完整保留
        if anchor is None:  # 初始获取失败不伪装失锚测试
            results.append(output)  # 公开未进入试验的情况
            continue  # 不能计算失锚路程
        base = align_reference(timeline, anchor["frame_t"], anchor["pose"]["map_id"], flow=anchor["flow"])  # 对齐起点
        bias = None if base is None else (anchor["pose"]["x"] - base["x"], anchor["pose"]["z"] - base["z"])  # 起点偏置
        rows = [r for r in samples if r["trial"] == trial["trial"] and r["phase"] == "outage"]  # 不混入恢复段
        raw_errors, drift_errors = [], []  # 时间配对精度不因中间路程空档而丢弃
        seen_frames = set()  # 控制和独立采样读到同一观测时只评分一次
        for row in rows:  # 包括失败帧以保留可用率
            pose = row["pose"]  # 只能评分实际发布的视觉状态
            if not pose.get("position_trusted") or pose.get("visual_duplicate_frame"):  # 不给不可用坐标造误差
                continue  # 失败距离另记终点
            if row["frame_t"] in seen_frames:  # 旧记录器也可能重复输出同一帧
                continue  # 相同呈现帧不能重复增加统计权重
            seen_frames.add(row["frame_t"])  # 在参考匹配前固定去重口径
            ref = align_reference(timeline, row["frame_t"], pose["map_id"], flow=row["flow"])  # 对齐呈现时刻
            distance = reference_path(  # 完成当前观测或统计步骤
                timeline,
                anchor["frame_t"],
                row["frame_t"],
                pose["map_id"],
                row["flow"],  # 完成当前观测或统计步骤
            )  # 折线路程
            lower_bound = reference_path(  # 完成当前观测或统计步骤
                timeline,
                anchor["frame_t"],
                row["frame_t"],
                pose["map_id"],
                row["flow"],
                allow_gaps=True,  # 完成当前观测或统计步骤
            )  # 空档内实际运动未知
            if ref is None:  # 当前坐标没有时间包围才不能评分
                output["unpaired_frames"] += 1  # 公开缺失率
                continue  # 不使用最近旧包代替真值
            delta = (pose["x"] - ref["x"], pose["z"] - ref["z"])  # 原始绝对偏差
            drift = None if bias is None else (delta[0] - bias[0], delta[1] - bias[1])  # 仅在评分中扣除起点偏差
            item = {  # 完成当前观测或统计步骤
                "trial": trial["trial"],  # 保存 trial 字段
                "distance_m": distance if distance is not None else lower_bound,  # 两类距离通过标签区分
                "distance_continuous": distance is not None,  # 有空档下界不能当严格连续路程
                "absolute_delta": delta,  # 保存 absolute_delta 字段
                "drift_delta": drift,  # 保存 drift_delta 字段
                "frame_t": row["frame_t"],  # 保存 frame_t 字段
                "error_allowance_m": pose.get("visual_chain_error_budget_m"),  # 公开预算与实测偏差
                "anchor_displacement_m": None  # 保存 anchor_displacement_m 字段
                if base is None  # 继续核验本组条件
                else math.dist((base["x"], base["z"]), (ref["x"], ref["z"])),  # 离最后强锚的直线距离
            }  # 可追溯数据
            scored.append(item)  # 不回灌视觉链
            output["paired_frames"] += 1  # 本次有效评分帧
            raw_errors.append(delta)  # 所有时间对齐的原始误差
            if drift is not None:  # 起点必须同样有独立配对
                drift_errors.append(drift)  # 增量漂移单列
            if distance is not None:  # 严格连续路程只使用没有缺口的前缀
                output["last_verified_distance_m"] = distance  # 可信且连续评分终点
                output["last_verified_error_m"] = math.hypot(*delta)  # 终点原始误差
                output["last_verified_drift_m"] = None if drift is None else math.hypot(*drift)  # 新增漂移
            if lower_bound is not None:  # 保留长路段存在参考空档的证据
                output["last_packet_path_lower_bound_m"] = lower_bound  # 不是全程连续精度证明
                output["last_paired_error_m"] = math.hypot(*delta)  # 终点仍有有效时间配对
                output["last_paired_drift_m"] = None if drift is None else math.hypot(*drift)  # 终点增量漂移
                output["last_anchor_displacement_m"] = item["anchor_displacement_m"]  # 累计路程和直线位移分开
        output["time_aligned_error"] = error_statistics(raw_errors)  # 不因路程空档隐去偏差
        output["time_aligned_drift"] = error_statistics(drift_errors)  # 不混入起点偏置
        failed = next((r for r in rows if not r["pose"].get("position_trusted")), None)  # 第一帧撤销信任
        if failed is not None:  # 把采样中断与预算失效分开
            prior = [  # 完成当前观测或统计步骤
                r
                for r in samples
                if r["trial"] == trial["trial"] and r["frame_t"] < failed["frame_t"]  # 完成当前观测或统计步骤
            ]  # 前一个呈现帧
            gap = failed["frame_t"] - prior[-1]["frame_t"] if prior else None  # 不使用处理结束时间
            output["failure_frame_gap_s"] = gap  # 解释转向等待造成的中断
            output["termination"] = (  # 完成当前观测或统计步骤
                "sample_gap"
                if gap is not None and gap > 2
                else failed["pose"].get("trust_reason")  # 列出本次试验的统计或说明项
            )  # 预算原因原样保留
            measured = failed.get("measurement") or {}  # 新记录器保留原始里程计结果
            if measured.get("reason") == "too_soon":  # 不将跳过采样误认为累计误差耗尽
                output["termination"] = "odometry_sample_too_soon"  # 证据来自实际返回值
            context = failed.get("sample_context") or {}  # 在门禁运行前保存的锚链信息
            if context and context.get("previous_evidence_time") is None:  # 先前强锚没有对应采样图像
                output["termination"] = "no_sampled_visual_anchor"  # 不与预算极限混为一谈
        trial_scores = [  # 完成当前观测或统计步骤
            r
            for r in scored
            if r["trial"] == trial["trial"] and r["distance_m"] is not None  # 完成当前观测或统计步骤
        ]  # 有距离口径的观测
        if trial_scores:  # 距离推测必须有实际观测支撑
            final = trial_scores[-1]  # 使用同一路段终点的平均预算增长率
            initial_cost = anchor["pose"].get("visual_chain_error_budget_m")  # 最后强锚自身的额度
            final_cost, limit = final.get("error_allowance_m"), trial.get("budget_limit_m")  # 不猜应用配置
            if (  # 继续核验本组条件
                initial_cost is not None
                and final_cost is not None
                and limit is not None
                and final_cost > initial_cost  # 完成当前观测或统计步骤
            ):  # 正增长才可线性外推
                output["linear_budget_projection_m"] = (  # 完成当前观测或统计步骤
                    final["distance_m"] * (limit - initial_cost) / (final_cost - initial_cost)  # 完成当前观测或统计步骤
                )  # 不是实测上限或物理精度保证
                output["projection_observed_distance_m"] = final["distance_m"]  # 公开样本范围
                output["projection_distance_continuous"] = final["distance_continuous"]  # 空档下界不能伪装严格路程
                output["projection_final_allowance_m"] = final_cost  # 便于复算
        stop = trial.get("stop_frame_t")  # 停用结束或证据失效时刻
        output["stop_reference_distance_m"] = (  # 完成当前观测或统计步骤
            None  # 完成当前观测或统计步骤
            if stop is None  # 继续核验本组条件
            else reference_path(  # 失败端点另列
                timeline,
                anchor["frame_t"],
                stop,
                anchor["pose"]["map_id"],
                anchor["flow"],  # 完成当前观测或统计步骤
            )  # 结束当前参数或记录构造
        )  # 不能把失败时路程当作可信续航距离
        results.append(output)  # 试验未到目标也保留

    def group(continuous):  # 严格连续路程和折线下界分别输出
        grouped = []  # 空箱明确未覆盖
        for lower, upper in pairwise(bins):  # 固定区间防止挑选有利样本
            rows = [  # 完成当前观测或统计步骤
                r  # 完成当前观测或统计步骤
                for r in scored  # 复用当前模块所需对象
                if r["distance_m"] is not None  # 继续核验本组条件
                and lower <= r["distance_m"] < upper  # 继续核验本组条件
                and (not continuous or r["distance_continuous"])  # 继续核验本组条件
            ]  # 同一统计口径
            grouped.append(  # 完成当前观测或统计步骤
                {  # 空箱也明确显示未覆盖
                    "from_m": lower,  # 保存 from_m 字段
                    "to_m": upper,  # 保存 to_m 字段
                    "trials": len({r["trial"] for r in rows}),  # 公开独立试验数量
                    "absolute_error": error_statistics([r["absolute_delta"] for r in rows]),  # 绝对误差
                    "incremental_drift": error_statistics(  # 保存 incremental_drift 字段
                        [r["drift_delta"] for r in rows if r["drift_delta"] is not None]  # 完成当前观测或统计步骤
                    ),  # 起点相对漂移
                }  # 结束当前参数或记录构造
            )  # 不把相关帧数当独立成功率
        return grouped  # 两种距离不混为严格结论

    verified = [r["last_verified_distance_m"] for r in results if "last_verified_distance_m" in r]  # 已观测续航
    return {  # 完成当前观测或统计步骤
        "trials": results,  # 保存 trials 字段
        "distance_bins": group(True),  # 原有严格连续口径
        "packet_polyline_lower_bound_bins": group(False),  # 有空档时仅是记录折线下界
        "max_verified_distance_m": max(verified, default=None),  # 样本最大值
        "scored_frames": scored,  # 保存 scored_frames 字段
        "max_packet_path_lower_bound_m": max(  # 保存 max_packet_path_lower_bound_m 字段
            (r["distance_m"] for r in scored if r["distance_m"] is not None),
            default=None,  # 完成当前观测或统计步骤
        ),  # 实际路程至少达到的样本值
        "max_anchor_displacement_m": max(  # 保存 max_anchor_displacement_m 字段
            (r["anchor_displacement_m"] for r in scored if r["anchor_displacement_m"] is not None),
            default=None,  # 完成当前观测或统计步骤
        ),  # 最远离锚距离
        "limit_claim": "Observed route-specific range only; not a universal guaranteed maximum.",  # 不外推全局上限
        "timing_limit": "Packet receive time and WGC presentation time include game/minimap latency; no fitted delay applied.",  # 保存 timing_limit 字段
        "projection_limit": "Linear budget projections assume unchanged average allowance growth; keyframe revisits, texture, sampling and gating can invalidate them. They are not measured maximums or accuracy bounds.",  # 不把外推当成验证结论
    }  # 公开时间误差


def analyze_directory(directory):  # 基于原始日志重算，不需要游戏在线
    directory = Path(directory)  # 统一参数类型
    samples = [  # 完成当前观测或统计步骤
        json.loads(line)  # 完成当前观测或统计步骤
        for line in (directory / "visual.jsonl").read_text(encoding="utf-8").splitlines()  # 复用当前模块所需对象
        if line.strip()  # 继续核验本组条件
    ]  # 逐帧状态
    references = [  # 完成当前观测或统计步骤
        json.loads(line)  # 完成当前观测或统计步骤
        for line in (directory / "reference.jsonl").read_text(encoding="utf-8").splitlines()  # 复用当前模块所需对象
        if line.strip()  # 继续核验本组条件
    ]  # 独立参考轨迹
    previous = (  # 完成当前观测或统计步骤
        json.loads((directory / "report.json").read_text(encoding="utf-8"))  # 完成当前观测或统计步骤
        if (directory / "report.json").exists()  # 继续核验本组条件
        else {}  # 继续核验本组条件
    )  # 保留采集时源码与终态
    saved_trials = (  # 完成当前观测或统计步骤
        json.loads((directory / "trials.json").read_text(encoding="utf-8"))  # 完成当前观测或统计步骤
        if (directory / "trials.json").exists()  # 继续核验本组条件
        else []  # 继续核验本组条件
    )  # 成功结束的原始试验
    candidate_trials = previous.get("runtime_trial_records") or max(  # 完成当前观测或统计步骤
        (saved_trials, previous.get("trials", [])),
        key=len,  # 完成当前观测或统计步骤
    )  # 不丢弃最后一个未完成试验
    runtime_keys = {  # 完成当前观测或统计步骤
        "trial",  # 列出本次试验的统计或说明项
        "repeat",  # 列出本次试验的统计或说明项
        "requested_m",  # 列出本次试验的统计或说明项
        "anchor",  # 列出本次试验的统计或说明项
        "disabled_call_start",  # 列出本次试验的统计或说明项
        "budget_limit_m",  # 列出本次试验的统计或说明项
        "last_trusted_frame_t",  # 列出本次试验的统计或说明项
        "visual_distance_m",  # 列出本次试验的统计或说明项
        "stop_frame_t",  # 列出本次试验的统计或说明项
        "reason",  # 列出本次试验的统计或说明项
        "disabled_calls",  # 列出本次试验的统计或说明项
        "recovery_s",  # 列出本次试验的统计或说明项
        "recovery_frames",  # 列出本次试验的统计或说明项
        "sampling_mode",  # 列出本次试验的统计或说明项
    }  # 只继承运行证据
    trials = [  # 完成当前观测或统计步骤
        {k: v for k, v in trial.items() if k in runtime_keys}
        for trial in candidate_trials  # 完成当前观测或统计步骤
    ]  # 清除旧评分避免重复重算残留
    report = {  # 完成当前观测或统计步骤
        **previous,  # 完成当前观测或统计步骤
        **summarize(samples, references, trials),  # 完成当前观测或统计步骤
        "runtime_trial_records": trials,  # 后续重算不依赖已经加工的统计
        "analysis_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),  # 保存 analysis_sha256 字段
    }  # 不改原始输入
    (directory / "report.json").write_text(  # 完成当前观测或统计步骤
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",  # 完成当前观测或统计步骤
    )  # 可追溯重算结果
    with (directory / "distance_errors.csv").open("w", encoding="utf-8-sig", newline="") as handle:  # Excel 可直接打开
        writer = csv.writer(handle)  # 固定列结构
        writer.writerow(  # 完成当前观测或统计步骤
            [  # 完成当前观测或统计步骤
                "distance_kind",  # 列出本次试验的统计或说明项
                "from_m",  # 列出本次试验的统计或说明项
                "to_m",  # 列出本次试验的统计或说明项
                "trials",  # 列出本次试验的统计或说明项
                "paired_frames",  # 列出本次试验的统计或说明项
                "absolute_median_m",  # 列出本次试验的统计或说明项
                "absolute_p95_m",  # 列出本次试验的统计或说明项
                "absolute_max_m",  # 列出本次试验的统计或说明项
                "drift_p95_m",  # 列出本次试验的统计或说明项
                "drift_max_m",  # 列出本次试验的统计或说明项
            ]  # 结束当前参数或记录构造
        )  # 误差单位明确
        for kind, key in (  # 复用当前模块所需对象
            ("continuous", "distance_bins"),  # 完成当前观测或统计步骤
            ("packet_polyline_lower_bound", "packet_polyline_lower_bound_bins"),  # 完成当前观测或统计步骤
        ):  # 不混淆空档路程
            for row in report[key]:  # 空箱同样导出
                a, d = row["absolute_error"], row["incremental_drift"]  # 两种误差各列
                writer.writerow(  # 完成当前观测或统计步骤
                    [  # 完成当前观测或统计步骤
                        kind,  # 完成当前观测或统计步骤
                        row["from_m"],  # 完成当前观测或统计步骤
                        row["to_m"],  # 完成当前观测或统计步骤
                        row["trials"],  # 完成当前观测或统计步骤
                        a["count"],  # 完成当前观测或统计步骤
                        a.get("median_m"),  # 完成当前观测或统计步骤
                        a.get("p95_m"),  # 完成当前观测或统计步骤
                        a.get("max_m"),  # 完成当前观测或统计步骤
                        d.get("p95_m"),  # 完成当前观测或统计步骤
                        d.get("max_m"),  # 完成当前观测或统计步骤
                    ]  # 结束当前参数或记录构造
                )  # 不用零填补缺失值
    print(  # 完成当前观测或统计步骤
        json.dumps(  # 完成当前观测或统计步骤
            {  # 完成当前观测或统计步骤
                "reason": report.get("reason"),  # 保存 reason 字段
                "trials": len(trials),  # 保存 trials 字段
                "max_verified_distance_m": report["max_verified_distance_m"],  # 保存 max_verified_distance_m 字段
                "max_packet_path_lower_bound_m": report[
                    "max_packet_path_lower_bound_m"
                ],  # 保存 max_packet_path_lower_bound_m 字段
            }  # 结束当前参数或记录构造
        )  # 结束当前参数或记录构造
    )  # 精简结果
    return report  # 可供离线图表和测试复用
