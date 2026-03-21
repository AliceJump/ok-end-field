# ===== device layer =====
import ctypes
import time
import win32gui

user32 = ctypes.windll.user32

# Windows 鼠标事件：相对移动
MOUSEEVENTF_MOVE = 0x0001


# ===== math layer =====
import math


def calc_direction_step(from_pos, to_pos, max_step=100, min_step=20, slow_radius=200, deadzone=4):
    """
    计算从当前位置移动到目标位置的鼠标移动步长。

    该函数会根据目标距离动态调整移动步长：
    - 距离较远：使用 max_step（快速移动）
    - 距离较近：逐渐减速
    - 非常接近：进入 deadzone 停止移动

    Args:
        from_pos (tuple[int,int]): 当前坐标 (x, y)
        to_pos (tuple[int,int]): 目标坐标 (x, y)
        max_step (int): 最大移动步长
        min_step (int): 最小移动步长
        slow_radius (int): 减速半径，进入该范围后开始减速
        deadzone (int): 死区半径，小于该距离直接停止

    Returns:
        tuple[int,int]:
            (dx, dy) 鼠标需要移动的相对距离
    """

    # 计算目标与当前点的向量
    dx_raw = to_pos[0] - from_pos[0]
    dy_raw = to_pos[1] - from_pos[1]

    # 欧几里得距离
    dist = math.hypot(dx_raw, dy_raw)

    # 如果已经非常接近目标，则停止移动
    if dist < deadzone:
        return 0, 0

    # 根据距离决定移动速度
    if dist > slow_radius:
        step = max_step
    else:
        # 距离越近速度越慢
        step = max(min_step, int(max_step * (dist / slow_radius)))

    # 归一化方向向量并乘以步长
    dx = round(dx_raw / dist * step)
    dy = round(dy_raw / dist * step)

    return dx, dy


def click_down(hwnd, x, y, key="left"):
    """
    在指定窗口内模拟鼠标按下事件。
    """

    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_RIGHTDOWN = 0x0008

    # 坐标转换
    screen_x, screen_y = win32gui.ClientToScreen(hwnd, (x, y))

    # 移动鼠标
    user32.SetCursorPos(screen_x, screen_y)

    if key == "left":
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    elif key == "right":
        user32.mouse_event(MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
def click_up(hwnd, key="left"):
    """
    在指定窗口内模拟鼠标抬起事件。
    """

    MOUSEEVENTF_LEFTUP = 0x0004
    MOUSEEVENTF_RIGHTUP = 0x0010

    if key == "left":
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    elif key == "right":
        user32.mouse_event(MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)

# ===== device control =====
def active_and_send_mouse_delta(hwnd, dx=1, dy=1, activate=True, only_activate=False, delay=0.02, steps=3):
    """
    激活指定窗口并发送相对鼠标移动。

    该函数主要用于游戏自动化或需要控制特定窗口输入的场景。
    功能包括：
    1. 确保目标窗口被激活
    2. 发送相对鼠标移动（mouse_event）

    Args:
        hwnd (int): 目标窗口句柄
        dx (int): 鼠标X方向移动距离（相对移动）
        dy (int): 鼠标Y方向移动距离（相对移动）
        activate (bool): 是否尝试激活窗口
        only_activate (bool): 仅激活窗口，不发送鼠标移动
        delay (float): 每一步之间的延迟
        steps (int): 将移动拆分成多少步执行（更平滑）

    Notes:
        - 使用 mouse_event 发送的是相对移动
        - steps 可以让移动更平滑，避免一次移动过大
    """

    # 如果只需要激活窗口，则强制 activate
    if only_activate:
        activate = True

    if activate:
        try:
            current_hwnd = win32gui.GetForegroundWindow()

            # 如果当前窗口不是目标窗口
            if current_hwnd != hwnd:

                # 检查窗口句柄是否有效
                if not win32gui.IsWindow(hwnd):
                    print(f"窗口激活失败: 无效的窗口句柄 {hwnd}")
                    return

                # 如果窗口最小化，先恢复
                if win32gui.IsIconic(hwnd):
                    win32gui.ShowWindow(hwnd, 9)  # SW_RESTORE
                    time.sleep(0.15)

                # 如果窗口不可见，先显示
                if not win32gui.IsWindowVisible(hwnd):
                    win32gui.ShowWindow(hwnd, 5)  # SW_SHOW
                    time.sleep(0.15)

                # 再次恢复窗口（比单纯 SetForegroundWindow 更可靠）
                win32gui.ShowWindow(hwnd, 9)
                time.sleep(0.05)

                # 尝试设置前台窗口
                try:
                    win32gui.SetForegroundWindow(hwnd)
                except win32gui.error:
                    # Windows 前台窗口限制：
                    # 通过模拟 Alt 键绕过限制
                    import win32api
                    import win32con

                    win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
                    time.sleep(0.01)

                    win32gui.SetForegroundWindow(hwnd)

                    win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)

                time.sleep(delay)

                # 检查窗口是否真的在前台
                final_hwnd = win32gui.GetForegroundWindow()
                if final_hwnd != hwnd:
                    print(f"窗口激活警告: 窗口未完全置于前台 " f"(目标:{hwnd}, 当前:{final_hwnd})")

        except win32gui.error as e:
            # 错误码 0 通常不是严重错误
            if e.winerror != 0:
                print(f"窗口激活失败 (Win32错误 {e.winerror}): {e}")

        except Exception as e:
            print(f"窗口激活失败 (未知错误): {type(e).__name__}: {e}")

    # 只激活窗口不发送鼠标移动
    if not only_activate:

        # 将移动拆分为多个小步执行
        for _ in range(steps):

            step_dx = round(dx / steps)
            step_dy = round(dy / steps)

            # 发送鼠标相对移动
            user32.mouse_event(MOUSEEVENTF_MOVE, step_dx, step_dy, 0, 0)

            time.sleep(delay)


# ===== control =====
def move_to_target_once(hwnd, ocr_obj, screen_center_func, max_step=100, min_step=20, slow_radius=200):
    """
    根据OCR识别结果，将鼠标向目标位置移动一次。

    主要流程：
    1. 获取 OCR 目标中心
    2. 获取屏幕中心
    3. 计算移动方向
    4. 发送鼠标移动

    Args:
        hwnd (int): 目标窗口句柄
        ocr_obj: OCR识别对象，需包含 x,y,width,height
        screen_center_func (callable): 获取屏幕中心点的函数
        max_step (int): 最大移动步长
        min_step (int): 最小移动步长
        slow_radius (int): 减速半径

    Returns:
        tuple[int,int] | None:
            返回本次移动的 (dx, dy)，
            如果没有目标则返回 None
    """

    # 没有识别到目标
    if ocr_obj is None:
        return None

    # OCR框中心
    target_center = (ocr_obj.x + ocr_obj.width // 2, ocr_obj.y + ocr_obj.height // 2)

    # 当前准星位置（通常为屏幕中心）
    center_pos = screen_center_func()

    # 计算移动向量
    dx, dy = calc_direction_step(
        center_pos, target_center, max_step=max_step, min_step=min_step, slow_radius=slow_radius
    )

    # 如果需要移动
    if dx != 0 or dy != 0:
        active_and_send_mouse_delta(hwnd, dx, dy)

    return dx, dy


def run_at_window_pos(hwnd, func, x, y, sleep_time=0.5, *args, **kwargs):
    """
    临时将鼠标移动到窗口客户区指定位置执行函数，
    执行结束后恢复原鼠标位置。

    常用于：
    - 点击窗口内特定位置
    - 在指定位置执行输入操作

    Args:
        hwnd (int): 窗口句柄
        func (callable): 要执行的函数
        x (int): 客户区X坐标
        y (int): 客户区Y坐标
        sleep_time (float): 操作前后的等待时间
        *args: 传递给 func 的参数
        **kwargs: 传递给 func 的关键字参数
    """

    # 获取当前鼠标位置
    pt = ctypes.wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    original = (pt.x, pt.y)

    try:
        # 将客户区坐标转换为屏幕坐标
        screen_x, screen_y = win32gui.ClientToScreen(hwnd, (x, y))

        # 移动鼠标
        user32.SetCursorPos(screen_x, screen_y)
        time.sleep(sleep_time)

        # 执行操作
        func(*args, **kwargs)

        time.sleep(sleep_time)

    finally:
        # 恢复鼠标位置
        user32.SetCursorPos(original[0], original[1])


def run_in_window(hwnd, func, *args, **kwargs):
    prev = win32gui.GetForegroundWindow()
    need_restore = prev != hwnd

    try:
        if need_restore:
            active_and_send_mouse_delta(hwnd, only_activate=True)

        return func(*args, **kwargs)

    finally:
        if need_restore and prev and win32gui.IsWindow(prev):
            try:
                win32gui.SetForegroundWindow(prev)
            except:
                pass
