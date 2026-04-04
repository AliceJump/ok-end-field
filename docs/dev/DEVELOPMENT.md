# ok-ef 开发文档

> 本文档面向希望参与或了解 ok-ef 项目开发的开发者，涵盖项目架构、目录结构、各文件职责、开发流程、测试与发布，以及已完成功能与建议计划表。

---

## 目录

1. [项目概览](#1-项目概览)
2. [架构总览](#2-架构总览)
3. [目录结构与文件职责](#3-目录结构与文件职责)
4. [开发环境搭建](#4-开发环境搭建)
5. [开发流程](#5-开发流程)
6. [测试](#6-测试)
7. [CI / CD 与发布流程](#7-ci--cd-与发布流程)
8. [已完成功能一览](#8-已完成功能一览)
9. [建议计划表（路线图）](#9-建议计划表路线图)

---

## 1. 项目概览

**ok-ef** 是基于 [ok-script](https://github.com/ok-oldking/ok-script) 框架开发的《终末地》游戏自动化工具。  
核心技术栈：

| 层次 | 技术 |
|------|------|
| 底层框架 | ok-script（截图、OCR、模板匹配、UI） |
| 截图 | WGC / BitBlt（Windows） |
| OCR | OnnxOCR + OpenVINO 加速 |
| 目标检测 | YOLOv8 ONNX 模型（OpenVINO 推理） |
| 模板匹配 | OpenCV TM_CCOEFF_NORMED |
| UI | PyQt6 / PySide6 + PyQt-Fluent-Widgets |
| 交互 | Windows PostMessage / Win32 API |
| 语言 | Python 3.12（仅支持此版本） |
| 打包 | PyAppify |

---

## 2. 架构总览

```
main.py / main_debug.py
        │
        ▼
   ok.OK(config)          ← ok-script 框架主入口，负责窗口捕获、任务调度、GUI
        │
        ├── onetime_tasks  ← 用户点击触发的一次性任务
        │       ├── DailyTask         (日常任务聚合)
        │       ├── BattleTask        (刷体力)
        │       ├── DeliveryTask      (自动送货)
        │       ├── TakeDeliveryTask  (运送委托接取)
        │       ├── WarehouseTransferTask (仓库物品转移)
        │       ├── EssenceScanTask   (基质扫描/上锁)
        │       ├── PeriodicScreenshotTask (定时截图)
        │       └── Test / TestStartGame  (开发调试用)
        │
        └── trigger_tasks  ← 后台持续运行的触发式任务
                ├── AutoCombatTask    (自动战斗)
                ├── AutoSkipDialogTask(自动跳过剧情)
                ├── AutoPickTask      (自动拾取)
                └── AutoLoginTask     (自动登录/领月卡)
```

### Mixin 继承链（DailyTask 为例）

```
DailyTask
 ├── DailyBuyMixin        (买物资)
 ├── DailyBattleMixin     (刷体力)  ─── BattleMixin, MapMixin, ZipLineMixin
 ├── DailyTradeMixin      (买卖货)  ─── NavigationMixin
 ├── DailyShopMixin       (信用商店)
 ├── DailyRoutineMixin    (其它日常) ── LiaisonMixin ─── NavigationMixin
 ├── DailyLiaisonMixin    (送礼)    ─── LiaisonMixin
 └── AccountMixin         (多账号)  ─── LoginMixin
```

---

## 3. 目录结构与文件职责

```
ok-end-field/
├── main.py                    # 正式版入口：启动 ok.OK(config)
├── main_debug.py              # 调试版入口：config['debug']=True
├── requirements.in            # 顶层依赖（ok-script, onnxocr, openvino, opencv-python）
├── requirements.txt           # 完整锁定依赖（pip-compile 生成）
├── pyappify.yml               # PyAppify 打包配置（China/Global/Debug 三种 Profile）
├── deploy.txt                 # 部分同步到更新库的文件列表
├── run_tests.ps1              # 本地一键跑所有单元测试（PowerShell）
├── auto_release.py/.ps1       # 辅助打版本 tag 的脚本
│
├── src/                       # 项目核心源码
│   ├── config.py              # 全局配置字典（传给 ok.OK），定义所有任务列表、窗口参数、OCR 参数等
│   ├── globals.py             # 全局单例（Globals），可存放跨任务共享状态（目前为空壳）
│   ├── OpenVinoYolo8Detect.py # YOLOv8 ONNX + OpenVINO 推理封装（战斗结束检测等）
│   │
│   ├── data/                  # 纯数据层，无 UI 无截图依赖
│   │   ├── FeatureList.py     # 枚举：所有模板匹配特征名（对应 assets/images/ 里的图片文件名）
│   │   ├── characters.py      # 干员数据字典（内部英文 key → 中文名/英文名/星级）
│   │   ├── characters_utils.py# 干员数据工具函数（生成可联络列表等）
│   │   ├── ocr_normalize_map.py # OCR 混淆字符映射（如 "别"↔"別"），用于纠正识别误差
│   │   ├── world_map.py       # 地图数据：地区列表、据点字典、可交易货品、副本分类等
│   │   ├── world_map_utils.py # 地图数据工具函数（按据点名获取地区等）
│   │   └── zh_en.py           # 中英翻译字典（物品中文名 → 英文 feature 名、仓库分类映射）
│   │
│   ├── essence/               # 基质（装备词条）识别模块
│   │   ├── __init__.py
│   │   ├── essence_recognizer.py # 核心：OCR 解析基质面板（名称、词条、等级），纯算法无截图
│   │   └── weapon_data.py    # 武器毕业词条数据与匹配函数
│   │
│   ├── image/                 # 图像处理工具层
│   │   ├── frame_processes.py # isolate_by_hsv_ranges：通用 HSV 颜色掩码提取
│   │   ├── hsv_config.py      # HSVRange 枚举：预定义颜色范围（白色、金色文字、深灰文字）
│   │   └── login_screenshot.py# 通过 Win32 截取登录界面（绕过 WGC 限制）
│   │
│   ├── interaction/           # 游戏交互层（Windows 专用）
│   │   ├── EfInteraction.py   # 继承 PostMessageInteraction，实现点击/滚动/键盘（后台可用）
│   │   ├── Key.py             # 移动方向键映射（WASD 等）
│   │   ├── KeyConfig.py       # 游戏热键配置（DEFAULT_COMMON_KEYS 等）及 KeyConfigManager
│   │   ├── Mouse.py           # 鼠标辅助函数（active_and_send_mouse_delta、run_at_window_pos 等）
│   │   └── ScreenPosition.py  # ScreenPosition：按屏幕比例生成 Box（top/bottom/left/right 等）
│   │
│   └── tasks/                 # 任务层（业务逻辑核心）
│       ├── BaseEfTask.py      # 所有任务的公共基类：封装特征查找、传送、导航、战斗状态判断等
│       ├── AutoCombatLogic.py # 自动战斗核心算法（独立于 Task，可被多个任务复用）
│       ├── AutoCombatTask.py  # 触发任务：后台自动战斗（使用 AutoCombatLogic）
│       ├── AutoLoginTask.py   # 触发任务：自动登录/领取月卡奖励
│       ├── AutoPickTask.py    # 触发任务：大世界自动拾取（白名单/黑名单）
│       ├── AutoSkipDialogTask.py # 触发任务：自动跳过剧情对话
│       ├── BattleTask.py      # 一次性任务：单独刷体力（复用 DailyBattleMixin）
│       ├── DailyTask.py       # 一次性任务：日常任务聚合执行器（MRO 组合各子 Mixin）
│       ├── DeliveryTask.py    # 一次性任务：自动送货（武陵，含滑索路径）
│       ├── EssenceScanTask.py # 一次性任务：基质扫描、自动上锁/弃置
│       ├── PeriodicScreenshotTask.py # 一次性任务：定时截图（用于样本采集）
│       ├── TakeDeliveryTask.py# 一次性任务：自动接取高价值运送委托（OCR+模板匹配）
│       ├── Test.py            # 开发调试用任务（随时可改，不上生产）
│       ├── TestStartGame.py   # 测试启动游戏流程（调试用）
│       ├── WarehouseTransferTask.py # 一次性任务：跨仓库物品转移
│       │
│       ├── account/
│       │   └── account_mixin.py # 多账号模式：账号列表解析、切号逻辑
│       │
│       ├── daily/             # DailyTask 的子 Mixin，每个文件对应一组日常子任务
│       │   ├── __init__.py
│       │   ├── daily_battle_mixin.py  # 刷体力（副本选择、能量淤积点导航）
│       │   ├── daily_buy_mixin.py     # 买物资（稳定物资需求、白名单过滤）
│       │   ├── daily_liaison_mixin.py # 送礼（干员联络台完整流程）
│       │   ├── daily_routine_mixin.py # 其它日常（邮件/委托/装备/收信用/线索/制造舱等）
│       │   ├── daily_shop_mixin.py    # 买信用商店（信用交易所、自动刷新）
│       │   └── daily_trade_mixin.py   # 买卖货（弹性需求物资、价格判断）
│       │
│       └── mixin/             # 通用能力 Mixin（跨任务复用）
│           ├── __init__.py
│           ├── battle_mixin.py    # 战斗能力：技能释放、必杀、连携技、战斗结束检测、排轴
│           ├── common.py          # 公共数据结构与工具：LiaisonResult、GoodsInfo、build_name_patterns
│           ├── liaison_mixin.py   # 干员联络：传送帝江号、导航联络站、送礼交互
│           ├── login_mixin.py     # 登录流程：登出→密码登录→等待进入主界面
│           ├── map_mixin.py       # 地图操作：打开任务界面→定位传送点→执行传送
│           ├── navigation_mixin.py# 导航循环：持续前进+动态对齐目标直到到达
│           └── zip_line_mixin.py  # 滑索操作：对齐滑索距离标识→按 E 连续移动
│
├── assets/                    # 静态资源（由 ok-script debug 模式自动裁剪生成）
│   ├── coco_detection.json    # COCO 格式标注，定义模板图片在游戏截图中的位置
│   ├── images/                # 模板匹配图片（文件名对应 FeatureList 枚举值）
│   ├── items/images/          # 物品图标模板（用于仓库转移物品识别）
│   └── models/yolo/best.onnx  # YOLOv8 战斗结束检测模型
│
├── docs/                      # 面向用户的功能说明文档
│   ├── 日常任务.md
│   ├── 体力本.md
│   ├── 排轴.md
│   └── 自动送货.md
│
├── target_doc/                # 待补充文档（需开发者填写）
│   └── 自动大世界收菜.md
│
├── i18n/                      # 国际化翻译文件（zh_CN/zh_TW/en_US/ja_JP/ko_KR/es_ES）
│
├── icons/                     # 程序图标
│
├── readme/                    # README 配图（1.jpg ~ 5.jpg）
│
├── tests/                     # 单元测试
│   ├── TestAutoCombat.py          # 战斗状态识别测试
│   ├── TestEssenceGoldGrid.py     # 基质金色格子识别测试
│   ├── TestEssenceImageFeatures.py# 基质图像特征测试
│   ├── TestEssenceRecognizer.py   # 基质 OCR 解析逻辑测试
│   ├── TestTakeDeliveryFunctions.py # 运送委托接取逻辑测试
│   ├── TestWarehouseSwitchOCR.py  # 仓库切换 OCR 测试
│   └── images/                    # 测试用截图样本
│
├── .github/
│   ├── workflows/
│   │   ├── build.yml              # 主 CI：测试 → 同步更新库 → PyAppify 打包 → GitHub Release
│   │   ├── mirrorchyan_uploading.yml  # Mirror 酱上传
│   │   └── mirrorchyan_release_note.yml # Mirror 酱发布说明
│   └── ISSUE_TEMPLATE/            # Bug 报告模板
│
└── x-anylabeling-asset/       # AnyLabeling 标注工具配置（用于标注新模板图片）
```

---

## 4. 开发环境搭建

### 前提条件

- Windows 10/11（必须，依赖 Win32 API）
- Python **3.12**（严格要求，其它版本不受支持）
- **管理员权限**启动 IDE 或 CMD（模拟按键需要权限）

### 安装步骤

```bash
# 1. 克隆项目
git clone https://github.com/AliceJump/ok-end-field.git
cd ok-end-field

# 2. 安装依赖
pip install -r requirements.txt --upgrade

# 3. 运行 Debug 版本
python main_debug.py
```

> **提示**：安装路径必须是纯英文，避免中文路径导致截图或模型加载失败。

### IDE 推荐配置

- PyCharm / VSCode 以**管理员身份**运行
- 解释器选择系统 Python 3.12，不要用虚拟环境隔离 Win32 依赖
- 将 `ok-end-field/` 设为项目根目录，保证相对路径（`assets/`、`configs/` 等）正确解析

---

## 5. 开发流程

### 5.1 新增一次性任务

1. 在 `src/tasks/` 下新建 `MyTask.py`，继承 `BaseEfTask`（或已有的 Mixin 组合）：

   ```python
   from src.tasks.BaseEfTask import BaseEfTask

   class MyTask(BaseEfTask):
       def __init__(self, *args, **kwargs):
           super().__init__(*args, **kwargs)
           self.name = "我的任务"
           self.description = "任务说明"
           self.default_config = {"选项A": True}
           self.config_description = {"选项A": "选项A的说明"}

       def run(self):
           self.ensure_main()
           # 业务逻辑
   ```

2. 在 `src/config.py` 的 `onetime_tasks` 列表中注册：

   ```python
   ["src.tasks.MyTask", "MyTask"],
   ```

3. 运行 `main_debug.py` 验证任务出现在 UI 任务列表中。

### 5.2 新增触发式任务

继承 `BaseEfTask` 和 `TriggerTask`，并在 `config.py` 的 `trigger_tasks` 列表中注册，其余同上。

### 5.3 编写 Mixin 扩展

- 新建 `src/tasks/mixin/my_feature_mixin.py`，继承 `BaseEfTask`。
- 只包含一组功能的方法，不包含 `run()`。
- 在需要该功能的任务中通过 Python 多重继承组合：

  ```python
  class DailyTask(MyFeatureMixin, OtherMixin, ...):
      ...
  ```

- **注意 MRO（方法解析顺序）**：`__init__` 中调用 `super().__init__()` 即可，Python 的 C3 线性化会自动处理。`default_config` 使用 `update` 叠加，不要直接赋值以免覆盖其它 Mixin 的配置。

### 5.4 添加新的模板图片（Feature）

1. 在 `main_debug.py` 模式下运行，框架会根据 `assets/coco_detection.json` 自动裁剪保留标注区域。
2. 使用 **AnyLabeling**（配置在 `x-anylabeling-asset/`）对新截图打矩形框标注，导出 COCO JSON，合并到 `assets/coco_detection.json`。
3. 运行 `compress.py`（cwd 为项目根目录），脚本会自动压缩图片并更新 `src/data/FeatureList.py`（无需手动填写）。
4. 在代码中通过 `self.find_feature(fL.my_new_feature)` 调用。

> 分辨率适配：若需要支持 2K/4K，按命名约定 `feature_name_2k`、`feature_name_4k` 提供对应尺寸的图片，`BaseEfTask.get_feature_by_resolution()` 会自动按分辨率选择。

### 5.5 添加新的 OCR 识别逻辑

- 使用 `self.ocr(box=..., match=re.compile("关键字"))` 进行区域 OCR。
- 使用 `self.wait_ocr(match=..., time_out=5)` 等待并返回结果。
- 使用 `self.wait_click_ocr(match=..., box=..., time_out=5)` 等待并点击。
- 若 OCR 有混淆字符问题，在 `src/data/ocr_normalize_map.py` 中添加映射。

### 5.6 热键适配

游戏内默认快捷键定义在 `src/interaction/KeyConfig.py`（`DEFAULT_COMMON_KEYS`、`DEFAULT_INDUSTRY_KEYS`、`DEFAULT_COMBAT_KEYS`）。若需要按热键操作，**不要硬编码按键字面值**（如 `'f'`），应通过下列封装函数发送，以自动适配用户自定义按键。

> ⚠️ 若某个按键允许用户自定义，**不可将该按键对应的 UI 元素作为模板图片**，否则用户改键后模板匹配将会失效。

#### 可用的按键发送函数

代码中只允许使用以下四种按键操作方式：

**1. 三个任务方法（在 `BaseEfTask` 子类中使用）**

```python
# 发送通用热键（DEFAULT_COMMON_KEYS 中的按键，如交互键、背包键等）
self.press_key(key: str, down_time: float = 0.02, after_sleep: float = 0, interval: int = -1)

# 发送集成工业专用热键（DEFAULT_INDUSTRY_KEYS）
self.press_industry_key(key: str, down_time: float = 0.02, after_sleep: float = 0, interval: int = -1)

# 发送战斗专用热键（DEFAULT_COMBAT_KEYS）
self.press_combat_key(key: str, down_time: float = 0.02, after_sleep: float = 0, interval: int = -1)
```

`key` 参数传入**默认按键字面值**（如 `'f'`、`'b'`），框架会通过 `KeyConfigManager.resolve_key()` 自动替换为用户的自定义值。

示例：

```python
# ✅ 正确：使用封装函数，支持用户改键
self.press_key('f')          # 交互键，默认 'f'，用户可自定义

# ❌ 错误：硬编码发送，用户改键后失效
self.send_key('f')
```

**2. `move_keys`（移动按键，仅用于方向键组合）**

```python
from src.interaction.move_interaction import move_keys

move_keys(hwnd, keys, duration)
```

- `keys`：`str` 或 `list[str]`，仅限 `"w"` / `"a"` / `"s"` / `"d"`
- `duration`：按住时长（秒）

此函数通过 `keybd_event` 模拟原始按键，适用于需要精确控制方向键持续时间的场景（如自动寻路移动）。方向键当前不在自定义热键范围内，可直接使用字面值。

### 5.7 代码规范

- 任务类和 Mixin 均使用中文 `name`/`description`/`config_description`，面向最终用户。
- 方法注释使用中文或中英双语。
- 不要在 Mixin 中直接定义 `name`/`description`（它们属于 Task）。
- 所有新 Mixin 需继承自 `BaseEfTask` 以保证类型正确，即使不直接使用其方法。
- 涉及战斗的配置（如 `技能释放`、`启动技能点数` 等）在 `AutoCombatTask` 和 `DailyBattleMixin` 中各维护一份，两处同步修改（代码中已有注释提示）。

---

## 6. 测试

### 运行全部测试

```powershell
# PowerShell（Windows）
./run_tests.ps1
```

```bash
# 或逐个运行
python -m unittest tests/TestEssenceRecognizer.py
python -m unittest tests/TestAutoCombat.py
```

### 测试文件说明

| 文件 | 测试内容 |
|------|----------|
| `TestAutoCombat.py` | 战斗/非战斗状态图像识别（使用 `tests/images/` 截图样本） |
| `TestEssenceRecognizer.py` | 基质 OCR 解析逻辑（`parse_essence_panel`、`_attach_levels`） |
| `TestEssenceGoldGrid.py` | 基质金色格子图像识别 |
| `TestEssenceImageFeatures.py` | 基质图像 Feature 匹配 |
| `TestTakeDeliveryFunctions.py` | 运送委托接取 OCR 结果处理逻辑（`process_ocr_results`） |
| `TestWarehouseSwitchOCR.py` | 仓库切换 OCR 识别 |

### 测试注意事项

- 测试全部为**离线单元测试**（使用本地截图样本），不需要运行游戏。
- 新功能开发时，将相关截图放入 `tests/images/`，编写对应 unittest。
- CI 会在每次打 tag 前自动运行所有测试，测试失败则中止发布。

---

## 7. CI / CD 与发布流程

### 触发条件

推送以 `v` 开头的 git tag（如 `v0.2.3`）后自动触发 `.github/workflows/build.yml`。

### CI 步骤

```
1. 配置 git UTF-8 编码
2. checkout（含 LFS）
3. 安装 Python 3.12 + requirements.txt
4. 内联 ok-script 源码（inline_ok_requirements）—— 减小用户更新包体积
5. 运行 tests/ 全部单元测试
6. 同步部分文件到更新库（cnb.cool + GitHub）—— 供已安装用户增量更新
7. PyAppify 打包 exe（China / Global 两个 Profile）
8. 发布 GitHub Release（附带更新日志 + 安装包）
9. 触发 Mirror 酱上传 & 发布说明 workflow
```

### 手动打版本

```bash
git tag v0.x.y
git push origin v0.x.y
```

或使用项目提供的辅助脚本：

```powershell
# PowerShell
./auto_release.ps1
```

```python
# 或 Python
python auto_release.py
```

---

## 8. 已完成功能一览

### 触发式任务（后台持续运行）

- [x] **自动战斗**：检测战斗开始/结束，自动普攻/技能/必杀/连携技，支持自定义排轴序列
- [x] **自动跳过剧情**：识别跳过按钮并自动确认
- [x] **自动拾取**：大世界白名单/黑名单过滤自动采集
- [x] **自动登录**：自动完成登录流程并领取月卡奖励

### 一次性任务

- [x] **日常任务**（完整流程，以下均可独立开关）
  - [x] 送礼（干员联络台赠礼、路遇干员交互）
  - [x] 收邮件
  - [x] 据点兑换（遍历所有地区/据点，支持优先货品序列）
  - [x] 转交运送委托 & 领取奖励
  - [x] 造装备（套组制造）
  - [x] 简易制作
  - [x] 收信用（好友助力 + 信用交易所领取）
  - [x] 帝江号收菜（线索收集 + 制造仓收取）
  - [x] 买信用商店（武库配额、嵌晶玉，自动刷新）
  - [x] 买卖货（弹性需求物资，价格上下限自动判断）
  - [x] 刷体力（全副本类型，含能量淤积点滑索导航）
  - [x] 买物资（稳定物资，白名单过滤）
  - [x] 周常奖励 & 日常奖励领取
  - [x] 多账号模式
- [x] **刷体力**（独立任务，复用日常任务刷体力逻辑）
- [x] **自动送货**（武陵，滑索路径配置化，支持多目标 NPC）
- [x] **运送委托接取**（OCR 识别奖励金额 + 图标识别券种，自动抢单）
- [x] **仓库物品转移**（发货仓库 → 收货仓库，支持多轮次）
- [x] **基质扫描**（OCR 解析词条，自动上锁毕业基质/弃置垃圾基质）
- [x] **定时截图**（数据采集 / YOLO 样本收集辅助工具）

### 底层能力

- [x] 后台截图（WGC + BitBlt 双模式）
- [x] OpenVINO 加速 OCR（CPU/NPU 自动选择）
- [x] YOLOv8 ONNX 战斗结束检测（支持 NPU 加速）
- [x] 多分辨率模板匹配适配（1080p/2K/4K）
- [x] HSV 颜色掩码辅助 OCR（金色文字、白色文字）
- [x] 游戏热键配置化（支持用户自定义按键）
- [x] 国际化框架（i18n，支持 6 种语言）
- [x] 滑索导航（距离标识 OCR 识别 + 自动对齐）

---

## 9. 建议计划表（路线图）

> 优先级：🔴 高 / 🟡 中 / 🟢 低  
> 状态：⬜ 未开始 / 🔄 进行中 / ✅ 已完成

### 功能扩展

| 优先级 | 功能 | 说明 | 状态 |
|--------|------|------|------|
| 🔴 | 更多副本支持 | 扩充 `stages_dict` 并适配进入/退出流程 | ⬜ |
| 🔴 | 大世界自动巡逻采集 | 按预设路线自动移动并触发 AutoPickTask | ⬜ |
| 🔴 | 更多据点/地区支持 | 随版本更新扩充 `world_map.py`（地区、据点、货品） | 🔄 |
| 🟡 | 更多干员联络支持 | 持续更新 `characters.py` 以支持新干员 | 🔄 |
| 🟡 | 基质扫描：更多词条规则 | 扩充 `weapon_data.py` 覆盖更多武器/基质毕业标准 | ⬜ |
| 🟡 | 仓库转移：更多物品 | 扩充 `item_to_warehouse_dict` 和 `ITEM_TRANSLATION_DICT` | ⬜ |

### 工程质量

| 优先级 | 项目 | 说明 | 状态 |
|--------|------|------|------|
| 🔴 | 补充核心模块单元测试 | 优先覆盖 `daily_trade_mixin`、`daily_battle_mixin` 的纯逻辑部分 | ⬜ |
| 🔴 | 完善用户文档 | `target_doc/` 下待补充的功能文档 | 🔄 |
| 🟡 | 统一错误处理规范 | 定义标准异常类型，减少裸 `except` | ⬜ |
| 🟡 | 配置项校验 | 对用户输入的配置项（如价格、券数）做范围/格式校验并给出友好提示 | ⬜ |
| 🟡 | 战斗结束 YOLO 模型扩充 | 增加更多战斗结束/失败场景的训练样本 | ⬜ |
| 🟢 | 重构 AutoCombatTask/DailyBattleMixin 重复代码 | 当前有注释说明两处需同步修改，应抽取共用基类 | ⬜ |
| 🟢 | 多语言内容补全 | 完善 `i18n/` 下英文等语言的翻译条目 | ⬜ |

### 基础设施

| 优先级 | 项目 | 说明 | 状态 |
|--------|------|------|------|
| 🟡 | 测试截图样本库扩充 | 覆盖更多分辨率（2K/4K）和游戏版本的样本图 | ⬜ |
| 🟢 | 自动化集成测试 | 基于录制回放的端到端测试（模拟截图序列） | ⬜ |

---

## 附录：关键 API 速查

### BaseEfTask 常用方法

| 方法 | 说明 |
|------|------|
| `self.ensure_main()` | 等待并确保进入游戏主界面 |
| `self.find_feature(fL.xxx)` | 模板匹配，返回 Box 列表或 None |
| `self.find_one(fL.xxx)` | 模板匹配，返回第一个 Box 或 None |
| `self.ocr(box=..., match=...)` | OCR 识别指定区域，match 可为字符串/正则 |
| `self.wait_ocr(match=..., time_out=5)` | 等待 OCR 匹配，超时返回 None |
| `self.wait_click_ocr(match=..., box=...)` | 等待 OCR 匹配后点击 |
| `self.click(box_or_xy)` | 点击 Box 中心或绝对坐标 |
| `self.press_key("key")` | 按键（支持 after_sleep） |
| `self.scroll(x, y, count)` | 鼠标滚轮(仅UI滚动) |
| `self.sleep(seconds)` | 等待（支持被中断检测） |
| `self.box_of_screen(x1, y1, x2, y2)` | 按比例创建 Box |
| `self.log_info/log_debug/log_error(msg)` | 日志输出 |
| `self.info_set(key, value)` | 在 UI 状态栏显示当前进度 |
| `self.in_combat_world()` | 判断是否在大世界（非战斗/副本） |
| `self.transfer_to_home_point()` | 传送到帝江号(默认左侧)传送点 |
| `self.align_ocr_or_find_target_to_center(...)` | 移动视角使扫描目标居中 |

[更多API](API.md)

### ScreenPosition（self.box）

| 属性 | 说明 |
|------|------|
| `self.box.top` | 上半屏幕 Box |
| `self.box.bottom` | 下半屏幕 Box |
| `self.box.left` | 左半屏幕 Box |
| `self.box.right` | 右半屏幕 Box |
| `self.box.top_left/top_right/bottom_left/bottom_right` | 四象限 Box |
