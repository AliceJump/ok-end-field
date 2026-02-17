# Copilot Instructions for ok-end-field

## 项目架构与主要组件
- 本项目为基于图像识别的自动化工具，目录结构清晰，核心代码位于 `src/` 下，按功能模块划分：
  - `src/data/`：地图、角色等数据结构与工具
  - `src/essence/`：精华识别与武器数据处理
  - `src/interaction/`：与屏幕、键鼠的交互封装
  - `src/tasks/`：各类自动化任务（如战斗、登录、日常等），每个任务为独立 Python 类
  - `src/ui/`：界面相关
- 配置文件集中在 `configs/`，支持任务、窗口、设备等多种配置，JSON 格式
- 资源文件（图片、数据集等）在 `assets/`，日志在 `logs/`

## 开发与调试流程
- 仅支持 Python 3.12，需以管理员权限运行（见 README.md）
- 安装依赖：`pip install -r requirements.txt --upgrade`
- 运行主程序：
  - Release: `python main.py`
  - Debug: `python main_debug.py`
- 测试用例位于 `tests/`，可直接运行单测脚本
- 日志输出详见 `logs/`，调试时关注 `ok-script.log.*`

## 约定与模式
- 任务类需继承自 `BaseEfTask`（见 `src/tasks/BaseEfTask.py`），并在 `configs/` 配置对应 JSON
- 图像识别、交互等均通过 `src/interaction/` 封装，避免直接操作底层库
- 配置与数据文件路径需使用相对路径，便于跨平台兼容
- 仅支持简体中文部分功能，注意国际化相关代码在 `i18n/`
- 不允许直接修改游戏文件或内存，仅模拟用户操作

## 依赖与集成
- 依赖见 `requirements.txt`，部分功能依赖外部项目 [ok-script](https://github.com/ok-oldking/ok-script)
- 资源与模型文件需放置于 `assets/`，如需新增数据集或图片，按现有结构归类

## 典型模式示例
- 新增自动化任务：
  1. 在 `src/tasks/` 新建 Python 文件，继承 `BaseEfTask`
  2. 在 `configs/` 新增对应 JSON 配置
  3. 在主程序注册任务
- 新增配置项：在 `configs/` 新增或修改 JSON 文件，并在相关模块读取

## 参考文件
- 结构与开发流程详见 [README.md](../README.md)
- 任务与交互模式详见 `src/tasks/`、`src/interaction/`
- 配置与国际化详见 `configs/`、`i18n/`

---
如遇特殊约定或不确定点，请优先查阅 README.md 或现有同类代码实现。