# ok-ef 开发者快速开始

> 目标读者：希望为 ok-ef 贡献代码的开发者。

---

## 1. 从源码运行项目

### 1.1 环境要求

| 项目 | 要求 |
|------|------|
| 操作系统 | Windows |
| Python | **3.12**（仅支持此版本） |
| 运行权限 | **管理员权限**（必须；需以管理员身份启动 CMD / PyCharm / VSCode） |
| 安装路径 | 纯英文路径（例如 `D:\dev\ok-end-field`），不要含中文或空格 |

### 1.2 克隆仓库

```bash
git clone --recurse-submodules https://github.com/AliceJump/ok-end-field.git
cd ok-end-field
```

> 项目包含子模块，务必加上 `--recurse-submodules`。

### 1.3 安装依赖

```bash
pip install -r requirements.txt --upgrade
```

### 1.4 启动程序

```bash
# Release 模式
python main.py

# Debug 模式（截图/日志更详细，推荐开发时使用）
python main_debug.py
```

程序启动后会打开 GUI 窗口，左侧列出所有可用任务。

---

## 2. 新建一个触发式任务

触发式任务（`TriggerTask`）在后台持续运行，满足条件时自动激活。以下示例新增一个最小化的触发式任务。

### 2.1 创建任务文件

在 `src/tasks/` 下新建文件，例如 `MyTriggerTask.py`：

```python
from ok import TriggerTask
from src.tasks.BaseEfTask import BaseEfTask

class MyTriggerTask(BaseEfTask, TriggerTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "我的触发任务"
        self.description = "在此描述任务功能"

    def run(self):
        # 在此编写触发后执行的逻辑
        self.log_info("触发任务已执行")
```

> **提示**：若任务需要战斗能力，可额外继承 `BattleMixin`；需要地图导航则继承 `MapMixin`。
> 日志建议统一使用 `self.log_info/self.log_debug/self.log_error`，避免在运行时代码中使用 `print`。

### 2.2 注册任务

打开 `src/config.py`，将新任务加入 `trigger_tasks` 列表：

```python
config = {
    ...
    "trigger_tasks": [
        ...,
        ["src.tasks.MyTriggerTask", "MyTriggerTask"],  # 新增
    ],
    ...
}
```

### 2.3 运行与验证

重新启动程序（`python main_debug.py`），在 GUI 右侧的触发任务列表中即可看到并启用新任务。

---

## 3. 新建一次性任务（可选）

一次性任务（`BaseTask` 子类）由用户点击触发，执行完毕后自动停止。流程与触发式任务相同，区别在于：

- 继承 `BaseTask`（而非 `TriggerTask`）
- 注册到 `config["onetime_tasks"]` 列表

---

## 后续阅读

| 文档 | 说明 |
|------|------|
| [DEVELOPMENT.md](DEVELOPMENT.md) | 完整架构、目录结构、CI/CD |
| [API.md](API.md) | BaseEfTask、Mixin、ScreenPosition 等详细 API |
