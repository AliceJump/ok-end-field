"""
批量提取游戏截图中左下角4个角色头像
使用蓝色血条检测定位头像位置
支持任意分辨率，使用归一化坐标（基于1920x1080标准）
"""
import cv2
import numpy as np
import sys
from pathlib import Path


# 标准分辨率 1920x1080 下的参数
STANDARD_WIDTH = 1920
STANDARD_HEIGHT = 1080

# 血条检测区域（归一化坐标）
BLOOD_BAR_ROI = {
    'x1': 0,
    'y1': 980 / STANDARD_HEIGHT,  # 约0.907
    'x2': 500 / STANDARD_WIDTH,   # 约0.260
    'y2': 1010 / STANDARD_HEIGHT  # 约0.935
}

# 血条位置范围（归一化Y坐标）
BLOOD_BAR_Y_MIN = 988 / STANDARD_HEIGHT  # 约0.915
BLOOD_BAR_Y_MAX = 1005 / STANDARD_HEIGHT  # 约0.931

# 头像参数（归一化）
PORTRAIT_OFFSET_X = -17 / STANDARD_WIDTH   # 相对血条中心左移
PORTRAIT_OFFSET_Y = -46 / STANDARD_HEIGHT  # 相对血条中心上移（左下角不变高度减14）
PORTRAIT_WIDTH = 54 / STANDARD_WIDTH       # 头像宽度
PORTRAIT_HEIGHT = 46 / STANDARD_HEIGHT     # 头像高度 (60-14=46, 左下角不变)


def detect_blue_bars(screenshot, roi=None):
    """检测蓝色血条位置"""
    height, width = screenshot.shape[:2]

    if roi is not None:
        # 将归一化坐标转换为实际像素坐标
        x1 = int(roi['x1'] * width)
        y1 = int(roi['y1'] * height)
        x2 = int(roi['x2'] * width)
        y2 = int(roi['y2'] * height)
        search_area = screenshot[y1:y2, x1:x2]
        offset_x, offset_y = x1, y1
    else:
        search_area = screenshot
        offset_x, offset_y = 0, 0

    hsv = cv2.cvtColor(search_area, cv2.COLOR_BGR2HSV)

    # 根据用户提供的 hsl(196, 98%, 54%) 调整HSV范围
    lower_blue = np.array([90, 180, 100])
    upper_blue = np.array([110, 255, 255])

    mask = cv2.inRange(hsv, lower_blue, upper_blue)

    # 形态学操作
    kernel = np.ones((2, 5), np.uint8)
    mask = cv2.dilate(mask, kernel, iterations=1)

    # 找轮廓
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    bars = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)

        # 转换为归一化坐标
        norm_x = (x + offset_x) / width
        norm_y = (y + offset_y) / height
        norm_w = w / width
        norm_h = h / height

        # 血条应该是细长的水平条，y坐标在范围内
        if (norm_w > 50/width and norm_h < 12/height and
                norm_w > norm_h * 3 and
                BLOOD_BAR_Y_MIN <= norm_y <= BLOOD_BAR_Y_MAX):
            # 计算血条中心的归一化坐标
            center_x = (x + offset_x + w // 2) / width
            center_y = (y + offset_y + h // 2) / height

            bars.append({
                'x': norm_x,
                'y': norm_y,
                'width': norm_w,
                'height': norm_h,
                'center_x': center_x,
                'center_y': center_y
            })

    # 按x坐标排序
    bars.sort(key=lambda b: b['x'])
    return bars


def extract_portraits(screenshot, output_dir, filename):
    """从截图中提取4个头像"""
    height, width = screenshot.shape[:2]

    # 检测蓝色血条
    blue_bars = detect_blue_bars(screenshot, BLOOD_BAR_ROI)

    if len(blue_bars) != 4:
        print(f"  警告: 检测到 {len(blue_bars)} 个血条，预期4个")
        return False

    # 验证血条位置是否符合公差数列
    x_coords = [bar['center_x'] for bar in blue_bars]
    diffs = [x_coords[i+1] - x_coords[i] for i in range(len(x_coords)-1)]

    if not all(abs(d - diffs[0]) < 0.005 for d in diffs):
        print("  警告: 血条x坐标不符合等差数列")
        return False

    # 根据血条位置确定头像位置
    portraits = []
    for i, bar in enumerate(blue_bars):
        # 头像中心在血条中心上方，然后左移
        portrait_center_x = bar['center_x'] + PORTRAIT_OFFSET_X
        portrait_center_y = bar['center_y'] + PORTRAIT_OFFSET_Y

        # 转换为实际像素坐标
        px_center_x = int(portrait_center_x * width)
        px_center_y = int(portrait_center_y * height)
        px_width = int(PORTRAIT_WIDTH * width)
        px_height = int(PORTRAIT_HEIGHT * height)

        portraits.append({
            'name': f'P{i+1}',
            'center_x': px_center_x,
            'center_y': px_center_y,
            'width': px_width,
            'height': px_height,
            'bar': bar
        })

    # 保存每个头像
    for p in portraits:
        x1 = max(0, p['center_x'] - p['width'] // 2)
        y1 = max(0, p['center_y'] - p['height'] // 2)
        x2 = x1 + p['width']
        y2 = y1 + p['height']
        portrait = screenshot[y1:y2, x1:x2]

        # 文件名包含偏移参数
        offset_info = f"x{int(PORTRAIT_OFFSET_X * width)}_y{int(-PORTRAIT_OFFSET_Y * height)}_w{p['width']}_h{p['height']}"
        output_path = output_dir / f"{filename}_{p['name']}_{offset_info}.png"
        cv2.imwrite(str(output_path), portrait)

    return True


def _validate_path(path: Path) -> Path:
    """验证并规范化路径，防止路径注入攻击"""
    resolved = path.resolve()
    # 检查路径是否包含危险字符
    if ".." in path.parts or "~" in str(path):
        raise ValueError(f"路径包含危险字符: {path}")
    return resolved

def main():
    if len(sys.argv) < 2:
        print("用法: python batch_extract_portraits.py <图片目录> [输出目录]")
        print("示例: python batch_extract_portraits.py health/ output/portraits/")
        sys.exit(1)

    input_dir = _validate_path(Path(sys.argv[1]))
    if len(sys.argv) >= 3:
        output_dir = _validate_path(Path(sys.argv[2]))
    else:
        output_dir = input_dir / "portraits"

    # 创建输出目录
    output_dir.mkdir(parents=True, exist_ok=True)

    # 支持的图片格式
    image_extensions = {'.png', '.jpg', '.jpeg', '.bmp', '.tiff'}

    # 查找所有图片
    image_files = [f for f in input_dir.iterdir()
                   if f.suffix.lower() in image_extensions and f.is_file()]

    if not image_files:
        print(f"未找到图片文件: {input_dir}")
        sys.exit(1)

    print(f"找到 {len(image_files)} 个图片文件")
    print(f"输出目录: {output_dir}")
    print()

    success_count = 0
    fail_count = 0

    for image_file in sorted(image_files):
        print(f"处理: {image_file.name}")

        screenshot = cv2.imread(str(image_file))
        if screenshot is None:
            print("  错误: 无法加载图片")
            fail_count += 1
            continue

        filename = image_file.stem
        if extract_portraits(screenshot, output_dir, filename):
            success_count += 1
            print("  成功: 提取4个头像")
        else:
            fail_count += 1
            print("  失败: 血条检测异常")

    print()
    print(f"完成: 成功 {success_count} 个, 失败 {fail_count} 个")


if __name__ == "__main__":
    main()