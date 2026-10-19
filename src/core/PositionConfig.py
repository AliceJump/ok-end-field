"""Application-level world-position service configuration."""

from __future__ import annotations

from qfluentwidgets import FluentIcon

from src.localization.minimap_heading_mixin import CONFIG_MIN_SCORE

POSITION_CONFIG_NAME = "Position Config"

CONFIG_WS_WAIT = "WS等待稳定秒数"
CONFIG_WS_MIN_HITS = "WS稳定最小位置数"
CONFIG_SYNC_DISTANCE = "航点校准最小距离(米)"
CONFIG_COMMIT_MIN_SHIFT = "位移提交阈值(像素)"
CONFIG_VISUAL_ANCHOR_CHAIN_CORRECTION = "关键帧视觉锚定链校正"
CONFIG_VISUAL_ANCHOR_CHAIN_WINDOW = "关键帧视觉锚定链校正窗口(拍)"
CONFIG_VISUAL_ANCHOR_CHAIN_EDGE_GAP = "关键帧视觉锚定链校正边间隔(拍)"
CONFIG_VISUAL_ANCHOR_CHAIN_MIN_RESPONSE = "关键帧视觉锚定链校正最小响应"
CONFIG_VISUAL_ANCHOR_CHAIN_ANCHOR_WEIGHT = "关键帧视觉锚定链校正锚点权重"
CONFIG_VISUAL_ANCHOR_CHAIN_MAX_CORRECTION = "关键帧视觉锚定链校正修正上限(米)"
CONFIG_VISUAL_ANCHOR_CHAIN_KEYFRAME = "关键帧视觉锚定链校正关键帧间隔(拍)"
CONFIG_VISUAL_ANCHOR_CHAIN_KEYFRAME_EDGES = "关键帧视觉锚定链校正关键帧边数"
CONFIG_VISUAL_ANCHOR_CHAIN_KEYFRAME_MIN_RESPONSE = "关键帧视觉锚定链校正关键帧最小响应"
CONFIG_VISUAL_ANCHOR_CHAIN_KEYFRAME_WEIGHT = "关键帧视觉锚定链校正关键帧锚点权重"
CONFIG_USE_EXPERIMENTAL = "使用实验技术"
CONFIG_EXPERIMENTAL_PORT = "实验技术后端端口"
CONFIG_EXPERIMENTAL_EXE = "实验技术后端程序路径"
CONFIG_EXPERIMENTAL_AUTOSTART = "实验技术自动启动后端"
CONFIG_EXPERIMENTAL_STOP_ON_EXIT = "实验技术停止定位时关闭后端"
CONFIG_EXPERIMENTAL_RELOGIN = "抓包失败时自动重新登录"
CONFIG_EXPERIMENTAL_UPDATE = "检查并更新后端"

#: Horizontal odometry shift needed before committing a visual displacement.
DEFAULT_COMMIT_MIN_SHIFT_PX = 2.0

DEFAULT_POSITION_CONFIG: dict = {
    CONFIG_WS_WAIT: 10.0,
    CONFIG_WS_MIN_HITS: 3,
    CONFIG_MIN_SCORE: 0.6,
    CONFIG_SYNC_DISTANCE: 100.0,
    CONFIG_COMMIT_MIN_SHIFT: DEFAULT_COMMIT_MIN_SHIFT_PX,
    CONFIG_VISUAL_ANCHOR_CHAIN_CORRECTION: True,
    CONFIG_VISUAL_ANCHOR_CHAIN_WINDOW: 60,
    CONFIG_VISUAL_ANCHOR_CHAIN_EDGE_GAP: 12,
    CONFIG_VISUAL_ANCHOR_CHAIN_MIN_RESPONSE: 0.25,
    CONFIG_VISUAL_ANCHOR_CHAIN_ANCHOR_WEIGHT: 50.0,
    CONFIG_VISUAL_ANCHOR_CHAIN_MAX_CORRECTION: 10.0,
    CONFIG_VISUAL_ANCHOR_CHAIN_KEYFRAME: 10,
    CONFIG_VISUAL_ANCHOR_CHAIN_KEYFRAME_EDGES: 8,
    CONFIG_VISUAL_ANCHOR_CHAIN_KEYFRAME_MIN_RESPONSE: 0.3,
    CONFIG_VISUAL_ANCHOR_CHAIN_KEYFRAME_WEIGHT: 25.0,
    CONFIG_USE_EXPERIMENTAL: False,
    CONFIG_EXPERIMENTAL_PORT: 8765,
    CONFIG_EXPERIMENTAL_EXE: "",
    CONFIG_EXPERIMENTAL_AUTOSTART: True,
    CONFIG_EXPERIMENTAL_STOP_ON_EXIT: False,
    CONFIG_EXPERIMENTAL_RELOGIN: True,
}

POSITION_CONFIG_DESCRIPTION: dict[str, str] = {
    CONFIG_WS_WAIT: "等 WS 位置流稳定（连续同一 mapId 的有效位置）的最大等待秒数",
    CONFIG_WS_MIN_HITS: "判为稳定所需连续有效位置个数",
    CONFIG_MIN_SCORE: "箭头角度检测最低置信度，低于该值朝向判为不可用",
    CONFIG_SYNC_DISTANCE: "累计移动达到该距离后，请求导航暂停并等待静止自动校准",
    CONFIG_COMMIT_MIN_SHIFT: "里程计位移提交阈值（像素）。误差是按采样次数累积的，"
    "而每样本位移 = 速度 * 采样间隔，所以慢走 / 原地挪时每米要积更多样本、"
    "漂移更大。设成 2~3 像素可让每米提交次数与速度脱钩（原地小步走几乎不提交），"
    "同时位置仍连续（未提交部分照常计入位置）。"
    "0 = 关闭，回到按时间提交的旧行为，便于 A/B 对比",
    CONFIG_VISUAL_ANCHOR_CHAIN_CORRECTION: (
        "用 WS 绝对锚点启动关键帧视觉锚定链校正，并用视觉重叠约束修正累计漂移后发布位置"
    ),
    CONFIG_VISUAL_ANCHOR_CHAIN_WINDOW: "关键帧视觉锚定链校正保留的最近切片数；越大越稳但计算越多",
    CONFIG_VISUAL_ANCHOR_CHAIN_EDGE_GAP: "每拍新切片最多和之前多少拍建立相对平移边",
    CONFIG_VISUAL_ANCHOR_CHAIN_MIN_RESPONSE: "切片对相位相关的最低响应；低于该值不建边",
    CONFIG_VISUAL_ANCHOR_CHAIN_ANCHOR_WEIGHT: "静止校准 WS 绝对锚点的权重；越大越贴近 WS",
    CONFIG_VISUAL_ANCHOR_CHAIN_MAX_CORRECTION: "单拍关键帧视觉锚定链校正量的安全上限（米）；超过则回退原始融合值",
    CONFIG_VISUAL_ANCHOR_CHAIN_KEYFRAME: "每隔多少拍建立一个关键帧；每次静止校准也会建关键帧",
    CONFIG_VISUAL_ANCHOR_CHAIN_KEYFRAME_EDGES: "每个关键帧最多和之前多少个关键帧建立直接视觉边",
    CONFIG_VISUAL_ANCHOR_CHAIN_KEYFRAME_MIN_RESPONSE: "关键帧视觉边的最低相位相关响应",
    CONFIG_VISUAL_ANCHOR_CHAIN_KEYFRAME_WEIGHT: "关键帧优化位置作为局部关键帧视觉锚定链校正软锚点的权重",
    CONFIG_USE_EXPERIMENTAL: (
        "改用外部抓包后端直接读取角色绝对坐标，绕开小地图里程计、地图 WS 与整条校准链。"
        "该技术可能违反游戏服务条款、存在封禁风险，请自行判断是否使用"
    ),
    CONFIG_EXPERIMENTAL_PORT: "外部抓包后端的本地 HTTP 端口（自动启动用它拉起后端，也用它探活）",
    CONFIG_EXPERIMENTAL_EXE: (
        "后端可执行文件的路径（也可以是放着它的目录）。留空则自动探测："
        "同级 endfield-capture-backend 仓库的 dist/ 目录、本仓库目录，以及系统 PATH"
    ),
    CONFIG_EXPERIMENTAL_AUTOSTART: (
        "后端没在跑时自动把它拉起来。**后端必须在游戏建立主连接之前就绪**，"
        "否则拿不到登录握手、解不出坐标；由本应用拉起可以保证这个顺序"
    ),
    CONFIG_EXPERIMENTAL_STOP_ON_EXIT: (
        "停止定位时一并关闭由本应用拉起的后端。默认关：后端的状态绑在当前这一局连接上，"
        "关掉再拉起等于丢了登录握手，必须重启游戏才能恢复"
    ),
    CONFIG_EXPERIMENTAL_UPDATE: (
        "立刻检查并更新抓包后端程序，不等下次启动。用在自己的网络刚恢复、或想确认当前用的是哪个版本时"
    ),
    CONFIG_EXPERIMENTAL_RELOGIN: (
        "抓包后端在跑、但当前没有可用坐标时（错过了登录握手，或还没看到游戏连接），"
        "自动退回登录界面再进入一次，逼出一条新连接让后端重新抓握手。"
        "**不会切换账号**；最多尝试 3 次，用尽即停手。关掉则只在日志里提示"
    ),
}


def _check_capture_backend_update(*_args) -> None:
    from src.localization.position_service import check_capture_backend_update_from_settings

    check_capture_backend_update_from_settings()


POSITION_CONFIG_TYPE = {
    CONFIG_USE_EXPERIMENTAL: {
        "type": "danger_switch",
        "sub_configs": {
            True: [
                CONFIG_EXPERIMENTAL_PORT,
                CONFIG_EXPERIMENTAL_EXE,
                CONFIG_EXPERIMENTAL_AUTOSTART,
                CONFIG_EXPERIMENTAL_STOP_ON_EXIT,
                CONFIG_EXPERIMENTAL_UPDATE,
                CONFIG_EXPERIMENTAL_RELOGIN,
            ]
        },
    },
    CONFIG_EXPERIMENTAL_UPDATE: {
        "type": "button",
        "text": CONFIG_EXPERIMENTAL_UPDATE,
        "icon": FluentIcon.DOWNLOAD,
        "callback": _check_capture_backend_update,
    },
}

__all__ = [
    "DEFAULT_COMMIT_MIN_SHIFT_PX",
    "DEFAULT_POSITION_CONFIG",
    "POSITION_CONFIG_DESCRIPTION",
    "POSITION_CONFIG_NAME",
    "POSITION_CONFIG_TYPE",
]
