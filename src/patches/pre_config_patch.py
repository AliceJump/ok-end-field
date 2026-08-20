# -*- coding: utf-8 -*-
import os


def install_pre_config_patch():
    """导入 src.config 前的预处理。

    Raises:
        None
    """
    # WA: set empty PATH to resolve qfluentwidgets access os.environ['PATH'] issue
    if "PATH" not in os.environ:
        os.environ["PATH"] = ""
