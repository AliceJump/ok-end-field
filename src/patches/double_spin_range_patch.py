from __future__ import annotations

_PATCH_INSTALLED = False


def install_double_spin_range_patch():
    """让 float 配置的 DoubleSpinBox 支持负值与大数值。

    背景：ok 库的 LabelAndDoubleSpinBox 不设置数值范围，QDoubleSpinBox
    默认范围为 0.00~99.99：
    - 负数（如 yaw_per_pixel 的 -0.1）会被钳成 0，用户一旦改动就会把
      配置写坏成 0；
    - 大数值（世界坐标 xyz 可到 ±500 以上）无法输入。
    本补丁在控件创建后把范围扩宽为 ±1e8，与 int SpinBox 的默认上限一致；
    不改变任何已有可用行为（原范围只会更窄）。
    """
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return
    try:
        from ok.ui.qt.tasks.LabelAndDoubleSpinBox import LabelAndDoubleSpinBox
    except Exception:
        # ok 库 GUI 不可用（如纯逻辑测试环境），跳过
        return

    orig_init = LabelAndDoubleSpinBox.__init__

    def patched_init(self, config_desc, config, key):
        orig_init(self, config_desc, config, key)
        self.spin_box.setRange(-99999999.0, 99999999.0)

    LabelAndDoubleSpinBox.__init__ = patched_init
    _PATCH_INSTALLED = True
