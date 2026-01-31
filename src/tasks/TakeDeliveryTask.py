import re
import time
from qfluentwidgets import FluentIcon

from ok import TriggerTask, Logger
from src.tasks.BaseEfTask import BaseEfTask

logger = Logger.get_logger(__name__)


class TakeDeliveryTask(BaseEfTask, TriggerTask):
    """
    TakeDeliveryTask

    功能：自动接取高价值调度任务。
    逻辑：同时识别“报酬金额”与“调度券类型（图标）”，满足条件则接取，否则刷新。

    配置说明：
    - `target_tickets`: 目标券种，列表。可选值：`ticket_valley`, `ticket_wuling`。
    - `min_reward`: 最低报酬金额（万）。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "运送委托接取"
        self.description = "在运送委托列表界面开启,任务按报酬排序,接武陵券需将滚动条拉到底部"
        self.icon = FluentIcon.ACCEPT
        self.default_config = {
            '_enabled': True,
            '接取谷地券': False,
            '接取谷地券最低金额': 5.0,
            '接取武陵券': True,
            '接取武陵券最低金额': 5.0
        }

    def process_ocr_results(self, full_texts, filter_min, reward_pattern):
        """
        处理 OCR 结果，提取奖励、接取按钮和刷新按钮。
        方便单元测试逻辑。
        """
        rewards = []
        accept_btns = []
        refresh_btn = None

        for t in full_texts:
            name = t.name.strip()
            if ("刷新" in name or "秒后可刷新" in name) and t.y > self.height * 0.8:
                refresh_btn = t
            elif "接取运送委托" in name:
                accept_btns.append(t)
            else:
                match = reward_pattern.search(name)
                if match:
                    try:
                        val = float(match.group(1))
                        if val >= filter_min and val <= 100:
                            rewards.append((t, val))  # 保存 OCR对象 和 提取出的数值
                        elif val > 100:
                            self.log_debug(f"金额异常过大({val}万)，可能是OCR误识别，已过滤")
                    except:
                        pass
        return rewards, accept_btns, refresh_btn

    def detect_ticket_type(self, reward_obj, ticket_types):
        """
        根据报酬文本的位置，推算图标区域，并尝试识别券种。
        """
        search_hw_ratio = 3.6
        search_h_ratio = 2.4

        min_box_size = 70

        search_width = max(reward_obj.height * search_hw_ratio, min_box_size)
        search_height = max(reward_obj.height * search_h_ratio, min_box_size)

        x_offset_val = (reward_obj.width / 2) - (search_width / 2)
        y_offset_val = -search_height
        target_real_height = search_height + reward_obj.height * 0.5

        icon_search_box = reward_obj.copy(
            x_offset = x_offset_val,
            y_offset = y_offset_val,
            width_offset = search_width - reward_obj.width,
            height_offset = target_real_height - reward_obj.height
        )

        # 边界检查
        if icon_search_box.y < 0:
            icon_search_box.height += icon_search_box.y
            icon_search_box.y = 0
        if icon_search_box.x < 0:
            icon_search_box.width += icon_search_box.x
            icon_search_box.x = 0

        found_ticket = self.find_feature(ticket_types, box=icon_search_box)
        if found_ticket:
             # 如果返回的是列表，取第一个
            return found_ticket[0] if isinstance(found_ticket, list) else found_ticket
        return None

    def run(self):
        reward_regex = r"(\d+\.?\d*)万"
        reward_pattern = re.compile(reward_regex, re.I)

        # 读取券种配置
        enable_valley = self.config.get('接取谷地券', False)
        enable_wuling = self.config.get('接取武陵券', True)
        valley_min = float(self.config.get('接取谷地券最低金额', 5.0))
        wuling_min = float(self.config.get('接取武陵券最低金额', 5.0))

        ticket_types = []
        if enable_valley:
            ticket_types.append('ticket_valley')
        if enable_wuling:
            ticket_types.append('ticket_wuling')

        if not ticket_types:
            self.log_info("警告: 未启用任何券种，任务退出")
            return

        active_mins = []
        if enable_valley:
            active_mins.append(valley_min)
        if enable_wuling:
            active_mins.append(wuling_min)
        filter_min = min(active_mins)

        while True:
            if not self.enabled:
                break

            try:

                full_texts = self.ocr(box=self.box_of_screen(0.05, 0.15, 0.95, 0.95))
                rewards, accept_btns, refresh_btn = self.process_ocr_results(full_texts, filter_min, reward_pattern)

                target_btn = None
                matched_msg = ""

                # 2. 遍历满足金额条件的所有报酬行，检查图标类型
                for reward_obj, val in rewards:
                    # 寻找该行对应的接取按钮
                    r_cy = reward_obj.y + reward_obj.height / 2
                    my_btn = None
                    for btn in accept_btns:
                        if abs(r_cy - (btn.y + btn.height / 2)) < btn.height * 0.8:
                            my_btn = btn
                            break

                    if not my_btn:
                        continue # 该行没找到按钮，跳过

                    ticket_result = self.detect_ticket_type(reward_obj, ticket_types)

                    if ticket_result:
                        # 根据具体的图标类型判断对应的金额阈值
                        is_qualified = False
                        if ticket_result.name == 'ticket_valley' and enable_valley and val >= valley_min:
                            is_qualified = True
                        elif ticket_result.name == 'ticket_wuling' and enable_wuling and val >= wuling_min:
                            is_qualified = True

                        if is_qualified:
                            target_btn = my_btn
                            matched_msg = f"金额={val}万, 类型={ticket_result.name}"
                            self.log_info(f"匹配成功: {matched_msg}")
                            break
                        else:
                            self.log_debug(f"类型匹配({ticket_result.name})但金额({val}万)不达标")
                    else:
                        self.log_debug(f"金额符合({val}万)但未找到券种图标")

                # 4. 执行操作
                if target_btn:
                    # 匹配成功后，增加日志并点击
                    self.log_info(f"准备接取任务：{matched_msg}")
                    self.click(target_btn, after_sleep=2, down_time=0.1)
                    return True
                else:
                    self.log_info("未找到符合条件(金额+类型)的委托，检测刷新")

                    # 1. 更新刷新按钮位置记忆 (只要OCR里有，就刷新位置)
                    if refresh_btn:
                        self.last_known_refresh_btn = refresh_btn

                    # 2. 尝试执行盲点刷新
                    # 逻辑：只要知道位置且CD到了就点，完全忽略当前文字内容（避免OCR读数卡死或识别错误）
                    btn_to_click = getattr(self, 'last_known_refresh_btn', None)

                    if btn_to_click:
                        last_click = getattr(self, 'last_refresh_time', 0)
                        elapsed = time.time() - last_click

                        if elapsed < 5.2:
                            # CD未好，检查检测次数
                            current_count = getattr(self, 'ocr_count_after_click', 0) + 1
                            self.ocr_count_after_click = current_count

                            if current_count < 2:
                                # 还不到2次，等待0.5s后继续循环进行下一次OCR
                                self.log_debug(f"刷新后第 {current_count} 次检测未果，等待 0.5s 继续检测...")
                                self.sleep(0.5)
                                continue
                            else:
                                # 已满2次，直接睡到CD结束
                                wait_time = 5.2 - elapsed
                                if wait_time > 0:
                                    self.log_debug(f"已检测 {current_count} 次，不再检测，等待 {wait_time:.2f}s 后刷新...")
                                    self.sleep(wait_time)

                        # CD已好（或睡醒），执行点击
                        self.log_info(f"执行盲点刷新 (坐标: {int(btn_to_click.x)}, {int(btn_to_click.y)})")
                        self.click(btn_to_click, after_sleep=0.5, down_time=0.2)
                        self.last_refresh_time = time.time()
                        self.ocr_count_after_click = 0 # 重置计数器
                    else:
                        self.log_info("警告: 尚未定位到刷新按钮位置，无法盲点")
                        self.sleep(1.0)
                        continue
            except Exception as e:
                self.log_info(f"TakeDeliveryTask error: {e}")
                if "SetCursorPos" in str(e) or "拒绝访问" in str(e):
                    self.log_info("警告: 检测到权限不足或光标控制失败，请尝试【以管理员身份运行】程序！")
                self.sleep(2)
                continue
