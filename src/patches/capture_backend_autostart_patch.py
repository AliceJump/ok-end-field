"""在**游戏启动之前**把抓包后端拉起来。

## 为什么必须挂在这里，而不是定位服务里

抓包后端的成败全在时机：游戏会话的密钥/nonce 只在连接开头出现一次（登录握手），
抓包晚于它就拿不到，整条流都推不出正确的解密位置。所以后端必须**早于游戏的主连接**就绪。

而任何依赖游戏画面的任务在窗口可截屏之前都不会被执行——``TaskExecutor.execute()``
要先 ``next_frame()``，而 ``can_capture()`` 要求设备已连上且能抓帧。也就是说，把拉起放在
普通任务里**永远太晚**：等它跑起来，玩家早就登录完了。

``StartController.do_start()`` 是"启动游戏"的唯一入口，``start_device()`` 里紧接着就
``execute(游戏路径)``。挂在它前面，正好是游戏进程存在之前的最后时机。

## 覆盖面与残留情形

- 由本应用启动游戏：覆盖（这是推荐用法）。
- 游戏已经在跑、但玩家还没登录：覆盖（``do_start`` 无论走哪个分支都会先到我们这里）。
- **玩家自己先开游戏、再开本应用**：覆盖不了，后端一定拿不到登录握手。此时后端会明确报
  「缺少连接起点，请重新启动游戏」，或用它的 ``--seed-pcap`` 接管一份从登录前开始的抓包。

补丁只做一件事，且**无论失败都不阻断游戏启动**——拉起后端是锦上添花，起不来不该让用户
连游戏都开不了。
"""

from __future__ import annotations

_PATCH_INSTALLED = False


def _position_service():
    from src.localization.position_service import get_world_pose_service

    return get_world_pose_service()


def ensure_capture_backend_before_game() -> bool:
    """按应用级定位服务的配置拉起后端；返回它当前是否可用。

    开关或自动启动关着、找不到任务、拉起失败都返回 False——调用方据此决定是否提示，
    但**不因此中止**游戏启动。
    """

    service = _position_service()
    if service is None:
        return False
    if not service.experimental_capture_enabled():
        return False
    # 关掉自动启动就只探活，留给"我自己管后端进程"的用法
    return bool(service.ensure_capture_backend())


def install_capture_backend_autostart_patch():
    """把"启动游戏"入口包一层，先确保抓包后端在跑。安装是幂等的。"""

    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return
    try:
        from ok.core import start_controller
    except Exception:
        # ok 库不可用：跳过，不影响启动
        return

    # 用模块级的 logger（``StartController`` 本身没有 self.logger），否则这两条日志会掉进黑洞
    logger = getattr(start_controller, "logger", None)
    original_do_start = start_controller.StartController.do_start

    def do_start(self, task=None, exit_after=False):
        try:
            if ensure_capture_backend_before_game() and logger is not None:
                logger.info("capture backend is ready before starting the game")
        except Exception as exc:  # 拉起失败绝不能挡住游戏
            if logger is not None:
                logger.error(f"ensure capture backend failed: {exc}")
        return original_do_start(self, task, exit_after)

    start_controller.StartController.do_start = do_start
    _PATCH_INSTALLED = True
