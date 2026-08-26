# ok-ef API 参考

返回：[文档索引](../zh-CN/index.md) / [README](https://github.com/AliceJump/ok-end-field/blob/master/README.md)

本文只记录 **ok-end-field 自己定义并由任务复用的接口**。`ocr`、`wait_ocr`、`wait_feature`、`wait_until`、`back`、`sleep`、`send_key` 等框架接口来自 `ok-script`，其完整参数和返回类型应以当前安装版本为准，不在此复制。

源码权威位置：

- 基类组合：[BaseEfTask.py](../../src/core/BaseEfTask.py)
- 核心能力：[src/core/base_mixin/](../../src/core/base_mixin/)
- 业务能力：[src/tasks/mixin/](../../src/tasks/mixin/)
- 配置：[global_config_store.py](../../src/core/global_config_store.py)、[BattleConfig.py](../../src/core/BattleConfig.py)、[KeyConfig.py](../../src/interaction/KeyConfig.py)

## 1. BaseEfTask 与 MRO

```python
from src.core.BaseEfTask import BaseEfTask
```

`BaseEfTask` 的实际基类顺序为：

```text
BaseEfTask
├── WindowArrowDrawingMixin       src/core/base_mixin/window_arrow_drawing_mixin.py
├── AccountOverrideMixin         src/core/base_mixin/account_override_mixin.py
├── GameFlowMixin                src/core/base_mixin/game_flow_mixin.py
├── RuntimeMixin                 src/core/base_mixin/runtime_mixin.py
├── ok.BaseTask
└── ProcessManager               src/core/base_mixin/process_manager.py
```

任务层 Mixin 通常最终继承 `BaseEfTask`：

```text
BattleMixin ────────────────> BaseEfTask
MapMixin ───────────────────> BaseEfTask
NavigationMixin ────────────> BaseEfTask
├── LiaisonMixin
└── ZipLineMixin
LoginMixin ─────────────────> BaseEfTask
└── AccountMixin
```

所有参与多重继承的 `__init__` 必须调用 `super().__init__(*args, **kwargs)`。任务配置应使用 `self.default_config.update(...)` 等增量方式，避免覆盖前序 Mixin 已注册的数据。

初始化后常用属性：

| 属性 | 含义 |
|------|------|
| `self.box` | `ScreenPosition(self)`，提供常用屏幕区域 |
| `self.key_config` | 全局 `Game Hotkey Config` 的持久化 `Config` |
| `self.key_manager` | `KeyConfigManager(self.key_config)` |
| `self.lang` | 按运行时 locale 加载 `assets/lang/<module>.json` 的访问器 |
| `self.current_user` / `self.current_account_id` | 当前账号上下文 |
| `self.once_sleep_time` | 全局 `Ensure Main Once Action Sleep.SingleActionWithDelay` |

### 1.1 坐标

```text
def box_of_screen(
    self, x=0, y=0, to_x=1.0, to_y=1.0,
    width=0.0, height=0.0, name=None,
    hcenter=False, vcenter=False, confidence=1.0,
)

def box_of_screen_scaled(
    self, original_screen_width, original_screen_height,
    x_original, y_original, to_x=0, to_y=0,
    width_original=0, height_original=0, name=None,
    hcenter=False, vcenter=False, confidence=1.0,
)
```

前者使用当前屏幕的比例坐标；后者将参考分辨率坐标缩放到当前窗口。`BaseEfTask` 会将传入的比例值统一保留三位小数后交给框架。

```python
left_panel = self.box_of_screen(0.0, 0.1, 0.35, 0.9)
button = self.box_of_screen_scaled(1920, 1080, 1600, 900, 1800, 1020)
```

`ScreenPosition` 当前提供：`top_left`、`top_right`、`bottom_left`、`bottom_right`、`bottom_right_quarter`、`left`、`right`、`top`、`bottom`、`center`，以及 `nav_b/nav_c/nav_esc/nav_panel`、`interact_pick_f`、`combat_skill_1..4`、`combat_ult_1..4`、`combat_default_link_skill`、`combat_skill_bar`、`combat_ult_bar`。

### 1.2 特征匹配

定义于 `src/core/base_mixin/runtime_mixin.py`。

```text
def find_feature(
    self, feature_name=None, horizontal_variance=0, vertical_variance=0,
    threshold=0, use_gray_scale=False,
    x=-1, y=-1, to_x=-1, to_y=-1, width=-1, height=-1,
    box=None, canny_lower=0, canny_higher=0,
    frame_processor=None, template=None,
    match_method=cv2.TM_CCOEFF_NORMED, screenshot=False,
    mask_function=None, frame=None, limit=0, target_height=0,
    feature=None,
)

def find_one(
    self, feature_name=None, horizontal_variance=0, vertical_variance=0,
    threshold=0, use_gray_scale=False, box=None,
    canny_lower=0, canny_higher=0, frame_processor=None,
    template=None, mask_function=None, frame=None,
    match_method=cv2.TM_CCOEFF_NORMED, screenshot=False,
    limit=1, target_height=0, feature=None,
)
```

- `feature=` 是 `feature_name=` 的别名。
- `find_feature` 接受单个特征或 `list/tuple`；每个名称都会先经过分辨率适配。
- 返回语义沿用框架：`find_feature` 返回结果列表，`find_one` 返回首个结果或空值。
- 未显式传 `box` 时，框架可使用 `assets/coco_annotations.json` 的标注区域及 `src/config.py` 中的默认 variance/threshold。

```python
from src.data.FeatureList import FeatureList as fL

results = self.find_feature([fL.monthly_card, fL.monthly_card2])
confirm = self.find_one(fL.skip_dialog_confirm, threshold=0.8)
if confirm:
    self.click(confirm)
```

```text
def get_feature_by_resolution(self, base_name: str)
```

根据 `self.width` 和 `FeatureList` 中实际存在的枚举值选择名称：

| 窗口宽度 | 尝试顺序 |
|----------|----------|
| `>= 3800` | `_4k`、`_2k`、无后缀 |
| `>= 2500` | `_2k`、`_4k`、无后缀 |
| 其它 | 无后缀、`_2k`、`_4k` |

全部不存在时抛出 `AttributeError`。传入值必须能与字符串拼接，通常使用 `FeatureList`（其成员是字符串枚举）或字符串。

```text
def wait_click_feature(
    self, feature, horizontal_variance=0, vertical_variance=0,
    threshold=0, relative_x=0.5, relative_y=0.5,
    time_out=0, pre_action=None, post_action=None, box=None,
    raise_if_not_found=True, use_gray_scale=False,
    canny_lower=0, canny_higher=0, click_after_delay=0,
    settle_time=-1, after_sleep=0, target_height=0,
    alt: bool = False,
)
```

命中后点击框内相对位置，成功返回 `True`，未命中且不抛异常时返回 `False`。`alt=True` 走 `click_with_alt`。

### 1.3 OCR 与登录截图

普通 `ocr`、`wait_ocr` 来自 `ok-script`。项目覆盖了点击等待和登录截图入口：

```text
def wait_click_ocr(
    self, x=0, y=0, to_x=1, to_y=1, width=0, height=0,
    box=None, name=None, match=None, threshold=0, frame=None,
    target_height=0, time_out=0, raise_if_not_found=False,
    recheck_time=0, after_sleep=0, post_action=None, log=False,
    screenshot=False, settle_time=-1, lib="default", alt=False,
)
```

它调用 `wait_ocr`，命中后点击并返回 OCR 结果；否则记录日志并返回 `None`。注意：`recheck_time > 0` 时会等待 `recheck_time` 秒后重新 OCR，以新坐标再执行点击。

```text
def login_screenshot(self, need_active=True)

def login_ocr(
    self, x=0, y=0, to_x=1, to_y=1, match=None,
    width=0, height=0, box=None, name=None, threshold=0,
    target_height=0, use_grayscale=False, log=False,
    frame_processor=None, lib="default", need_active=True,
)
```

这些入口使用 Win32 屏幕捕获绕过登录界面无法由常规 WGC 帧可靠捕获的问题。

### 1.4 点击、按键和移动

```text
def click(
    self, x=-1, y=-1, move_back=False, name=None, interval=-1,
    move=True, down_time=0.01, after_sleep=0,
    key="left", hcenter=False, vcenter=False,
)
```

`click` 在调用框架点击前先做危险状态检查；检测到危险图标会终止游戏并抛异常。可将 `Box` 作为第一个位置参数传入：

```python
self.click(confirm_box, after_sleep=0.5)
self.click(0.5, 0.5)
```

```text
def click_with_alt(
    self, x=-1, y=-1, move_back=False, name=None, interval=-1,
    move=True, down_time=0.01, after_sleep=0, key="left",
)

def scroll(self, x: int, y: int, count: int) -> None
def scroll_relative(self, x: float, y: float, count: int) -> None
```

`scroll` 使用窗口内像素位置；`scroll_relative` 使用 `0..1` 比例位置。正数向上、负数向下。需要控制游戏视角滚轮的代码当前另有直接调用 `pyautogui.scroll` 的场景，不能假定 UI 滚动封装适合所有视角操作。

```text
def press_key(self, key: str, down_time=0.02, after_sleep=0, interval=-1)
def press_industry_key(self, key: str, down_time=0.02, after_sleep=0, interval=-1)
def press_combat_key(self, key: str, down_time=0.02, after_sleep=0, interval=-1)
def press_esc(self)
def move_keys(self, keys, duration, need_back=False)
```

前三个方法分别以 `common`、`industry`、`combat` 类型调用 `KeyConfigManager.resolve_key`，参数传默认按键值，例如 `self.press_key("m")`。`move_keys` 用于 `w/a/s/d` 等持续移动；当前实现不使用 `need_back` 参数。

`press_esc()` 仅用于过剧情（对话时）触发 ESC，底层使用 `BaseEfTask` 统一初始化的键盘控制器。其他返回主界面、关闭页面等流程不得使用该函数。

```text
def dodge_forward(self, pre_hold=0.004, dodge_down_time=0.003, after_sleep=0.005)

def move_to_target_once(
    self, ocr_obj, max_step=100, min_step=20,
    slow_radius=200, deadzone=4,
)
```

步长、减速半径和死区会按当前分辨率缩放。

### 1.5 场景与 UI

```text
def is_main(self, esc=False, need_active=True) -> bool
def ensure_main(self, esc=True, time_out=90, after_sleep=2, need_active=True)
def in_world(self)
def in_combat_world(self)
```

- `in_world` 通过 `esc` 特征判断处于大世界，并设置 `_logged_in=True`。
- `in_combat_world` 通过 `top_left_tab` 特征判断战斗场景；它不是“非战斗大世界”判断。
- `is_main` 会依次尝试大世界检测、登录奖励/月卡弹窗处理、已知确认弹窗和 OCR 规则；`esc=True` 时最后按返回。
- `ensure_main` 反复调用 `is_main`，默认超时为 90 秒，失败抛异常。

```text
def ensure_map(self, addtional_feature=None, time_out=30)
```

反复发送配置化地图键 `m`，通过 `in_map`、`transaction_icon`、`main_centre_icon` 或附加特征确认地图。参数名源码中保留拼写 `addtional_feature`。

```text
def wait_ui_stable(
    self, method="phash", threshold=5, stable_time=0.5,
    max_wait=5, refresh_interval=1, box=None,
)
```

支持 `phash`、`dhash`、`pixel`、`ssim`。`box` 可为 `Box` 或 `(x, y, width, height)`。对于 `ssim`，`threshold` 是相似度，调用方通常应传如 `0.98` 的浮点值，而不是默认整数 `5`。

```text
def safe_back(self, match=None, feature=None, box=None,
              time_out=30, once_time_out=2) -> bool
```

至少提供 `match` 或 `feature`；目标未出现时持续返回，成功返回 `True`，总超时返回 `False`。

### 1.6 YOLO

```text
def yolo_detect(
    self, name: str | list[str], frame=None, box=None, conf=0.7,
    detections=None, model_key=None,
) -> list[Box]

def list_yolo_targets(self, model_key: str | None = None) -> list[str]
def set_yolo_model(self, model_key: str)
def release_yolo_detector(self)
```

模型注册以 [src/yolo/models.py](../../src/yolo/models.py) 为准。未传 `model_key` 时，根据第一个目标名称路由模型；传 `detections` 可跳过推理用于测试。ROI 检测结果会映射回全屏坐标，按置信度降序返回。空 `name` 抛 `ValueError`。开启框架 `use_overlay` 后，原始结果画黄色框、筛选结果画红色框。

## 2. 配置 API

### 2.1 任务配置

任务通过框架属性增量注册配置：

```python
self.default_config.update({"启用功能": True})
self.config_description.update({"启用功能": "控制是否执行该步骤。"})
self.config_type["模式"] = {
    "type": "drop_down",
    "options": ["快速", "稳定"],
}
self.default_config_group.update({"常用": ["启用功能", "模式"]})
```

运行时使用 `self.config.get(key, default)`。`BaseEfTask.load_config()` 会按完整 MRO 收集各类的 `config_key_migrations`，先迁移 `configs/<TaskClass>.json` 的旧键，再调用框架加载。

```text
def register_config_groups(self, groups: dict, dropdown_name="配置选择")
```

该方法创建带 `sub_configs` 的下拉配置，并为分组内尚无默认值的键补 `None`。它会直接使用 `self.config_description.update(...)`，调用前应确保任务已按框架惯例初始化配置字典。

### 2.2 全局配置

```python
from src.core.global_config_store import (
    KEY_CONFIG_NAME,
    get_global_config,
)

hotkeys = get_global_config(KEY_CONFIG_NAME)
map_key = hotkeys.get("Map Key", "m")
```

当前全局配置项：

| 常量/名称 | 内容 |
|-----------|------|
| `Game Hotkey Config` | 通用、工业、战斗键位 |
| `Battle Config` | `DEFAULT_BATTLE_CONFIG` 战斗参数 |
| `Ensure Main Once Action Sleep` | `SingleActionWithDelay` |
| `Zip Line Config` | 送货/淤积点滑索路线与滚动设置 |

`get_global_config(name)` 返回持久化 `ok.util.config.Config`。未知名称只有在已加载配置中能找到对应键时才回退返回该配置，否则抛 `RuntimeError`。全局配置页由 `get_all_visible_configs()` 和 `GlobalConfigTab` 构建。

### 2.3 按键配置

```python
class KeyConfigManager:
    def __init__(self, key_config: dict = None): ...
    def update_config(self, key_config: dict): ...
    def resolve_key(self, key: str, key_type: str = "common") -> str: ...
```

不存在 `resolve_common_key`、`resolve_industry_key` 或 `resolve_combat_key`。`resolve_key` 在指定默认表中按默认值反查配置名称，再读取用户值；找不到时原样返回。

当前默认表比旧文档多出以下键：

- 通用：`Handbook Key=f8`、`Recruitment Key=f9`
- 工业：`Area Build Key=y`、`Blueprint Key=f1`、`Product Icon Toggle Key=f4`

所有可改键操作应走 `press_key`/`press_industry_key`/`press_combat_key`。固定技能数字、方向键和系统修饰键等代码可按其明确语义使用底层发送接口。

### 2.4 战斗配置与账号覆盖

```text
def get_battle_config(self, key: str, default=None)
```

`BattleMixin` 的读取顺序：

1. `BattleConfigManager` 从全局 `Battle Config` 取值。
2. 任务配置 `使用独立配置` 为关闭（False）时直接返回全局值。此时账号任务覆盖不会生效。
3. 为开启（True）时读取当前任务配置，以全局值兜底。
4. `AccountOverrideMixin` 绑定后的 `self.config.get` 可再按当前稳定账号 ID 应用账号任务覆盖；账号覆盖仅在 `使用独立配置=True` 时为最高优先级。

账号上下文通过以下接口设置：

```text
def set_current_account(self, username, account_id)

def iter_multi_account_context(
    self, repeat_times=1, empty_accounts_message=None,
    account_log_suffix="", allow_multi_account=True,
)
```

`iter_multi_account_context` 在多账户模式下解析账号列表、绑定账号覆盖并调用 `login_flow(username)`；单账号模式清空账号上下文。账号列表每行只需要账号，旧 `账号, 密码` 格式仅取逗号前账号，密码被忽略。

## 3. 业务 Mixin

### 3.1 BattleMixin

```python
from src.tasks.mixin.battle_mixin import BattleMixin
```

```text
def in_combat(self, required_yellow=0)
def in_team(self)
def is_combat_ended(self)
def wait_in_combat(self, time_out=3, click=False)
def get_skill_bar_count(self)
def ocr_lv(self)
def use_ult(self, ult_sequence: str = None)
def use_link_skill(self)
def approach_enemy(self)
def auto_battle(self, no_battle: bool = False)
```

关键语义：

- `in_team` 从首个框开始匹配 `skill_1`，随后再命中一个连续技能即确认队伍状态（至少 2 个特征；`skill_1` 位于最后一个框时为单人队伍，1 个即可）。
- `in_combat` 要求技能条数量达到阈值、处于队伍且没有等级 UI。
- `is_combat_ended` 要求内部退出条件连续命中两次；该条件是“出现等级 UI 或不在队伍”。
- `use_ult(None)` 按 `1..4` 寻找可用终极技；传值时只尝试该角色。返回是否释放成功。
- `use_link_skill` 仅在识别到连携技特征后发送配置化战斗键 `e`。
- `auto_battle` 每轮委托 `AutoCombatLogic.run`，全局保护超时 420 秒；`no_battle=True` 传给战斗逻辑，使其等待而不主动战斗。成功/失败返回布尔值。
- 战斗结束并非通过 YOLO 单一判断；当前循环还结合战斗时间和 `battle_space_left`/`b` 特征。

### 3.2 MapMixin

```text
from src.tasks.mixin.map_mixin import MapMixin

def task_to_transfer_point(self, need_location_list=None)
def clear_icon_in_map(self, need_reserve_icon_name=None, ocr=False)
def to_near_transfer_point(self, need_track, need_location_list=None, need_reserve_icon_name=None)
```

`task_to_transfer_point` 从任务界面定位地图，地图稳定后再调用 `to_near_transfer_point`。`to_near_transfer_point` 传送至附近传送点。

### 3.3 NavigationMixin

```text
from src.tasks.mixin.navigation_mixin import NavigationMixin

def navigate_until_target(
    self, target, nav=None,
    target_is_ocr=True, nav_is_ocr=False,
    time_out=60, pre_loop_callback=None,
    found_special_callback=None,
    target_is_yolo=False, nav_is_yolo=False,
    box=None, target_vertical_variance=0.0,
    need_v=False, max_run_time=-1,
    allow_rotate_search=True,
)
```

持续按 `W` 前进，目标和导航标识分别可用 OCR、YOLO 或 Feature。`nav=None` 表示纯直线搜索；`found_special_callback` 返回非 `None` 时该值直接作为函数结果。目标需持续稳定 2 秒（OCR）或 1 秒（其它方式）才返回 `True`。超时返回 `False`。`max_run_time` 只限制 `ctrl` 切换的奔跑累计时间，不限制按住 `W`：`-1` 不限制，`0` 全程步行，正数达到上限后切步行。`allow_rotate_search` 默认 `True`，导航连续丢失时旋转视角整圈搜索；送礼等不应转动视角的场景传 `False` 跳过旋转直接按中键并后退搜索。

```text
def start_tracking_and_align_target(
    self, target_feature_in_map, target_feature_out_map,
)
```

在地图点击目标并启动追踪，关闭地图，等待地图外图标并做水平对齐；返回布尔值。

```text
def align_ocr_or_find_target_to_center(
    self, ocr_match_or_feature_name_list,
    only_x=False, only_y=False, box=None, threshold=0.8,
    max_time=50, ocr=True, use_yolo=False, back_prev=False,
    raise_if_fail=True, is_num=False, need_scroll=False,
    max_step=120, min_step=20, slow_radius=350, deadzone=8,
    once_time=0.05, tolerance=50,
    ocr_frame_processor_list=None, allow_random_move=True,
)
```

在 OCR、Feature 或 YOLO 模式下将目标对齐屏幕中心。`max_time` 是算法尝试尺度，内部最多循环 `max_time * 2`，不是秒数。失败时默认抛异常；`raise_if_fail=False` 返回 `False`。`back_prev` 参数当前未被方法体使用。

### 3.4 LiaisonMixin

```text
from src.tasks.mixin.liaison_mixin import LiaisonMixin

def transfer_to_home_point(self, box=None, should_check_out_boat=False)
def navigate_to_main_hall(self) -> bool
def navigate_to_operator_liaison_station(self)
def perform_operator_liaison(self)
def collect_gifts(self, time_out=30)
def give_gifts(self, time_out=30, gift_entry_clicked=False)
def collect_and_give_gifts(self)
```

- `transfer_to_home_point` 默认在地图左半屏找传送点；调用方传 `box=self.box.right` 才是右侧点。`should_check_out_boat=True` 的实际语义是：若点击不到帝江号入口，则认为已经在帝江号区域并直接返回主界面，不会执行“退出好友船”。
- `navigate_to_main_hall` 最多向前移动两次并 OCR 区域名；即使未识别到也记录日志后返回 `True`，是宽松检查。
- `navigate_to_operator_liaison_station` 使用地图追踪和 `navigate_until_target`；途中发现聊天图标可返回 `LiaisonResult.FIND_CHAT_ICON`，因此返回值不只布尔值。
- `perform_operator_liaison` 选择配置的优先对象，找不到时回退任一可联络对象，完成联络界面、目标对齐和聊天交互；它不包含收礼/送礼步骤。
- `collect_and_give_gifts` 处理当前交流界面中的收礼或送礼入口；收礼成功后继续尝试送礼。

### 3.5 ZipLineMixin

```text
from src.tasks.mixin.zip_line_mixin import ZipLineMixin

def on_zip_line_start(
    self, delivery_to, need_scroll=None, target=None, need_v=True,
)

def zip_line_list_go(
    self, zip_line_list, need_scroll=None, target=None, need_v=False,
)

def ensure_click_on_zip_line(self, max_attempts=5)
```

`on_zip_line_start` 先等待滑索 UI，随后从 `self.config[delivery_to]` 读取整数序列并调用 `zip_line_list_go`；它不是接收距离列表的入口。每段距离通过金色/白色 HSV 预处理 OCR 并居中，点击进入滑索后持续发送固定键 `e`，直到滑索 UI 再次出现。单段等待上限 240 秒；初始滑索 UI 等待上限 60 秒，超时抛异常。

`target` 为 `(目标, "ocr" | "yolo" | 其它)`，用于最后一段后的目标对齐；其它类型按 Feature 处理。流程末尾确保回到主界面。

### 3.6 LoginMixin

```text
from src.tasks.mixin.login_mixin import LoginMixin

def login_flow(self, username: str, password: str | None = None)
def click_text(
    self, match: str, box=None,
    need_wait_disappear=True, success_match=None,
)
```

`login_flow` 不是用户名密码输入流程。它用于切换到登录界面的“最近账号”：

1. 判断当前是否已登录，必要时回到主界面并退出当前账号。
2. 等待登录界面的 `logout` 特征并确认登出。
3. 点击“最近”，再用 `username` 后四位 OCR 点击最近账号。
4. 点击“登录”，通过重新出现 `logout` 特征确认成功。

`password` 仅为旧调用兼容参数，当前不存储、不输入、也不参与账号判定。失败路径可能返回 `False` 或抛 `RuntimeError`；成功无显式返回值。后四位匹配要求最近账号列表中后四位唯一。

## 4. 语言资源 API

```python
from src.data.lang import (
    ACTIVE_LOCALES_CONFIG,
    SUPPORTED_LOCALES,
    get_lang_accessor,
    get_lang_module_value,
)
```

每个模块对应单个 `assets/lang/<module>.json`：

```json
{
  "k_confirm": {
    "zh_CN": {"string": "确认"},
    "zh_TW": {"string": "確認"}
  },
  "k_amount": {
    "zh_CN": {"pattern": "^\\d+$"},
    "zh_TW": {"pattern": "^\\d+$"}
  }
}
```

访问 `self.lang.<module>.<key>` 时，`string` 返回字符串、`pattern` 返回编译后的正则、`terms` 返回列表。直接属性解析的优先级是 `string`、`pattern`、`terms`；`LangNode.as_matcher()`/`build_matcher()` 的优先级是 `pattern`、`string`、`terms`。正常资源节点应只设置其中一种，避免依赖该差异。

当前活动 OCR locale 只有 `zh_CN` 和 `zh_TW`；其它四种 locale 虽可存在于 JSON 和 gettext 目录，但在 `ACTIVE_LOCALES_CONFIG` 中关闭，不会进入 `SUPPORTED_LOCALES`。完整维护流程见 [i18n 与 OCR 配置流程](i18n_OCR配置流程.md)。
