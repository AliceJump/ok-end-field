# ok-ef API 参考文档

> 面向开发者的详细 API 参考，涵盖核心基类、Mixin 能力层、工具类及数据层的所有公共接口。

---

## 目录

1. [BaseEfTask](#1-baseeftask)
   - [截图与特征匹配](#11-截图与特征匹配)
   - [OCR 识别](#12-ocr-识别)
   - [点击与交互](#13-点击与交互)
   - [按键操作](#14-按键操作)
   - [移动与闪避](#15-移动与闪避)
   - [场景判断](#16-场景判断)
   - [导航与传送](#17-导航与传送)
   - [UI 等待](#18-ui-等待)
   - [日志与状态](#19-日志与状态)
   - [图像处理](#110-图像处理)
   - [YOLO 检测](#111-yolo-检测)
   - [登录界面专用](#112-登录界面专用)
   - [其它工具](#113-其它工具)
2. [ScreenPosition](#2-screenposition)
3. [KeyConfigManager](#3-keyconfigmanager)
4. [BattleMixin](#4-battlemixin)
5. [MapMixin](#5-mapmixin)
6. [NavigationMixin](#6-navigationmixin)
7. [LiaisonMixin](#7-liaisonmixin)
8. [ZipLineMixin](#8-ziplinemixin)
9. [LoginMixin](#9-loginmixin)
10. [EssenceRecognizer（纯算法层）](#10-essencerecognizer纯算法层)
11. [数据工具函数](#11-数据工具函数)

---

## 1. BaseEfTask

所有任务与 Mixin 的公共基类，封装了截图、识别、交互等核心能力。

```python
from src.tasks.BaseEfTask import BaseEfTask
```

### 1.1 截图与特征匹配

#### `find_feature`

```python
def find_feature(
    self,
    feature_name,
    *,
    box=None,
    threshold=0,
    use_gray_scale=False,
    horizontal_variance=0,
    vertical_variance=0,
    x=-1, y=-1, to_x=-1, to_y=-1,
    width=-1, height=-1,
    canny_lower=0, canny_higher=0,
    frame_processor=None,
    template=None,
    match_method=cv2.TM_CCOEFF_NORMED,
    screenshot=False,
    mask_function=None,
    frame=None,
)
```

在当前帧中进行模板匹配，返回匹配到的 `Box` 列表（未匹配时返回空列表）。
`feature_name` 可传入 `FeatureList` 枚举成员或字符串（图片文件名，不含 `.png`）。
会根据当前分辨率自动选择 `_2k` / `_4k` 后缀图片。

| 参数 | 类型 | 说明 |
|------|------|------|
| `feature_name` | `FeatureList` \| `str` | 特征名（枚举成员或字符串） |
| `box` | `Box \| None` | 限制搜索区域；`None` 表示全屏 |
| `threshold` | `float` | 匹配阈值，默认 `0`（使用框架默认值） |
| `use_gray_scale` | `bool` | 是否转灰度匹配 |
| `horizontal_variance` | `int` | 水平搜索扩展像素 |
| `vertical_variance` | `int` | 垂直搜索扩展像素 |
| `frame` | `ndarray \| None` | 传入指定帧，`None` 则自动截取最新帧 |

```python
boxes = self.find_feature(fL.main_menu_icon, box=self.box.top_right)
if boxes:
    self.click(boxes[0])
```

---

#### `find_one`

```python
def find_one(
    self,
    feature_name,
    **kwargs,
) -> Box | None
```

`find_feature` 的简化版：返回第一个匹配的 `Box`，未匹配时返回 `None`。参数同 `find_feature`。

```python
box = self.find_one(fL.confirm_btn)
if box:
    self.click(box)
```

---

#### `get_feature_by_resolution`

```python
def get_feature_by_resolution(
    self,
    base_name: str,
)
```

根据当前窗口宽度（1080p / 2K / 4K）返回最合适的特征名字符串。

- `width >= 3800`：优先 `_4k`，其次 `_2k`，最后无后缀
- `2500 ≤ width < 3800`：优先 `_2k`，其次 `_4k`，最后无后缀
- `width < 2500`：无后缀、`_2k`、`_4k` 顺序查找

若找不到任何可用资源，抛出 `AttributeError`。

---

### 1.2 OCR 识别

> 以下方法继承自 `ok-script` 框架的 `BaseTask`，`BaseEfTask` 直接转发，行为一致。

#### `ocr`

```python
def ocr(
    self,
    box=None,
    match=None,
    name=None,
    threshold=0,
    target_height=0,
    use_grayscale=False,
    log=False,
    frame_processor=None,
    lib='default',
) -> list[dict]
```

对指定区域进行 OCR 识别，返回结果列表，每项包含 `text`、`box` 等字段。

| 参数 | 类型 | 说明 |
|------|------|------|
| `box` | `Box \| None` | 识别区域；`None` 为全屏 |
| `match` | `str \| re.Pattern \| None` | 文本过滤条件（字符串子串或正则） |
| `name` | `str \| None` | 日志标签 |
| `threshold` | `float` | 置信度阈值 |

```python
result = self.ocr(box=self.box.bottom, match=re.compile(r"\d+"))
```

---

#### `wait_ocr`

```python
def wait_ocr(
    self,
    match,
    box=None,
    time_out=5,
    **kwargs,
) -> list[dict] | None
```

阻塞等待直到 OCR 匹配成功或超时。成功返回结果列表，超时返回 `None`。

```python
result = self.wait_ocr(match="确认", box=self.box.center, time_out=10)
```

---

#### `wait_click_ocr`

```python
def wait_click_ocr(
    self,
    match,
    box=None,
    time_out=5,
    after_sleep=0.3,
    **kwargs,
) -> bool
```

等待 OCR 匹配成功后点击匹配区域。成功返回 `True`，超时返回 `False`。

```python
self.wait_click_ocr(match="开始", box=self.box.bottom, time_out=8)
```

---

#### `run_ocr_rules`

```python
def run_ocr_rules(
    self,
    rules: list[list],
) -> bool
```

顺序执行一批「OCR 匹配 → 点击」规则，适合处理多步流程。`rules` 为规则列表，每项 `[match, box, ...]`，内部自动等待并点击。返回全部规则均成功时为 `True`。

---

#### `login_ocr`

```python
def login_ocr(
    self,
    x=0,
    y=0,
    to_x=1,
    to_y=1,
    match=None,
    width=0,
    height=0,
    box=None,
    name=None,
    threshold=0,
    target_height=0,
    use_grayscale=False,
    log=False,
    frame_processor=None,
    lib='default',
)
```

与 `ocr` 参数相同，但针对**登录界面截图**（`login_screenshot()`）进行识别，绕过 WGC 限制。

---

### 1.3 点击与交互

> 基础 `click` / `back` 等方法继承自 `ok-script` 框架。

#### `click`

```python
def click(
    self,
    x=-1, y=-1,
    *,
    box=None,
    name=None,
    interval=-1,
    move=True,
    down_time=0.01,
    after_sleep=0,
    key='left',
)
```

点击指定坐标或 `Box` 中心。`x`/`y` 为 `0~1` 时视为比例坐标，否则为像素坐标；也可直接传 `Box` 对象。

```python
self.click(0.5, 0.5)          # 屏幕中心（比例）
self.click(box=confirm_box)   # 点击 Box 中心
```

---

#### `click_with_alt`

```python
def click_with_alt(
    self,
    x=-1, y=-1,
    move_back=False,
    name=None,
    interval=-1,
    move=True,
    down_time=0.01,
    after_sleep=0,
    key='left',
)
```

按住 `Alt` 键再点击，常用于游戏内物品转移等需要组合键的操作。参数同 `click`。

---

#### `scroll`

```python
def scroll(
    self,
    x: int,
    y: int,
    count: int,
) -> None
```

在像素坐标 `(x, y)` 处滚动鼠标滚轮。`count` 正数向上，负数向下(仅UI界面有效,游戏视角放大无效**需要使用pyautogui.scroll**)

---

#### `scroll_relative`

```python
def scroll_relative(
    self,
    x: float,
    y: float,
    count: int,
) -> None
```

在比例坐标 `(x, y)` 处滚动鼠标滚轮，坐标范围 `0~1`。

---

#### `find_confirm`

```python
def find_confirm(self) -> Box | None
```

在屏幕中查找「确认」按钮，返回 `Box` 或 `None`。

---

#### `click_confirm`

```python
def click_confirm(
    self,
    after_sleep=0.5,
    time_out=5,
) -> bool
```

等待「确认」按钮出现并点击。

---

#### `find_f`

```python
def find_f(self) -> Box | None
```

查找 `F` 键交互提示图标，返回 `Box` 或 `None`。用于判断当前场景是否可与 NPC / 物品互动。

---

#### `find_reward_ok`

```python
def find_reward_ok(self) -> Box | None
```

查找奖励领取确认按钮，返回 `Box` 或 `None`。

---

#### `active_and_send_mouse_delta`

```python
def active_and_send_mouse_delta(
    self,
    dx=1, dy=1,
    activate=True,
    only_activate=False,
    delay=0.02,
    steps=3,
)
```

激活游戏窗口并发送鼠标增量移动，用于旋转视角。

| 参数 | 类型 | 说明 |
|------|------|------|
| `dx` | `int` | 水平像素增量 |
| `dy` | `int` | 垂直像素增量 |
| `activate` | `bool` | 是否先激活窗口 |
| `only_activate` | `bool` | 仅激活，不发送鼠标事件 |
| `delay` | `float` | 每步延迟（秒） |
| `steps` | `int` | 分多步发送 |

---

### 1.4 按键操作

#### `press_key`

```python
def press_key(
    self,
    key: str,
    **kwargs,
)
```

发送通用按键，`key` 为热键名称（如 `'f'`、`'esc'`、`'space'`）。`kwargs` 透传给底层框架（如 `after_sleep`）。

---

#### `press_game_key`

```python
def press_game_key(
    self,
    key: str,
    key_type: str = 'common',
    down_time: float = 0.02,
    after_sleep: float = 0,
    interval: int = -1,
)
```

发送游戏通用热键，`key` 会经过 `KeyConfigManager.resolve_common_key()` 转换，自动适配用户自定义按键。

```python
self.press_game_key('m')   # 打开地图（默认 M 键，支持用户自定义）
self.press_game_key('f')   # 交互键（默认 F）
```

---

#### `press_industry_key`

```python
def press_industry_key(
    self,
    key: str,
    **kwargs,
)
```

发送集成工业专用热键，经 `resolve_industry_key()` 转换。

---

#### `press_combat_key`

```python
def press_combat_key(
    self,
    key: str,
    **kwargs,
)
```

发送战斗专用热键，经 `resolve_combat_key()` 转换。

---

#### `move_keys`

```python
def move_keys(
    self,
    keys,
    duration,
    need_back=False,
)
```

同时按住多个方向键并持续 `duration` 秒后松开。`need_back=True` 时会先执行一次返回之前窗口的操作。

```python
self.move_keys(['w', 'd'], duration=1.5)   # 向右前方移动 1.5 秒
```

---

### 1.5 移动与闪避

#### `dodge_forward`

```python
def dodge_forward(
    self,
    pre_hold: float = 0.004,
    dodge_down_time: float = 0.003,
    after_sleep: float = 0.005,
)
```

向前闪避（先短暂按住 `W` 键，再按闪避键）。

#### `dodge_backward`

```python
def dodge_backward(
    self,
    pre_hold: float = 0.004,
    dodge_down_time: float = 0.003,
    after_sleep: float = 0.005,
)
```

向后闪避（先短暂按住 `S` 键，再按闪避键）。

---

#### `move_to_target_once`

```python
def move_to_target_once(
    self,
    ocr_obj,
    max_step=100,
    min_step=20,
    slow_radius=200,
)
```

根据 OCR 对象所在位置，单步移动鼠标使目标朝向屏幕中心。`max_step` / `min_step` 控制步长，`slow_radius` 为减速半径（像素）。

---

### 1.6 场景判断

#### `is_main`

```python
def is_main(
    self,
    esc=False,
    need_active=True,
) -> bool
```

判断当前是否处于游戏主界面（大世界）。`esc=True` 时会先尝试按 ESC 关闭弹窗。

---

#### `ensure_main`

```python
def ensure_main(
    self,
    esc=True,
    time_out=60,
    after_sleep=2,
    need_active=True,
)
```

阻塞等待直到进入主界面，超时则抛出异常。

---

#### `in_world`

```python
def in_world(self) -> bool
```

判断当前是否在大世界（战斗界面除外）。与 `in_combat_world()` 区别：后者还排除了副本/战斗状态。

---

#### `in_combat_world`

```python
def in_combat_world(self) -> bool
```

判断是否处于大世界战斗状态。

---

#### `in_bg`

```python
def in_bg(self) -> bool
```

判断游戏是否在后台运行（窗口最小化或非激活）。

---

#### `in_friend_boat`

```python
def in_friend_boat(self) -> bool
```

判断当前是否处于帝江号（好友船）场景。

---

#### `ensure_in_friend_boat`

```python
def ensure_in_friend_boat(self)
```

等待确保进入好友帝江号场景。

---

#### `skip_dialog`

```python
def skip_dialog(self)
```

检测并跳过当前对话框（点击跳过按钮并确认）。

---

### 1.7 导航与传送

#### `ensure_map`

```python
def ensure_map(
    self,
    addtional_match=None,
    time_out=30,
)
```

等待并确保地图界面已打开。`addtional_match` 为额外的 OCR 匹配条件。

---

#### `transfer_to_home_point`

```python
def transfer_to_home_point(
    self,
    box=None,
    should_check_out_boat=False,
)
```

传送到帝江号右侧传送点。`should_check_out_boat=True` 时会先检查是否在帝江号,在则退出。（由 `LiaisonMixin` 实现，参见第 7 节）

---

#### `to_model_area`

```python
def to_model_area(
    self,
    area,
    model,
)
```

导航到指定地区的指定模块区域（如「武陵」→「仓库」）。`area` 为地区名，`model` 为模块名。

---

#### `enter_home_room_list`

```python
def enter_home_room_list(
    self,
    timeout=6,
) -> bool
```

进入基地房间列表页面（按 `i` 键）。返回是否成功进入。

---

### 1.8 UI 等待

#### `wait_ui_stable`

```python
def wait_ui_stable(
    self,
    method='phash',
    threshold=5,
    stable_time=0.5,
    max_wait=5,
    refresh_interval=0.2,
) -> bool
```

等待 UI 界面停止变化（动画结束等）后再继续执行。

| 参数 | 说明 |
|------|------|
| `method` | 帧比较算法：`'phash'`（感知哈希）、`'dhash'`（差分哈希）、`'pixel'`（像素差）、`'ssim'`（结构相似度） |
| `threshold` | phash/dhash 为汉明距离（默认 5）；pixel 为平均像素差（默认 5）；ssim 为相似度（默认 0.98） |
| `stable_time` | 连续稳定所需时间（秒），默认 `0.5` |
| `max_wait` | 最大等待时间（秒），默认 `5` |
| `refresh_interval` | 截帧间隔（秒），默认 `0.2` |

返回 `True` 表示已稳定，`False` 表示超时。

---

#### `wait_pop_up`

```python
def wait_pop_up(
    self,
    time_out=15,
    after_sleep=0,
)
```

等待弹窗出现并自动关闭，常用于等待奖励弹窗。

---

#### `safe_back`

```python
def safe_back(
    self,
    match,
    box=None,
    time_out: float = 30,
    ocr_time_out: float = 2,
)
```

在 `time_out` 秒内等待 OCR 匹配出现；若未出现则持续按 `Back` 键直到匹配或超时。

---

#### `wait_login`

```python
def wait_login(self)
```

阻塞等待登录完成，进入主界面后返回。

---

### 1.9 日志与状态

#### `log_info`

```python
def log_info(
    self,
    msg: str,
)
```

输出 INFO 级别日志，显示在 GUI 日志面板和控制台。

#### `log_debug`

```python
def log_debug(
    self,
    msg: str,
)
```

输出 DEBUG 级别日志（仅在 debug 模式下显示）。

#### `log_error`

```python
def log_error(
    self,
    msg: str,
)
```

输出 ERROR 级别日志。

#### 日志使用约定

1. 在任务类 / Mixin 内，优先使用 `self.log_info/self.log_debug/self.log_error`。
2. 在非任务模块中，使用模块级 logger（`Logger.get_logger(__name__)`）。
3. 运行时代码中避免使用 `print` 作为日志输出。

---

#### `info_set`

```python
def info_set(
    self,
    key,
    value,
)
```

在 GUI 状态栏显示当前进度信息。多账号模式下会自动在 `key` 后追加账号后缀。

```python
self.info_set("当前据点", "武陵-西城")
self.info_set("剩余体力", remaining_stamina)
```

---

### 1.10 图像处理

#### `isolate_by_hsv_ranges`

```python
def isolate_by_hsv_ranges(
    self,
    frame,
    ranges,
    invert=True,
    kernel_size=2,
) -> ndarray
```

使用 HSV 颜色掩码提取指定颜色区域。`ranges` 为 `HSVRange` 枚举列表。`invert=True` 时将目标颜色区域以外设为黑色（常用于 OCR 前处理）。

```python
from src.image.hsv_config import HSVRange
white_only = self.isolate_by_hsv_ranges(frame, [HSVRange.WHITE_TEXT])
```

---

#### `make_hsv_isolator`

```python
def make_hsv_isolator(
    self,
    ranges,
) -> Callable
```

返回一个闭包函数 `frame_processor`，可直接传给 `find_feature` 或 `ocr` 的 `frame_processor` 参数。

```python
processor = self.make_hsv_isolator([HSVRange.GOLD_TEXT])
result = self.ocr(box=price_box, frame_processor=processor)
```

---

### 1.11 YOLO 检测

#### `yolo_detect`

```python
def yolo_detect(
    self,
    name: str | list[str],
    frame=None,
    box=None,
    conf=0.7,
) -> list[Box]
```

使用 YOLOv8 ONNX 模型检测目标，按置信度降序返回 `Box` 列表。

| 参数 | 类型 | 说明 |
|------|------|------|
| `name` | `str \| list[str]` | 目标类别名称（单个或多个） |
| `frame` | `ndarray \| None` | 指定帧，`None` 则自动截取 |
| `box` | `Box \| None` | 限制检测 ROI，`None` 为全屏 |
| `conf` | `float` | 置信度阈值，默认 `0.7` |

```python
boxes = self.yolo_detect("battle_end", box=self.box.center, conf=0.6)
```

---

### 1.12 登录界面专用

以下方法使用 Win32 直接截取登录界面（绕过 WGC 不截取登录界面的限制）。

#### `login_screenshot`

```python
def login_screenshot(self) -> ndarray
```

截取当前登录界面截图并返回。

#### `login_ocr`

```python
def login_ocr(
    self,
    x=0,
    y=0,
    to_x=1,
    to_y=1,
    match=None,
    width=0,
    height=0,
    box=None,
    name=None,
    threshold=0,
    target_height=0,
    use_grayscale=False,
    log=False,
    frame_processor=None,
    lib='default',
)
```

参数同 `ocr`，但基于 `login_screenshot()` 的截图。

#### `login_find_feature`

```python
def login_find_feature(
    self,
    feature_name=None,
    horizontal_variance=0,
    vertical_variance=0,
    threshold=0,
    use_gray_scale=False,
    x=-1,
    y=-1,
    to_x=-1,
    to_y=-1,
    width=-1,
    height=-1,
    box=None,
    canny_lower=0,
    canny_higher=0,
    frame_processor=None,
    template=None,
    match_method=cv2.TM_CCOEFF_NORMED,
    screenshot=False,
    mask_function=None,
    frame=None,
)
```

参数同 `find_feature`，但基于 `login_screenshot()` 的截图。

---

### 1.13 其它工具

#### `screen_center`

```python
def screen_center(self) -> tuple[int, int]
```

返回游戏窗口的中心像素坐标 `(cx, cy)`。

---

#### `read_essence_info`

```python
def read_essence_info(self) -> EssenceInfo | None
```

对当前屏幕截图进行基质信息识别，返回 `EssenceInfo` 对象或 `None`。

---

#### `kill_game`

```python
def kill_game(self)
```

强制终止游戏进程（用于异常恢复）。

---

## 2. ScreenPosition

`self.box` 属性类型，按当前窗口分辨率生成各位置的 `Box`。

```python
from src.interaction.ScreenPosition import ScreenPosition
# 在 Task 内通过 self.box 访问
```

### 固定区域

| 属性 | 说明 |
|------|------|
| `self.box.top` | 上半屏幕（全宽） |
| `self.box.bottom` | 下半屏幕（全宽） |
| `self.box.left` | 左半屏幕（全高） |
| `self.box.right` | 右半屏幕（全高） |
| `self.box.top_left` | 左上四分之一 |
| `self.box.top_right` | 右上四分之一 |
| `self.box.bottom_left` | 左下四分之一 |
| `self.box.bottom_right` | 右下四分之一 |
| `self.box.center` | 中心区域（宽高各 50%） |

### 导航面板

| 属性 | 说明 |
|------|------|
| `self.box.nav_b` | 背包键（B）图标位置 |
| `self.box.nav_c` | 角色键（C）图标位置 |
| `self.box.nav_esc` | ESC 菜单图标位置 |
| `self.box.nav_panel` | 导航面板整体区域 |

### 战斗技能栏

| 属性 | 说明 |
|------|------|
| `self.box.combat_skill_1` ~ `combat_skill_4` | 技能 1-4 图标位置 |
| `self.box.combat_ult_1` ~ `combat_ult_4` | 终极技能 1-4 图标位置 |
| `self.box.combat_default_link_skill` | 连携技（E键）图标位置 |
| `self.box.combat_skill_bar` | 技能栏整体区域 |
| `self.box.combat_ult_bar` | 终极技能栏整体区域 |

### 交互

| 属性 | 说明 |
|------|------|
| `self.box.interact_pick_f` | F 键拾取提示图标位置 |

### 自定义区域

使用 `self.box_of_screen(x1, y1, x2, y2)` 按比例创建任意 `Box`（均为 `0~1`）：

```python
price_area = self.box_of_screen(0.4, 0.6, 0.8, 0.8)
```

---

## 3. KeyConfigManager

管理游戏热键与用户自定义按键的映射关系。通过 `self.key_manager` 访问。

```python
from src.interaction.KeyConfig import KeyConfigManager, DEFAULT_COMMON_KEYS, DEFAULT_INDUSTRY_KEYS, DEFAULT_COMBAT_KEYS
```

### 默认按键表

**通用（DEFAULT_COMMON_KEYS）**

| 配置键名 | 默认按键 | 说明 |
|---------|---------|------|
| `Dodge Key` | `lshift` | 闪避 |
| `Jump Key` | `space` | 跳跃 |
| `Interact Key` | `f` | 交互 |
| `Backpack Key` | `b` | 背包 |
| `Valuables Key` | `n` | 贵重品 |
| `Team Key` | `u` | 队伍 |
| `Operator Key` | `c` | 干员 |
| `Mission Key` | `j` | 任务 |
| `Track Key` | `v` | 追踪 |
| `Map Key` | `m` | 地图 |
| `Baker Key` | `h` | 帝江号 |
| `Mail Key` | `k` | 邮件 |
| `Quick Tool Key` | `r` | 快捷工具 |

**集成工业（DEFAULT_INDUSTRY_KEYS）**

| 配置键名 | 默认按键 | 说明 |
|---------|---------|------|
| `Industry Plan Key` | `t` | 工业规划 |
| `Place Belt Key` | `e` | 放置传送带 |
| `Place Pipeline Key` | `q` | 放置管道 |
| `Equipment List Key` | `z` | 设备列表 |
| `Overview Mode Key` | `capslock` | 全局视图 |
| `Storage Mode Key` | `x` | 仓库模式 |

**战斗（DEFAULT_COMBAT_KEYS）**

| 配置键名 | 默认按键 | 说明 |
|---------|---------|------|
| `Link Skill Key` | `e` | 连携技 |

### 方法

#### `resolve_common_key`

```python
def resolve_common_key(
    self,
    key: str,
) -> str
```

将默认通用按键值映射为用户自定义值。若用户未自定义，返回原值。

```python
actual = self.key_manager.resolve_common_key('m')  # 返回用户设置的地图键
```

#### `resolve_industry_key`

```python
def resolve_industry_key(
    self,
    key: str,
) -> str
```

将默认工业按键值映射为用户自定义值。

#### `resolve_combat_key`

```python
def resolve_combat_key(
    self,
    key: str,
) -> str
```

将默认战斗按键值映射为用户自定义值。

#### `update_config`

```python
def update_config(
    self,
    key_config: dict,
)
```

更新用户按键配置（通常在任务初始化时由框架自动调用）。

---

## 4. BattleMixin

战斗能力 Mixin，提供技能释放、战斗状态检测、排轴等能力。

```python
from src.tasks.mixin.battle_mixin import BattleMixin
```

#### `in_combat`

```python
def in_combat(
    self,
    required_yellow=0,
) -> bool
```

判断是否处于战斗状态。`required_yellow` 为必要的黄色格子数（用于更严格的判定）。

#### `in_team`

```python
def in_team(self) -> bool
```

判断是否已组队（队伍成员 > 1）。

#### `is_combat_ended`

```python
def is_combat_ended(self) -> bool
```

判断战斗是否结束（检测结算/奖励界面）。

#### `wait_in_combat`

```python
def wait_in_combat(
    self,
    time_out=3,
    click=False,
) -> bool
```

等待进入战斗状态。`click=True` 时会同时持续点击鼠标触发攻击。

#### `use_ult`

```python
def use_ult(
    self,
    ult_sequence: str = None,
)
```

释放终极技能。`ult_sequence` 为角色序列字符串（如 `'1234'`），`None` 时使用配置值。

#### `use_link_skill`

```python
def use_link_skill(self)
```

释放连携技（按 `Link Skill Key`）。

#### `perform_attack_weave`

```python
def perform_attack_weave(self)
```

执行普攻排轴（按配置的技能序列持续输出）。

#### `approach_enemy`

```python
def approach_enemy(self)
```

向最近敌人靠近（移动至攻击范围内）。

#### `auto_battle`

```python
def auto_battle(
    self,
    no_battle: bool = False,
)
```

完整自动战斗循环：持续普攻/技能/终极，直到战斗结束。`no_battle=True` 时仅等待战斗结束而不主动攻击。

#### `get_skill_bar_count`

```python
def get_skill_bar_count(self) -> int
```

通过图像识别获取当前技能槽数量。

#### `ocr_lv`

```python
def ocr_lv(self) -> int | None
```

OCR 识别当前角色等级，返回整数或 `None`。

---

## 5. MapMixin

地图操作 Mixin，提供传送点导航能力。

```python
from src.tasks.mixin.map_mixin import MapMixin
```

#### `task_to_transfer_point`

```python
def task_to_transfer_point(
    self,
    test_target_box=None,
)
```

打开任务追踪界面，定位并执行传送点传送。`test_target_box` 用于测试时指定目标 Box。

#### `to_near_transfer_point`

```python
def to_near_transfer_point(
    self,
    test_target_box,
)
```

导航至附近传送点（短距离）。

---

## 6. NavigationMixin

导航循环 Mixin，提供目标追踪与自动对齐能力。

```python
from src.tasks.mixin.navigation_mixin import NavigationMixin
```

#### `navigate_until_target`

```python
def navigate_until_target(
    self,
    target_ocr_pattern,
    nav_feature_name,
    time_out: int = 60,
    pre_loop_callback=None,
    found_special_callback=None,
)
```

持续导航直到目标 Feature 出现（到达目标点）。

| 参数 | 说明 |
|------|------|
| `target_feature_in_map` | 地图上的目标特征（小地图图标等） |
| `target_feature_out_map` | 到达目标后屏幕上出现的特征（如 NPC 交互框） |
| `time_out` | 最大等待秒数 |

#### `start_tracking_and_align_target`

```python
def start_tracking_and_align_target(
    self,
    target_feature_in_map,
    target_feature_out_map,
)
```

启动追踪：循环对齐目标方向并前进，直到发现目标出现在地图外（即已到达）。

#### `align_ocr_or_find_target_to_center`

```python
def align_ocr_or_find_target_to_center(
    self,
    ocr_match_or_feature_name_list,
    only_x=False,
    only_y=False,
    box=None,
    threshold=0.8,
    max_time=50,
    ocr=True,
    use_yolo=False,
    back_prev=False,
    raise_if_fail=True,
    is_num=False,
    need_scroll=False,
    max_step=100,
    min_step=20,
    slow_radius=200,
    once_time=0.5,
    tolerance=TOLERANCE,
    ocr_frame_processor_list=None,
)
```

旋转视角（水平鼠标移动）使目标特征或 OCR 文字居中。

| 参数 | 说明 |
|------|------|
| `target_feature` | 目标特征名（优先使用） |
| `ocr_match` | OCR 匹配条件（`target_feature` 为 `None` 时使用） |
| `box` | 搜索区域 |
| `max_step` | 每步最大像素偏移 |
| `min_step` | 减速阶段最小步长 |
| `slow_radius` | 距中心多少像素内开始减速 |

---

## 7. LiaisonMixin

干员联络 Mixin，提供帝江号导航、联络站寻路、送礼完整流程。继承自 `NavigationMixin`。

```python
from src.tasks.mixin.liaison_mixin import LiaisonMixin
```

#### `transfer_to_home_point`

```python
def transfer_to_home_point(
    self,
    box=None,
    should_check_out_boat=False,
)
```

传送到帝江号右侧传送点（使用大地图传送）。`should_check_out_boat=True` 时若当前在好友船则先退出。

#### `navigate_to_main_hall`

```python
def navigate_to_main_hall(self) -> bool
```

从传送点导航至帝江号主厅。返回是否成功。

#### `navigate_to_operator_liaison_station`

```python
def navigate_to_operator_liaison_station(self)
```

从主厅导航至干员联络站（自动追踪并对齐 NPC）。

#### `perform_operator_liaison`

```python
def perform_operator_liaison(self)
```

执行完整的干员联络流程：寻路 → 交互 → 送礼 → 领奖 → 退出。

#### `collect_and_give_gifts`

```python
def collect_and_give_gifts(self)
```

在联络站内遍历所有可送礼干员并完成送礼。

---

## 8. ZipLineMixin

滑索操作 Mixin，提供滑索距离识别与连续移动能力。继承自 `NavigationMixin`。

```python
from src.tasks.mixin.zip_line_mixin import ZipLineMixin
```

#### `on_zip_line_start`

```python
def on_zip_line_start(
    self,
    delivery_to,
    need_scroll=None,
)
```

启动单段滑索：等待出现滑索距离标识 → OCR 识别距离 → 对齐 → 按 `E` 发射。`delivery_to` 为目标位置描述。

#### `zip_line_list_go`

```python
def zip_line_list_go(
    self,
    zip_line_list,
    need_scroll=None,
)
```

顺序执行多段滑索列表 `zip_line_list`，每段调用 `on_zip_line_start`。

---

## 9. LoginMixin

登录流程 Mixin，提供自动登出、密码登录完整流程。

```python
from src.tasks.mixin.login_mixin import LoginMixin
```

#### `login_flow`

```python
def login_flow(
    self,
    username: str,
    password: str,
)
```

完整登录流程：登出当前账号 → 输入用户名/密码 → 等待进入主界面。

---

## 10. EssenceRecognizer（纯算法层）

基质词条识别模块，不依赖截图，可单独测试。

```python
from src.essence.essence_recognizer import parse_essence_panel, read_essence_info, EssenceInfo, EssenceEntry
```

### 数据类

#### `EssenceEntry`

```python
@dataclass
class EssenceEntry:
    name: str       # 词条名称
    value: str      # 词条数值文本
    level: int      # 词条强化等级（0 表示未识别）
```

#### `EssenceInfo`

```python
@dataclass
class EssenceInfo:
    name: str                  # 基质名称
    entries: list[EssenceEntry]# 词条列表
    source: str                # 来源标注（如装备类型）

    @property
    def entry_names(self) -> tuple[str, ...]: ...   # 所有词条名元组
    @property
    def key(self) -> str: ...                       # 用于匹配毕业规则的唯一键
```

### 函数

#### `parse_essence_panel`

```python
def parse_essence_panel(
    texts: Sequence[Box],
    *,
    max_entries: int = 3,
) -> EssenceInfo | None
```

从 OCR 结果列表（`task.ocr()` 返回值）中解析基质面板，返回 `EssenceInfo` 或 `None`。纯算法，无截图依赖。

#### `read_essence_info`

```python
def read_essence_info(task) -> EssenceInfo | None
```

高层封装：截图 → OCR → `parse_essence_panel`，直接返回当前屏幕的基质信息。

#### `ocr_essence_panel`

```python
def ocr_essence_panel(task) -> list[Box]
```

对当前屏幕进行基质面板 OCR，返回原始 OCR Box 列表。

#### `ocr_essence_levels`

```python
def ocr_essence_levels(task) -> list[Box]
```

OCR 识别基质词条强化等级数字，返回 Box 列表。

---

## 11. 数据工具函数

### world_map_utils

```python
from src.data.world_map_utils import get_area_by_outpost_name, get_goods_by_outpost_name, get_stage_category
```

#### `get_area_by_outpost_name`

```python
def get_area_by_outpost_name(outpost_name: str) -> str
```

根据据点名称返回所属地区名。

```python
area = get_area_by_outpost_name("武陵-西城")  # → "武陵"
```

#### `get_goods_by_outpost_name`

```python
def get_goods_by_outpost_name(outpost_name: str) -> list[str]
```

返回指定据点可交易的货品列表。

#### `get_stage_category`

```python
def get_stage_category(stage_name: str) -> str
```

根据副本名称返回副本类型分类（如「普通」「高阶」「能量淤积点」）。

---

### characters_utils

```python
from src.data.characters_utils import get_contact_list_with_feature_list
```

#### `get_contact_list_with_feature_list`

```python
def get_contact_list_with_feature_list() -> dict[str, str]
```

返回当前可联络干员的字典：`{内部英文 key: Feature 图片名}`，用于联络站寻路时的模板匹配。

---

### build_name_patterns

```python
from src.tasks.mixin.common import build_name_patterns
```

#### `build_name_patterns`

```python
def build_name_patterns(find_name: str) -> list[re.Pattern]
```

将干员名称（中文或英文）转换为 OCR 匹配用的正则列表，自动处理常见 OCR 误识字符。

```python
patterns = build_name_patterns("凯尔希")
result = self.wait_ocr(match=patterns[0], box=name_box)
```
