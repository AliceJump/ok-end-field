"""
批量提取游戏截图中左下角4个角色头像
使用蓝色血条检测定位头像位置
支持任意分辨率，使用归一化坐标（基于1920x1080标准）
"""
import cv2
import sys
from pathlib import Path

# 从 src 导入血条检测模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.image.blood_bar_detector import (
    detect_blue_bars,
    get_portrait_bbox,
    validate_bars,
    BLOOD_BAR_ROI,
    PORTRAIT_OFFSET_X,
    PORTRAIT_OFFSET_Y,
    PORTRAIT_WIDTH,
    PORTRAIT_HEIGHT,
    FULL_HP_WIDTH_PX,
)


def extract_portraits(screenshot, output_dir, filename):
    """从截图中提取4个头像"""
    height, width = screenshot.shape[:2]

    # 检测蓝色血条
    blue_bars = detect_blue_bars(screenshot, BLOOD_BAR_ROI)

    if len(blue_bars) != 4:
        print(f"  警告: 检测到 {len(blue_bars)} 个血条，预期4个")
        return False

    # 验证血条位置是否符合等差数列
    if not validate_bars(blue_bars):
        print("  警告: 血条x坐标不符合等差数列")
        return False

    # 保存每个头像
    for i, bar in enumerate(blue_bars, 1):
        x1, y1, x2, y2 = get_portrait_bbox(bar, width, height)
        portrait = screenshot[y1:y2, x1:x2]

        # 文件名包含偏移参数
        offset_info = f"x{int(PORTRAIT_OFFSET_X * width)}_y{int(-PORTRAIT_OFFSET_Y * height)}_w{x2-x1}_h{y2-y1}"
        output_path = output_dir / f"{filename}_P{i}_{offset_info}.png"
        cv2.imwrite(str(output_path), portrait)

    return True


def _validate_path(path: Path) -> Path:
    """验证并规范化路径，防止路径注入攻击"""
    try:
        resolved = path.resolve()
    except RuntimeError as e:
        raise ValueError(f"路径解析失败(可能存在循环符号链接): {path}") from e
    # 检查路径是否包含危险字符
    if ".." in path.parts or "~" in str(path):
        raise ValueError(f"路径包含危险字符: {path}")
    return resolved

def main():
    if len(sys.argv) < 2:
        print("用法: python batch_extract_portraits.py <图片目录> [输出目录]")
        print("示例: python batch_extract_portraits.py health/ output/portraits/")
        sys.exit(1)

    try:
        input_dir = _validate_path(Path(sys.argv[1]))
        if len(sys.argv) >= 3:
            output_dir = _validate_path(Path(sys.argv[2]))
        else:
            output_dir = input_dir / "portraits"

        if input_dir == output_dir:
            print("错误: 输入目录和输出目录不能相同")
            sys.exit(1)

        if not input_dir.is_dir():
            print(f"错误: 输入路径不是目录: {input_dir}")
            sys.exit(1)

        # 创建输出目录
        output_dir.mkdir(parents=True, exist_ok=True)

        # 支持的图片格式
        image_extensions = {'.png', '.jpg', '.jpeg', '.bmp', '.tiff'}

        # 查找所有图片
        image_files = [f for f in input_dir.iterdir()
                       if f.suffix.lower() in image_extensions and f.is_file()]
    except ValueError as e:
        print(f"路径错误: {e}")
        sys.exit(1)
    except OSError as e:
        print(f"文件系统错误: {e}")
        sys.exit(1)

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