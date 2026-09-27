"""项目路径解析统一入口。

配置目录名一律取 ``Config.config_folder``（其值由 ok 框架在 ``Ok`` 实例化时
从 ``src/config.py`` 的 ``config["config_folder"]`` 注入），避免各模块写死 ``"configs"``。

⚠️ 不要在**模块级**调用本模块的函数并把结果存成常量：
``Config.config_folder`` 的类默认值要到 ``Ok`` 实例化后才会被替换成真实值，
模块级求值会把 import 时刻的默认值固化下来。需要模块级路径时，改成函数
（惰性求值），在使用点再调用。
"""

from __future__ import annotations

from ok.util.config import Config
from ok.util.file import get_relative_path

DEFAULT_CONFIG_FOLDER = "configs"


def config_folder() -> str:
    """返回配置目录名（默认 ``configs``）。"""
    return Config.config_folder or DEFAULT_CONFIG_FOLDER


def config_path(*parts: str) -> str:
    """返回配置目录下的路径（基准为当前工作目录，已归一化）。"""
    return get_relative_path(config_folder(), *parts)
