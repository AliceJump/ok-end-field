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

from ok import Box

from src.core.BaseEfTask import BaseEfTask
from src.data.FeatureList import FeatureList as fL


def edit_distance(left: str, right: str, max_distance: int | None = None) -> int | None:
    """计算 Levenshtein 距离，插入、删除和替换一个字符的代价均为 1。

    Args:
        left: 待比较的字符串。
        right: 待比较的另一个字符串。
        max_distance: 允许的最大编辑距离，须为非负整数；None 表示不限制。

    Returns:
        阈值内的实际编辑距离（包含 0），超限时返回 None。
        未设置阈值时返回完整编辑距离。
    """
    # 让较短的 left 对应滚动数组，将额外空间限制为 O(min(m, n))。
    if len(left) > len(right):
        return edit_distance(right, left, max_distance=max_distance)
    # 长度差是编辑距离的下界，超限时无需继续计算。
    if max_distance is not None and len(right) - len(left) > max_distance:
        return None
    previous = list(range(len(left) + 1))
    for i, right_char in enumerate(right, 1):
        current = [i]
        for j, left_char in enumerate(left, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (left_char != right_char)))
        # 当前 right 前缀与 left 任意前缀的距离都超限时，可以提前结束：
        # 后续行的最小值不会减小，因此最终距离也必然超限。
        if max_distance is not None and min(current) > max_distance:
            return None
        previous = current
    distance = previous[-1]
    return distance if max_distance is None or distance <= max_distance else None



def build_name_patterns(find_name: str):
    """
    根据角色名称生成 OCR 匹配模式（优化版：合并 regex，避免组合爆炸）
    """

    # 1️⃣ 滑窗拆分
    keys = [find_name[i : i + 2] for i in range(len(find_name) - 1)] if len(find_name) >= 2 else [find_name]

    patterns = []

    for key in keys:
        parts = []

        for ch in key:
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
    friend_price: int | None
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

                若识别失败则返回默认值 0
        """

        result = self.wait_ocr(
            match=re.compile(r"^[\d.]*k?/\d+k?$", re.IGNORECASE),
            box=self.box_of_screen(1400 / 1920, 0, 1, 70 / 1080),
            log=True,
        )

        if result:
            ticket_str = result[0].name.split("/")[0]
            if "k" in ticket_str.lower():
                # "2k" -> 2000, "k" -> 1000
                num_part = ticket_str.lower().replace("k", "")
                ticket = int(float(num_part) * 1000) if num_part else 1000
            else:
                ticket = int(ticket_str)

            self.log_info(f"ticket:{ticket}")

            return ticket
        else:
            # OCR失败时默认返回 0
            return 0

    def plus_max(self):
        for plus_button in [fL.plus_button, fL.market_plus_button]:
            plus_button = self.wait_feature(
                feature=plus_button, box=self.box.bottom_right, threshold=0.8, raise_if_not_found=False, time_out=1
            )
            if plus_button:
                break

        if not plus_button:
            return False
        plus_click_place = plus_button
        plus_click_place.x -= int((1849 - 1741) / 1920 * self.width)
        self.log_info("找到加号按钮，执行点击")
        self.click(plus_click_place, after_sleep=0.5)
        return True
