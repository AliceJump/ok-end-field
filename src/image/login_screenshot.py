import ctypes
import win32gui
import win32ui
import win32con
from PIL import Image, ImageGrab

def capture_window(hwnd):
    """
    截取指定窗口客户区截图（不包含标题栏/边框）

    Args:
        hwnd (int): 窗口句柄

    Returns:
        PIL.Image: 截图结果
    """

    # 获取窗口客户区尺寸
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    width = right - left
    height = bottom - top

    # 获取窗口DC
    hwnd_dc = win32gui.GetDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()

    # 创建位图对象
    save_bitmap = win32ui.CreateBitmap()
    save_bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
    save_dc.SelectObject(save_bitmap)

    # 将窗口内容拷贝到内存DC
    result = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 1)

    # 如果 PrintWindow 失败，回退 BitBlt
    if result != 1:
        save_dc.BitBlt((0, 0), (width, height), mfc_dc, (0, 0), win32con.SRCCOPY)

    # 转换为 PIL Image
    bmpinfo = save_bitmap.GetInfo()
    bmpstr = save_bitmap.GetBitmapBits(True)

    img = Image.frombuffer("RGB", (bmpinfo["bmWidth"], bmpinfo["bmHeight"]), bmpstr, "raw", "BGRX", 0, 1)

    # 释放资源
    win32gui.DeleteObject(save_bitmap.GetHandle())
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwnd_dc)

    return img


def capture_window_by_screen(hwnd):
    """
    通过“全虚拟桌面截图 + 裁剪窗口客户区”的方式获取窗口截图

    支持多显示器：窗口位于主屏原点左侧/上方（屏幕坐标为负值）时，
    pyautogui.screenshot() 只截主屏会导致裁剪越界全黑，
    因此改用 ImageGrab.grab(all_screens=True) 截取整个虚拟桌面，
    再按虚拟桌面原点偏移裁剪出窗口客户区。

    Args:
        hwnd (int): 窗口句柄

    Returns:
        PIL.Image: 截图结果（仅客户区）
    """

    # 1️⃣ 获取客户区在屏幕中的位置
    left, top = win32gui.ClientToScreen(hwnd, (0, 0))
    right, bottom = win32gui.ClientToScreen(hwnd, win32gui.GetClientRect(hwnd)[2:])

    width = right - left
    height = bottom - top

    # 2️⃣ 全虚拟桌面截图（包含主屏以外的负坐标区域）
    screen = ImageGrab.grab(all_screens=True)

    # 3️⃣ 虚拟桌面原点偏移后裁剪窗口客户区
    virtual_x = ctypes.windll.user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
    virtual_y = ctypes.windll.user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
    cropped = screen.crop((left - virtual_x, top - virtual_y,
                           left - virtual_x + width, top - virtual_y + height))

    return cropped
