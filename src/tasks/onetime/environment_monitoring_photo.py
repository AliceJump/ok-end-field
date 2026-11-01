import cv2  # 复用项目已有图像处理依赖。
import numpy as np  # 使用数组掩码检查图标颜色与几何。

from src.image.frame_processes import isolate_by_hsv_ranges  # 复用公共 HSV 颜色掩码工具。


def xiaochihe_target_checked(frame):  # 只判断小池荷条件标记，调用方负责确认相机界面与目标文字。
    if not isinstance(frame, np.ndarray) or frame.dtype != np.uint8:  # 缺失或非截图数组不视为条件满足。
        return False  # 保守拒绝无法识别的输入。
    if frame.ndim != 3 or frame.shape[2] != 3 or min(frame.shape[:2]) == 0:  # 要求有效的 BGR 三通道截图。
        return False  # 不对无效截图执行颜色转换。
    height, width = frame.shape[:2]  # 按当前截图分辨率缩放参考区域。
    x1, x2 = (round(value * width / 1920) for value in (47, 83))  # 区域来自实测 1080p 截图左下标记。
    y1, y2 = (round(value * height / 1080) for value in (720, 756))  # 标记中心约为参考坐标（65，738）。
    crop = frame[y1:y2, x1:x2]  # 仅裁剪条件图标，避免目标文字和背景亮点干扰。
    if not crop.size:  # 极小或不完整截图可能没有可用区域。
        return False  # 没有图标证据时返回未满足。
    crop = cv2.resize(crop, (36, 36), interpolation=cv2.INTER_LINEAR)  # 在统一参考尺寸下检查圆环与勾形。
    yellow_ranges = (((18, 55, 150), (42, 255, 255)),)  # 保留原黄色光晕的色相、饱和度和亮度范围。
    yellow = isolate_by_hsv_ranges(crop, yellow_ranges, invert=False, kernel_size=0)[:, :, 0] > 0  # 不反色、不扩张笔画。
    if np.count_nonzero(yellow) < 45:  # 灰方块和纯白高光不具有足够黄色提示。
        return False  # 先排除没有条件满足颜色的标记。
    bright_ranges = (((0, 0, 210), (180, 80, 255)), ((18, 0, 210), (42, 255, 255)))  # 合并原近白与明亮黄色条件。
    bright = isolate_by_hsv_ranges(crop, bright_ranges, invert=False, kernel_size=0)[:, :, 0] > 0  # 保留逐像素几何语义。
    yy, xx = np.ogrid[:36, :36]  # 构造参考区域内的几何坐标。
    radius = np.hypot(xx - 17.5, yy - 17.5)  # 圆环中心来自两张实测图标的共同位置。
    ring = (radius >= 9.5) & (radius <= 13.5)  # 只检查外圈，内部勾线单独验证。
    if bright[ring].mean() < 0.35:  # 少量黄色噪点无法组成足够完整的亮圆环。
        return False  # 缺少外圈时不确认条件满足。
    for half in (xx < 17.5, xx >= 17.5, yy < 17.5, yy >= 17.5):  # 检查四个半圈，拒绝单边背景亮条。
        if bright[ring & half].mean() < 0.25:  # 保留右上勾线与圆环相接处的自然缺口。
            return False  # 任一方向缺少圆环证据时拒绝。
    if bright[11:16, 12:19].mean() > 0.25:  # 勾形左上内部应有暗部，防止整块黄色背景误判。
        return False  # 实心色块不是条件勾选图标。
    strokes = (bright[17:22, 11:16], bright[20:24, 16:21], bright[13:18, 20:25])  # 分别检查勾的左臂、底点和右臂。
    return bool(all(stroke.mean() >= 0.25 for stroke in strokes))  # 只有圆环与内部勾同时存在才确认满足。
