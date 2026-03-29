"""
Common utilities for tasks.

本文件包含任务系统中常用的基础数据结构与工具函数：

内容包括：
- 干员联络名称匹配规则构建
- 联络流程结果枚举
- 商品信息数据结构
- 通用任务工具函数

依赖：
    ok.Box
    BaseEfTask
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional
from src.data.FeatureList import FeatureList as fL
from src.data.ocr_normalize_map import ocr_confusion_map
from ok import Box
from src.tasks.BaseEfTask import BaseEfTask


def build_name_patterns(find_name: str):
    """
    根据角色名称生成 OCR 匹配模式（优化版：合并 regex，避免组合爆炸）
    """

    # 1️⃣ 滑窗拆分
    if len(find_name) >= 2:
        keys = [find_name[i : i + 2] for i in range(len(find_name) - 1)]
    else:
        keys = [find_name]

    patterns = []

    for key in keys:
        parts = []

        for ch in key:
            if ch in ocr_confusion_map:
                options = [ch] + ocr_confusion_map[ch]
                part = "(" + "|".join(map(re.escape, options)) + ")"
            else:
                part = re.escape(ch)

            parts.append(part)

        # 拼成一个 regex
        pattern = "".join(parts)
        patterns.append(re.compile(pattern))

    return patterns


class LiaisonResult(int, Enum):
    """
    干员联络导航流程结果枚举。

    用于描述导航到联络站时的状态。

    枚举值：

        SUCCESS
            成功到达目标

        FAIL
            导航失败

        FIND_CHAT_ICON
            导航过程中发现干员聊天交互图标
    """

    SUCCESS = 1
    FAIL = 2
    FIND_CHAT_ICON = 3


@dataclass
class GoodsInfo:
    """
    单个货物信息结构。

    用于记录商店识别到的商品信息。

    Attributes:
        good_name (str):
            商品名称

        good_price (int):
            商品价格

        friend_price (Optional[int]):
            好友价格（若存在）

        stock_quantity (int):
            库存数量

        name_box (Box):
            商品名称区域

        friend_name_box (Optional[Box]):
            好友价格区域
    """

    good_name: str
    good_price: int
    friend_price: Optional[int]
    stock_quantity: int
    name_box: "Box"
    friend_name_box: Optional["Box"]


class Common(BaseEfTask):
    """
    任务系统的通用工具类。

    提供多个任务之间共享的功能。
    """

    def detect_ticket_number(self):
        """
        识别当前门票数量。

        通过 OCR 读取屏幕右上角的门票信息，
        格式通常为：

            120/200

        Returns:
            int
                当前门票数量

                若识别失败则返回默认值 200
        """

        result = self.wait_ocr(
            match=re.compile(r'^\d{1,4}/\d{1,3}$'),
            box=self.box_of_screen(
                1400 / 1920,
                0,
                1,
                70 / 1080
            ),
            log=True
        )

        if result:
            ticket = int(result[0].name.split("/")[0])

            self.log_info(f"ticket:{ticket}")

            return ticket
        else:
            # OCR失败时默认返回最大值
            return 200
    def plus_max(self):
        for plus_button in [fL.plus_button, fL.market_plus_button]:
            plus_button = self.find_one(feature_name=plus_button, box=self.box.bottom_right, threshold=0.8)
            if plus_button:
                break

        if not plus_button:
            return False
        plus_click_place = plus_button
        plus_click_place.x -= int((1849 - 1741) / 1920 * self.width)
        self.log_info("找到加号按钮，执行点击")
        self.click(plus_click_place, after_sleep=1)
        return True
