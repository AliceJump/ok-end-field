# -*- coding: utf-8 -*-
import os


def install_pre_config_patch():
    """导入 src.config 前的预处理。

    Raises:
        None
    """
    # WA: set default PATH to resolve qfluentwidgets access os.environ['PATH'] issue
    # 使用 os.defpath 而非空串，避免非绝对路径命令（如 git/python）无法解析
    os.environ.setdefault("PATH", os.defpath)
