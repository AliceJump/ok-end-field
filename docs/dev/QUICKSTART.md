# ok-ef 开发者快速开始

返回：[文档索引](../zh-CN/index.md) / [README](https://github.com/AliceJump/ok-end-field/blob/master/README.md)

## 1. 从源码运行

| 项目 | 当前要求 |
|------|----------|
| 操作系统 | Windows 10/11；交互、截图和登录流程依赖 Win32 |
| Python | 3.12；CI 和 China 打包配置均固定为 3.12 |
| 权限 | 建议管理员权限运行终端/IDE，以与游戏权限一致 |
| 游戏窗口 | 16:9，最低 `1920x1080`（1080P）；见 `src/config.py` |

```powershell
git clone --recurse-submodules https://github.com/AliceJump/ok-end-field.git
Set-Location ok-end-field
```

经上述命令克隆后，确保安装了 [uv](https://docs.astral.sh/uv/) 这一依赖管理程序，随后：

```powershell
uv sync
uv run python main_debug.py
```

若 clone 时漏了子模块：

```powershell
git submodule update --init --recursive
```

`main.py` 和 `main_debug.py` 都会先调用 `install_startup_patches()`，再构造 `ok.OK(config)`；Debug 入口只额外设置 `config["debug"] = True`。

## 2. 新增一次性任务

最小任务放在 `src/tasks/onetime/MyTask.py`：

```python
from src.core.BaseEfTask import BaseEfTask


class MyTask(BaseEfTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "我的任务"
        self.description = "执行一个可重复验证的步骤"
        self.default_config.update({"等待秒数": 1})
        self.config_description.update({
            "等待秒数": "操作完成后的等待时间。",
        })

    def run(self):
        self.ensure_main()
        self.log_info("任务开始")
        self.sleep(self.config.get("等待秒数", 1))
```

在 `src/config.py` 的 `config["onetime_tasks"]` 中注册模块路径和类名：

```python
["src.tasks.onetime.MyTask", "MyTask"],
```

不要直接赋值 `self.default_config = {...}`；多重继承任务需要保留 MRO 中其它 Mixin 已注册的配置。

## 3. 新增触发式任务

```python
from ok import TriggerTask

from src.core.BaseEfTask import BaseEfTask


class MyTriggerTask(BaseEfTask, TriggerTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "我的触发任务"
        self.description = "后台检查条件并执行动作"

    def run(self):
        if self.find_one("some_feature"):
            self.log_info("检测到目标")
```

注册到 `config["trigger_tasks"]`：

```python
["src.tasks.trigger.MyTriggerTask", "MyTriggerTask"],
```

现有组合可作为 MRO 参考：`AutoCombatTask(BattleMixin, TriggerTask)`、`ItemNavigatorTask(WsPositionMixin, BaseEfTask, TriggerTask)`。业务 Mixin 已继承 `BaseEfTask` 时不需要再次显式列出 `BaseEfTask`。

## 4. 复用现有能力

核心能力现在位于 `src/core/base_mixin/`，业务能力位于 `src/tasks/mixin/`：

```python
from src.tasks.mixin.battle_mixin import BattleMixin
from src.tasks.mixin.map_mixin import MapMixin


class MyBattleTask(MapMixin, BattleMixin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "地图战斗任务"

    def run(self):
        self.ensure_main()
        self.ensure_map()
```

常用规则：

- Feature 使用 `FeatureList` 或其中存在的字符串名称；`find_feature` 返回列表，`find_one` 返回首项。
- OCR 文本使用 `self.lang.<module>.<key>`，资源写入统一的 `assets/lang/<module>.json`。
- 可改键操作用 `press_key`、`press_industry_key` 或 `press_combat_key`；参数传默认按键值。
- `self.click(box)` 可点击识别框；当前项目封装不使用 `click(box=...)` 示例。
- `login_flow(username)` 选择登录界面的最近账号，不输入密码。

## 5. 验证

运行全部测试：

```powershell
uv run python -m unittest discover -s tests -p "Test*.py"
```

仓库的 `scripts/testing/run_tests.ps1` 会逐个运行 `tests/*.py`，也可使用：

```powershell
.\scripts\testing\run_tests.ps1
```

新增语言引用后至少运行：

```powershell
uv run python -m unittest tests.TestCheckLang
uv run python -m unittest tests.TestPoLocaleConsistency
```

涉及窗口、OCR 或游戏状态的行为仍需用 `main_debug.py` 实机验证；测试集中既有纯离线测试，也有依赖样本图片和 `ok-script` 测试工具的测试，不能概括为全部不需要项目资源。

## 相关文档

- [开发指南](DEVELOPMENT.md)：架构、目录、任务注册、配置、测试和发布
- [API 参考](API.md)：项目自有基类和 Mixin 接口
- [i18n 与 OCR 配置流程](i18n_OCR配置流程.md)：统一语言 JSON 与 OCR 混淆补丁
- [文字识别示例](文字识别示例.md)
- [图像模板匹配示例](图像模板匹配示例.md)
