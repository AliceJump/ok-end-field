"""
导出战斗头像模板: 裁剪战斗截图头像, 按角色命名(去重), 生成 labelme JSON 标注
用法: python export_battle_templates.py
输出: crops/battle_icon_{char}.png + crops/{截图}.json (labelme格式)
"""
import cv2
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from batch_extract_portraits import (
    detect_blue_bars, BLOOD_BAR_ROI, PORTRAIT_OFFSET_X, PORTRAIT_OFFSET_Y,
    PORTRAIT_WIDTH, PORTRAIT_HEIGHT,
)

OK_TEMPLATES = Path(__file__).parent.parent.parent / "ok_templates"
OUTPUT_DIR = Path(__file__).parent / "crops"

# 完整 ground truth（反向匹配为基础 + 用户纠错）
GROUND_TRUTH = {
    '50.png': {
        1: 'contact_yvonne',     # 反向匹配一致
        2: 'contact_rossi',      # 用户纠错: 洛茜
        3: 'contact_gilberta',   # 反向匹配一致
        4: 'contact_ember',      # 反向匹配一致
    },
    '61.png': {
        1: 'contact_laevatain',  # 用户纠错: 莱万汀
        2: 'contact_ardelia',    # 用户纠错: 艾尔黛拉
        3: 'contact_wulfgard',   # 用户纠错: 狼卫
        4: 'contact_ember',      # 反向匹配一致
    },
    '68.png': {
        1: 'contact_yvonne',     # 反向匹配一致
        2: 'contact_gilberta',   # 反向匹配一致
        3: 'contact_last_rite',  # 用户纠错: 别礼
        4: 'contact_ember',      # 用户纠错: 余烬
    },
    '15.png': {
        1: 'contact_zhuang_fangyi',  # 用户确认: 庄方宜
        2: 'contact_perlica',        # 用户确认: 佩丽卡
        3: 'contact_avywenna',       # 用户确认: 艾维文娜
        4: 'contact_liino',          # 用户确认: 梨诺
    },
    '33.png': {
        1: 'contact_chen_qianyu',    # 用户确认: 陈千语
        2: 'contact_pogranichnik',   # 用户确认: 骏卫
        3: 'contact_lifeng',         # 用户确认: 黎风
        4: 'contact_da_pan',         # 用户确认: 大潘
    },
    '40.png': {
        1: 'contact_arclight',   # 用户确认: 弧光
        2: 'contact_xaihi',      # 用户确认: 赛希
        3: 'contact_tangtang',   # 用户确认: 汤汤
        4: 'contact_estella',    # 用户确认: 埃特拉
    },
    '41.png': {
        1: 'contact_endministrator',  # 用户确认: 管理员(endministrator)
        2: 'contact_alesh',      # 用户确认: 阿列什
        3: 'contact_snowshine',  # 用户确认: 昼雪
        4: 'contact_catcher',    # 用户确认: 卡契尔
    },
    '48.png': {
        1: 'contact_antal',      # 用户确认: 安塔尔
        2: 'contact_fluorite',   # 用户确认: 萤石
        3: 'contact_akekuri',    # 用户确认: 秋栗
        4: None,                 # 未放角色
    },
}


def extract_portraits_with_bbox(screenshot):
    """提取4个头像，返回 [(img, bbox), ...]；血条检测失败时回退固定区域"""
    height, width = screenshot.shape[:2]
    blue_bars = detect_blue_bars(screenshot, BLOOD_BAR_ROI)
    positions = []
    if len(blue_bars) == 4:
        x_coords = [bar.center_x for bar in blue_bars]
        diffs = [x_coords[i+1] - x_coords[i] for i in range(len(x_coords)-1)]
        if all(abs(d - diffs[0]) < 0.005 for d in diffs):
            for bar in blue_bars:
                pcx = int((bar.center_x + PORTRAIT_OFFSET_X) * width)
                pcy = int((bar.center_y + PORTRAIT_OFFSET_Y) * height)
                pw = int(PORTRAIT_WIDTH * width)
                ph = int(PORTRAIT_HEIGHT * height)
                positions.append((max(0, pcx - pw // 2), max(0, pcy - ph // 2), pw, ph))
    if not positions:
        # 固定区域回退（标准位置）
        pw = int(PORTRAIT_WIDTH * width)
        ph = int(PORTRAIT_HEIGHT * height)
        for x_norm, y_norm in ((40/1920, 927/1080), (156/1920, 927/1080),
                               (273/1920, 927/1080), (390/1920, 927/1080)):
            positions.append((int(x_norm * width), int(y_norm * height), pw, ph))
    results = []
    for x1, y1, pw, ph in positions:
        x2 = x1 + pw
        y2 = y1 + ph
        results.append((screenshot[y1:y2, x1:x2].copy(), (x1, y1, x2, y2)))
    return results


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    saved_chars = set()

    for fname, gt in GROUND_TRUTH.items():
        screenshot = cv2.imread(str(OK_TEMPLATES / fname))
        if screenshot is None:
            print(f"无法加载: {fname}")
            continue
        height, width = screenshot.shape[:2]
        portraits = extract_portraits_with_bbox(screenshot)
        if portraits is None:
            print(f"无法提取4个头像: {fname}")
            continue

        shapes = []
        for i, (img, (x1, y1, x2, y2)) in enumerate(portraits, start=1):
            label = gt[i]
            if label is None:
                print(f"  [{fname} P{i}] 未标注角色, 跳过")
                continue
            shapes.append({
                "label": label,
                "points": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
                "group_id": None,
                "shape_type": "rectangle",
                "flags": {},
                "description": f"battle portrait P{i}",
            })
            if label not in saved_chars:
                saved_chars.add(label)
                out_name = f"battle_icon_{label.replace('contact_', '')}.png"
                cv2.imwrite(str(OUTPUT_DIR / out_name), img)
                print(f"  [{fname} P{i}] -> {out_name} bbox=({x1},{y1},{x2},{y2})")
            else:
                print(f"  [{fname} P{i}] {label} 已存在, 跳过")

        json_data = {
            "version": "3.3.10",
            "flags": {},
            "shapes": shapes,
            "imagePath": fname,
            "imageData": None,
            "imageHeight": height,
            "imageWidth": width,
            "description": "battle portraits (user confirmed ground truth)",
        }
        json_path = OUTPUT_DIR / f"{Path(fname).stem}.json"
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)
        print(f"  JSON: {json_path}")

    print(f"\n输出去重头像 ({len(saved_chars)} 个)")


if __name__ == "__main__":
    main()