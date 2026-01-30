import os

import numpy as np
from ok import ConfigOption
from src.interaction.EfInteraction import EfInteraction

version = "dev"


# 不需要修改version, Github Action打包会自动修改
def make_bottom_left_black(frame):  # 可选. 某些游戏截图时遮挡UID使用
    """
    将图像左下角的一部分像素修改为黑色。

    参数:
        frame: 来自 OpenCV 的输入图像 (NumPy 数组)。

    返回:
        左下角被遮挡后的图像。如果发生错误（例如无效图像），则返回原始图像。
    """
    try:
        height, width = frame.shape[:2]  # 获取高度和宽度

        # 计算黑色矩形的大小
        black_width = int(0.072 * width)
        black_height = int(0.034 * height)

        # 计算左下角矩形的起始坐标
        start_x = int(0.054 * width)
        start_y = height - black_height

        # 创建黑色矩形 (NumPy 0 数组)
        black_rect = np.zeros((black_height, black_width, frame.shape[2]), dtype=frame.dtype)  # 确保数据类型一致

        # 用黑色矩形替换图像的左下角部分
        frame[start_y:height, start_x:start_x + black_width] = black_rect

        return frame
    except Exception as e:
        print(f"Error processing frame: {e}")
        return frame


config = {
    'debug': False,  # Optional, default: False
    'use_gui': True,  # 目前只支持True
    'config_folder': 'configs',  # 最好不要修改
    'screenshot_processor': make_bottom_left_black,  # 在截图的时候对frame进行修改, 可选
    'gui_icon': 'icons/icon.png',  # 窗口图标, 最好不需要修改文件名
    'wait_until_before_delay': 0,
    'wait_until_check_delay': 0,
    'wait_until_settle_time': 0,  # 调用 wait_until时候, 在第一次满足条件的时候, 会等待再次检测, 以避免某些滑动动画没到预定位置就在动画路径中被检测到
    'ocr': {  # 可选, 使用的OCR库
        'lib': 'onnxocr',
        'params': {
            'use_openvino': True,
        }
    },
    'windows': {  # Windows游戏请填写此设置
        'exe': ['Endfield.exe'],
        # 'hwnd_class': 'UnrealWindow', #增加重名检查准确度
        'interaction': EfInteraction,
        # Genshin:某些操作可以后台, 部分游戏支持 PostMessage:可后台点击, 极少游戏支持 ForegroundPostMessage:前台使用PostMessage Pynput/PyDirect:仅支持前台使用
        'capture_method': ['WGC', 'BitBlt_RenderFull'],
        # Windows版本支持的话, 优先使用WGC, 否则使用BitBlt_Full. 支持的capture有 BitBlt, WGC, BitBlt_RenderFull, DXGI
        'check_hdr': True,  # 当用户开启AutoHDR时候提示用户, 但不禁止使用
        'force_no_hdr': False,  # True=当用户开启AutoHDR时候禁止使用
        'require_bg': True  # 要求使用后台截图
    },
    'start_timeout': 60,  # default 60
    'window_size': {  # ok-script窗口大小
        'width': 1200,
        'height': 800,
        'min_width': 600,
        'min_height': 450,
    },
    'supported_resolution': {
        # 'ratio': '16:9',  # 支持的游戏分辨率
        'min_size': (1280, 720),  # 支持的最低游戏分辨率
        # 'resize_to': [(2560, 1440), (1920, 1080), (1600, 900), (1280, 720)],  # 可选, 如果非16:9自动缩放为 resize_to
    },
    'links': {  # 关于里显示的链接, 可选
        'default': {
            'github': 'https://github.com/ok-oldking/ok-end-field',
            'discord': 'https://discord.gg/vVyCatEBgA',
            'sponsor': 'https://www.paypal.com/ncp/payment/JWQBH7JZKNGCQ',
            'qq_group': 'https://qm.qq.com/q/NcWHQU6q8k',
            'share': 'Download from https://github.com/ok-oldking/ok-end-field',
            'faq': 'https://github.com/ok-oldking/ok-end-field',
            'qq_channel': 'https://pd.qq.com/s/djmm6l44y',
        },
        'zh_CN': {
            'github': 'https://github.com/ok-oldking/ok-end-field',
            'discord': 'https://discord.gg/vVyCatEBgA',
            'sponsor': 'https://afdian.com/a/ok-oldking',
            'share': 'OK-WW 夸克网盘下载：https://pan.quark.cn/s/75b55ef72a34 GitHub下载: https://github.com/ok-oldking/ok-end-field',
            'faq': 'https://cnb.cool/ok-oldking/ok-ef-update/-/blob/main/README.md',
            'qq_group': 'https://qm.qq.com/q/NcWHQU6q8k',
            'qq_channel': 'https://pd.qq.com/s/djmm6l44y',
        },
    },
    'screenshots_folder': "screenshots",  # 截图存放目录, 每次重新启动会清空目录
    'gui_title': 'ok-ef',  # 窗口名
    'template_matching': {  # 可选, 如使用OpenCV的模板匹配
        'coco_feature_json': os.path.join('assets', 'coco_detection.json'),
        # coco格式标记, 需要png图片, 在debug模式运行后, 会对进行切图仅保留被标记部分以减少图片大小
        'default_horizontal_variance': 0.002,  # 默认x偏移, 查找不传box的时候, 会根据coco坐标, match偏移box内的
        'default_vertical_variance': 0.002,  # 默认y偏移
        'default_threshold': 0.8,  # 默认threshold
        'hcenter_features': ['skill_e', 'pick_f', 'skip_dialog_confirm'],
        'vcenter_features': ['skill_e', 'skip_dialog_confirm']
    },
    'version': version,  # 版本
    'my_app': ['src.globals', 'Globals'],  # 可选. 全局单例对象, 可以存放加载的模型, 使用og.my_app调用
    'onetime_tasks': [  # 用户点击触发的任务
        ["src.tasks.DailyTask", "DailyTask"],
        ["src.tasks.TakeDeliveryTask", "TakeDeliveryTask"],
        # ["src.tasks.BuySellTask", "BuySellTask"],
        ["ok.task.DiagnosisTask", "DiagnosisTask"],
    ],
    'trigger_tasks': [  # 不断执行的触发式任务
        ["src.tasks.AutoCombatTask", "AutoCombatTask"],
        ["src.tasks.AutoSkipDialogTask", "AutoSkipDialogTask"],
        ["src.tasks.AutoPickTask", "AutoPickTask"],
        ["src.tasks.AutoLoginTask", "AutoLoginTask"]
    ]
}
