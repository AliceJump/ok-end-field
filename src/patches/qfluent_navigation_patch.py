from __future__ import annotations

_PATCH_INSTALLED = False


def install_qfluent_navigation_patch():
    """修复 qfluentwidgets 导航面板在窗口显示前 expand 导致启动崩溃。

    背景：ok 库 MainWindow.set_window_size 在窗口显示前调用
    navigationInterface.expand(False)，NavigationPanel.expand 内部走
    _stopIndicatorAnimation -> _onIndicatorAniFinished。该方法对
    _findIndicatorItem(item) 的结果直接调用 setAboutSelected(False)，而窗口
    尚未显示时导航项 isVisible() 全为 False，_findIndicatorItem 沿父链找不到
    可见项返回 None，启动即抛
    AttributeError: 'NoneType' object has no attribute 'setAboutSelected'。

    本补丁重写 _onIndicatorAniFinished：指示项为 None 时跳过 setAboutSelected
    （此时没有正在显示的指示器，行为与 setCurrentItem 里已有的判空一致）。

    安装是原子的：先保存原始方法，任何一步失败都会回滚并记录日志，只有全部
    赋值成功后才标记为已安装，失败后下次调用可重试。
    """
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return
    try:
        from qfluentwidgets.components.navigation.navigation_panel import NavigationPanel
    except Exception:
        # qfluentwidgets 不可用，跳过，不影响启动
        return
    logger = None
    originals = {
        "_onIndicatorAniFinished": NavigationPanel._onIndicatorAniFinished,
    }
    try:
        from ok import Logger

        logger = Logger.get_logger(__name__)
    except Exception:
        pass

    def _patched_on_indicator_ani_finished(self):
        item = self.currentItem()
        if not item:
            return

        item.setSelected(True)
        indicator_item = self._findIndicatorItem(item)
        if indicator_item is not None:
            indicator_item.setAboutSelected(False)
        self.indicator.hide()

    try:
        NavigationPanel._onIndicatorAniFinished = _patched_on_indicator_ani_finished
    except Exception as exc:
        NavigationPanel._onIndicatorAniFinished = originals["_onIndicatorAniFinished"]
        if logger is not None:
            logger.warning("qfluent_navigation_patch 安装失败，已回滚已修改的值: %s", exc)
        return
    _PATCH_INSTALLED = True
