# OCR 使用分类梳理

> 梳理日期：2026-08-21。统计项目内所有 OCR 调用（`src/` 下共 157 处，27 个文件），按「识别结果是否被业务读取」分为三类，供开发与 OCR 混淆补丁维护参考。

## 核心判断标准

- **点击型**：识别结果只当坐标用（识别到就点击），**不读取 `Box.name` 内容**。
- **判断型**：读取 `box.name` / 识别文本做分支判断、数字解析、状态判断，**或依据 OCR 匹配结果是否存在/可见性做决策**（如导航中判断目标是否可见，即使不读 `name`）——识别结果本身是决策依据。
- **混合/复杂**：点击 + 读内容联动，或同一代码块多次 OCR 协同。

## 分类汇总

| 分类 | 数量 | 特征 |
|------|------|------|
| 点击型 | ~70 处 | `wait_click_ocr` 一键点击；`wait_ocr` 结果直接 `click(result[0])`；坐标对准 |
| 判断型 | ~35 处 | 读 `name` 做数字解析 / 状态判断 / 名称决策；OCR 结果可见性判断 |
| 混合/复杂 | ~10 处 | 点击后读 name 决定走向；大区域全文本解析 |
| 未分类 | ~42 处 | 纯调试测试（`test_ocr` 等）、重复调用的辅助调用，未进入业务分类 |

> 三类合计约 115 处，其余 ~42 处为调试/辅助调用（如 `daily_outpost_mixin.py` 的 `test_ocr`），故总数 157 处。

## 一、点击型（识别只当坐标）

形式：
- `wait_click_ocr(...)` 一步完成——领奖、换队、使用药剂、页签切换、确定/放弃按钮。
- `wait_ocr` 结果直接 `self.click(result[0])`——定位按钮后点击。
- 坐标对准型：`src/core/base_mixin/navigation_mixin.py:413` `align_ocr_or_find_target_to_center`，结果只取 x/y 做鼠标对准移动，被 battle / zip_line / delivery 复用。

典型文件：
- `src/tasks/daily/misc/daily_reward_mixin.py`（周常/理智补给各领取入口，多为布尔判断的点击）
- `src/tasks/daily/misc/daily_craft_mixin.py`（存放/装备/制作页签）
- `src/tasks/daily/misc/daily_logistics_mixin.py`（一键领取、各节点按钮）
- `src/tasks/onetime/WarehouseTransferTask.py`（仓库切换、确认、存放）
- `src/tasks/onetime/DemoDrawTask.py`、`src/tasks/trigger/AutoInteractionTask.py`

## 二、判断型（需要识别结果做业务决策）

### 2.1 数字 / 价格解析
- 门票：`src/tasks/mixin/common.py:134` `detect_ticket_number`，`re.compile(r'^[\d.]*k?/\d+k?$')` 解析 "2k"→2000。
- 价格：`src/tasks/daily/daily_shop_mixin.py:84/91`、`src/tasks/daily/daily_trade_mixin.py:86/139/157`。
- 时效：`src/tasks/daily/daily_battle_mixin.py:371/402`，单位（天/小时）+ 数字解析排序消耗。
- 据点券数：`src/tasks/daily/misc/daily_outpost_mixin.py:10`。

### 2.2 状态判断
- 等待文案出现/消失：`zip_line_mixin.py`（滑索停止提示）、`daily_liaison_mixin.py`（舰桥/信赖弹窗）、`game_flow_mixin.py`、`DeliveryTask.py` 的 tab 文案。
- 目标/标记可见性：`navigation_mixin.py:112/185`（导航中 OCR 判断目标是否可见，不读 name 不点击）。
- 导航回退：`runtime_mixin.py:450` `safe_back` 用 OCR 判断目标可见性。

### 2.3 名称决策
- 拾取黑/白名单：`src/tasks/trigger/AutoPickTask.py:50-53`，读 `texts[0].name` 决定是否拾取 / 按 F 次数。
- 仓库识别：`WarehouseTransferTask.py:73-80`，读 name 判断"武陵仓库/谷地仓库"。
- 装备词条：`src/essence/essence_recognizer.py:367`，全量文本聚类解析基质名称/来源/词条/等级。

## 三、混合 / 复杂

- 点击后读 name 决定走向：
  - `daily_credit_mixin.py:10-17`：点击"收取信用/无待领取"后，`"收取信用" in result[0].name` 决定是否 `wait_pop_up`。
  - `daily_boat_mixin.py:231`：点击后按 name 判断"收取/培养/停工"哪种。
  - `daily_battle_mixin.py:798`：读 name 过滤"已选"后点击。
  - `daily_logistics_mixin.py:129`：点击 + 读 name 决定后续步骤起始下标。
- 大区域全文本解析：
  - `DeliveryTask.py:259-301`：多区域 OCR 文本计数 + 名称分组组装 DeliveryRow。
  - `TakeDeliveryTask.py:188`：全量文本 + 正则金额解析 + 接取判断。
- 复杂联动：`DeliveryTask.py:539-550` 同一代码块内坐标点击 + 状态判断多次 OCR。

## 敏感度结论

**判断型核心链路**（对 OCR 识别质量最敏感，配 `ocr_text_fix.json` 混淆补丁收益最大）：
- `AutoPickTask`（掉落物名称决策）
- `DeliveryTask` / `TakeDeliveryTask`（委托行、金额、地点回填）
- `daily_trade`（价格筛选）
- `essence_recognizer`（词条文本解析）

除上述核心链路外，其余约 105 处（点击型 + 混合型 + 未分类）对识别内容不敏感，只需"能匹配到位置"即可，混淆补丁对其帮助有限。

## 附注

- `daily_outpost_mixin.py:201/208/215` 的 `test_ocr` / `test_ocr_full` 为纯 OCR 识别测试（调试用途）。
- 判断"点击型 vs 判断型"时以**是否读取识别文本内容**为准，识别后只当坐标 = 点击型。
