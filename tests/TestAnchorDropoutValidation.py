"""NCC 实际停用和独立参考路程评分回归，不发送游戏输入。"""  # 实机入口另行显式启用

import hashlib
import tempfile
import unittest  # 使用仓库测试入口
from pathlib import Path
from types import SimpleNamespace  # 构造最小元信息
from unittest.mock import Mock, patch  # 验证 NCC 原方法没有执行

from scripts.nav import replay_visual_chain
from scripts.nav.anchor_dropout_analysis import reference_path, summarize  # 独立评分函数
from scripts.nav.anchor_dropout_gate import NccDropoutGate  # 实验边界
from scripts.nav.replay_visual_chain import replay
from scripts.nav.visual_validation import ReferenceTimeline  # 复用时间索引
from tests import TestVisualPositionSource as source_tests  # 复用合成移动图像而不重复发现测试类


class TestAnchorDropoutValidation(unittest.TestCase):  # 验证实验本身而非伪造成功结果
    def test_replay_rejects_patch_path_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recording = root / "recording.jsonl"
            recording.write_text(
                '{"scale_m_per_px": 1, "t": 0, "patch": "../outside.png"}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "patch_escapes_allowed_root"):
                replay(recording, root=root)

    def test_replay_refuses_to_overwrite_a_recording(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recording = root / "recording.jsonl"
            recording.write_text('{"scale_m_per_px": 1}\n', encoding="utf-8")
            argv = ["replay_visual_chain.py", "--output", str(recording), str(recording)]
            with (
                patch.object(replay_visual_chain.sys, "argv", argv),
                patch.object(replay_visual_chain, "ROOT", root),
                self.assertRaisesRegex(ValueError, "output_must_not_overwrite_recording"),
            ):
                replay_visual_chain.main()

    def test_replay_uses_the_application_error_budget_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recording = root / "recording.jsonl"
            recording.write_text('{"scale_m_per_px": 1}\n', encoding="utf-8")
            trace = [{"trusted": False, "reason": "test", "t": 0}]
            with patch.object(replay_visual_chain, "_replay_trace", return_value=(trace, hashlib.sha256())):
                report = replay(recording, root=root)
            self.assertEqual(report["max_error_m"], 40.0)

    def reference(self, t, x, z=0, **extra):  # 生成同会话独立轨迹
        return {"time": t, "x": x, "z": z, "mapId": "map02", "flow": 1, "scene": 102, **extra}  # 字段与抓包一致

    def test_distance_uses_all_turns_and_rejects_time_or_identity_gaps(self):  # 不将往返直线距离当路程
        points = [self.reference(1, 0), self.reference(1.2, 1), self.reference(1.4, 0)]  # 两米往返
        self.assertAlmostEqual(reference_path(ReferenceTimeline(points), 1.05, 1.35, "map02", 1), 1.5)  # 裁切后积分
        for bad in (  # 复用当前模块所需对象
            [points[0], self.reference(2, 0)],  # 完成当前观测或统计步骤
            [points[0], {**points[1], "flow": 2}, points[2]],  # 完成当前观测或统计步骤
        ):  # 空档或换连接
            self.assertIsNone(  # 完成当前观测或统计步骤
                reference_path(
                    ReferenceTimeline(bad), bad[0]["time"], bad[-1]["time"], "map02", 1
                )  # 完成当前观测或统计步骤
            )  # 不给缺口零误差

    def test_raw_error_and_incremental_drift_are_separate_and_failure_not_scored(self):  # 不隐去标定偏置
        references = [self.reference(1 + i * 0.2, i) for i in range(7)]  # 五米每秒连续轨迹

        def row(t, x, trusted=True):  # 视觉相对抓包有固定两米偏置及漂移
            return {  # 完成当前观测或统计步骤
                "trial": 1,  # 保存 trial 字段
                "phase": "outage",  # 保存 phase 字段
                "frame_t": t,  # 保存 frame_t 字段
                "flow": 1,  # 完整实验身份
                "pose": {"map_id": "map02", "x": x, "z": 0, "position_trusted": trusted},  # 保存 pose 字段
            }  # 不包含参考输入

        anchor = row(1.1, 2.5)  # 起点真值为半米，视觉多两米
        rows = [row(1.3, 3.6), row(1.5, 4.7), row(1.7, None, False)]  # 新增漂移一到二分米
        rows.insert(1, dict(rows[0]))  # 同一实际呈现帧重复记录不应增加统计权重
        report = summarize(rows, references, [{"trial": 1, "anchor": anchor, "stop_frame_t": 1.7}])  # 事后评分
        trial = report["trials"][0]  # 检查可信与失效终点分开
        self.assertEqual(trial["paired_frames"], 2)  # 失效帧不参与精度
        self.assertAlmostEqual(trial["last_verified_distance_m"], 2)  # 最后可信路程
        self.assertAlmostEqual(trial["stop_reference_distance_m"], 3)  # 失败点有独立距离
        self.assertAlmostEqual(trial["last_verified_error_m"], 2.2)  # 保留原始误差
        self.assertAlmostEqual(trial["last_verified_drift_m"], 0.2)  # 单列新增漂移

    def test_disabled_gate_never_calls_ncc_and_invalid_hud_cannot_propagate(self):  # 验证真正停用
        tracker = Mock()  # 不需要加载实际地图
        tracker.locator = SimpleNamespace(_prepare_template=Mock(return_value=object()))  # 有效 HUD
        gate = NccDropoutGate(tracker)  # 只绑定本次实验
        gate.locate, gate.enabled = object(), True  # 模拟已确认身份
        self.assertFalse(gate(None, None).accepted)  # 不发布新的绝对观测
        tracker.update.assert_not_called()  # 核心保证：原 NCC 没执行
        tracker.locator._prepare_template.return_value = None  # 加载或无效 HUD
        self.assertIsNone(gate(None, None).locate)  # 不能用旧元信息撑过加载
        tracker.update.assert_not_called()  # 无效 HUD 同样不触发 NCC

    def test_reference_gap_keeps_endpoint_error_but_labels_distance_as_lower_bound(self):  # 空档不能隐藏终点误差
        references = [  # 完成当前观测或统计步骤
            self.reference(1, 0),  # 完成当前观测或统计步骤
            self.reference(1.2, 1),  # 完成当前观测或统计步骤
            self.reference(2.2, 2),  # 完成当前观测或统计步骤
            self.reference(2.4, 3),  # 完成当前观测或统计步骤
        ]  # 中间一秒缺口
        timeline = ReferenceTimeline(references)  # 时间索引
        self.assertIsNone(reference_path(timeline, 1.1, 2.3, "map02", 1))  # 严格连续距离不成立
        self.assertAlmostEqual(reference_path(timeline, 1.1, 2.3, "map02", 1, allow_gaps=True), 2)  # 折线下界单列
        anchor = {"frame_t": 1.1, "flow": 1, "pose": {"map_id": "map02", "x": 0.5, "z": 0}}  # 时间包围起点
        row = {  # 完成当前观测或统计步骤
            "trial": 1,  # 保存 trial 字段
            "phase": "outage",  # 保存 phase 字段
            "frame_t": 2.3,  # 保存 frame_t 字段
            "flow": 1,  # 保存 flow 字段
            "pose": {"map_id": "map02", "x": 3, "z": 0, "position_trusted": True},  # 保存 pose 字段
        }  # 终点有半米误差
        report = summarize([row], references, [{"trial": 1, "anchor": anchor}])  # 离线评分
        self.assertIsNone(report["max_verified_distance_m"])  # 不宣称连续路程验证成功
        self.assertAlmostEqual(report["max_packet_path_lower_bound_m"], 2)  # 仍可报告路程下界
        self.assertAlmostEqual(report["trials"][0]["time_aligned_error"]["max_m"], 0.5)  # 不能丢弃有配对的误差

    def test_actual_source_continues_without_any_ncc_call_or_anchor_refresh(self):  # 使用真实图像匹配验证门禁
        fixture = source_tests.TestVisualPositionSource()  # 不执行其他测试案例
        fixture.setUp()  # 生成固定种子地图与移动画面
        source, _ = fixture._layered_source()  # 正常获取强锚
        self.addCleanup(source.close)  # 清理实际定位器
        gate = NccDropoutGate(source.tracker)  # 保存实际跟踪方法
        source.tracker.update = gate  # 本次测试独享实例
        source.sample(fixture.frame(504), now=1.4)  # 正常 NCC 为门禁提供已确认身份
        gate.original = Mock(side_effect=AssertionError("NCC must remain disabled"))  # 任何原方法调用都失败
        gate.enabled = True  # 从下一帧完全关闭 NCC
        for i in range(1, 21):  # 验证超过原四秒限制
            state = source.sample(fixture.frame(504 + i), now=1.4 + i * 0.25)  # 真实移动图像
            self.assertTrue(state["position_trusted"], (i, state))  # 真实视觉链继续
            self.assertFalse(state["absolute_position_valid"])  # 没有伪造绝对锚
            self.assertEqual(source._last_absolute_t, 1.4)  # 原锚年龄严格保留
        gate.original.assert_not_called()  # NCC 被物理阻断
        self.assertEqual(gate.disabled_calls, 20)  # 逐帧审计完整


if __name__ == "__main__":  # 允许独立运行
    unittest.main()  # 不启动游戏运行时
