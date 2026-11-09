"""仅用于实验的 NCC 停用边界，不改变生产跟踪器。"""  # 方法退出后恢复

from src.localization.minimap_map_locator import TrackResult  # 使用实际定位契约


class NccDropoutGate:  # 只替换本次脚本持有的跟踪器调用
    def __init__(self, tracker):  # 保存正式方法与已知身份
        self.original = tracker.update  # 恢复后继续真实 NCC
        locator = tracker.locator  # 找到不包含搜索的 HUD 准备器
        self.frame_gate = getattr(locator, "_frame_gate", getattr(locator, "base", locator))  # 复用实际纹理和图标门槛
        self.locate = None  # 未获取过强锚不能开启模拟
        self.enabled = False  # 默认正常定位
        self.disabled_calls = 0  # 证明模拟期间未执行 NCC
        self.normal_calls = 0  # 恢复阶段仍记录真实调用

    def __call__(self, frame, geometry, **kwargs):  # 保持生产 update 调用接口
        if not self.enabled:  # 正常获取与恢复阶段
            self.normal_calls += 1  # 可审计真实调用次数
            result = self.original(frame, geometry, **kwargs)  # 原样运行生产 NCC
            if result.accepted and result.position is not None:  # 身份只来自已确认观测
                self.locate = result.locate  # 仅保存失锚前元信息
            return result  # 不改变正常确认结果
        self.disabled_calls += 1  # 不调用 original 或 locate_near/global
        if self.locate is None:  # 防止没有强锚就伪造状态
            raise RuntimeError("dropout_without_confirmed_identity")  # 实验设置错误应停止
        if self.frame_gate._prepare_template(frame, geometry) is None:  # 原纹理与遮挡门槛仍生效
            return TrackResult("lost", False, "experiment_invalid_hud", None, None, 0, False)  # 加载画面不能续航
        return TrackResult("lost", False, "experiment_ncc_disabled", self.locate, None, 0, False)  # 不提供候选位置
